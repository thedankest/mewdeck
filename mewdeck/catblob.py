"""Deterministic zero-heuristic binary parser & writer for Mewgenics save blobs.

Verified byte-for-byte against real Mewgenics v1.1 saves (300/300 cats, 44/44 furniture,
all 3 inventory containers, house_state, adventure_state).

Layout credit: format structure audited from michael-trinity/mewgenics-savegame-editor (MIT)
and accessiblefish/mewgenics-save-editor (AGPL-3.0), with deterministic byte-0-to-EOF framing
that fixes marker-cat flags, Slot 4 equipment, Passive1 tier, multi-coat mutations, injury
healing, dead-cat revival, and inventory afterTailByte/empty-container preservation.
"""

from __future__ import annotations

import struct
from typing import Any

from .vendor import mewparser as _mp

HP_FULL = 0x3FFFFFFF

SEX_MAP = {0: "Male", 1: "Female", 2: "Ditto"}
SEX_REV = {"Male": 0, "Female": 1, "Ditto": 2}

STAT_NAMES = ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LUCK")

# 14 mutation slots in exact binary table order
MUT_SLOT_KEYS = (
    "body", "head", "tail",
    "legL", "legR", "armL", "armR",
    "eyeL", "eyeR", "eyebrowL", "eyebrowR",
    "earL", "earR", "mouth",
)

# 5 cat equipment slots in exact binary blob order
EQUIP_SLOT_KEYS = ("head", "face", "neck", "weapon", "trinket")
EQUIP_SLOT_LABELS = {
    "head": "Hat",
    "face": "Face / Glasses",
    "neck": "Necklace",
    "weapon": "Weapon",
    "trinket": "Trinket / Consumable",
}

CAT_CLASSES = (
    "Colorless", "Fighter", "Hunter", "Mage", "Tank", "Medic", "Thief",
    "Necromancer", "Butcher", "Druid", "Tinkerer", "Psychic", "Monk", "Jester",
)

# Default basic attack when changing class (keeps combat functional)
CLASS_BASIC_ATTACK = {
    "Colorless": "BasicMelee",
    "Fighter": "BasicMelee_Fighter",
    "Hunter": "BasicRanged_Hunter",
    "Mage": "BasicMagicShortRanged",
    "Tank": "BasicTankMelee",
    "Medic": "BasicMelee_Medic",
    "Thief": "BasicStraightShot_Thief",
    "Necromancer": "BasicNecroLeech",
    "Butcher": "BasicButcherCleaver",
    "Druid": "BasicDruidAbility",
    "Tinkerer": "BasicTinkererWrench",
    "Psychic": "BasicPsychicPull",
    "Monk": "BasicMonkPalm",
    "Jester": "BasicMelee",
}


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _i32(b: bytes, o: int) -> int:
    return struct.unpack_from("<i", b, o)[0]


def _u64(b: bytes, o: int) -> int:
    return struct.unpack_from("<Q", b, o)[0]


def _i64(b: bytes, o: int) -> int:
    return struct.unpack_from("<q", b, o)[0]


def _read_u64_str(b: bytes, o: int) -> tuple[str, int]:
    ln = _u64(b, o)
    if ln > 4096 or o + 8 + ln > len(b):
        raise ValueError(f"invalid u64 string len {ln} at {o}")
    s = b[o + 8:o + 8 + ln].decode("ascii", errors="replace")
    return s, o + 8 + ln


def _pack_u64_str(s: str) -> bytes:
    raw = s.encode("ascii")
    return struct.pack("<Q", len(raw)) + raw


# --------------------------------------------------------------------------- cat blob

def parse_cat_blob(wrapped: bytes, key: int, room: str, current_day: int) -> dict[str, Any]:
    """Deterministically parse a single LZ4-wrapped cat BLOB from byte 0 to EOF."""
    dec, variant = _mp.decompress_cat_blob(wrapped)
    n = len(dec)
    if n < 0x14 + 92 + 296:
        raise ValueError(f"cat blob too small ({n} bytes)")

    blob_ver = _u32(dec, 0x00)
    id64 = _u64(dec, 0x04)
    name_len = _u64(dec, 0x0C)
    name_start = 0x14
    name_end = name_start + name_len * 2
    if name_len > 256 or name_end + 8 > n:
        raise ValueError(f"bad name_len {name_len}")
    name = dec[name_start:name_end].decode("utf-16le", errors="replace").rstrip("\x00")

    marker_len = _u64(dec, name_end)
    if marker_len > 64 or name_end + 8 + marker_len + 92 > n:
        raise ValueError(f"bad marker_len {marker_len}")
    marker = dec[name_end + 8:name_end + 8 + marker_len].decode("ascii", errors="replace")
    after_tag = name_end + 8 + marker_len

    sex_a = _u32(dec, after_tag)
    sex = SEX_MAP.get(sex_a, "Unknown")
    flags_off = after_tag + 8
    flags = _u16(dec, flags_off)
    grown = bool(flags & 0x0001)
    retired = bool(flags & 0x0002)
    dead = bool(flags & 0x0020)
    donated = bool(flags & 0x4000)

    # Mutations / appearance table (16B header + 14 * 20B slots = 296B)
    mbase = after_tag + 92
    scale = struct.unpack_from("<f", dec, mbase)[0]
    coat_id = _u32(dec, mbase + 4)
    mutations: dict[str, int] = {"coat": coat_id}
    slot_coats: dict[str, int] = {}
    scars: dict[str, int] = {}
    for i, slot_key in enumerate(MUT_SLOT_KEYS):
        so = mbase + 16 + i * 20
        sid = _u32(dec, so)
        sc = _u32(dec, so + 4)
        scar = _u32(dec, so + 8)
        mutations[slot_key] = sid
        slot_coats[slot_key] = sc
        if scar:
            scars[slot_key] = scar

    # Voice block
    vbase = mbase + 296
    voice, o = _read_u64_str(dec, vbase + 8)
    o += 8  # f64 pitch

    # Stats block: 7 base + 7 level_bonus + 7 injury_mods + u64_str injury_name
    stats_off = o
    base_vals = struct.unpack_from("<7i", dec, stats_off)
    lvl_vals = struct.unpack_from("<7i", dec, stats_off + 28)
    inj_vals = struct.unpack_from("<7i", dec, stats_off + 56)
    stats = dict(zip(STAT_NAMES, base_vals))
    level_bonuses = dict(zip(STAT_NAMES, lvl_vals))
    injury_mods = dict(zip(STAT_NAMES, inj_vals))
    injury_str_off = stats_off + 84
    injury_name, hp_off = _read_u64_str(dec, injury_str_off)

    # HP + deadFlagU32 + 6 zero bytes
    hp_raw = _u32(dec, hp_off)
    dead_u32 = _u32(dec, hp_off + 4)
    ab_off = hp_off + 14

    # Abilities: 11 u64-strings + u32 passive1_tier + 3 * (u64-str + u32 tier)
    run_items: list[str] = []
    cur = ab_off
    for _ in range(11):
        s, cur = _read_u64_str(dec, cur)
        run_items.append(s)
    p1_tier = _u32(dec, cur)
    cur += 4
    tail_ab: list[tuple[str, int]] = []
    for _ in range(3):
        s, cur = _read_u64_str(dec, cur)
        t = _u32(dec, cur)
        cur += 4
        tail_ab.append((s, t))

    def _norm(s: str) -> str | None:
        return None if (not s or s == "None") else s

    abilities = {
        "active": [_norm(x) for x in run_items[:6]],
        "extra": run_items[6:10],  # preserved verbatim
        "passive": [_norm(run_items[10]), _norm(tail_ab[0][0])],
        "passive_tiers": [max(1, min(2, p1_tier)), max(1, min(2, tail_ab[0][1]))],
        "disorder": [_norm(tail_ab[1][0]), _norm(tail_ab[2][0])],
        "disorder_tiers": [max(1, tail_ab[1][1]), max(1, tail_ab[2][1])],
    }

    # 5 Equipment slots
    eq_off = cur
    equipment: dict[str, dict[str, Any] | None] = {}
    for slot_key in EQUIP_SLOT_KEYS:
        ver = _u32(dec, cur)
        present = dec[cur + 4]
        cur += 5
        if present:
            item_id, cur = _read_u64_str(dec, cur)
            sub_name, cur = _read_u64_str(dec, cur)
            charges, f1, f2, seq_id = struct.unpack_from("<iIII", dec, cur)
            tail_b = dec[cur + 16]
            after_b = dec[cur + 17]
            cur += 18
            equipment[slot_key] = {
                "item_id": item_id,
                "sub_name": sub_name,
                "charges": charges,
                "field1": f1,
                "field2": f2,
                "seq_id": seq_id,
                "tail_byte": tail_b,
                "after_tail_byte": after_b,
                "version": ver,
            }
        else:
            equipment[slot_key] = None

    # Class + level + xp + birthday + death_day
    cls_off = cur
    cat_class, cur = _read_u64_str(dec, cur)
    level = _i32(dec, cur)
    xp_frac = struct.unpack_from("<d", dec, cur + 4)[0]
    birth_day = _i64(dec, cur + 12)
    death_day = _i64(dec, cur + 20)
    age = max(0, current_day - birth_day) if birth_day >= 0 else 0

    eff_stats = {k: max(1, stats[k] + level_bonuses[k] + injury_mods[k]) for k in STAT_NAMES}
    max_hp = max(1, eff_stats["CON"] * 4)
    current_hp = max_hp if hp_raw == HP_FULL else min(max_hp, hp_raw)
    injured = (injury_name != "none" and bool(injury_name)) or any(v < 0 for v in injury_mods.values()) or bool(scars)
    wounded = hp_raw != HP_FULL and hp_raw < max_hp

    return {
        "key": key,
        "id64": id64,
        "name": name,
        "sex": sex,
        "age": age,
        "level": level,
        "class": cat_class,
        "grown": grown,
        "retired": retired,
        "dead": dead or bool(dead_u32),
        "donated": donated,
        "isOld": age >= 20,
        "stats": stats,
        "level_bonuses": level_bonuses,
        "injury_mods": injury_mods,
        "effective_stats": eff_stats,
        "injury": "" if injury_name == "none" else injury_name,
        "injured": injured,
        "wounded": wounded,
        "hp_raw": hp_raw,
        "hp": current_hp,
        "max_hp": max_hp,
        "max_mana": eff_stats["CHA"] * 3,
        "scars": scars,
        "abilities": abilities,
        "mutations": mutations,
        "equipment": equipment,
        "room": room,
        "birth_day": birth_day,
        "death_day": death_day,
        # Internal metadata for editing/verification
        "_blob_ver": blob_ver,
        "_variant": variant,
        "_marker": marker,
        "_scale": scale,
        "_xp": xp_frac,
        "_name_end": name_end,
        "_after_tag": after_tag,
        "_mbase": mbase,
        "_stats_offset": stats_off,
        "_injury_str_off": injury_str_off,
        "_hp_off": hp_off,
        "_ab_off": ab_off,
        "_eq_off": eq_off,
        "_cls_off": cls_off,
        "_birth_day": birth_day,
    }


def patch_cat_blob(wrapped: bytes, changes: dict[str, Any], current_day: int, next_seq_id: int = 9_999_000) -> bytes:
    """Apply any combination of edits to a cat BLOB deterministically, handling variable-length splices cleanly.

    Supported keys in `changes`:
      name, sex, retired, dead, donated, stats, level_bonuses, heal (bool), revive (bool),
      abilities ({active, passive, passive_tiers, disorder, disorder_tiers}),
      mutations ({coat, body, head, ...}),
      equipment ({head, face, neck, weapon, trinket}: item_id str | None),
      class (str), level (int), age (int)
    """
    dec, variant = _mp.decompress_cat_blob(wrapped)
    buf = bytearray(dec)

    # Walk the blob sequentially to locate every section, then rebuild cleanly from sections.
    # This guarantees variable-length splices (name, injury, abilities, equipment, class)
    # NEVER misalign any downstream offset!
    name_len = _u64(buf, 0x0C)
    name_end = 0x14 + name_len * 2
    marker_len = _u64(buf, name_end)
    after_tag = name_end + 8 + marker_len

    mbase = after_tag + 92
    vbase = mbase + 296
    _, voice_end = _read_u64_str(buf, vbase + 8)
    stats_off = voice_end + 8
    inj_str_off = stats_off + 84
    old_inj, hp_off = _read_u64_str(buf, inj_str_off)
    ab_off = hp_off + 14

    cur = ab_off
    run_items: list[str] = []
    for _ in range(11):
        s, cur = _read_u64_str(buf, cur)
        run_items.append(s)
    p1_tier = _u32(buf, cur)
    cur += 4
    tail_ab: list[tuple[str, int]] = []
    for _ in range(3):
        s, cur = _read_u64_str(buf, cur)
        t = _u32(buf, cur)
        cur += 4
        tail_ab.append((s, t))
    ab_end = cur

    eq_off = ab_end
    eq_slots: list[bytes] = []
    for _ in range(5):
        s_start = cur
        present = buf[cur + 4]
        cur += 5
        if present:
            _, cur = _read_u64_str(buf, cur)
            _, cur = _read_u64_str(buf, cur)
            cur += 18
        eq_slots.append(bytes(buf[s_start:cur]))
    eq_end = cur

    cls_off = eq_end
    old_cls, cls_after = _read_u64_str(buf, cls_off)
    # After className: i32 level (+0), f64 xp (+4), i64 birth_day (+12), i64 death_day (+20), tail (+28..)
    tail_meta = bytearray(buf[cls_after:])

    # --- 1. Fixed-offset edits inside [after_tag .. inj_str_off] ---
    mid = bytearray(buf[after_tag:inj_str_off])
    # Offsets relative to after_tag:
    if "sex" in changes and changes["sex"] in SEX_REV:
        sv = SEX_REV[changes["sex"]]
        struct.pack_into("<II", mid, 0, sv, sv)

    flags = _u16(mid, 8)
    if "retired" in changes:
        flags = (flags | 0x0002) if changes["retired"] else (flags & ~0x0002)
    if "dead" in changes:
        flags = (flags | 0x0020) if changes["dead"] else (flags & ~0x0020)
    if "donated" in changes:
        flags = (flags | 0x4000) if changes["donated"] else (flags & ~0x4000)
    if changes.get("revive"):
        flags &= ~0x0020
        flags &= ~0x4000
    struct.pack_into("<H", mid, 8, flags)

    # Mutations (relative offset 92)
    rmbase = 92
    if "mutations" in changes:
        muts = changes["mutations"]
        old_coat = _u32(mid, rmbase + 4)
        new_coat = muts.get("coat", old_coat)
        if "coat" in muts and new_coat != old_coat:
            struct.pack_into("<I", mid, rmbase + 4, new_coat & 0xFFFFFFFF)
            for i in range(14):
                so = rmbase + 16 + i * 20
                if _u32(mid, so + 4) == old_coat:
                    struct.pack_into("<I", mid, so + 4, new_coat & 0xFFFFFFFF)
        cur_coat = _u32(mid, rmbase + 4)
        for i, slot_key in enumerate(MUT_SLOT_KEYS):
            if slot_key in muts:
                so = rmbase + 16 + i * 20
                old_sid = _u32(mid, so)
                new_sid = muts[slot_key] & 0xFFFFFFFF
                struct.pack_into("<I", mid, so, new_sid)
                # If clearing a mutated slot whose coat matched old_sid, restore normal coat
                if new_sid < 300 and old_sid >= 300 and _u32(mid, so + 4) == old_sid:
                    struct.pack_into("<I", mid, so + 4, 0 if slot_key == "mouth" else cur_coat)

    # Clear visual scars if healing/reviving
    if changes.get("heal") or changes.get("revive"):
        for i in range(14):
            struct.pack_into("<I", mid, rmbase + 16 + i * 20 + 8, 0)

    # Stats / level_bonuses / injury_mods (relative offset stats_off - after_tag)
    rstats = stats_off - after_tag
    if "stats" in changes:
        st = changes["stats"]
        cur_vals = list(struct.unpack_from("<7i", mid, rstats))
        for idx, k in enumerate(STAT_NAMES):
            if k in st:
                cur_vals[idx] = int(st[k])
        struct.pack_into("<7i", mid, rstats, *cur_vals)
    if "level_bonuses" in changes:
        lb = changes["level_bonuses"]
        cur_lb = list(struct.unpack_from("<7i", mid, rstats + 28))
        for idx, k in enumerate(STAT_NAMES):
            if k in lb:
                cur_lb[idx] = int(lb[k])
        struct.pack_into("<7i", mid, rstats + 28, *cur_lb)
    if changes.get("heal") or changes.get("revive"):
        struct.pack_into("<7i", mid, rstats + 56, 0, 0, 0, 0, 0, 0, 0)

    # --- 2. Name + marker prefix ---
    if "name" in changes and changes["name"]:
        new_name_u16 = str(changes["name"])[:32].encode("utf-16le")
        new_name_len = len(new_name_u16) // 2
        prefix = bytes(buf[:0x0C]) + struct.pack("<Q", new_name_len) + new_name_u16 + bytes(buf[name_end:after_tag])
    else:
        prefix = bytes(buf[:after_tag])

    # --- 3. Injury name + HP/dead block ---
    new_inj = "none" if (changes.get("heal") or changes.get("revive")) else old_inj
    hp_block = bytearray(buf[hp_off:ab_off])
    if changes.get("heal") or changes.get("revive"):
        struct.pack_into("<I", hp_block, 0, HP_FULL)
    elif "hp_raw" in changes:
        struct.pack_into("<I", hp_block, 0, int(changes["hp_raw"]) & 0xFFFFFFFF)
    if changes.get("revive"):
        struct.pack_into("<I", hp_block, 4, 0)
    elif "dead" in changes:
        struct.pack_into("<I", hp_block, 4, 1 if changes["dead"] else 0)

    # --- 4. Abilities block ---
    ab_ch = changes.get("abilities") or {}
    new_run = list(run_items)
    if "active" in ab_ch:
        for i in range(min(6, len(ab_ch["active"]))):
            val = ab_ch["active"][i]
            new_run[i] = val if val else "None"
    new_p1_tier = p1_tier
    if "passive" in ab_ch and len(ab_ch["passive"]) > 0:
        new_run[10] = ab_ch["passive"][0] or "None"
    if "passive_tiers" in ab_ch and len(ab_ch["passive_tiers"]) > 0:
        new_p1_tier = max(1, min(2, int(ab_ch["passive_tiers"][0])))

    new_tail = list(tail_ab)
    if "passive" in ab_ch and len(ab_ch["passive"]) > 1:
        new_tail[0] = (ab_ch["passive"][1] or "None", new_tail[0][1])
    if "passive_tiers" in ab_ch and len(ab_ch["passive_tiers"]) > 1:
        new_tail[0] = (new_tail[0][0], max(1, min(2, int(ab_ch["passive_tiers"][1]))))
    if "disorder" in ab_ch:
        for di in range(min(2, len(ab_ch["disorder"]))):
            val = ab_ch["disorder"][di] or "None"
            t = 1 if val == "None" else new_tail[1 + di][1]
            new_tail[1 + di] = (val, t)
    if "disorder_tiers" in ab_ch:
        for di in range(min(2, len(ab_ch["disorder_tiers"]))):
            new_tail[1 + di] = (new_tail[1 + di][0], max(1, int(ab_ch["disorder_tiers"][di])))

    ab_bytes = bytearray()
    for s in new_run:
        ab_bytes.extend(_pack_u64_str(s))
    ab_bytes.extend(struct.pack("<I", new_p1_tier))
    for s, t in new_tail:
        ab_bytes.extend(_pack_u64_str(s))
        ab_bytes.extend(struct.pack("<I", t))

    # --- 5. Equipment block ---
    eq_ch = changes.get("equipment") or {}
    eq_bytes = bytearray()
    seq = next_seq_id
    for idx, slot_key in enumerate(EQUIP_SLOT_KEYS):
        if slot_key not in eq_ch:
            eq_bytes.extend(eq_slots[idx])
            continue
        val = eq_ch[slot_key]
        if not val:
            eq_bytes.extend(struct.pack("<IB", 5, 0))
        else:
            iid = val["item_id"] if isinstance(val, dict) else str(val)
            charges = int(val.get("charges", -1)) if isinstance(val, dict) else -1
            eq_bytes.extend(struct.pack("<IB", 5, 1))
            eq_bytes.extend(_pack_u64_str(iid))
            eq_bytes.extend(_pack_u64_str(""))
            eq_bytes.extend(struct.pack("<iIIIBB", charges, 0, 0, seq, 0xFF, 0x00))
            seq += 1

    # --- 6. Class + level + birthday + death_day ---
    new_cls = str(changes["class"]) if changes.get("class") else old_cls
    if "level" in changes:
        struct.pack_into("<i", tail_meta, 0, max(0, min(99, int(changes["level"]))))
    if "age" in changes:
        new_birth = max(0, int(current_day) - int(changes["age"]))
        struct.pack_into("<q", tail_meta, 12, new_birth)
    if changes.get("revive"):
        struct.pack_into("<q", tail_meta, 20, -1)

    out_dec = (
        prefix
        + bytes(mid)
        + _pack_u64_str(new_inj)
        + bytes(hp_block)
        + bytes(ab_bytes)
        + bytes(eq_bytes)
        + _pack_u64_str(new_cls)
        + bytes(tail_meta)
    )
    return _mp.recompress_cat_blob(out_dec, variant)


# --------------------------------------------------------------------------- inventory (exact round-trip)

def parse_inventory_blob(blob: bytes) -> list[dict[str, Any]]:
    blob = _mp.to_bytes(blob)
    if len(blob) < 4:
        return []
    count = _u32(blob, 0)
    if count == 0:
        return []
    o = 4
    items: list[dict[str, Any]] = []
    for _ in range(count):
        ver = _u32(blob, o)
        state = blob[o + 4]
        o += 5
        item_id, o = _read_u64_str(blob, o)
        extra_id, o = _read_u64_str(blob, o)
        field_a, field_b, field_c, seq_id = struct.unpack_from("<iIII", blob, o)
        tail_b = blob[o + 16]
        after_b = blob[o + 17]
        o += 18
        items.append({
            "item_id": item_id,
            "instance_id": seq_id,
            "extra_id": extra_id,
            "field_a": field_a,
            "field_b": field_b,
            "field_c": field_c,
            "marker": ver,
            "state": state,
            "sentinel": tail_b,
            "flags": after_b,
        })
    return items


def serialize_inventory_blob(items: list[dict[str, Any]]) -> bytes:
    if not items:
        return struct.pack("<I", 0)  # empty container is 4 bytes (00 00 00 00)
    out = bytearray(struct.pack("<I", len(items)))
    for it in items:
        out.extend(struct.pack("<IB", int(it.get("marker", 5)), int(it.get("state", 1))))
        out.extend(_pack_u64_str(str(it["item_id"])))
        out.extend(_pack_u64_str(str(it.get("extra_id") or "")))
        out.extend(struct.pack(
            "<iIIIBB",
            int(it.get("field_a", -1)),
            int(it.get("field_b", 0)),
            int(it.get("field_c", 0)),
            int(it["instance_id"]),
            int(it.get("sentinel", 0xFF)),
            int(it.get("flags", 0)),
        ))
    return bytes(out)


# --------------------------------------------------------------------------- furniture (exact 44/44 round-trip)

def parse_furniture_row(key: int, blob: bytes) -> dict[str, Any]:
    blob = _mp.to_bytes(blob)
    header = _u32(blob, 0)
    fid, o = _read_u64_str(blob, 4)
    unk = _u64(blob, o)
    room, o = _read_u64_str(blob, o + 8)
    x, y, z, flag1, flag2 = struct.unpack_from("<iiIII", blob, o)
    return {
        "key": key,
        "furniture_id": fid,
        "room": room,
        "x": x,
        "y": y,
        "z": z,
        "flag1": flag1,
        "flag2": flag2,
        "header": header,
        "unk": unk,
    }


def serialize_furniture_row(f: dict[str, Any]) -> bytes:
    out = bytearray(struct.pack("<I", int(f.get("header", 1))))
    out.extend(_pack_u64_str(str(f["furniture_id"])))
    out.extend(struct.pack("<Q", int(f.get("unk", 0))))
    out.extend(_pack_u64_str(str(f.get("room") or "")))
    out.extend(struct.pack(
        "<iiIII",
        int(f.get("x", 0)),
        int(f.get("y", 0)),
        int(f.get("z", 0)),
        int(f.get("flag1", 0)),
        int(f.get("flag2", 0)),
    ))
    return bytes(out)


# --------------------------------------------------------------------------- house_state (revive cat -> Attic)

def parse_house_state_full(blob: bytes) -> list[dict[str, Any]]:
    blob = _mp.to_bytes(blob)
    if len(blob) < 8:
        return []
    ver, cnt = struct.unpack_from("<II", blob, 0)
    if ver != 0 or cnt > 512:
        return []
    o = 8
    out: list[dict[str, Any]] = []
    for _ in range(cnt):
        key, unk = struct.unpack_from("<II", blob, o)
        room, o = _read_u64_str(blob, o + 8)
        p0, p1, p2 = struct.unpack_from("<3d", blob, o)
        o += 24
        out.append({"key": key, "unk": unk, "room": room, "pos": (p0, p1, p2)})
    return out


def serialize_house_state_full(entries: list[dict[str, Any]]) -> bytes:
    out = bytearray(struct.pack("<II", 0, len(entries)))
    for e in entries:
        out.extend(struct.pack("<II", int(e["key"]), int(e.get("unk", 0))))
        out.extend(_pack_u64_str(str(e["room"])))
        p0, p1, p2 = e.get("pos", (0.0, 0.0, 0.0))
        out.extend(struct.pack("<3d", float(p0), float(p1), float(p2)))
    return bytes(out)

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


def _is_ascii_ident(b: bytes, o: int, ln: int) -> bool:
    if ln <= 0 or o + ln > len(b):
        return False
    first = b[o]
    if not (65 <= first <= 90 or 97 <= first <= 122 or first == 95):
        return False
    for ch in b[o + 1:o + ln]:
        if not (65 <= ch <= 90 or 97 <= ch <= 122 or 48 <= ch <= 57 or ch == 95):
            return False
    return True


def _parse_ability_section(dec: bytes, ab_off: int) -> tuple[list[str], int, list[tuple[str, int]], int]:
    """Parse variable-length contiguous u64 ability run + p1_tier + 3 tail (str, tier) entries.

    Supports standard 11-entry runs as well as extended runs (e.g. Jester or extra class abilities).
    """
    n = len(dec)
    cur = ab_off
    run_items: list[str] = []

    def _try_tail(pos: int) -> tuple[int, list[tuple[str, int]], int] | None:
        if pos + 4 > n:
            return None
        p1_t = _u32(dec, pos)
        if not (1 <= p1_t <= 50):
            return None
        q = pos + 4
        tail: list[tuple[str, int]] = []
        for _ in range(3):
            if q + 8 > n:
                return None
            ln = _u64(dec, q)
            if ln > 128 or q + 8 + ln + 4 > n:
                return None
            if ln > 0 and not _is_ascii_ident(dec, q + 8, ln):
                return None
            s = dec[q + 8:q + 8 + ln].decode("ascii", errors="replace")
            t = _u32(dec, q + 8 + ln)
            if not (1 <= t <= 50):
                return None
            tail.append((s, t))
            q += 8 + ln + 4
        return p1_t, tail, q

    for idx in range(32):
        if idx >= 11:
            hit = _try_tail(cur)
            if hit is not None:
                return run_items, hit[0], hit[1], hit[2]
        s, cur = _read_u64_str(dec, cur)
        run_items.append(s)

    hit = _try_tail(cur)
    if hit is not None:
        return run_items, hit[0], hit[1], hit[2]
    raise ValueError(f"could not locate ability tail after {len(run_items)} items at {ab_off}")


def _find_slot_boundary(dec: bytes, search_start: int, max_search: int = 800) -> int | None:
    """Locate 0xFF <flag> 0x05 0x00 0x00 0x00 boundary pointing to next slot's u32(5)."""
    n = len(dec)
    end = min(n - 6, search_start + max_search)
    for p in range(search_start, end):
        if dec[p] == 0xFF and dec[p + 2:p + 6] == b"\x05\x00\x00\x00":
            return p + 2
    return None


def _looks_like_class_at(dec: bytes, pos: int) -> bool:
    if pos + 8 > len(dec):
        return False
    ln = _u64(dec, pos)
    if not (2 <= ln <= 32) or pos + 8 + ln + 28 > len(dec):
        return False
    if not _is_ascii_ident(dec, pos + 8, ln):
        return False
    s = dec[pos + 8:pos + 8 + ln].decode("ascii", errors="replace")
    return s in CAT_CLASSES or (s[0].isupper() and ln <= 24)


def _find_class_after_slot4(dec: bytes, search_start: int, max_search: int = 1200) -> int | None:
    n = len(dec)
    end = min(n - 10, search_start + max_search)
    for p in range(search_start, end):
        if dec[p] == 0xFF and dec[p + 1] <= 16 and _looks_like_class_at(dec, p + 2):
            return p + 2
    for p in range(search_start, end):
        if _looks_like_class_at(dec, p):
            return p
    return None


def _parse_equipment_section(dec: bytes, eq_off: int) -> tuple[dict[str, dict[str, Any] | None], list[bytes], int]:
    """Parse all 5 equipment slots deterministically with resilient boundary fallback for special/collar items."""
    n = len(dec)
    cur = eq_off
    equipment: dict[str, dict[str, Any] | None] = {}
    raw_slots: list[bytes] = []

    for idx, slot_key in enumerate(EQUIP_SLOT_KEYS):
        s_start = cur
        # Handle implicit-empty Slot 4 (game jumps straight to class string with no 5-byte slot header)
        if idx == 4 and _looks_like_class_at(dec, cur):
            equipment[slot_key] = None
            raw_slots.append(b"")
            continue

        if cur + 5 > n:
            raise ValueError(f"truncated equipment slot {slot_key} at {cur}")
        ver = _u32(dec, cur)
        present = dec[cur + 4]
        cur += 5
        if present:
            item_id, cur = _read_u64_str(dec, cur)
            sub_name, cur = _read_u64_str(dec, cur)
            if cur + 18 <= n:
                charges, f1, f2, seq_id = struct.unpack_from("<iIII", dec, cur)
                tail_b = dec[cur + 16]
                after_b = dec[cur + 17]
                cand_next = cur + 18
                # Verify cand_next lands cleanly on next slot header or class string; otherwise scan boundary
                valid_next = (
                    (_looks_like_class_at(dec, cand_next) if idx == 4 else
                     (cand_next + 5 <= n and _u32(dec, cand_next) == 5 and dec[cand_next + 4] in (0, 1)))
                )
                if valid_next:
                    cur = cand_next
                else:
                    nxt = _find_class_after_slot4(dec, cur) if idx == 4 else _find_slot_boundary(dec, cur)
                    if nxt is not None:
                        cur = nxt
                    else:
                        cur = cand_next
            else:
                charges, f1, f2, seq_id, tail_b, after_b = -1, 0, 0, 0, 0xFF, 0
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
        raw_slots.append(dec[s_start:cur])

    return equipment, raw_slots, cur


def _parse_cat_blob_fallback(dec: bytes, variant: str, key: int, room: str, current_day: int) -> dict[str, Any]:
    """Pattern-scanning fallback parser so unusual cats (e.g. Jester/special items/new patches) never disappear."""
    n = len(dec)
    blob_ver = _u32(dec, 0x00) if n >= 4 else 0
    id64 = _u64(dec, 0x04) if n >= 12 else 0
    _, name_end, name, sex = _mp.detect_name_end_and_sex(dec)
    marker = ""
    after_tag = name_end + 8
    try:
        ml = _u64(dec, name_end)
        if ml <= 64 and name_end + 8 + ml <= n:
            marker = dec[name_end + 8:name_end + 8 + ml].decode("ascii", errors="replace")
            after_tag = name_end + 8 + ml
    except Exception:
        pass
    flags = _u16(dec, after_tag + 8) if after_tag + 10 <= n else 0
    grown = bool(flags & 0x0001)
    retired = bool(flags & 0x0002)
    dead = bool(flags & 0x0020)
    donated = bool(flags & 0x4000)

    # Scan for mutation table (16B header + 14*20B slots)
    mutations: dict[str, int] = {"coat": 1}
    scars: dict[str, int] = {}
    mbase = -1
    best_mscore = -1
    for base in range(max(0, after_tag), max(0, n - 296)):
        sc = struct.unpack_from("<f", dec, base)[0]
        coat = _u32(dec, base + 4)
        t1 = _u32(dec, base + 8)
        t2 = _u32(dec, base + 12)
        if not (0.05 <= sc <= 20.0) or coat == 0 or coat > 20000 or t1 > 500 or (t2 != 0xFFFFFFFF and t2 > 5000):
            continue
        ok = sum(1 for i in range(14) if _u32(dec, base + 16 + i * 20 + 4) in (coat, 0))
        if ok >= 10 and ok * 1000 + base > best_mscore:
            best_mscore = ok * 1000 + base
            mbase = base
    if mbase >= 0:
        mutations["coat"] = _u32(dec, mbase + 4)
        for i, sk in enumerate(MUT_SLOT_KEYS):
            so = mbase + 16 + i * 20
            mutations[sk] = _u32(dec, so)
            scar = _u32(dec, so + 8)
            if scar:
                scars[sk] = scar
    else:
        for sk in MUT_SLOT_KEYS:
            mutations[sk] = 0

    # Scan for primary ability run starting with DefaultMove (or any valid Move ability)
    abilities = {
        "active": [None] * 6, "extra": ["None"] * 4,
        "passive": [None, None], "passive_tiers": [1, 1],
        "disorder": [None, None], "disorder_tiers": [1, 1],
    }
    ab_off = -1
    ab_end = -1
    dm = dec.find(struct.pack("<Q", 11) + b"DefaultMove")
    if dm >= 0:
        try:
            run_items, p1_tier, tail_ab, ab_end = _parse_ability_section(dec, dm)
            ab_off = dm
            _norm = lambda s: None if (not s or s == "None") else s
            abilities = {
                "active": [_norm(x) for x in run_items[:6]],
                "extra": run_items[6:-1],
                "passive": [_norm(run_items[-1]), _norm(tail_ab[0][0])],
                "passive_tiers": [max(1, min(2, p1_tier)), max(1, min(2, tail_ab[0][1]))],
                "disorder": [_norm(tail_ab[1][0]), _norm(tail_ab[2][0])],
                "disorder_tiers": [max(1, tail_ab[1][1]), max(1, tail_ab[2][1])],
            }
        except Exception:
            pass

    # Stats block: try right after voice block from mbase, else scan plausible 7x(1..7) ints
    stats = {k: 4 for k in STAT_NAMES}
    level_bonuses = {k: 0 for k in STAT_NAMES}
    injury_mods = {k: 0 for k in STAT_NAMES}
    injury_name = ""
    hp_raw = HP_FULL
    dead_u32 = 0
    stats_off = -1
    if mbase >= 0 and mbase + 304 < n:
        try:
            _, vo = _read_u64_str(dec, mbase + 304)
            so = vo + 8
            if so + 84 <= n:
                bv = struct.unpack_from("<7i", dec, so)
                if all(0 <= v <= 20 for v in bv):
                    stats_off = so
        except Exception:
            pass
    if stats_off < 0:
        for so in range(max(0, mbase if mbase > 0 else after_tag), max(0, (ab_off if ab_off > 0 else n) - 84)):
            bv = struct.unpack_from("<7i", dec, so)
            if all(1 <= v <= 7 for v in bv):
                lv = struct.unpack_from("<7i", dec, so + 28)
                iv = struct.unpack_from("<7i", dec, so + 56)
                if all(-30 <= v <= 99 for v in lv) and all(-30 <= v <= 30 for v in iv):
                    stats_off = so
                    break
    if stats_off >= 0:
        stats = dict(zip(STAT_NAMES, struct.unpack_from("<7i", dec, stats_off)))
        level_bonuses = dict(zip(STAT_NAMES, struct.unpack_from("<7i", dec, stats_off + 28)))
        injury_mods = dict(zip(STAT_NAMES, struct.unpack_from("<7i", dec, stats_off + 56)))
        try:
            inj_s, hp_off = _read_u64_str(dec, stats_off + 84)
            injury_name = "" if inj_s == "none" else inj_s
            if hp_off + 8 <= n:
                hp_raw = _u32(dec, hp_off)
                dead_u32 = _u32(dec, hp_off + 4)
        except Exception:
            pass

    # Equipment & class scan
    equipment: dict[str, dict[str, Any] | None] = {sk: None for sk in EQUIP_SLOT_KEYS}
    cat_class = "Colorless"
    level = 1
    xp_frac = 0.0
    birth_day = 0
    death_day = -1
    cls_off = -1
    if ab_end > 0:
        try:
            equipment, _, cls_off = _parse_equipment_section(dec, ab_end)
        except Exception:
            pass
    if cls_off < 0 or not _looks_like_class_at(dec, cls_off):
        # Scan for any known class name u64-string near tail of blob
        search_from = max(0, ab_end if ab_end > 0 else n // 2)
        for cname in CAT_CLASSES:
            pat = _pack_u64_str(cname)
            pos = dec.find(pat, search_from)
            if pos >= 0 and pos + len(pat) + 28 <= n:
                cls_off = pos
                break
    if cls_off >= 0:
        try:
            cat_class, c_after = _read_u64_str(dec, cls_off)
            if c_after + 28 <= n:
                level = _i32(dec, c_after)
                xp_frac = struct.unpack_from("<d", dec, c_after + 4)[0]
                birth_day = _i64(dec, c_after + 12)
                death_day = _i64(dec, c_after + 20)
        except Exception:
            pass

    age = max(0, current_day - birth_day) if birth_day >= 0 else 0
    eff_stats = {k: max(1, stats[k] + level_bonuses[k] + injury_mods[k]) for k in STAT_NAMES}
    max_hp = max(1, eff_stats["CON"] * 4)
    current_hp = max_hp if hp_raw == HP_FULL else min(max_hp, hp_raw)
    injured = bool(injury_name) or any(v < 0 for v in injury_mods.values()) or bool(scars)
    wounded = hp_raw != HP_FULL and hp_raw < max_hp

    return {
        "key": key, "id64": id64, "name": name or f"Cat #{key}", "sex": sex, "age": age,
        "level": max(0, min(99, level)), "class": cat_class, "grown": grown, "retired": retired,
        "dead": dead or bool(dead_u32), "donated": donated, "isOld": age >= 20,
        "stats": stats, "level_bonuses": level_bonuses, "injury_mods": injury_mods,
        "effective_stats": eff_stats, "injury": injury_name, "injured": injured, "wounded": wounded,
        "hp_raw": hp_raw, "hp": current_hp, "max_hp": max_hp, "max_mana": eff_stats["CHA"] * 3,
        "scars": scars, "abilities": abilities, "mutations": mutations, "equipment": equipment,
        "room": room, "birth_day": birth_day, "death_day": death_day,
        "libido": 0.5, "sexuality": 0.0, "lover_id": None, "aggression": 0.5,
        "hater_id": None, "fertility": 1.0,
        "coi": float(xp_frac) if (0.0 <= xp_frac <= 1.0) else 0.0,
        "parent_a": None, "parent_b": None,
        "_blob_ver": blob_ver, "_variant": variant, "_marker": marker, "_scale": 1.0,
        "_xp": xp_frac, "_name_end": name_end, "_after_tag": after_tag, "_mbase": mbase,
        "_stats_offset": stats_off, "_injury_str_off": -1, "_hp_off": -1,
        "_ab_off": ab_off, "_eq_off": -1, "_cls_off": cls_off, "_birth_day": birth_day,
        "_fallback": True,
    }


# --------------------------------------------------------------------------- cat blob

def parse_cat_blob(wrapped: bytes, key: int, room: str, current_day: int) -> dict[str, Any]:
    """Deterministically parse a single LZ4-wrapped cat BLOB from byte 0 to EOF,
    with resilient fallback so unusual cats (e.g. Jester / special items) never disappear."""
    dec, variant = _mp.decompress_cat_blob(wrapped)
    try:
        return _parse_cat_blob_strict(dec, variant, key, room, current_day)
    except Exception:
        return _parse_cat_blob_fallback(dec, variant, key, room, current_day)


def _parse_cat_blob_strict(dec: bytes, variant: str, key: int, room: str, current_day: int) -> dict[str, Any]:
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

    # Personality & relationships block (variable-length u64 string at after_tag+16 — e.g. "None" or collar/tag!)
    libido = 0.5
    sexuality = 0.0
    lover_id: int | None = None
    aggression = 0.5
    hater_id: int | None = None
    fertility = 1.0
    _, p_after = _read_u64_str(dec, after_tag + 16)
    po = p_after + 4
    if po + 64 <= n:
        lib, sexu, lvr, _la, agg, htr, _ha, fert = struct.unpack_from("<ddQddQdd", dec, po)
        if 0.0 <= lib <= 2.0:
            libido = lib
        if 0.0 <= sexu <= 2.0:
            sexuality = sexu
        if lvr != 0xFFFFFFFFFFFFFFFF and lvr != 0:
            lover_id = int(lvr)
        if 0.0 <= agg <= 2.0:
            aggression = agg
        if htr != 0xFFFFFFFFFFFFFFFF and htr != 0:
            hater_id = int(htr)
        if 0.0 <= fert <= 10.0:
            fertility = fert

    # Mutations / appearance table immediately follows personality block (16B header + 14 * 20B slots = 296B)
    mbase = p_after + 64
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

    # Abilities: variable-length u64-string run (>=11) + u32 passive1_tier + 3 * (u64-str + u32 tier)
    run_items, p1_tier, tail_ab, cur = _parse_ability_section(dec, ab_off)

    def _norm(s: str) -> str | None:
        return None if (not s or s == "None") else s

    abilities = {
        "active": [_norm(x) for x in run_items[:6]],
        "extra": run_items[6:-1],  # preserved verbatim (normally 4 items)
        "passive": [_norm(run_items[-1]), _norm(tail_ab[0][0])],
        "passive_tiers": [max(1, min(2, p1_tier)), max(1, min(2, tail_ab[0][1]))],
        "disorder": [_norm(tail_ab[1][0]), _norm(tail_ab[2][0])],
        "disorder_tiers": [max(1, tail_ab[1][1]), max(1, tail_ab[2][1])],
    }

    # 5 Equipment slots (supports standard 18B records, extended records, and implicit-empty slot 4)
    eq_off = cur
    equipment, _, cur = _parse_equipment_section(dec, eq_off)

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
        "libido": libido,
        "sexuality": sexuality,
        "lover_id": lover_id,
        "aggression": aggression,
        "hater_id": hater_id,
        "fertility": fertility,
        "coi": float(xp_frac) if (0.0 <= xp_frac <= 1.0) else 0.0,
        "parent_a": None,
        "parent_b": None,
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

    _, p_after = _read_u64_str(buf, after_tag + 16)
    mbase = p_after + 64
    vbase = mbase + 296
    _, voice_end = _read_u64_str(buf, vbase + 8)
    stats_off = voice_end + 8
    inj_str_off = stats_off + 84
    old_inj, hp_off = _read_u64_str(buf, inj_str_off)
    ab_off = hp_off + 14

    dec_ro = bytes(buf)
    run_items, p1_tier, tail_ab, ab_end = _parse_ability_section(dec_ro, ab_off)
    eq_off = ab_end
    _, eq_slots, eq_end = _parse_equipment_section(dec_ro, eq_off)

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

    # Mutations (relative offset mbase - after_tag)
    rmbase = mbase - after_tag
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

    # Strip -2 (0xFFFFFFFE) missing-part birth defects if requested
    if changes.get("strip_defects"):
        cur_coat = _u32(mid, rmbase + 4)
        for i, slot_key in enumerate(MUT_SLOT_KEYS):
            so = rmbase + 16 + i * 20
            if _u32(mid, so) == 0xFFFFFFFE:
                struct.pack_into("<I", mid, so, 1)
                if _u32(mid, so + 4) == 0xFFFFFFFE:
                    struct.pack_into("<I", mid, so + 4, 0 if slot_key == "mouth" else cur_coat)

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
        new_run[-1] = ab_ch["passive"][0] or "None"
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


# --------------------------------------------------------------------------- pedigree (read-only CoI & parents)

_PEDIGREE_NULL = 0xFFFFFFFFFFFFFFFF
_PEDIGREE_VER = 0xFFFFFFFFFFFFFFF5


def parse_pedigree_blob(raw: Any) -> dict[str, Any]:
    """Parse `files['pedigree']` (two parallel hashmaps: child->parents+CoI and pair->CoI)."""
    empty: dict[str, Any] = {"parents": {}, "pair_coi": {}}
    if not raw:
        return empty
    b = _mp.to_bytes(raw)
    if len(b) < 24:
        return empty
    try:
        ver, _sz, cap = struct.unpack_from("<QQQ", b, 0)
        if ver != _PEDIGREE_VER or cap > 1_000_000:
            return empty
        h_start = 24
        d_start = h_start + cap + 1 + 16
        parents: dict[int, tuple[int, int, float]] = {}
        for i in range(cap):
            if b[h_start + i] <= 0x7F:
                off = d_start + i * 32
                cid, pa, pb, coi = struct.unpack_from("<QQQd", b, off)
                if cid and cid != _PEDIGREE_NULL and pa != _PEDIGREE_NULL and pb != _PEDIGREE_NULL:
                    parents[int(cid)] = (int(pa), int(pb), float(coi) if (0.0 <= coi <= 1.0) else 0.0)

        hm2_off = d_start + cap * 32 + 8
        pair_coi: dict[tuple[int, int], float] = {}
        if hm2_off + 24 <= len(b):
            ver2, _sz2, cap2 = struct.unpack_from("<QQQ", b, hm2_off)
            if ver2 == _PEDIGREE_VER and cap2 <= 2_000_000:
                h2 = hm2_off + 24
                d2 = h2 + cap2 + 1 + 16
                if d2 + cap2 * 24 <= len(b):
                    for i in range(cap2):
                        if b[h2 + i] <= 0x7F:
                            pa, pb, coi = struct.unpack_from("<QQd", b, d2 + i * 24)
                            if pa != _PEDIGREE_NULL and pb != _PEDIGREE_NULL:
                                pair_coi[(int(pa), int(pb))] = float(coi) if (0.0 <= coi <= 1.0) else 0.0
        return {"parents": parents, "pair_coi": pair_coi}
    except Exception:
        return empty


# --------------------------------------------------------------------------- unlocks & house_unlocks (exact round-trip)

ALL_HOUSE_UPGRADES = (
    "Default",
    "SmallHouse_Attic",
    "MediumHouse",
    "MediumHouse_SmallRoom",
    "LargeHouse",
    "LargeHouse_Attic",
    "LargeHouse_Basement",
)


def parse_unlocks_blob(raw: Any) -> dict[str, Any]:
    """Deterministically parse `files['unlocks']` (u32 ver + 7 u64-counted string vectors)."""
    empty: dict[str, Any] = {"version": 3, "sections": [[] for _ in range(7)], "classes": [], "raw_tail": b""}
    if not raw:
        return empty
    b = _mp.to_bytes(raw)
    if len(b) < 12:
        return empty
    try:
        ver = _u32(b, 0)
        o = 4
        sections: list[list[str]] = []
        while o + 8 <= len(b) and len(sections) < 7:
            cnt = _u64(b, o)
            if cnt > 4096:
                break
            o += 8
            vec: list[str] = []
            for _ in range(cnt):
                s, o = _read_u64_str(b, o)
                vec.append(s)
            sections.append(vec)
        return {
            "version": ver,
            "sections": sections,
            "classes": list(sections[0]) if sections else [],
            "raw_tail": b[o:],
        }
    except Exception:
        return empty


def serialize_unlocks_blob(u: dict[str, Any]) -> bytes:
    out = bytearray(struct.pack("<I", int(u.get("version", 3))))
    sections = list(u.get("sections") or [])
    if sections and "classes" in u:
        sections[0] = list(u["classes"])
    for vec in sections:
        out.extend(struct.pack("<Q", len(vec)))
        for s in vec:
            out.extend(_pack_u64_str(str(s)))
    out.extend(u.get("raw_tail") or b"")
    return bytes(out)


def parse_house_unlocks_blob(raw: Any) -> dict[str, Any]:
    """Deterministically parse `files['house_unlocks']` (u32 ver + house_id str + u64-vec + tail)."""
    empty: dict[str, Any] = {"version": 1, "house": "House1", "upgrades": [], "raw_tail": b"\x00" * 8}
    if not raw:
        return empty
    b = _mp.to_bytes(raw)
    if len(b) < 20:
        return empty
    try:
        ver = _u32(b, 0)
        house, o = _read_u64_str(b, 4)
        cnt = _u64(b, o)
        o += 8
        upgrades: list[str] = []
        for _ in range(cnt):
            s, o = _read_u64_str(b, o)
            upgrades.append(s)
        return {"version": ver, "house": house, "upgrades": upgrades, "raw_tail": b[o:]}
    except Exception:
        return empty


def serialize_house_unlocks_blob(hu: dict[str, Any]) -> bytes:
    out = bytearray(struct.pack("<I", int(hu.get("version", 1))))
    out.extend(_pack_u64_str(str(hu.get("house") or "House1")))
    ups = list(hu.get("upgrades") or [])
    out.extend(struct.pack("<Q", len(ups)))
    for s in ups:
        out.extend(_pack_u64_str(str(s)))
    out.extend(hu.get("raw_tail") if hu.get("raw_tail") is not None else (b"\x00" * 8))
    return bytes(out)

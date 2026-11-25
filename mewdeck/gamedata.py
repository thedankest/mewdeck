"""Unobfuscated Mewgenics gamedata (items, set bonuses, actives, passives, disorders, mutations)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "gamedata"

# 14 mutation body-part slots in exact binary table order (matches MUTATION_SLOT_MAP in mewparser)
MUTATION_SLOTS: list[tuple[str, str, str, str | None]] = [
    # (field_key, label, category, mirror_partner_key)
    ("body", "Body", "body", None),
    ("head", "Head", "head", None),
    ("tail", "Tail", "tail", None),
    ("legL", "Rear Leg L", "legs", "legR"),
    ("legR", "Rear Leg R", "legs", "legL"),
    ("armL", "Front Leg L", "legs", "armR"),
    ("armR", "Front Leg R", "legs", "armL"),
    ("eyeL", "Eye L", "eyes", "eyeR"),
    ("eyeR", "Eye R", "eyes", "eyeL"),
    ("eyebrowL", "Eyebrow L", "eyebrows", "eyebrowR"),
    ("eyebrowR", "Eyebrow R", "eyebrows", "eyebrowL"),
    ("earL", "Ear L", "ears", "earR"),
    ("earR", "Ear R", "ears", "earL"),
    ("mouth", "Mouth", "mouth", None),
]
MUTATION_SLOT_BY_KEY = {k: (lbl, cat, partner) for k, lbl, cat, partner in MUTATION_SLOTS}


def _load(rel: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / rel).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def items() -> dict[str, dict[str, Any]]:
    return _load("items/items.json")


@lru_cache(maxsize=1)
def set_bonuses() -> dict[str, dict[str, Any]]:
    return _load("items/set_bonuses.json")


@lru_cache(maxsize=1)
def actives() -> dict[str, dict[str, Any]]:
    return _load("abilities/actives.json")


@lru_cache(maxsize=1)
def passives() -> dict[str, dict[str, Any]]:
    return _load("abilities/passives.json")


@lru_cache(maxsize=1)
def disorders() -> dict[str, dict[str, Any]]:
    return _load("abilities/disorders.json")


@lru_cache(maxsize=1)
def mutations() -> dict[str, dict[str, dict[str, Any]]]:
    return _load("mutations/mutations.json")


@lru_cache(maxsize=1)
def furniture() -> dict[str, dict[str, Any]]:
    return _load("furniture/furniture.json")


@lru_cache(maxsize=1)
def injuries() -> dict[str, dict[str, Any]]:
    return _load("injuries.json")


INJURY_NAMES = {
    "str": "Broken Paw",
    "dex": "Torn Tendon",
    "con": "Broken Rib",
    "int": "Concussion",
    "spd": "Broken Leg",
    "cha": "Disfigured",
    "lck": "Jinxed",
    "burned": "Immolated",
    "bleeding": "Exsanguinated",
    "poisoned": "Poisoned",
    "cursed": "Cursed",
    "radiated": "Radiated",
}


def injury_info(injury_id: str | None) -> dict[str, Any]:
    if not injury_id or injury_id == "none":
        return {"id": "", "name": "", "desc": ""}
    low = injury_id.lower()
    db = injuries()
    if injury_id in db:
        rec = dict(db[injury_id])
        if low in INJURY_NAMES and rec.get("name", "").lower() == low:
            rec["name"] = INJURY_NAMES[low]
        return rec
    return {"id": injury_id, "name": INJURY_NAMES.get(low, humanize_id(injury_id)), "desc": ""}


def furniture_info(fid: str) -> dict[str, Any]:
    db = furniture()
    if fid in db:
        return db[fid]
    return {"id": fid, "name": humanize_id(fid), "effects": {}}


def item_info(iid: str | None) -> dict[str, Any]:
    if not iid:
        return {"id": "", "name": "Empty", "kind": "", "rarity": "", "desc": ""}
    db = items()
    if iid in db:
        return db[iid]
    return {"id": iid, "name": humanize_id(iid), "kind": "other", "rarity": "", "desc": ""}


def humanize_id(raw: str) -> str:
    if not raw or raw == "None":
        return "None"
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    return s.replace("_", " ").strip()


def format_stats(stats: dict[str, int] | None) -> str:
    if not stats:
        return ""
    parts = [f"{'+' if v > 0 else ''}{v} {k}" for k, v in stats.items() if v]
    return "  ·  ".join(parts)


def item_set_info(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return set bonus records for any armor set(s) this item belongs to."""
    raw = item.get("set")
    if not raw:
        return []
    sb = set_bonuses()
    out = []
    for part in str(raw).split(","):
        k = part.strip()
        if k in sb:
            out.append(sb[k])
    return out


def active_info(ability_id: str | None) -> dict[str, Any]:
    if not ability_id or ability_id == "None":
        return {"id": "None", "name": "None", "class": "", "desc": "", "slot": "active"}
    db = actives()
    if ability_id in db:
        return db[ability_id]
    if ability_id.endswith("2") and ability_id[:-1] in db:
        base = dict(db[ability_id[:-1]])
        base["id"] = ability_id
        base["name"] = base["name"] + " +"
        return base
    return {"id": ability_id, "name": humanize_id(ability_id), "class": "", "desc": "", "slot": "active"}


def passive_info(passive_id: str | None, tier: int = 1) -> dict[str, Any]:
    if not passive_id or passive_id == "None":
        return {"id": "None", "name": "None", "class": "", "desc": "", "desc2": "", "max_tier": 1}
    db = passives()
    rec = db.get(passive_id)
    if rec is None and passive_id.endswith("2"):
        rec = db.get(passive_id[:-1])
    if rec is None:
        return {"id": passive_id, "name": humanize_id(passive_id), "class": "", "desc": "", "desc2": "", "max_tier": 2}
    out = dict(rec)
    if tier >= 2:
        out["display_name"] = f"{rec['name']} +"
        out["active_desc"] = rec.get("desc2") or rec.get("desc") or ""
    else:
        out["display_name"] = rec["name"]
        out["active_desc"] = rec.get("desc") or ""
    return out


def disorder_info(disorder_id: str | None) -> dict[str, Any]:
    if not disorder_id or disorder_id == "None":
        return {"id": "None", "name": "None", "desc": ""}
    db = disorders()
    if disorder_id in db:
        return db[disorder_id]
    return {"id": disorder_id, "name": humanize_id(disorder_id), "desc": ""}


def signed_mut_id(val: int | None) -> int:
    """Convert raw u32 mutation slot value to signed int (0xFFFFFFFE -> -2)."""
    if val is None:
        return 0
    return val - 0x100000000 if val >= 0x80000000 else val


def _clean_mut_name(category: str, mid: int, rec: dict[str, Any] | None) -> str:
    raw = (rec or {}).get("name") or ""
    if raw and not raw.startswith("#"):
        return raw.split("(")[0].strip().title() if "(" in raw else raw
    if mid == -2:
        return f"No {category.title()}"
    st = format_stats((rec or {}).get("stats"))
    if st:
        return st.replace("  ·  ", " / ")
    return f"{category.title()} Mutation #{mid}"


def _merged_missing_record(cat_db: dict[str, Any], category: str) -> dict[str, Any]:
    r2 = cat_db.get("2") if isinstance(cat_db.get("2"), dict) else {}
    rm2 = cat_db.get("-2") if isinstance(cat_db.get("-2"), dict) else {}
    name = _clean_mut_name(category, -2, r2 if (r2.get("name") and not str(r2["name"]).startswith("#")) else rm2)
    desc = rm2.get("desc") or r2.get("desc") or "Birth defect (missing body part)."
    stats = rm2.get("stats") or r2.get("stats") or {}
    return {"id": -2, "name": name, "desc": desc, "stats": stats, "tag": "birth_defect", "is_mutation": True}


def mutation_info(category: str, raw_id: int | None) -> dict[str, Any]:
    """Resolve a mutation slot ID to its unobfuscated name, description, and stats."""
    sid = signed_mut_id(raw_id)
    cat_db = mutations().get(category, {})
    if sid <= 0:
        if sid == -2:
            return _merged_missing_record(cat_db, category)
        return {"id": 0, "name": "Normal (none)", "desc": "", "stats": {}, "is_mutation": False}
    rec = cat_db.get(str(sid))
    if sid >= 300:
        name = _clean_mut_name(category, sid, rec)
        return {
            "id": sid,
            "name": name,
            "desc": (rec or {}).get("desc", ""),
            "stats": (rec or {}).get("stats", {}),
            "tag": (rec or {}).get("tag"),
            "is_mutation": True,
        }
    return {"id": sid, "name": f"Normal (#{sid})", "desc": "Default non-mutated appearance.", "stats": {},
            "is_mutation": False}


def mutations_for_category(category: str) -> list[dict[str, Any]]:
    """All selectable mutations/birth defects for a body part or fur texture, sorted cleanly."""
    cat_db = mutations().get(category, {})
    out: list[dict[str, Any]] = []
    seen_missing = False
    for k, rec in cat_db.items():
        if not k.lstrip("-").isdigit():
            continue
        mid = int(k)
        if mid < 300:
            if rec.get("tag") == "birth_defect" and not seen_missing:
                seen_missing = True
                m = _merged_missing_record(cat_db, category)
                m["raw_id"] = 0xFFFFFFFE
                out.append(m)
            continue
        name = _clean_mut_name(category, mid, rec)
        out.append({
            "id": mid,
            "raw_id": mid,
            "name": name,
            "desc": rec.get("desc", ""),
            "stats": rec.get("stats", {}),
            "tag": rec.get("tag"),
        })
    out.sort(key=lambda x: (x.get("tag") == "birth_defect", x["name"].startswith(("+", "-")), x["name"].lower()))
    return out

"""Cat build presets, clipboard clone/paste, and full Armor Set lookup."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import catblob as cb
from . import gamedata as gd

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewdeck"
PRESETS_PATH = DATA_HOME / "presets.json"

# Built-in 1-press cat build templates (uses real verified gamedata IDs)
BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "name": "All-7 Perfect Breeder",
        "desc": "All 7 base stats, healthy, zero disorders, zero birth defects, unretired.",
        "stats": {s: 7 for s in cb.STAT_NAMES},
        "heal": True,
        "retired": False,
        "clear_disorders": True,
        "clear_defects": True,
    },
    {
        "name": "Immortal Tank",
        "desc": "Tank class · Max stats (+5 CON/STR bonus) · Scrap Set armor · healthy.",
        "class": "Tank",
        "stats": {s: 7 for s in cb.STAT_NAMES},
        "level_bonuses": {"STR": 4, "DEX": 0, "CON": 6, "INT": 0, "SPD": 2, "CHA": 2, "LUCK": 2},
        "heal": True,
        "clear_disorders": True,
    },
    {
        "name": "Arcane Caster",
        "desc": "Mage class · Max stats (+6 INT/CHA bonus) · healthy & disorder-free.",
        "class": "Mage",
        "stats": {s: 7 for s in cb.STAT_NAMES},
        "level_bonuses": {"STR": 0, "DEX": 2, "CON": 2, "INT": 6, "SPD": 3, "CHA": 6, "LUCK": 2},
        "heal": True,
        "clear_disorders": True,
    },
    {
        "name": "Crit Hunter",
        "desc": "Hunter class · Max stats (+6 DEX/LUCK, +4 SPD bonus) · healthy.",
        "class": "Hunter",
        "stats": {s: 7 for s in cb.STAT_NAMES},
        "level_bonuses": {"STR": 1, "DEX": 6, "CON": 2, "INT": 0, "SPD": 4, "CHA": 1, "LUCK": 6},
        "heal": True,
        "clear_disorders": True,
    },
    {
        "name": "Clean Slate",
        "desc": "Full HP, cure all injuries & scars, clear all disorders & birth defects.",
        "heal": True,
        "clear_disorders": True,
        "clear_defects": True,
    },
]


def load_user_presets() -> list[dict[str, Any]]:
    try:
        if PRESETS_PATH.is_file():
            data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_user_preset(preset: dict[str, Any]) -> None:
    presets = [p for p in load_user_presets() if p.get("name") != preset.get("name")]
    presets.insert(0, preset)
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_PATH.write_text(json.dumps(presets[:24], indent=2), encoding="utf-8")


def delete_user_preset(name: str) -> None:
    presets = [p for p in load_user_presets() if p.get("name") != name]
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_PATH.write_text(json.dumps(presets, indent=2), encoding="utf-8")


def snapshot_cat_as_preset(vals: dict[str, Any], name: str) -> dict[str, Any]:
    return {
        "name": name,
        "desc": f"{vals['class']} · Base {sum(vals['stats'].values())}/49 · Saved from cat",
        "user": True,
        "class": vals["class"],
        "stats": dict(vals["stats"]),
        "level_bonuses": dict(vals["level_bonuses"]),
        "abilities": {
            "active": list(vals["abilities"]["active"]),
            "passive": list(vals["abilities"]["passive"]),
            "passive_tiers": list(vals["abilities"]["passive_tiers"]),
            "disorder": list(vals["abilities"]["disorder"]),
            "disorder_tiers": list(vals["abilities"]["disorder_tiers"]),
        },
        "equipment": dict(vals["equipment"]),
        "heal": True,
    }


def armor_sets_catalog() -> list[dict[str, Any]]:
    """Return all armor sets paired with their matching equippable items by slot."""
    sb_map = gd.set_bonuses()
    items_by_set: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for it in gd.items().values():
        raw = it.get("set")
        if not raw:
            continue
        sk = it.get("kind")
        if sk not in cb.EQUIP_SLOT_KEYS:
            continue
        for part in str(raw).split(","):
            sid = part.strip()
            if sid in sb_map:
                items_by_set.setdefault(sid, {}).setdefault(sk, []).append(it)

    out: list[dict[str, Any]] = []
    for sid, sb in sb_map.items():
        slot_map = items_by_set.get(sid)
        if not slot_map:
            continue
        # Pick 1 canonical item per slot (prefer non-cursed, higher stat sum)
        picked: dict[str, dict[str, Any]] = {}
        for sk, cands in slot_map.items():
            cands.sort(key=lambda x: (bool(x.get("cursed")), -sum((x.get("stats") or {}).values()), x["name"].lower()))
            picked[sk] = cands[0]
        out.append({
            "id": sid,
            "name": sb["name"],
            "pieces": sb.get("pieces", 3),
            "stats": sb.get("stats") or {},
            "desc": sb.get("desc") or "",
            "slots": picked,
        })
    out.sort(key=lambda x: x["name"].lower())
    return out

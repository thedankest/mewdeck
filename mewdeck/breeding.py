"""Game-accurate Mewgenics house room rules, furniture priority guide, breeding simulation & CoI calculator.

Includes:
- Practical rule-based house zoning (Retirement, Nursery/Kittens, Battle-Ready, Breeding, Sickbay, General, Locked)
  with Hater fight prevention and overcrowding Comfort warnings (-1 Comfort per cat over 4).
- Per-role Mewgenics Furniture Priority Guide + unplaced Storage recommendations.
- Exact Mewgenics pedigree/CoI calculator, Offspring Simulator (ENS), and optional SA Breeding Optimizer.
"""

from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catblob as cb
from . import gamedata as gd

STAT_NAMES = cb.STAT_NAMES

GOALS: list[tuple[str, str, dict[str, float]]] = [
    ("balanced", "All-7 Balanced", {s: 1.0 for s in STAT_NAMES}),
    ("melee", "Melee (STR/CON/SPD)", {"STR": 1.8, "CON": 1.5, "SPD": 1.3, "DEX": 0.8, "INT": 0.4, "CHA": 0.6, "LUCK": 0.9}),
    ("caster", "Caster (INT/CHA/CON)", {"INT": 1.9, "CHA": 1.6, "CON": 1.2, "SPD": 1.0, "LUCK": 0.9, "STR": 0.4, "DEX": 0.5}),
    ("ranged", "Ranged (DEX/LUCK/SPD)", {"DEX": 1.9, "LUCK": 1.5, "SPD": 1.3, "CON": 0.9, "CHA": 0.7, "STR": 0.5, "INT": 0.6}),
]

ROOM_ROLES = ("retirement", "nursery", "fighters", "breeding", "sickbay", "general", "locked")
ROOM_ROLE_LABELS = {
    "retirement": "Retirement (Retired)",
    "nursery": "Nursery (Kittens)",
    "fighters": "Battle-Ready",
    "breeding": "Breeding",
    "sickbay": "Sickbay (Recovery)",
    "general": "Everyone Else",
    "locked": "Locked (Keep)",
    "quarantine": "Retirement (Retired)",  # legacy alias
}
ROOM_ROLE_SHORT = {
    "retirement": "Retired",
    "nursery": "Kittens",
    "fighters": "Fighters",
    "breeding": "Breeding",
    "sickbay": "Sickbay",
    "general": "General",
    "locked": "Locked",
    "quarantine": "Retired",
}

# Verified Mewgenics room furniture stat guide (Comfort, Stimulation, Health, Appeal, Evolution).
# Comfort/Stimulation/Health/Evolution apply per-room; Appeal is house-wide across all rooms.
ROOM_FURNITURE_GUIDE: dict[str, dict[str, Any]] = {
    "breeding": {
        "prioritize": ("Comfort", "Stimulation"),
        "optional": ("Health", "Evolution"),
        "avoid": (),
        "short": "Comfort + Stimulation",
        "tip": "Comfort sparks mating & stops fights (-1 per cat over 4). Stimulation inherits higher parent stats & spells.",
    },
    "fighters": {
        "prioritize": ("Comfort", "Health"),
        "optional": ("Appeal",),
        "avoid": ("Evolution",),
        "short": "Comfort + Health (no Evo)",
        "tip": "Keep Comfort > 0 so fighters don't brawl & get injured. Avoid Evolution unless you want random mutations.",
    },
    "nursery": {
        "prioritize": ("Comfort", "Health"),
        "optional": ("Evolution", "Appeal"),
        "avoid": (),
        "short": "Comfort + Health",
        "tip": "Comfort & Health keep kittens safe while growing up. Add Evolution only if you want mutated kittens.",
    },
    "retirement": {
        "prioritize": ("Health", "Appeal"),
        "optional": ("Comfort",),
        "avoid": ("Stimulation",),
        "short": "Health + Appeal dump",
        "tip": "Health wards off sudden old-age death & contagion. Great spot to stack Appeal (global stray stat boost!).",
    },
    "sickbay": {
        "prioritize": ("Health", "Comfort"),
        "optional": ("Appeal",),
        "avoid": ("Evolution",),
        "short": "Health + Comfort",
        "tip": "Stack Health to heal injuries & block contagion fast. Keep Comfort >= 0 so recovering cats don't fight.",
    },
    "general": {
        "prioritize": ("Comfort", "Appeal"),
        "optional": ("Health",),
        "avoid": (),
        "short": "Comfort + Appeal",
        "tip": "Keep Comfort positive (max 4 cats) to stop Fight Club brawls. Appeal boosts strays house-wide from any room.",
    },
    "locked": {
        "prioritize": ("Comfort", "Health"),
        "optional": ("Appeal",),
        "avoid": (),
        "short": "Comfort + Health",
        "tip": "Pinned cats stay put and are skipped by Room Rules. Keep Comfort positive so they stay peaceful.",
    },
}


def furniture_guide_for(role: str) -> dict[str, Any]:
    return ROOM_FURNITURE_GUIDE.get(role, ROOM_FURNITURE_GUIDE["general"])


# --------------------------------------------------------------------------- rule persistence

def _rules_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewdeck"
    return base / "room_rules.json"


def load_room_rules(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or _rules_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out: dict[str, dict[str, Any]] = {}
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, dict):
                    role = str(v.get("role", ""))
                    if role == "quarantine":
                        role = "retirement"
                    if role in ROOM_ROLES:
                        mc = int(v.get("max_cats", 4))
                        out[k] = {"role": role, "max_cats": max(1, min(12, mc))}
            return out
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save_room_rules(specs: list["RoomSpec"], path: Path | None = None) -> None:
    p = path or _rules_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {sp.name: {"role": sp.role, "max_cats": int(sp.max_cats)} for sp in specs}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


# --------------------------------------------------------------------------- pedigree & CoI

def pair_coi(a_key: int, b_key: int, pedigree: dict[str, Any]) -> float:
    """Return Coefficient of Inbreeding (0.0 .. 1.0) for hypothetical offspring of cats A & B.

    Uses the save's exact `parents_coi_memo` first, then falls back to Wright's kinship
    recurrence over `pedigree['parents']` (exact up to 6 generations).
    """
    if a_key == b_key:
        return 0.5
    memo: dict[tuple[int, int], float] = pedigree.get("pair_coi") or {}
    if (a_key, b_key) in memo:
        return float(memo[(a_key, b_key)])
    if (b_key, a_key) in memo:
        return float(memo[(b_key, a_key)])

    parents: dict[int, tuple[int, int, float]] = pedigree.get("parents") or {}
    if not parents:
        return 0.0

    cache: dict[tuple[int, int], float] = {}

    def kinship(x: int, y: int, depth: int = 0) -> float:
        if depth > 7 or x <= 0 or y <= 0:
            return 0.0
        k = (x, y) if x <= y else (y, x)
        if k in cache:
            return cache[k]
        if x == y:
            px = parents.get(x)
            fx = px[2] if px else 0.0
            val = 0.5 * (1.0 + fx)
            cache[k] = val
            return val
        px = parents.get(x)
        py = parents.get(y)
        if not px and not py:
            cache[k] = 0.0
            return 0.0
        if px and not py:
            val = 0.5 * (kinship(px[0], y, depth + 1) + kinship(px[1], y, depth + 1))
        elif py and not px:
            val = 0.5 * (kinship(x, py[0], depth + 1) + kinship(x, py[1], depth + 1))
        else:
            if x > y:
                val = 0.5 * (kinship(px[0], y, depth + 1) + kinship(px[1], y, depth + 1))
            else:
                val = 0.5 * (kinship(x, py[0], depth + 1) + kinship(x, py[1], depth + 1))
        cache[k] = val
        return val

    return min(1.0, max(0.0, kinship(a_key, b_key)))


def coi_label(coi: float) -> tuple[str, str]:
    """Return (short_label, severity: 'good'|'info'|'warn'|'bad')."""
    pct = coi * 100.0
    if coi <= 0.001:
        return ("0% Safe", "good")
    if coi < 0.20:
        return (f"{pct:.0f}% Low", "info")
    if coi < 0.35:
        return (f"{pct:.0f}% Risky", "warn")
    return (f"{pct:.0f}% Inbred!", "bad")


# --------------------------------------------------------------------------- cat traits & scoring

def has_eternal_youth(cat: dict[str, Any]) -> bool:
    ab = cat["abilities"]
    return "EternalYouth" in (ab.get("disorder") or []) or "EternalYouth" in (ab.get("passive") or [])


def is_kitten(cat: dict[str, Any]) -> bool:
    return (not cat.get("grown", True)) or cat.get("age", 0) <= 1


def is_retired_cat(cat: dict[str, Any]) -> bool:
    return bool(cat.get("retired"))


def is_sick(cat: dict[str, Any]) -> bool:
    return bool(cat.get("injured")) or bool(cat.get("wounded")) or disorder_count(cat) > 0


def defect_count(cat: dict[str, Any]) -> int:
    return sum(1 for k, v in cat["mutations"].items() if k != "coat" and gd.signed_mut_id(v) == -2)


def disorder_count(cat: dict[str, Any]) -> int:
    return sum(1 for d in (cat["abilities"].get("disorder") or []) if d and d != "EternalYouth")


def mutation_count(cat: dict[str, Any]) -> int:
    return sum(1 for k, v in cat["mutations"].items() if k != "coat" and v >= 300)


def stat_sum(cat: dict[str, Any]) -> int:
    return sum(cat["stats"].values())


def perfect_sevens(cat: dict[str, Any]) -> int:
    return sum(1 for v in cat["stats"].values() if v >= 7)


def can_breed(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["key"] == b["key"]:
        return False
    if a["dead"] or b["dead"] or a["donated"] or b["donated"]:
        return False
    if has_eternal_youth(a) or has_eternal_youth(b):
        return False
    gs = {a["sex"], b["sex"]}
    return "Ditto" in gs or gs == {"Male", "Female"}


def hates_each_other(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("hater_id") == b["key"] or b.get("hater_id") == a["key"]


def valid_room_pairs(room_cats: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Return (valid_breeding_pairs, hater_conflicts) for a list of cats in one room.

    Applies Mewgenics' exact per-room lover exclusivity & hater conflict rules.
    """
    keys = {c["key"] for c in room_cats}
    lovers = {c["key"]: c["lover_id"] for c in room_cats if c.get("lover_id") in keys}
    haters = {c["key"]: c["hater_id"] for c in room_cats if c.get("hater_id") in keys}

    valid: list[tuple[dict[str, Any], dict[str, Any]]] = []
    conflicts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for i, a in enumerate(room_cats):
        for b in room_cats[i + 1 :]:
            ak, bk = a["key"], b["key"]
            if haters.get(ak) == bk or haters.get(bk) == ak:
                conflicts.append((a, b))
                continue
            if not can_breed(a, b):
                continue
            al, bl = lovers.get(ak), lovers.get(bk)
            if al is not None and al != bk:
                continue
            if bl is not None and bl != ak:
                continue
            valid.append((a, b))
    return valid, conflicts


@dataclass
class PairEval:
    cat_a: dict[str, Any]
    cat_b: dict[str, Any]
    coi: float
    expected_stats: dict[str, float]
    expected_total: float
    expected_sevens: float
    novel_disorder_prob: float
    spell1_prob: float
    passive_prob: float
    ens: float
    is_lovers: bool = False


def evaluate_pair(
    a: dict[str, Any],
    b: dict[str, Any],
    stimulation: float,
    pedigree: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> PairEval:
    """Compute exact Mewgenics expected offspring metrics & ENS quality score."""
    stim = max(0.0, float(stimulation))
    p_high = (1.0 + 0.01 * stim) / (2.0 + 0.01 * stim)
    w = weights or GOALS[0][2]

    exp_stats: dict[str, float] = {}
    weighted_sum = 0.0
    exp_sevens = 0.0
    for s in STAT_NAMES:
        va, vb = a["stats"][s], b["stats"][s]
        hi, lo = (va, vb) if va >= vb else (vb, va)
        ev = p_high * hi + (1.0 - p_high) * lo
        exp_stats[s] = ev
        weighted_sum += ev * w.get(s, 1.0)
        if hi >= 7 and lo >= 7:
            exp_sevens += 1.0
        elif hi >= 7:
            exp_sevens += p_high

    coi = pair_coi(a["key"], b["key"], pedigree)
    novel_dis = min(1.0, 0.02 + 0.4 * max(coi - 0.20, 0.0))
    inh_dis = 0.15 * (disorder_count(a) + disorder_count(b))
    exp_def = 0.40 * (defect_count(a) + defect_count(b))

    spell1 = min(1.0, 0.20 + 0.025 * stim)
    passive = min(1.0, 0.05 + 0.01 * stim)

    w_norm = sum(w.get(s, 1.0) for s in STAT_NAMES) / 7.0
    norm_stats = weighted_sum / max(0.01, w_norm)

    ens = norm_stats + exp_sevens * 1.4 - (novel_dis + inh_dis) * 5.0 - exp_def * 1.0
    if coi >= 0.20:
        ens -= (coi - 0.19) * 18.0

    is_lovers = (a.get("lover_id") == b["key"]) or (b.get("lover_id") == a["key"])
    if is_lovers:
        ens += 1.2

    return PairEval(
        cat_a=a,
        cat_b=b,
        coi=coi,
        expected_stats=exp_stats,
        expected_total=sum(exp_stats.values()),
        expected_sevens=exp_sevens,
        novel_disorder_prob=novel_dis,
        spell1_prob=spell1,
        passive_prob=passive,
        ens=ens,
        is_lovers=is_lovers,
    )


def cat_breed_score(cat: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """Single-cat breeding value (used for sorting, culling hints, and room routing)."""
    w = weights or GOALS[0][2]
    w_norm = sum(w.get(s, 1.0) for s in STAT_NAMES) / 7.0
    st = sum(cat["stats"][s] * w.get(s, 1.0) for s in STAT_NAMES) / max(0.01, w_norm)
    score = st + perfect_sevens(cat) * 1.5 - disorder_count(cat) * 4.0 - defect_count(cat) * 1.0
    if cat["retired"]:
        score -= 2.5
    if cat["age"] >= 18:
        score -= 1.5
    if cat["sex"] == "Ditto":
        score += 1.5
    return score


def cat_fighter_score(cat: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """Combat readiness score for Battle-Ready room routing."""
    w = weights or GOALS[0][2]
    w_norm = sum(w.get(s, 1.0) for s in STAT_NAMES) / 7.0
    eff = cat.get("effective_stats") or cat["stats"]
    st = sum(eff[s] * w.get(s, 1.0) for s in STAT_NAMES) / max(0.01, w_norm)
    score = st + perfect_sevens(cat) * 1.2 + min(10, cat.get("level", 1)) * 0.6
    score -= disorder_count(cat) * 4.5 + defect_count(cat) * 1.5
    if cat.get("injured") or cat.get("wounded"):
        score -= 8.0
    if cat.get("retired"):
        score -= 30.0
    if is_kitten(cat):
        score -= 20.0
    return score


def best_partners_for(
    cat: dict[str, Any],
    pool: list[dict[str, Any]],
    stimulation: float,
    pedigree: dict[str, Any],
    weights: dict[str, float] | None = None,
    limit: int = 12,
) -> list[PairEval]:
    """Return top compatible house partners for `cat`, ranked by ENS."""
    out: list[PairEval] = []
    for other in pool:
        if not can_breed(cat, other):
            continue
        if hates_each_other(cat, other):
            continue
        out.append(evaluate_pair(cat, other, stimulation, pedigree, weights))
    out.sort(key=lambda pe: pe.ens, reverse=True)
    return out[:limit]


# --------------------------------------------------------------------------- room specs & storage furniture guide

@dataclass
class RoomSpec:
    name: str
    role: str = "general"  # retirement | nursery | fighters | breeding | sickbay | general | locked
    max_cats: int = 4
    stimulation: float = 0.0
    comfort: float = 0.0
    health: float = 0.0
    appeal: float = 0.0
    evolution: float = 0.0


@dataclass
class RoomPlan:
    spec: RoomSpec
    cats: list[dict[str, Any]]
    pairs: list[PairEval]
    conflicts: list[tuple[dict[str, Any], dict[str, Any]]]
    ey_cats: list[dict[str, Any]]
    score: float


@dataclass
class OptimizationResult:
    goal_id: str
    plans: list[RoomPlan]
    unassigned: list[dict[str, Any]]
    moves: dict[int, str]
    total_ens: float
    before_ens: float


@dataclass
class RoomRulePlan:
    spec: RoomSpec
    cats: list[dict[str, Any]]
    pairs: list[PairEval]
    conflicts: list[tuple[dict[str, Any], dict[str, Any]]]
    effective_comfort: float
    warnings: list[str]
    storage_picks: list[dict[str, Any]]


@dataclass
class RulePlanResult:
    goal_id: str
    plans: list[RoomRulePlan]
    moves: dict[int, str]
    total_conflicts: int
    before_conflicts: int
    warnings: list[str]


def compute_room_specs(
    parsed: dict[str, Any],
    saved_rules: dict[str, dict[str, Any]] | None = None,
) -> list[RoomSpec]:
    """Build RoomSpec list from the save's actual rooms, placed furniture stats, and user rules."""
    rooms = list(parsed.get("rooms") or [])
    if not rooms:
        rooms = ["Attic"]

    totals: dict[str, dict[str, float]] = {r: {} for r in rooms}
    for f in parsed["furniture"]["placed"]:
        rm = f.get("room")
        if rm in totals:
            for ek, ev in gd.furniture_info(f["furniture_id"]).get("effects", {}).items():
                totals[rm][ek] = totals[rm].get(ek, 0.0) + float(ev)

    specs: list[RoomSpec] = []
    for rm in rooms:
        ef = totals[rm]
        specs.append(RoomSpec(
            name=rm,
            role="general",
            max_cats=4,  # Mewgenics applies -1 Comfort per cat above 4 in a room
            stimulation=ef.get("Stimulation", 0.0),
            comfort=ef.get("Comfort", 0.0),
            health=ef.get("Health", 0.0),
            appeal=ef.get("Appeal", 0.0),
            evolution=ef.get("Evolution", 0.0),
        ))

    # Practical default house zoning matching standard Mewgenics layouts:
    # Attic -> Retirement, Small (left) -> Nursery (Kittens), Large (right) -> Battle-Ready, extra -> Breeding/Sickbay
    if len(specs) == 1:
        specs[0].role = "general"
    else:
        assigned_breed = False
        for sp in specs:
            low = sp.name.lower()
            if "attic" in low:
                sp.role = "retirement"
            elif "small" in low:
                sp.role = "nursery"
            elif "large" in low:
                sp.role = "fighters"
            elif not assigned_breed:
                sp.role = "breeding"
                assigned_breed = True
            elif sp.health >= 4:
                sp.role = "sickbay"
            else:
                sp.role = "general"

    # Overlay saved user preferences
    if saved_rules:
        for sp in specs:
            sr = saved_rules.get(sp.name)
            if sr:
                role = sr.get("role", sp.role)
                if role == "quarantine":
                    role = "retirement"
                if role in ROOM_ROLES:
                    sp.role = role
                if "max_cats" in sr:
                    sp.max_cats = max(1, min(12, int(sr["max_cats"])))
    return specs


def storage_furniture_recommendations(
    parsed: dict[str, Any],
    spec: RoomSpec,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Find unplaced furniture items already in Storage that best match `spec.role`'s priority stats."""
    guide = furniture_guide_for(spec.role)
    prio = set(guide["prioritize"])
    opt = set(guide["optional"])
    avoid = set(guide["avoid"])

    counts: dict[str, int] = {}
    for f in parsed["furniture"]["unplaced"]:
        fid = f["furniture_id"]
        counts[fid] = counts.get(fid, 0) + 1

    scored: list[dict[str, Any]] = []
    for fid, cnt in counts.items():
        info = gd.furniture_info(fid)
        efs = info.get("effects") or {}
        if not efs:
            continue
        sc = 0.0
        has_prio = False
        for ek, ev in efs.items():
            v = float(ev)
            if ek in prio:
                sc += v * 3.0
                if v > 0:
                    has_prio = True
            elif ek in opt:
                sc += v * 1.2
            elif ek in avoid:
                sc -= max(0.0, v) * 4.0
        if has_prio and sc > 0:
            scored.append({
                "id": fid,
                "name": info["name"],
                "count": cnt,
                "effects": efs,
                "score": sc,
            })
    scored.sort(key=lambda x: (-x["score"], x["name"]))
    return scored[:limit]


# --------------------------------------------------------------------------- deterministic rule-based house manager

def _has_hater_in(cat: dict[str, Any], group: list[dict[str, Any]]) -> bool:
    return any(hates_each_other(cat, o) for o in group)


def _room_warnings(sp: RoomSpec, cats: list[dict[str, Any]], conflicts: list[tuple[dict[str, Any], dict[str, Any]]]) -> tuple[float, list[str]]:
    over = max(0, len(cats) - 4)
    eff_comfort = sp.comfort - over
    warns: list[str] = []
    if conflicts:
        warns.append(f"{len(conflicts)} Hater conflict{'s' if len(conflicts) != 1 else ''} (fight risk!)")
    if over > 0:
        warns.append(f"{len(cats)} cats in room (-{over} Comfort overcrowding)")
    if len(cats) >= 2 and eff_comfort < 0:
        warns.append(f"Effective Comfort {eff_comfort:+.0f}: Fight Club brawl risk!")
    elif sp.role == "breeding" and len(cats) >= 2 and sp.comfort <= 0:
        warns.append("Comfort <= 0: low mating — add Comfort furniture")
    elif sp.role in ("retirement", "sickbay") and len(cats) >= 1 and sp.health <= 0:
        warns.append("Health <= 0: add Health furniture (illness risk)")
    return eff_comfort, warns


def apply_room_rules(
    parsed: dict[str, Any],
    specs: list[RoomSpec],
    goal_id: str = "balanced",
    *,
    avoid_haters: bool = True,
) -> RulePlanResult:
    """Deterministically route every house cat according to room rules while preventing Hater fights."""
    weights = dict(next((g[2] for g in GOALS if g[0] == goal_id), GOALS[0][2]))
    pedigree = parsed.get("pedigree") or {}
    house_cats = [c for c in parsed["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]

    # Count current hater conflicts before sorting
    before_by_rm: dict[str, list[dict[str, Any]]] = {}
    for c in house_cats:
        before_by_rm.setdefault(c["room"], []).append(c)
    before_conflicts = sum(len(valid_room_pairs(cs)[1]) for cs in before_by_rm.values())

    assigned_rooms: dict[str, list[dict[str, Any]]] = {sp.name: [] for sp in specs}
    placed: set[int] = set()
    spec_by_name = {sp.name: sp for sp in specs}

    # 1. Locked rooms stay completely untouched
    for sp in specs:
        if sp.role == "locked":
            for c in house_cats:
                if c["room"] == sp.name:
                    assigned_rooms[sp.name].append(c)
                    placed.add(c["key"])

    def try_place(cat: dict[str, Any], candidates: list[RoomSpec], *, allow_overcap: bool = False, allow_hater: bool = False) -> bool:
        if cat["key"] in placed:
            return True
        if not candidates:
            return False
        # Prefer keeping the cat in its current room if eligible, then highest free space & comfort
        ordered = sorted(
            candidates,
            key=lambda r: (
                0 if not _has_hater_in(cat, assigned_rooms[r.name]) else 1,
                0 if len(assigned_rooms[r.name]) < r.max_cats else 1,
                0 if cat["room"] == r.name else 1,
                len(assigned_rooms[r.name]) - r.max_cats,
                -r.comfort,
            ),
        )
        for rm in ordered:
            cur = assigned_rooms[rm.name]
            if not allow_overcap and len(cur) >= rm.max_cats:
                continue
            if avoid_haters and not allow_hater and _has_hater_in(cat, cur):
                continue
            cur.append(cat)
            placed.add(cat["key"])
            return True
        return False

    by_role: dict[str, list[RoomSpec]] = {r: [] for r in ROOM_ROLES}
    for sp in specs:
        role = "retirement" if sp.role == "quarantine" else sp.role
        by_role.setdefault(role, []).append(sp)

    # Sort each role's rooms by its most relevant furniture stat
    by_role["nursery"].sort(key=lambda r: (r.comfort + r.health, r.comfort), reverse=True)
    by_role["sickbay"].sort(key=lambda r: (r.health, r.comfort), reverse=True)
    by_role["retirement"].sort(key=lambda r: (r.name == "Attic", r.health, r.appeal), reverse=True)
    by_role["fighters"].sort(key=lambda r: (r.comfort, r.health), reverse=True)
    by_role["breeding"].sort(key=lambda r: (r.stimulation, r.comfort), reverse=True)
    by_role["general"].sort(key=lambda r: (r.comfort, r.appeal), reverse=True)

    movable = [c for c in house_cats if c["key"] not in placed]

    # Phase 1: Kittens / Babies -> Nursery
    if by_role["nursery"]:
        kittens = sorted([c for c in movable if is_kitten(c)], key=lambda c: (c["age"], -stat_sum(c)))
        for c in kittens:
            try_place(c, by_role["nursery"])

    # Phase 2: Sick / Injured / Disordered -> Sickbay
    if by_role["sickbay"]:
        sick = sorted(
            [c for c in movable if c["key"] not in placed and is_sick(c)],
            key=lambda c: (bool(c.get("wounded")), bool(c.get("injured")), disorder_count(c)),
            reverse=True,
        )
        for c in sick:
            try_place(c, by_role["sickbay"])

    # Phase 3: Retired -> Retirement
    if by_role["retirement"]:
        retired = sorted(
            [c for c in movable if c["key"] not in placed and is_retired_cat(c)],
            key=lambda c: (c.get("age", 0), c["room"] in {r.name for r in by_role["retirement"]}),
            reverse=True,
        )
        for c in retired:
            try_place(c, by_role["retirement"])

    # Phase 4: Best Battle-Ready Adults -> Fighters
    if by_role["fighters"]:
        fighters = sorted(
            [c for c in movable if c["key"] not in placed and not is_retired_cat(c) and not is_kitten(c)],
            key=lambda c: cat_fighter_score(c, weights),
            reverse=True,
        )
        for c in fighters:
            try_place(c, by_role["fighters"])

    # Phase 5: Best Compatible Pairs -> Breeding
    if by_role["breeding"]:
        breed_pool = [c for c in movable if c["key"] not in placed and not is_retired_cat(c) and not is_kitten(c)]
        for br_room in by_role["breeding"]:
            # Pair up highest-ENS compatible pairs (CoI < 0.25 preferred)
            while len(assigned_rooms[br_room.name]) + 2 <= br_room.max_cats:
                avail = [c for c in breed_pool if c["key"] not in placed]
                best_pe: PairEval | None = None
                for i, a in enumerate(avail):
                    if avoid_haters and _has_hater_in(a, assigned_rooms[br_room.name]):
                        continue
                    for b in avail[i + 1 :]:
                        if not can_breed(a, b) or hates_each_other(a, b):
                            continue
                        if avoid_haters and _has_hater_in(b, assigned_rooms[br_room.name]):
                            continue
                        pe = evaluate_pair(a, b, br_room.stimulation, pedigree, weights)
                        if best_pe is None or pe.ens > best_pe.ens:
                            best_pe = pe
                if best_pe is None:
                    break
                assigned_rooms[br_room.name].append(best_pe.cat_a)
                assigned_rooms[br_room.name].append(best_pe.cat_b)
                placed.add(best_pe.cat_a["key"])
                placed.add(best_pe.cat_b["key"])
            # Fill any single leftover slot in breeding room with best compatible cat
            while len(assigned_rooms[br_room.name]) < br_room.max_cats:
                avail = sorted(
                    [c for c in breed_pool if c["key"] not in placed],
                    key=lambda c: cat_breed_score(c, weights),
                    reverse=True,
                )
                added = False
                for c in avail:
                    if avoid_haters and _has_hater_in(c, assigned_rooms[br_room.name]):
                        continue
                    assigned_rooms[br_room.name].append(c)
                    placed.add(c["key"])
                    added = True
                    break
                if not added:
                    break

    # Phase 6: Everyone else / overflow — place peacefully without dropping any cat
    non_locked = [sp for sp in specs if sp.role != "locked"] or list(specs)
    remaining = [c for c in movable if c["key"] not in placed]
    for c in remaining:
        # 6a. Try general rooms first within cap, no haters
        if try_place(c, by_role["general"]):
            continue
        # 6b. Try matching role over-cap (e.g. >4 retired cats still stay in Attic rather than crashing Fighters)
        if is_retired_cat(c) and try_place(c, by_role["retirement"], allow_overcap=True):
            continue
        if is_kitten(c) and try_place(c, by_role["nursery"], allow_overcap=True):
            continue
        if is_sick(c) and try_place(c, by_role["sickbay"], allow_overcap=True):
            continue
        # 6c. Try any non-locked room with space and no haters
        if try_place(c, non_locked, allow_overcap=False):
            continue
        # 6d. Try general/matching/any non-locked over-cap without haters
        if try_place(c, by_role["general"] + by_role["retirement"] + non_locked, allow_overcap=True):
            continue
        # 6e. Last resort (every room has a hater): keep in current room or safest room
        fallback = [spec_by_name[c["room"]]] if c["room"] in spec_by_name else non_locked
        try_place(c, fallback, allow_overcap=True, allow_hater=True)

    # Final build: evaluate each room's warnings, pairs, effective comfort, storage picks, and moves
    plans: list[RoomRulePlan] = []
    moves: dict[int, str] = {}
    total_conflicts = 0
    all_warnings: list[str] = []

    for sp in specs:
        cats = assigned_rooms[sp.name]
        ey_cnt = sum(1 for c in cats if has_eternal_youth(c))
        reg = [c for c in cats if not has_eternal_youth(c)]
        pairs_raw, conflicts = valid_room_pairs(reg)
        pevals = [evaluate_pair(a, b, sp.stimulation + ey_cnt, pedigree, weights) for a, b in pairs_raw]
        pevals.sort(key=lambda p: p.ens, reverse=True)
        eff_comf, warns = _room_warnings(sp, cats, conflicts)
        total_conflicts += len(conflicts)
        for w in warns:
            all_warnings.append(f"{sp.name.replace('_', ' ')}: {w}")
        picks = storage_furniture_recommendations(parsed, sp, limit=3)
        plans.append(RoomRulePlan(
            spec=sp,
            cats=cats,
            pairs=pevals,
            conflicts=conflicts,
            effective_comfort=eff_comf,
            warnings=warns,
            storage_picks=picks,
        ))
        for c in cats:
            if c["room"] != sp.name:
                moves[c["key"]] = sp.name

    return RulePlanResult(
        goal_id=goal_id,
        plans=plans,
        moves=moves,
        total_conflicts=total_conflicts,
        before_conflicts=before_conflicts,
        warnings=all_warnings,
    )


# --------------------------------------------------------------------------- SA Breeding Optimizer (legacy & advanced)

def evaluate_current_layout(
    house_cats: list[dict[str, Any]],
    specs: list[RoomSpec],
    pedigree: dict[str, Any],
    weights: dict[str, float],
) -> tuple[list[RoomPlan], float]:
    """Score the save's current room layout using the exact same ENS scorer."""
    by_room: dict[str, list[dict[str, Any]]] = {r.name: [] for r in specs}
    for c in house_cats:
        if c["room"] in by_room:
            by_room[c["room"]].append(c)

    has_breed = any(r.role == "breeding" for r in specs)
    plans: list[RoomPlan] = []
    total = 0.0
    for sp in specs:
        cats = by_room[sp.name]
        ey = [c for c in cats if has_eternal_youth(c)]
        reg = [c for c in cats if not has_eternal_youth(c)]
        eff_stim = sp.stimulation + len(ey)
        pairs_raw, conflicts = valid_room_pairs(reg)
        pevals = [evaluate_pair(a, b, eff_stim, pedigree, weights) for a, b in pairs_raw]
        pevals.sort(key=lambda p: p.ens, reverse=True)
        rscore = (sum(p.ens for p in pevals) / len(pevals) * min(2.0, 1.0 + 0.25 * len(pevals))) if pevals else 0.0
        rscore -= len(conflicts) * 12.0
        if sp.role == "breeding" or (not has_breed and sp.role != "locked"):
            total += rscore
        plans.append(RoomPlan(sp, reg, pevals, conflicts, ey, rscore))
    return plans, total


def optimize_house(
    parsed: dict[str, Any],
    specs: list[RoomSpec],
    goal_id: str = "balanced",
    *,
    iterations: int = 1600,
    seed: int = 42,
) -> OptimizationResult:
    """Optimize house cat placement across breeding rooms via Simulated Annealing + Greedy routing."""
    weights = dict(next((g[2] for g in GOALS if g[0] == goal_id), GOALS[0][2]))
    pedigree = parsed.get("pedigree") or {}
    house_cats = [c for c in parsed["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]

    _, before_ens = evaluate_current_layout(house_cats, specs, pedigree, weights)

    locked_rooms = {r.name for r in specs if r.role == "locked"}
    locked_cats = {c["key"]: c["room"] for c in house_cats if c["room"] in locked_rooms}

    movable = [c for c in house_cats if c["key"] not in locked_cats]
    ey_cats = [c for c in movable if has_eternal_youth(c)]
    reg_cats = [c for c in movable if not has_eternal_youth(c)]

    breed_rooms = sorted([r for r in specs if r.role == "breeding"], key=lambda r: r.stimulation, reverse=True)
    if not breed_rooms:
        # Fallback if user zoned all rooms for rules: optimize across non-locked rooms
        breed_rooms = sorted(
            [r for r in specs if r.role not in ("locked", "retirement")] or [r for r in specs if r.role != "locked"],
            key=lambda r: (r.stimulation, r.comfort),
            reverse=True,
        )[: max(1, len(specs) - 1)]
    breed_names = {r.name for r in breed_rooms}
    sick_rooms = sorted([r for r in specs if r.role == "sickbay" and r.name not in breed_names], key=lambda r: r.health, reverse=True)
    nursery_rooms = sorted([r for r in specs if r.role in ("nursery", "general", "fighters") and r.name not in breed_names], key=lambda r: r.comfort, reverse=True)
    quar_rooms = [r for r in specs if r.role in ("retirement", "quarantine") and r.name not in breed_names]

    ey_assign: dict[str, list[dict[str, Any]]] = {r.name: [] for r in specs}
    if breed_rooms:
        for i, ec in enumerate(ey_cats):
            ey_assign[breed_rooms[i % len(breed_rooms)].name].append(ec)
    elif specs:
        for ec in ey_cats:
            ey_assign[specs[0].name].append(ec)

    cat_by_key = {c["key"]: c for c in house_cats}
    pair_cache: dict[tuple[int, int, int], PairEval] = {}

    def get_pair(ak: int, bk: int, stim_i: int) -> PairEval:
        k = (ak, bk, stim_i) if ak < bk else (bk, ak, stim_i)
        pe = pair_cache.get(k)
        if pe is None:
            pe = evaluate_pair(cat_by_key[ak], cat_by_key[bk], float(stim_i), pedigree, weights)
            pair_cache[k] = pe
        return pe

    breed_cap = sum(r.max_cats for r in breed_rooms)
    ranked = sorted(reg_cats, key=lambda c: cat_breed_score(c, weights), reverse=True)
    sa_pool = ranked[: max(breed_cap + 8, min(len(ranked), 28))]
    pool_keys = [c["key"] for c in sa_pool]

    rng = random.Random(seed)
    state: dict[int, str | None] = {k: None for k in pool_keys}
    room_counts = {r.name: 0 for r in breed_rooms}
    for c in sa_pool:
        placed = False
        for br in breed_rooms:
            if room_counts[br.name] < br.max_cats:
                state[c["key"]] = br.name
                room_counts[br.name] += 1
                placed = True
                break
        if not placed:
            state[c["key"]] = None

    room_stim_i = {r.name: round(r.stimulation + len(ey_assign[r.name])) for r in specs}

    def score_state(st: dict[int, str | None]) -> float:
        by_rm: dict[str, list[dict[str, Any]]] = {r.name: [] for r in breed_rooms}
        for ck, rn in st.items():
            if rn is not None:
                by_rm[rn].append(cat_by_key[ck])
        tot = 0.0
        for br in breed_rooms:
            cs = by_rm[br.name]
            if not cs:
                continue
            pairs_raw, conflicts = valid_room_pairs(cs)
            tot -= len(conflicts) * 18.0
            if not pairs_raw:
                tot -= 3.0 * len(cs)
                continue
            si = room_stim_i[br.name]
            scores = [get_pair(a["key"], b["key"], si).ens for a, b in pairs_raw]
            avg = sum(scores) / len(scores)
            tot += avg * min(2.0, 1.0 + 0.25 * len(scores))
            tot += sum(0.08 for c in cs if c["room"] == br.name)
        return tot

    if breed_rooms and len(pool_keys) >= 2:
        cur_score = score_state(state)
        best_state = dict(state)
        best_score = cur_score
        room_names = [r.name for r in breed_rooms] + [None]
        max_by_rm = {r.name: r.max_cats for r in breed_rooms}

        t_start, t_end = 12.0, 0.15
        for step in range(iterations):
            temp = t_start * ((t_end / t_start) ** (step / max(1, iterations - 1)))
            if rng.random() < 0.65:
                a, b = rng.sample(pool_keys, 2)
                if state[a] == state[b]:
                    continue
                state[a], state[b] = state[b], state[a]
                ns = score_state(state)
                delta = ns - cur_score
                if delta >= 0 or rng.random() < math.exp(delta / max(1e-6, temp)):
                    cur_score = ns
                    if cur_score > best_score:
                        best_score = cur_score
                        best_state = dict(state)
                else:
                    state[a], state[b] = state[b], state[a]
            else:
                a = rng.choice(pool_keys)
                old_rm = state[a]
                cand_rm = rng.choice(room_names)
                if cand_rm == old_rm:
                    continue
                if cand_rm is not None:
                    cnt = sum(1 for v in state.values() if v == cand_rm)
                    if cnt >= max_by_rm[cand_rm]:
                        continue
                state[a] = cand_rm
                ns = score_state(state)
                delta = ns - cur_score
                if delta >= 0 or rng.random() < math.exp(delta / max(1e-6, temp)):
                    cur_score = ns
                    if cur_score > best_score:
                        best_score = cur_score
                        best_state = dict(state)
                else:
                    state[a] = old_rm
        state = best_state

    final_rooms: dict[str, list[dict[str, Any]]] = {r.name: [] for r in specs}
    assigned: set[int] = set()

    for ck, rn in locked_cats.items():
        if rn in final_rooms:
            final_rooms[rn].append(cat_by_key[ck])
            assigned.add(ck)

    for ck, rn in state.items():
        if rn is not None and ck not in assigned:
            final_rooms[rn].append(cat_by_key[ck])
            assigned.add(ck)

    rem = [c for c in ranked if c["key"] not in assigned]
    for c in rem:
        if disorder_count(c) > 0 or c["injured"]:
            order = sick_rooms + nursery_rooms + quar_rooms + breed_rooms
        elif defect_count(c) > 0 or cat_breed_score(c, weights) < 24.0:
            order = quar_rooms + nursery_rooms + sick_rooms + breed_rooms
        else:
            order = nursery_rooms + sick_rooms + quar_rooms + breed_rooms
        for rm in order:
            if len(final_rooms[rm.name]) < rm.max_cats:
                final_rooms[rm.name].append(c)
                assigned.add(c["key"])
                break

    unassigned = [c for c in ranked if c["key"] not in assigned]
    fallback = (
        next((r.name for r in nursery_rooms), None)
        or next((r.name for r in quar_rooms), None)
        or next((r.name for r in reversed(specs) if r.role != "locked"), specs[0].name)
    )

    plans: list[RoomPlan] = []
    total_ens = 0.0
    moves: dict[int, str] = {}

    for sp in specs:
        reg = final_rooms[sp.name]
        ey = ey_assign[sp.name]
        eff_stim = sp.stimulation + len(ey)
        pairs_raw, conflicts = valid_room_pairs(reg)
        pevals = [evaluate_pair(a, b, eff_stim, pedigree, weights) for a, b in pairs_raw]
        pevals.sort(key=lambda p: p.ens, reverse=True)
        rscore = (sum(p.ens for p in pevals) / len(pevals) * min(2.0, 1.0 + 0.25 * len(pevals))) if pevals else 0.0
        rscore -= len(conflicts) * 12.0
        if sp.name in breed_names:
            total_ens += rscore
        plans.append(RoomPlan(sp, reg, pevals, conflicts, ey, rscore))
        for c in reg + ey:
            if c["room"] != sp.name:
                moves[c["key"]] = sp.name

    for c in unassigned:
        if c["room"] != fallback:
            moves[c["key"]] = fallback

    return OptimizationResult(
        goal_id=goal_id,
        plans=plans,
        unassigned=unassigned,
        moves=moves,
        total_ens=total_ens,
        before_ens=before_ens,
    )

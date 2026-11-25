"""Game-accurate Mewgenics breeding simulation, CoI calculator, and room organizer.

Breeding formulas & pedigree math adapted from PurpleMyst/mewgenics_breeding_helper (MIT)
and frankieg33/MewgenicsBreedingManager, tailored for MewDeck's 1080p gamepad UI with
1-press save application via `Changes.room_moves`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
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

ROOM_ROLES = ("breeding", "nursery", "sickbay", "quarantine", "locked")
ROOM_ROLE_LABELS = {
    "breeding": "Breeding",
    "nursery": "General / Nursery",
    "sickbay": "Sickbay (Health)",
    "quarantine": "Quarantine / Cull",
    "locked": "Locked (Keep)",
}


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
            # Recurse on the deeper/newer individual (higher SQL key)
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


# --------------------------------------------------------------------------- compatibility & ENS

def has_eternal_youth(cat: dict[str, Any]) -> bool:
    ab = cat["abilities"]
    return "EternalYouth" in (ab.get("disorder") or []) or "EternalYouth" in (ab.get("passive") or [])


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
    # Novel disorder probability + inherited disorder burden
    novel_dis = min(1.0, 0.02 + 0.4 * max(coi - 0.20, 0.0))
    inh_dis = 0.15 * (disorder_count(a) + disorder_count(b))
    exp_def = 0.40 * (defect_count(a) + defect_count(b))

    spell1 = min(1.0, 0.20 + 0.025 * stim)
    passive = min(1.0, 0.05 + 0.01 * stim)

    # Normalize weighted sum back to ~7-stat scale
    w_norm = sum(w.get(s, 1.0) for s in STAT_NAMES) / 7.0
    norm_stats = weighted_sum / max(0.01, w_norm)

    # ENS = expected weighted stats + bonus for guaranteed/likely 7s - malady penalties - inbreeding penalty
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
        if cat.get("hater_id") == other["key"] or other.get("hater_id") == cat["key"]:
            continue
        out.append(evaluate_pair(cat, other, stimulation, pedigree, weights))
    out.sort(key=lambda pe: pe.ens, reverse=True)
    return out[:limit]


# --------------------------------------------------------------------------- room stats & optimizer

@dataclass
class RoomSpec:
    name: str
    role: str = "breeding"  # breeding | nursery | sickbay | quarantine | locked
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
    moves: dict[int, str]  # cat_key -> new_room (only cats that actually change room)
    total_ens: float
    before_ens: float


def compute_room_specs(parsed: dict[str, Any]) -> list[RoomSpec]:
    """Build RoomSpec list from the save's actual rooms and placed furniture stats."""
    rooms = list(parsed.get("rooms") or [])
    if not rooms:
        rooms = ["Attic"]

    totals: dict[str, dict[str, float]] = {r: {} for r in rooms}
    for f in parsed["furniture"]["placed"]:
        rm = f.get("room")
        if rm in totals:
            for ek, ev in gd.furniture_info(f["furniture_id"]).get("effects", {}).items():
                totals[rm][ek] = totals[rm].get(ek, 0.0) + float(ev)

    # Heuristic defaults: highest-stimulation rooms -> Breeding, high-Health -> Sickbay, Attic -> Nursery
    specs: list[RoomSpec] = []
    for rm in rooms:
        ef = totals[rm]
        stim = ef.get("Stimulation", 0.0)
        comf = ef.get("Comfort", 0.0)
        hlth = ef.get("Health", 0.0)
        appl = ef.get("Appeal", 0.0)
        evol = ef.get("Evolution", 0.0)
        specs.append(RoomSpec(
            name=rm,
            role="breeding",
            max_cats=4 if "Small" in rm else 6,
            stimulation=stim,
            comfort=comf,
            health=hlth,
            appeal=appl,
            evolution=evol,
        ))

    # Smart role assignment when multiple rooms exist
    if len(specs) >= 2:
        ranked_stim = sorted(specs, key=lambda r: (r.stimulation, r.comfort, r.name != "Attic"), reverse=True)
        for i, r in enumerate(ranked_stim):
            if i < max(1, len(specs) - 1):
                r.role = "breeding"
            elif r.health >= 4 and r.stimulation < ranked_stim[0].stimulation:
                r.role = "sickbay"
            else:
                r.role = "nursery"
        # If there's an Attic with lower stimulation than another room, make it Nursery by default
        if len(specs) >= 3:
            for r in specs:
                if r.name == "Attic" and r.stimulation < ranked_stim[0].stimulation:
                    r.role = "nursery"
    return specs


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
        if sp.role == "breeding":
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
    """Optimize house cat placement across rooms via Simulated Annealing + Greedy routing.

    Runs in ~60-150ms in pure Python so ROG Ally / Steam Deck gets instant results.
    """
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
    sick_rooms = sorted([r for r in specs if r.role == "sickbay"], key=lambda r: r.health, reverse=True)
    nursery_rooms = sorted([r for r in specs if r.role == "nursery"], key=lambda r: r.comfort, reverse=True)
    quar_rooms = [r for r in specs if r.role == "quarantine"]

    # Route Eternal Youth cats to highest-stimulation breeding rooms (free +1 Stim buff)
    ey_assign: dict[str, list[dict[str, Any]]] = {r.name: [] for r in specs}
    for c in locked_cats:
        pass
    if breed_rooms:
        for i, ec in enumerate(ey_cats):
            ey_assign[breed_rooms[i % len(breed_rooms)].name].append(ec)
    elif specs:
        for ec in ey_cats:
            ey_assign[specs[0].name].append(ec)

    # Pre-cache pair scores per room stimulation level for blazing-fast SA
    cat_by_key = {c["key"]: c for c in house_cats}
    pair_cache: dict[tuple[int, int, int], PairEval] = {}

    def get_pair(ak: int, bk: int, stim_i: int) -> PairEval:
        k = (ak, bk, stim_i) if ak < bk else (bk, ak, stim_i)
        pe = pair_cache.get(k)
        if pe is None:
            pe = evaluate_pair(cat_by_key[ak], cat_by_key[bk], float(stim_i), pedigree, weights)
            pair_cache[k] = pe
        return pe

    # Build candidate pool for breeding rooms: top healthy/breedable cats
    breed_cap = sum(r.max_cats for r in breed_rooms)
    ranked = sorted(reg_cats, key=lambda c: cat_breed_score(c, weights), reverse=True)
    # Keep top candidates up to max(breed_cap + 6, len(ranked)) so SA can swap in/out
    sa_pool = ranked[: max(breed_cap + 8, min(len(ranked), 28))]
    pool_keys = [c["key"] for c in sa_pool]

    # Room assignment state: cat_key -> room_name | None (unassigned from breeding rooms)
    rng = random.Random(seed)
    state: dict[int, str | None] = {k: None for k in pool_keys}
    # Greedy seed into breeding rooms
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
            # Reward strong average pair quality + multiple viable pairs (diminishing returns)
            tot += avg * min(2.0, 1.0 + 0.25 * len(scores))
            # Mild move-stability bonus so tied solutions keep cats where they already are
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
                # Swap two cats' rooms
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
                # Move one cat to another room (or out to bench)
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

    # Finalize assignments: breeding rooms + locked rooms + greedy overflow routing
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

    # Greedy routing for remaining regular cats (sickbay -> nursery -> quarantine -> any breeding room with space)
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
    # Keep overflow cats in a fallback room (Attic or last non-locked room) so no cat is ever dropped
    fallback = next((r.name for r in nursery_rooms), None) or next((r.name for r in reversed(specs) if r.role != "locked"), specs[0].name)

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
        if sp.role == "breeding":
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

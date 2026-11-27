"""House Room Rules, Furniture Priority Guide & Breeding Helper UI (MewDeck Rooms Tab + Partner Inspector).

Replaces chaotic blind auto-shuffling with practical rule-based house zoning
(Retired -> Attic, Kittens -> Nursery, Battle-Ready -> Fighters, Breeding, Sickbay, Locked),
Hater fight prevention, per-room Furniture Priority Guide + Storage picks,
plus the Offspring Simulator (Best Pairs) and Partner Inspector.
"""

from __future__ import annotations

from typing import Any

import pygame

from . import breeding as br
from . import editors as ed
from . import gamedata as gd


def _coi_color(ui, sev: str):
    return {"good": ui.GOOD, "info": ui.INFO, "warn": ui.ACCENT, "bad": ui.BAD}.get(sev, ui.DIM)


def _room_label(rm: str) -> str:
    return (rm or "Unassigned").replace("_", " ")


_STAT_ABBR = {"Comfort": "Comf", "Stimulation": "Stim", "Health": "Hlth", "Appeal": "App", "Evolution": "Evo", "FoodStorage": "Food", "FightRisk": "Risk", "BreedSuppression": "NoBreed"}


def _role_color(ui, role: str):
    return {
        "retirement": (186, 146, 224),
        "nursery": ui.INFO,
        "fighters": (236, 120, 96),
        "breeding": ui.GOOD,
        "sickbay": ui.ACCENT,
        "general": ui.DIM,
        "locked": ui.FAINT,
    }.get(role, ui.DIM)


def _cat_tag(c: dict[str, Any]) -> str:
    if br.is_kitten(c):
        return "Kitten"
    if br.is_retired_cat(c):
        return "Retired"
    if br.is_sick(c):
        return "Sick"
    return c.get("class") or "Adult"


def draw_pair_simulator(ui, surf: pygame.Surface, box: pygame.Rect, pe: br.PairEval, stimulation: float,
                        room_moves: dict[int, str] | None = None) -> None:
    """Draw side-by-side offspring simulator card for a breeding pair."""
    ui.rrect(surf, box, ui.CARD, 24)
    x, y, w = box.x + 28, box.y + 24, box.w - 56
    a, b = pe.cat_a, pe.cat_b
    moves = room_moves or {}

    ui.text(surf, "OFFSPRING SIMULATOR", (x, y), 18, ui.FAINT, "black")
    cl, sev = br.coi_label(pe.coi)
    ccol = _coi_color(ui, sev)
    ui.pill(surf, f"CoI {cl}", box.right - 28, y + 10, ui.mix(ccol, ui.CARD, .7), ccol, size=19, anchor="midright")
    y += 32

    pair_title = f"{a['name'] or '#' + str(a['key'])}  ×  {b['name'] or '#' + str(b['key'])}"
    ui.text(surf, pair_title, (x, y), 30, ui.TEXT, "black", maxw=w)
    y += 42

    bx = x
    bx = ui.pill(surf, f"Exp {pe.expected_total:.1f}/49", bx, y, ui.mix(ui.GOOD, ui.CARD, .7), ui.GOOD, size=19).right + 8
    bx = ui.pill(surf, f"{pe.expected_sevens:.1f} × 7s", bx, y, ui.mix(ui.ACCENT, ui.CARD, .7), ui.ACCENT, size=19).right + 8
    bx = ui.pill(surf, f"ENS {pe.ens:.1f}", bx, y, ui.CARD_HI, ui.TEXT, size=19).right + 8
    if pe.is_lovers:
        ui.pill(surf, "LOVERS", bx, y, (235, 118, 175), ui.DARK, size=18)
    y += 46

    ui.text(surf, "STAT", (x, y), 17, ui.FAINT, "black")
    ui.text(surf, "PARENTS", (x + 120, y), 17, ui.FAINT, "black")
    ui.text(surf, "EXPECTED", (box.right - 28, y), 17, ui.FAINT, "black", anchor="topright")
    y += 26

    for s in br.STAT_NAMES:
        va, vb = a["stats"][s], b["stats"][s]
        ev = pe.expected_stats[s]
        ico = ed.get_stat_icon(s, 28)
        if ico is not None:
            surf.blit(ico, ico.get_rect(midleft=(x - 2, y + 12)))
        ui.text(surf, s, (x + 30, y + 12), 20, ui.TEXT, "black", anchor="midleft")
        ui.text(surf, f"{va} / {vb}", (x + 128, y + 12), 21, ui.DIM, "bold", anchor="midleft")
        bar_x = x + 220
        bar_w = w - 310
        ui.rrect(surf, (bar_x, y + 4, bar_w, 16), ui.CARD_HI, 6, ink=False)
        fill_w = max(8, round(bar_w * min(7.0, ev) / 7.0))
        ui.rrect(surf, (bar_x, y + 4, fill_w, 16), ui.stat_col(round(ev)), 6, ink=False)
        pygame.draw.rect(surf, ui.INK, (bar_x, y + 4, bar_w, 16), width=2, border_radius=6)
        ui.text(surf, f"{ev:.1f}", (box.right - 28, y + 12), 22, ui.stat_col(round(ev)), "black", anchor="midright")
        y += 34

    y += 14
    ibox = pygame.Rect(x, y, w, 134)
    ui.rrect(surf, ibox, ui.CARD_HI, 16)
    ix, iy = ibox.x + 20, ibox.y + 14
    ui.text(surf, f"INHERITANCE ODDS  (STIM {stimulation:+.0f})", (ix, iy), 17, ui.FAINT, "black")
    iy += 28
    rows_info = [
        ("1st Active Spell", f"{pe.spell1_prob * 100:.0f}%", ui.INFO),
        ("Passive Inherit", f"{pe.passive_prob * 100:.0f}%", ui.ACCENT),
        ("Novel Disorder Risk", f"{pe.novel_disorder_prob * 100:.0f}%", ui.BAD if pe.novel_disorder_prob > 0.05 else ui.GOOD),
    ]
    for lab, val, col in rows_info:
        ui.text(surf, lab, (ix, iy), 21, ui.DIM)
        ui.text(surf, val, (ibox.right - 20, iy), 22, col, "black", anchor="topright")
        iy += 30

    fy = ibox.bottom + 16
    if fy + 50 <= box.bottom - 10:
        eff_a = moves.get(a["key"], a["room"])
        eff_b = moves.get(b["key"], b["room"])
        ra = _room_label(eff_a) + ("*" if eff_a != a["room"] else "")
        rb = _room_label(eff_b) + ("*" if eff_b != b["room"] else "")
        same = eff_a == eff_b
        queued = (eff_a != a["room"]) or (eff_b != b["room"])
        note = " (paired!)" if (same and queued) else (" (already together)" if same else f"  +  {rb}")
        ui.text(surf, f"Rooms: {ra}{note}",
                (x, fy), 21, ui.ACCENT if (same and queued) else (ui.GOOD if same else ui.DIM), maxw=w)
        ui.text(surf, "Press A to pair both in your best breeding room.",
                (x, fy + 28), 20, ui.FAINT, maxw=w)


class PartnerDetailScreen:
    """Inspect one cat's pedigree, personality, relationships, and best compatible partners."""

    def __init__(self, ui, app, cat: dict[str, Any], stimulation: float, goal_idx: int) -> None:
        self.ui = ui
        self.app = app
        self.cat = cat
        self.stimulation = stimulation
        self.goal_idx = goal_idx
        self.title = f"Partners for {cat['name'] or '#' + str(cat['key'])}"
        self.list = ui.ListView(row_h=100, gap=10)
        self.rebuild()

    def on_enter(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        p = self.app.snap.parsed
        house = [c for c in p["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
        weights = br.GOALS[self.goal_idx][2]
        partners = br.best_partners_for(self.cat, house, self.stimulation, p.get("pedigree") or {}, weights, limit=25)
        self.list.set_rows([self.ui.Row(pe.cat_b["name"] or f"#{pe.cat_b['key']}", data=pe) for pe in partners])

    def _best_breeding_room(self) -> str:
        specs = br.compute_room_specs(self.app.snap.parsed, br.load_room_rules())
        best = max(specs, key=lambda r: (r.role == "breeding", r.stimulation, r.comfort))
        return best.name

    def handle(self, btn: str) -> None:
        ui = self.ui
        if btn in (ui.UP, ui.DOWN):
            self.list.move(-1 if btn == ui.UP else 1)
        elif btn in (ui.LT, ui.RT):
            self.list.move(-8 if btn == ui.LT else 8)
        elif btn == ui.Y:
            self.goal_idx = (self.goal_idx + 1) % len(br.GOALS)
            self.rebuild()
            self.app.toast(f"Goal: {br.GOALS[self.goal_idx][1]}", ui.ACCENT, 1.8)
        elif btn == ui.A and self.list.current:
            pe: br.PairEval = self.list.current.data
            dst = self._best_breeding_room()
            for c in (pe.cat_a, pe.cat_b):
                if c["room"] != dst:
                    self.app.pending.room_moves[c["key"]] = dst
                else:
                    self.app.pending.room_moves.pop(c["key"], None)
            self.app.toast(f"Queued {pe.cat_a['name']} + {pe.cat_b['name']} » {_room_label(dst)} (Menu to save)", ui.GOOD, 3.0)
        elif btn == ui.START:
            self.app.write()
        elif btn == ui.B:
            self.app.pop()

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(self.ui.A)
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        pe: br.PairEval = row.data
        other = pe.cat_b
        scol = ui.SEX_COL.get(other["sex"], ui.DIM)
        pygame.draw.circle(surf, ui.mix(scol, ui.CARD, .25), (r.x + 42, r.centery), 22)
        ui.text(surf, {"Male": "M", "Female": "F"}.get(other["sex"], "?"), (r.x + 42, r.centery), 22, ui.DARK, "black", anchor="center")

        nr = ui.text(surf, other["name"] or f"#{other['key']}", (r.x + 80, r.y + 14), 30, ui.TEXT, "black", maxw=320)
        if pe.is_lovers:
            ui.pill(surf, "LOVERS", nr.right + 10, nr.centery, (235, 118, 175), ui.DARK, size=16, anchor="midleft")
        rm = _room_label(self.app.pending.room_moves.get(other["key"], other["room"]))
        ui.text(surf, f"{other['class']} · {rm} · Exp {pe.expected_total:.1f} ({pe.expected_sevens:.1f}×7)",
                (r.x + 80, r.bottom - 14), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 340)

        cl, sev = br.coi_label(pe.coi)
        ccol = _coi_color(ui, sev)
        ui.pill(surf, cl, r.right - 140, r.centery, ui.mix(ccol, ui.CARD, .7), ccol, size=18, anchor="midright")
        ui.text(surf, f"ENS {pe.ens:.1f}", (r.right - 24, r.centery), 24, ui.ACCENT, "black", anchor="midright")

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 18, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 18)
        c = self.cat
        all_cats = self.app.snap.parsed["all_cats"]
        pa = all_cats.get(c.get("parent_a"), {}).get("name") if c.get("parent_a") else ""
        pb = all_cats.get(c.get("parent_b"), {}).get("name") if c.get("parent_b") else ""
        lineage = f"Parents: {pa or '?'} × {pb or '?'}" if (pa or pb) else "Founder (wild/stray)"
        traits = [
            f"{c['sex']} · {c['age']}d · Sum {br.stat_sum(c)} ({br.perfect_sevens(c)}×7)",
            lineage,
            f"Lib {c['libido']:.0%} · Agg {c['aggression']:.0%} · Fert {c['fertility']:.2f}",
        ]
        if c.get("lover_name"):
            traits.append(f"Loves {c['lover_name']}")
        if c.get("hater_name"):
            traits.append(f"Hates {c['hater_name']}")
        ui.text(surf, "  ·  ".join(traits), (area.x, area.y + 14), 22, ui.DIM, anchor="midleft", maxw=area.w - 280)
        ui.pill(surf, f"Goal: {br.GOALS[self.goal_idx][1]}", area.right - 16, area.y + 14, ui.CARD_HI, ui.ACCENT,
                size=19, anchor="midright")

        list_w = area.w - 620
        la = pygame.Rect(area.x, area.y + 48, list_w, area.h - 48)
        if not self.list.rows:
            ui.empty_state(surf, la, "Heart", "No compatible partners",
                           "No compatible breeding partners found in house for this cat.")
        else:
            self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 580, area.y + 48, 580, area.h - 48)
        if self.list.current:
            draw_pair_simulator(ui, surf, box, self.list.current.data, self.stimulation, self.app.pending.room_moves)
        else:
            ui.rrect(surf, box, ui.CARD, 24)

    def hint_items(self):
        return [("A", "Pair in Best Room"), ("Y", "Switch Goal"), ("B", "Back")] + self.ui.pending_hint(self.app)


class BreedingTab:
    """House Rooms & Breeding Helper tab (Room Rules & Furniture Guide, Best Pairs, Partners & Rooms)."""

    name = "Rooms"
    MODES = [
        ("rules", "Room Rules"),
        ("pairs", "Best Pairs"),
        ("partners", "Partners & Rooms"),
    ]

    def __init__(self, ui, app) -> None:
        self.ui = ui
        self.app = app
        self.m = 0
        self.goal_idx = 0
        self.query = ""
        self.specs: list[br.RoomSpec] = []
        self.rule_result: br.RulePlanResult | None = None
        self.top_pairs: list[br.PairEval] = []
        self.best_stim: float = 0.0
        self._plan_key: tuple | None = None
        self._plan_snap: Any = None
        self.list = ui.ListView(row_h=100, gap=10)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []
        self.room_pill_rects: list[tuple[pygame.Rect, dict[str, Any]]] = []
        self.goal_rect: pygame.Rect | None = None
        self.search_rect: pygame.Rect | None = None

    def on_enter(self) -> None:
        p = self.app.snap.parsed
        saved = br.load_room_rules()
        existing_roles = {r.name: (r.role, r.max_cats) for r in self.specs}
        self.specs = br.compute_room_specs(p, saved)
        for sp in self.specs:
            if sp.name in existing_roles:
                sp.role, sp.max_cats = existing_roles[sp.name]
        self.best_stim = max(
            (r.stimulation for r in self.specs if r.role == "breeding"),
            default=max((r.stimulation for r in self.specs), default=0.0),
        )
        self._run_planner()
        self._rebuild()

    def _run_planner(self) -> None:
        snap = self.app.snap
        key = (
            id(snap),
            self.goal_idx,
            tuple((s.name, s.role, s.max_cats) for s in self.specs),
        )
        if key == self._plan_key and self._plan_snap is snap and self.rule_result is not None:
            return
        self._plan_key = key
        self._plan_snap = snap
        p = snap.parsed
        goal_id = br.GOALS[self.goal_idx][0]
        self.rule_result = br.apply_room_rules(p, self.specs, goal_id=goal_id, avoid_haters=True)
        self.best_stim = max(
            (r.stimulation for r in self.specs if r.role == "breeding"),
            default=max((r.stimulation for r in self.specs), default=0.0),
        )
        house = [c for c in p["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
        ped = p.get("pedigree") or {}
        weights = br.GOALS[self.goal_idx][2]
        pairs: list[br.PairEval] = []
        for i, a in enumerate(house):
            for b in house[i + 1 :]:
                if not br.can_breed(a, b) or br.hates_each_other(a, b):
                    continue
                pairs.append(br.evaluate_pair(a, b, self.best_stim, ped, weights))
        pairs.sort(key=lambda pe: pe.ens, reverse=True)
        self.top_pairs = pairs[:60]

    def _matches_cat(self, c: dict[str, Any], q: str) -> bool:
        if not q:
            return True
        hay = f"{c['name']} {c['class']} {c['room']} {c['sex']}".lower()
        return all(t in hay for t in q.lower().split())

    def _rebuild(self) -> None:
        ui = self.ui
        mode = self.MODES[self.m][0]
        rows = []
        if mode == "rules" and self.rule_result:
            rows.append(ui.Row("Apply Room Rules", data=("apply_rules", self.rule_result)))
            for rp in self.rule_result.plans:
                rows.append(ui.Row(_room_label(rp.spec.name), data=("rule_room", rp)))
        elif mode == "pairs":
            for pe in self.top_pairs:
                if self.query:
                    hay = f"{pe.cat_a['name']} {pe.cat_b['name']} {pe.cat_a['class']} {pe.cat_b['class']}".lower()
                    if not all(t in hay for t in self.query.lower().split()):
                        continue
                rows.append(ui.Row(f"{pe.cat_a['name']} × {pe.cat_b['name']}", data=("pair", pe)))
        else:
            house = [c for c in self.app.snap.parsed["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
            weights = br.GOALS[self.goal_idx][2]
            house.sort(key=lambda c: br.cat_breed_score(c, weights), reverse=True)
            for c in house:
                if self._matches_cat(c, self.query):
                    rows.append(ui.Row(c["name"] or f"#{c['key']}", data=("partner_cat", c)))
        self.list.set_rows(rows)

    def _cycle_cat_room(self, c: dict[str, Any]) -> None:
        rooms = self.app.snap.parsed.get("rooms") or ["Attic"]
        cur_rm = self.app.pending.room_moves.get(c["key"], c["room"])
        ni = (rooms.index(cur_rm) + 1) % len(rooms) if cur_rm in rooms else 0
        new_rm = rooms[ni]
        if new_rm == c["room"]:
            self.app.pending.room_moves.pop(c["key"], None)
        else:
            self.app.pending.room_moves[c["key"]] = new_rm
        self.app.toast(f"{c['name'] or '#' + str(c['key'])} » {_room_label(new_rm)}", self.ui.INFO, 1.6)

    def _count_for(self, q: str) -> int:
        mode = self.MODES[self.m][0]
        if mode == "pairs":
            if not q:
                return len(self.top_pairs)
            toks = q.lower().split()
            return sum(
                1 for pe in self.top_pairs
                if all(t in f"{pe.cat_a['name']} {pe.cat_b['name']} {pe.cat_a['class']} {pe.cat_b['class']}".lower() for t in toks)
            )
        house = [c for c in self.app.snap.parsed["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
        return sum(1 for c in house if self._matches_cat(c, q))

    def _open_search(self) -> None:
        if self.MODES[self.m][0] == "rules":
            return
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        self.app.push(ed.SearchModal(self.ui, self.app, "Filter Cats / Pairs", self.query, self._count_for, done))

    def _best_breeding_room(self) -> str:
        best = max(self.specs, key=lambda r: (r.role == "breeding", r.stimulation, r.comfort))
        return best.name

    def handle(self, btn: str) -> bool:
        ui = self.ui
        mode = self.MODES[self.m][0]
        row = self.list.current

        if btn in (ui.UP, ui.DOWN):
            self.list.move(-1 if btn == ui.UP else 1)
        elif btn in (ui.LT, ui.RT):
            self.list.move(-8 if btn == ui.LT else 8)
        elif btn in (ui.LEFT, ui.RIGHT):
            self.m = (self.m + (1 if btn == ui.RIGHT else -1)) % len(self.MODES)
            self.list.sel = 0
            self._rebuild()
        elif btn == ui.Y:
            self.goal_idx = (self.goal_idx + 1) % len(br.GOALS)
            self._run_planner()
            self._rebuild()
            self.app.toast(f"Stat Priority: {br.GOALS[self.goal_idx][1]}", ui.ACCENT, 2.0)
        elif btn == ui.SELECT and mode != "rules":
            self._open_search()
        elif btn == ui.B and self.query:
            self.query = ""
            self.list.sel = 0
            self._rebuild()
        elif btn == ui.A and row:
            kind, obj = row.data
            if kind == "apply_rules" and self.rule_result:
                if not self.rule_result.moves:
                    self.app.toast("All cats already match your room rules!", ui.GOOD, 2.2)
                else:
                    for ck, rm in self.rule_result.moves.items():
                        self.app.pending.room_moves[ck] = rm
                    self.app.toast(f"Queued {len(self.rule_result.moves)} rule moves · press Menu to save", ui.GOOD, 3.5)
            elif kind == "rule_room":
                sp: br.RoomSpec = obj.spec
                i = br.ROOM_ROLES.index(sp.role) if sp.role in br.ROOM_ROLES else 0
                sp.role = br.ROOM_ROLES[(i + 1) % len(br.ROOM_ROLES)]
                br.save_room_rules(self.specs)
                self._run_planner()
                self._rebuild()
                self.app.toast(f"{_room_label(sp.name)} » {br.ROOM_ROLE_LABELS[sp.role]}", ui.ACCENT, 1.8)
            elif kind == "pair":
                pe: br.PairEval = obj
                dst = self._best_breeding_room()
                for c in (pe.cat_a, pe.cat_b):
                    if c["room"] != dst:
                        self.app.pending.room_moves[c["key"]] = dst
                    else:
                        self.app.pending.room_moves.pop(c["key"], None)
                self.app.toast(f"Queued {pe.cat_a['name']} + {pe.cat_b['name']} » {_room_label(dst)} (Menu to save)", ui.GOOD, 3.0)
            elif kind == "partner_cat":
                self.app.push(PartnerDetailScreen(ui, self.app, obj, self.best_stim, self.goal_idx))
        elif btn == ui.X and row:
            kind, obj = row.data
            if kind == "rule_room":
                sp: br.RoomSpec = obj.spec
                sp.max_cats = 2 if sp.max_cats >= 8 else sp.max_cats + 1
                br.save_room_rules(self.specs)
                self._run_planner()
                self._rebuild()
                self.app.toast(f"{_room_label(sp.name)} cap: {sp.max_cats} cats (4 = no Comfort penalty)", ui.INFO, 2.0)
            elif kind == "partner_cat":
                self._cycle_cat_room(obj)
            else:
                return False
        else:
            return False
        return True

    def click(self, pos) -> None:
        if self.goal_rect and self.goal_rect.collidepoint(pos):
            self.handle(self.ui.Y)
            return
        if self.search_rect and self.search_rect.collidepoint(pos):
            self._open_search()
            return
        for r, i in self.chip_rects:
            if r.collidepoint(pos):
                self.m = i
                self.list.sel = 0
                self._rebuild()
                return
        for r, cat in self.room_pill_rects:
            if r.collidepoint(pos):
                self._cycle_cat_room(cat)
                return
        i = self.list.hit(pos)
        if i is not None:
            kind = self.list.rows[i].data[0]
            if i == self.list.sel or kind == "apply_rules":
                self.list.sel = i
                self.handle(self.ui.A)
            else:
                self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        kind, obj = row.data
        if kind == "apply_rules":
            res: br.RulePlanResult = obj
            mv = len(res.moves)
            ui.rrect(surf, (r.x + 4, r.y + 8, 8, r.h - 16), ui.GOOD if mv else ui.LINE, 4, ink=False)
            ui.text(surf, "Apply House Room Rules", (r.x + 28, r.y + 15), 31, ui.GOOD if mv else ui.TEXT, "black")
            c_note = f"{res.total_conflicts} fights" if res.total_conflicts else "0 Hater fights"
            sub = f"{mv} cat move{'s' if mv != 1 else ''} · {c_note} · rules saved automatically"
            ui.text(surf, sub, (r.x + 28, r.bottom - 15), 21, ui.BAD if res.total_conflicts else ui.DIM, anchor="bottomleft", maxw=r.w - 220)
            ui.pill(surf, f"APPLY ({mv})" if mv else "SORTED", r.right - 26, r.centery,
                    ui.GOOD if mv else ui.CARD_HI, ui.DARK if mv else ui.DIM, size=20, anchor="midright")
        elif kind == "rule_room":
            rp: br.RoomRulePlan = obj
            sp = rp.spec
            role_col = _role_color(ui, sp.role)
            guide = br.furniture_guide_for(sp.role)
            ui.rrect(surf, (r.x + 4, r.y + 8, 8, r.h - 16), role_col, 4, ink=False)
            nr = ui.text(surf, _room_label(sp.name), (r.x + 28, r.y + 14), 31, ui.TEXT, "black", maxw=280)
            ui.pill(surf, br.ROOM_ROLE_LABELS[sp.role], nr.right + 12, nr.centery,
                    ui.mix(role_col, ui.CARD, .74), role_col, size=17, anchor="midleft")
            if rp.conflicts:
                meta = f"! {len(rp.conflicts)} HATER FIGHT · Comf {rp.effective_comfort:+.0f} · Hlth {sp.health:+.0f}"
                mcol = ui.BAD
            elif len(rp.cats) >= 2 and rp.effective_comfort < 0:
                meta = f"! Eff Comf {rp.effective_comfort:+.0f} (fight risk) · Need: {guide['short']}"
                mcol = ui.BAD
            else:
                meta = f"Comf {rp.effective_comfort:+.0f} · Stim {sp.stimulation:+.0f} · Hlth {sp.health:+.0f} · Guide: {guide['short']}"
                mcol = ui.DIM
            ui.text(surf, meta, (r.x + 28, r.bottom - 14), 20, mcol, anchor="bottomleft", maxw=r.w - 210)
            over = len(rp.cats) > sp.max_cats
            ui.pill(surf, f"{len(rp.cats)}/{sp.max_cats} cats", r.right - 26, r.y + 28,
                    ui.mix(ui.BAD, ui.CARD, .65) if over else ui.CARD_HI, ui.BAD if over else ui.TEXT, size=19, anchor="midright")
            if rp.storage_picks:
                ui.text(surf, f"+{len(rp.storage_picks)} in storage", (r.right - 26, r.bottom - 16), 19, ui.GOOD, "bold", anchor="bottomright")
        elif kind == "pair":
            pe: br.PairEval = obj
            a, b = pe.cat_a, pe.cat_b
            nr = ui.text(surf, f"{a['name']}  ×  {b['name']}", (r.x + 26, r.y + 14), 30, ui.TEXT, "black", maxw=r.w - 340)
            if pe.is_lovers:
                ui.pill(surf, "LOVERS", nr.right + 10, nr.centery, (235, 118, 175), ui.DARK, size=16, anchor="midleft")
            eff_a = self.app.pending.room_moves.get(a["key"], a["room"])
            eff_b = self.app.pending.room_moves.get(b["key"], b["room"])
            same = eff_a == eff_b
            ra = _room_label(eff_a) + ("*" if eff_a != a["room"] else "")
            rb = _room_label(eff_b) + ("*" if eff_b != b["room"] else "")
            loc = f"{ra}" + (" (together)" if same else f" + {rb}")
            ui.text(surf, f"Exp {pe.expected_total:.1f} ({pe.expected_sevens:.1f}×7)  ·  {loc}",
                    (r.x + 26, r.bottom - 14), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 300)
            cl, sev = br.coi_label(pe.coi)
            ccol = _coi_color(ui, sev)
            ui.pill(surf, cl, r.right - 148, r.centery, ui.mix(ccol, ui.CARD, .7), ccol, size=18, anchor="midright")
            ui.text(surf, f"ENS {pe.ens:.1f}", (r.right - 24, r.centery), 24, ui.ACCENT, "black", anchor="midright")
        elif kind == "partner_cat":
            c = obj
            scol = ui.SEX_COL.get(c["sex"], ui.DIM)
            pygame.draw.circle(surf, ui.mix(scol, ui.CARD, .25), (r.x + 42, r.centery), 22)
            ui.text(surf, {"Male": "M", "Female": "F"}.get(c["sex"], "?"), (r.x + 42, r.centery), 22, ui.DARK, "black", anchor="center")
            nr = ui.text(surf, c["name"] or f"#{c['key']}", (r.x + 80, r.y + 14), 30, ui.TEXT, "black", maxw=300)
            cur_rm = self.app.pending.room_moves.get(c["key"], c["room"])
            moved = cur_rm != c["room"]
            rp = ui.pill(surf, _room_label(cur_rm) + (" *" if moved else ""), nr.right + 12, nr.centery,
                         ui.ACCENT if moved else ui.CARD_HI, ui.DARK if moved else ui.DIM, size=17, anchor="midleft")
            self.room_pill_rects.append((rp, c))
            score = br.cat_breed_score(c, br.GOALS[self.goal_idx][2])
            parts = [_cat_tag(c), f"{c['age']}d", f"Sum {br.stat_sum(c)}", f"{br.perfect_sevens(c)}×7"]
            if c.get("lover_name"):
                parts.append(f"Loves {c['lover_name']}")
            if c.get("hater_name"):
                parts.append(f"Hates {c['hater_name']}")
            ui.text(surf, " · ".join(parts), (r.x + 80, r.bottom - 14), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 260)
            ui.text(surf, f"Score {score:.1f}", (r.right - 26, r.centery), 23, ui.GOOD if score >= 36 else ui.TEXT, "black", anchor="midright")

    def _draw_rule_summary(self, surf, box: pygame.Rect, res: br.RulePlanResult) -> None:
        ui = self.ui
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 28, box.y + 22, box.w - 56
        ui.text(surf, "HOUSE RULE PLAN", (x, y), 18, ui.ACCENT, "black")
        y += 30
        ui.text(surf, f"{len(res.moves)} move{'s' if len(res.moves) != 1 else ''} · {res.total_conflicts} fights",
                (x, y), 30, ui.GOOD if res.total_conflicts == 0 else ui.BAD, "black", maxw=w)
        y += 40

        # Compact room zoning summary chips
        for rp in res.plans[:5]:
            rc = _role_color(ui, rp.spec.role)
            ui.text(surf, _room_label(rp.spec.name), (x, y + 12), 21, ui.TEXT, "bold", anchor="midleft", maxw=175)
            ui.pill(surf, br.ROOM_ROLE_SHORT[rp.spec.role], x + 185, y + 12,
                    ui.mix(rc, ui.CARD, .74), rc, size=16, anchor="midleft")
            c_txt = f"{len(rp.cats)}/{rp.spec.max_cats} · Comf {rp.effective_comfort:+.0f}"
            ui.text(surf, c_txt, (box.right - 28, y + 12), 19,
                    ui.BAD if (len(rp.cats) >= 2 and rp.effective_comfort < 0) else ui.DIM, "bold", anchor="midright")
            y += 32

        if res.warnings:
            y += 6
            wlines: list[str] = []
            for ln in res.warnings[:2]:
                wlines += ed.wrap_lines(ui.F(19, "bold"), f"! {ln}", w - 28, max_lines=2)
            wlines = wlines[:3]
            wbox = pygame.Rect(x, y, w, 28 + len(wlines) * 26)
            ui.rrect(surf, wbox, ui.mix(ui.BAD, ui.CARD, .82), 14, ink=False)
            wy = wbox.y + 10
            for ln in wlines:
                ui.text(surf, ln, (wbox.x + 14, wy), 19, ui.BAD, "bold", maxw=wbox.w - 28)
                wy += 26
            y = wbox.bottom + 12
        else:
            y += 8

        ui.text(surf, "PLANNED CAT MOVES", (x, y), 17, ui.FAINT, "black")
        y += 26
        if not res.moves:
            ui.text(surf, "Every cat is already in the right room for your rules.", (x, y), 21, ui.GOOD, maxw=w)
        else:
            by_key = {c["key"]: c for c in self.app.snap.parsed["cats"]}
            shown = 0
            for ck, dst in res.moves.items():
                if y + 30 > box.bottom - 52:
                    rem = len(res.moves) - shown
                    if rem > 0:
                        ui.text(surf, f"…and {rem} more moves", (x, y), 19, ui.FAINT)
                    break
                c = by_key.get(ck)
                nm = (c["name"] or f"#{ck}") if c else f"#{ck}"
                tag = _cat_tag(c) if c else ""
                src = _room_label(c["room"]) if c else "?"
                mv = f"{src} » {_room_label(dst)}"
                mv_w = min(ui.F(19, "bold").size(mv)[0], w - 200)
                ui.text(surf, f"{nm} ({tag})", (x, y), 20, ui.TEXT, "bold", maxw=w - mv_w - 14)
                ui.text(surf, mv, (box.right - 28, y), 19, ui.ACCENT, "bold", anchor="topright", maxw=mv_w + 2)
                y += 28
                shown += 1

        ui.text(surf, "Press A to queue moves · then Save (Start) to write.", (x, box.bottom - 32), 19, ui.FAINT, maxw=w)

    def _draw_rule_room_detail(self, surf, box: pygame.Rect, rp: br.RoomRulePlan) -> None:
        ui = self.ui
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 28, box.y + 22, box.w - 56
        sp = rp.spec
        rc = _role_color(ui, sp.role)
        guide = br.furniture_guide_for(sp.role)

        ui.text(surf, _room_label(sp.name).upper(), (x, y + 10), 22, ui.TEXT, "black", anchor="midleft")
        ui.pill(surf, br.ROOM_ROLE_LABELS[sp.role], box.right - 28, y + 10,
                ui.mix(rc, ui.CARD, .74), rc, size=18, anchor="midright")
        y += 34

        # 1. FURNITURE PRIORITY GUIDE inset card
        tip_lines = ed.wrap_lines(ui.F(19), guide["tip"], w - 32, max_lines=2)
        gbox_h = 120 + len(tip_lines) * 23
        gbox = pygame.Rect(x, y, w, gbox_h)
        ui.rrect(surf, gbox, ui.CARD_HI, 16)
        gx, gy = gbox.x + 16, gbox.y + 12
        ui.text(surf, "FURNITURE PRIORITY GUIDE", (gx, gy), 16, ui.ACCENT, "black")
        gy += 26
        px = gx
        for st in guide["prioritize"]:
            px = ui.pill(surf, f"+ {st}", px, gy, ui.mix(ui.GOOD, ui.CARD_HI, .68), ui.GOOD, size=17).right + 6
        for st in guide["avoid"]:
            px = ui.pill(surf, f"Avoid {st}", px, gy, ui.mix(ui.BAD, ui.CARD_HI, .68), ui.BAD, size=17).right + 6
        gy += 36
        for ln in tip_lines:
            ui.text(surf, ln, (gx, gy), 19, ui.DIM)
            gy += 23
        # Live room furniture scorecard bar inside the guide card
        gy += 4
        eff_col = ui.BAD if (len(rp.cats) >= 2 and rp.effective_comfort < 0) else (ui.GOOD if rp.effective_comfort > 0 else ui.DIM)
        score_line = f"Now: Comf {rp.effective_comfort:+.0f} · Stim {sp.stimulation:+.0f} · Hlth {sp.health:+.0f} · App {sp.appeal:+.0f} · Evo {sp.evolution:+.0f}"
        ui.text(surf, score_line, (gx, gy), 17, eff_col, "bold", maxw=w - 32)
        y = gbox.bottom + 14

        # Warnings if any
        if rp.warnings:
            for ln in ed.wrap_lines(ui.F(19, "bold"), f"! {rp.warnings[0]}", w, max_lines=2):
                ui.text(surf, ln, (x, y), 19, ui.BAD, "bold", maxw=w)
                y += 25
            y += 3

        # 2. ASSIGNED CATS
        ui.text(surf, f"CATS FOR THIS ROOM ({len(rp.cats)}/{sp.max_cats})", (x, y), 17, ui.FAINT, "black")
        y += 24
        storage_reserve = 128 if rp.storage_picks else 42
        max_cat_y = box.bottom - storage_reserve
        if not rp.cats:
            ui.text(surf, "No house cats match this rule right now.", (x, y), 20, ui.FAINT)
            y += 28
        else:
            shown = 0
            for c in rp.cats:
                if y + 26 > max_cat_y:
                    rem = len(rp.cats) - shown
                    if rem > 0:
                        ui.text(surf, f"…and {rem} more cats", (x, y), 19, ui.FAINT)
                        y += 24
                    break
                moved = c["room"] != sp.name
                sx = {"Male": "M", "Female": "F"}.get(c["sex"], "?")
                tag = _cat_tag(c)
                from_txt = f" « {_room_label(c['room'])}" if moved else ""
                ui.text(surf, f"[{sx}] {c['name']} · {tag} · Sum {br.stat_sum(c)}{from_txt}",
                        (x, y), 20, ui.ACCENT if moved else ui.TEXT, "bold" if moved else "semi", maxw=w)
                y += 26
                shown += 1

        # 3. TOP STORAGE PICKS for this room
        if rp.storage_picks and y + 66 <= box.bottom - 34:
            sy = max(y + 8, box.bottom - 118)
            ui.text(surf, "PLACE FROM STORAGE (BEST MATCHES)", (x, sy), 16, ui.GOOD, "black")
            sy += 24
            for pk in rp.storage_picks[:2]:
                ef = " · ".join(f"{'+' if v > 0 else ''}{v} {_STAT_ABBR.get(k, k[:4])}" for k, v in pk["effects"].items() if v)
                cnt = f" ×{pk['count']}" if pk["count"] > 1 else ""
                ef_w = min(ui.F(18, "bold").size(ef)[0], w - 150)
                ui.text(surf, f"{pk['name']}{cnt}", (x, sy), 19, ui.TEXT, "bold", maxw=w - ef_w - 16)
                ui.text(surf, ef, (box.right - 28, sy), 18, ui.GOOD, "bold", anchor="topright", maxw=ef_w + 2)
                sy += 25

        ui.text(surf, "A: change rule  ·  X: adjust max cap (4 avoids Comfort penalty)", (x, box.bottom - 30), 18, ui.FAINT, maxw=w)

    def _draw_cat_summary(self, surf, box: pygame.Rect, c: dict[str, Any]) -> None:
        ui = self.ui
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 28, box.y + 24, box.w - 56
        ui.text(surf, "CAT BREEDING PROFILE", (x, y), 18, ui.FAINT, "black")
        y += 30
        ui.text(surf, f"{c['name'] or '#' + str(c['key'])}  ({c['sex']}, {c['age']}d)", (x, y), 32, ui.TEXT, "black", maxw=w)
        y += 44
        cur_rm = self.app.pending.room_moves.get(c["key"], c["room"])
        ui.text(surf, f"{_cat_tag(c)} · Room: {_room_label(cur_rm)} · Base Sum {br.stat_sum(c)}/49 ({br.perfect_sevens(c)}×7)",
                (x, y), 22, ui.ACCENT, "bold", maxw=w)
        y += 38

        for s in br.STAT_NAMES:
            v = c["stats"][s]
            ico = ed.get_stat_icon(s, 28)
            if ico is not None:
                surf.blit(ico, ico.get_rect(midleft=(x, y + 10)))
            ui.text(surf, s, (x + 36, y + 10), 21, ui.TEXT, "black", anchor="midleft")
            bx = x + 112
            bw = w - 172
            ui.rrect(surf, (bx, y + 2, bw, 16), ui.CARD_HI, 6, ink=False)
            if v > 0:
                ui.rrect(surf, (bx, y + 2, max(8, round(bw * min(7, v) / 7)), 16), ui.stat_col(v), 6, ink=False)
            pygame.draw.rect(surf, ui.INK, (bx, y + 2, bw, 16), width=2, border_radius=6)
            ui.text(surf, str(v), (box.right - 28, y + 10), 22, ui.stat_col(v), "black", anchor="midright")
            y += 31

        y += 14
        all_cats = self.app.snap.parsed["all_cats"]
        pa = all_cats.get(c.get("parent_a"), {}).get("name") if c.get("parent_a") else ""
        pb = all_cats.get(c.get("parent_b"), {}).get("name") if c.get("parent_b") else ""
        cl, sev = br.coi_label(c.get("coi", 0.0))
        lines = [
            ("Lineage", f"{pa or '?'} × {pb or '?'}" if (pa or pb) else "Founder (no parents)", ui.TEXT),
            ("Inbreeding (CoI)", cl, _coi_color(ui, sev)),
            ("Personality", f"Libido {c['libido']:.0%} · Agg {c['aggression']:.0%} · Fert {c['fertility']:.2f}", ui.DIM),
            ("Lover", c.get("lover_name") or "None", (235, 118, 175) if c.get("lover_name") else ui.FAINT),
            ("Hater / Rival", c.get("hater_name") or "None", ui.BAD if c.get("hater_name") else ui.FAINT),
        ]
        for lab, val, col in lines:
            ui.text(surf, lab, (x, y), 20, ui.FAINT, "bold")
            ui.text(surf, val, (box.right - 28, y), 21, col, "bold", anchor="topright", maxw=w - 170)
            y += 30
        ui.text(surf, "A: Find partners · X (or click room) to cycle room.",
                (x, box.bottom - 30), 19, ui.FAINT, maxw=w)

    def draw(self, surf, area: pygame.Rect) -> None:
        ui = self.ui
        self.chip_rects = []
        self.room_pill_rects = []
        if not hasattr(self, "seg_anim"):
            self.seg_anim = ui.RectAnim()
        self.chip_rects, _ = ui.segbar(surf, [(lbl, None, None, ui.GOOD if k == "rules" else ui.ACCENT)
                                              for k, lbl in self.MODES],
                                       self.m, area.x, area.y + 18, "DPAD_LR", self.seg_anim,
                                       mpos=getattr(self.app, "mouse_pos", (-1, -1)))

        self.goal_rect = ui.pill(surf, f"Goal: {br.GOALS[self.goal_idx][1]} (Y)", area.right - 20,
                                 self.chip_rects[0][0].centery, ui.CARD_HI, ui.ACCENT, size=20, anchor="midright")
        if self.MODES[self.m][0] != "rules":
            self.search_rect = ed.draw_search_pill(ui, surf, self.goal_rect.left - 14,
                                                   self.chip_rects[0][0].centery, self.query, anchor="midright")
        else:
            self.search_rect = None

        list_w = area.w - 620
        la = pygame.Rect(area.x, area.y + 62, list_w, area.h - 62)
        if not self.list.rows:
            if self.query:
                ui.empty_state(surf, la, "Heart", f'No matches for "{self.query}"', "Press B to clear the search.")
            else:
                ui.empty_state(surf, la, "Heart", "No house cats available",
                               "Bring cats home to organize rooms and simulate breeding pairs.")
        else:
            self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 580, area.y + 62, 580, area.h - 62)
        row = self.list.current
        if not row:
            ui.rrect(surf, box, ui.CARD, 24)
            return
        kind, obj = row.data
        if kind == "apply_rules" and self.rule_result:
            self._draw_rule_summary(surf, box, self.rule_result)
        elif kind == "rule_room":
            self._draw_rule_room_detail(surf, box, obj)
        elif kind == "pair":
            draw_pair_simulator(ui, surf, box, obj, self.best_stim, self.app.pending.room_moves)
        elif kind == "partner_cat":
            self._draw_cat_summary(surf, box, obj)

    def hint_items(self):
        mode = self.MODES[self.m][0]
        if mode == "rules":
            return [("A", "Apply / Rule"), ("X", "Room Cap"), ("Y", "Goal"), ("DPAD_LR", "View")]
        if mode == "pairs":
            return [("A", "Pair in Best Room"), ("Y", "Goal"), ("SELECT", "Search"), ("DPAD_LR", "View")]
        return [("A", "Find Partners"), ("X", "Cycle Room"), ("Y", "Goal"), ("SELECT", "Search"), ("DPAD_LR", "View")]

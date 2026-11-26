"""Cat Organizer & Breeding Helper UI (MewDeck Breeding Tab + Partner Inspector).

Brings Mewgenics Breeding Helper / Breeding Manager capabilities directly into MewDeck's
1080p gamepad UI, with 1-press application of optimized room layouts straight to the save.
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


def draw_pair_simulator(ui, surf: pygame.Surface, box: pygame.Rect, pe: br.PairEval, stimulation: float) -> None:
    """Draw side-by-side offspring simulator card for a breeding pair."""
    ui.rrect(surf, box, ui.CARD, 24)
    x, y, w = box.x + 28, box.y + 24, box.w - 56
    a, b = pe.cat_a, pe.cat_b

    ui.text(surf, "OFFSPRING SIMULATOR", (x, y), 18, ui.FAINT, "black")
    cl, sev = br.coi_label(pe.coi)
    ccol = _coi_color(ui, sev)
    ui.pill(surf, f"CoI {cl}", box.right - 28, y + 10, ui.mix(ccol, ui.CARD, .7), ccol, size=19, anchor="midright")
    y += 32

    pair_title = f"{a['name'] or '#' + str(a['key'])}  ×  {b['name'] or '#' + str(b['key'])}"
    ui.text(surf, pair_title, (x, y), 30, ui.TEXT, "black", maxw=w)
    y += 42

    # Badges: expected total, expected 7s, ENS, Lovers
    bx = x
    bx = ui.pill(surf, f"Exp {pe.expected_total:.1f}/49", bx, y, ui.mix(ui.GOOD, ui.CARD, .7), ui.GOOD, size=19).right + 8
    bx = ui.pill(surf, f"{pe.expected_sevens:.1f} × 7s", bx, y, ui.mix(ui.ACCENT, ui.CARD, .7), ui.ACCENT, size=19).right + 8
    bx = ui.pill(surf, f"ENS {pe.ens:.1f}", bx, y, ui.CARD_HI, ui.TEXT, size=19).right + 8
    if pe.is_lovers:
        ui.pill(surf, "LOVERS", bx, y, (235, 118, 175), ui.DARK, size=18)
    y += 46

    # Stat-by-stat expected offspring table (Parent A / Parent B / Expected)
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
    # Inheritance odds box
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

    # Parent rooms & relationship footer
    fy = ibox.bottom + 16
    if fy + 50 <= box.bottom - 10:
        ra = _room_label(a["room"])
        rb = _room_label(b["room"])
        same = a["room"] == b["room"]
        ui.text(surf, f"Current rooms: {ra}" + (" (already together)" if same else f"  +  {rb}"),
                (x, fy), 21, ui.GOOD if same else ui.DIM, maxw=w)
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
        specs = br.compute_room_specs(self.app.snap.parsed)
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
        # Compact header strip with pedigree & personality traits
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
            ui.text(surf, "No compatible breeding partners found in house.", (la.centerx, la.y + 100), 28, ui.FAINT, "bold", anchor="midtop")
        else:
            self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 580, area.y + 48, 580, area.h - 48)
        if self.list.current:
            draw_pair_simulator(ui, surf, box, self.list.current.data, self.stimulation)
        else:
            ui.rrect(surf, box, ui.CARD, 24)

    def hint_items(self):
        return [("A", "Pair in Best Room"), ("Y", "Switch Goal"), ("B", "Back")] + self.ui.pending_hint(self.app)


class BreedingTab:
    """Cat Organizer & Breeding Helper tab (Room Optimizer, Best Pairs, Partner Finder, Room Roster)."""

    name = "Breeding"
    MODES = [
        ("optimizer", "Room Optimizer"),
        ("pairs", "Best Pairs"),
        ("partners", "Partner Finder"),
        ("roster", "Room Organizer"),
    ]

    def __init__(self, ui, app) -> None:
        self.ui = ui
        self.app = app
        self.m = 0
        self.goal_idx = 0
        self.seed = 42
        self.query = ""
        self.specs: list[br.RoomSpec] = []
        self.result: br.OptimizationResult | None = None
        self.top_pairs: list[br.PairEval] = []
        self.best_stim: float = 0.0
        self._opt_key: tuple | None = None
        self._opt_snap: Any = None
        self.list = ui.ListView(row_h=100, gap=10)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []
        self.goal_rect: pygame.Rect | None = None
        self.search_rect: pygame.Rect | None = None

    def on_enter(self) -> None:
        p = self.app.snap.parsed
        existing_roles = {r.name: (r.role, r.max_cats) for r in self.specs}
        self.specs = br.compute_room_specs(p)
        for sp in self.specs:
            if sp.name in existing_roles:
                sp.role, sp.max_cats = existing_roles[sp.name]
        self.best_stim = max((r.stimulation for r in self.specs if r.role == "breeding"), default=0.0)
        self._run_optimizer()
        self._rebuild()

    def _run_optimizer(self) -> None:
        snap = self.app.snap
        key = (
            id(snap),
            self.goal_idx,
            self.seed,
            tuple((s.name, s.role, s.max_cats) for s in self.specs),
        )
        if key == self._opt_key and self._opt_snap is snap and self.result is not None:
            return
        self._opt_key = key
        self._opt_snap = snap
        p = snap.parsed
        goal_id = br.GOALS[self.goal_idx][0]
        self.result = br.optimize_house(p, self.specs, goal_id=goal_id, seed=self.seed)
        self.best_stim = max((r.stimulation for r in self.specs if r.role == "breeding"), default=0.0)
        # Also compute global top pairs across all house cats
        house = [c for c in p["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
        ped = p.get("pedigree") or {}
        weights = br.GOALS[self.goal_idx][2]
        pairs: list[br.PairEval] = []
        for i, a in enumerate(house):
            for b in house[i + 1 :]:
                if not br.can_breed(a, b):
                    continue
                if a.get("hater_id") == b["key"] or b.get("hater_id") == a["key"]:
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
        if mode == "optimizer" and self.result:
            moves_n = len(self.result.moves)
            rows.append(ui.Row("Apply Optimized Room Plan", data=("apply", self.result)))
            rows.append(ui.Row("Re-Optimize (New Seed)", data=("rerun", self.result)))
            for rp in self.result.plans:
                rows.append(ui.Row(_room_label(rp.spec.name), data=("room_plan", rp)))
        elif mode == "pairs":
            for pe in self.top_pairs:
                if self.query:
                    hay = f"{pe.cat_a['name']} {pe.cat_b['name']} {pe.cat_a['class']} {pe.cat_b['class']}".lower()
                    if not all(t in hay for t in self.query.lower().split()):
                        continue
                rows.append(ui.Row(f"{pe.cat_a['name']} × {pe.cat_b['name']}", data=("pair", pe)))
        elif mode == "partners":
            house = [c for c in self.app.snap.parsed["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
            weights = br.GOALS[self.goal_idx][2]
            house.sort(key=lambda c: br.cat_breed_score(c, weights), reverse=True)
            for c in house:
                if self._matches_cat(c, self.query):
                    rows.append(ui.Row(c["name"] or f"#{c['key']}", data=("partner_cat", c)))
        else:  # roster (manual Room Organizer)
            house = [c for c in self.app.snap.parsed["cats"] if c["room"] and c["room"] != "(ADVENTURE)" and not c["dead"]]
            house.sort(key=lambda c: (self.app.pending.room_moves.get(c["key"], c["room"]), -br.stat_sum(c), c["name"].lower()))
            for c in house:
                if self._matches_cat(c, self.query):
                    rows.append(ui.Row(c["name"] or f"#{c['key']}", data=("roster_cat", c)))
        self.list.set_rows(rows)

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
        if self.MODES[self.m][0] == "optimizer":
            return
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        self.app.push(ed.SearchModal(self.ui, self.app, "Filter Breeding List", self.query, self._count_for, done))

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
            self._run_optimizer()
            self._rebuild()
            self.app.toast(f"Breeding Goal: {br.GOALS[self.goal_idx][1]}", ui.ACCENT, 2.0)
        elif btn == ui.SELECT and mode != "optimizer":
            self._open_search()
        elif btn == ui.B and self.query:
            self.query = ""
            self.list.sel = 0
            self._rebuild()
        elif btn == ui.A and row:
            kind, obj = row.data
            if kind == "apply" and self.result:
                if not self.result.moves:
                    self.app.toast("Cats are already in their optimal rooms!", ui.GOOD, 2.2)
                else:
                    for ck, rm in self.result.moves.items():
                        self.app.pending.room_moves[ck] = rm
                    self.app.toast(f"Queued {len(self.result.moves)} room moves · press Menu to save", ui.GOOD, 3.5)
            elif kind == "rerun":
                self.seed += 1
                self._run_optimizer()
                self._rebuild()
                self.app.toast("Re-optimized room layout", ui.INFO, 1.8)
            elif kind == "room_plan":
                sp: br.RoomSpec = obj.spec
                i = br.ROOM_ROLES.index(sp.role)
                sp.role = br.ROOM_ROLES[(i + 1) % len(br.ROOM_ROLES)]
                self._run_optimizer()
                self._rebuild()
                self.app.toast(f"{_room_label(sp.name)} » {br.ROOM_ROLE_LABELS[sp.role]}", ui.ACCENT, 1.8)
            elif kind == "pair":
                pe: br.PairEval = obj
                dst = self._best_breeding_room()
                for c in (pe.cat_a, pe.cat_b):
                    if c["room"] != dst:
                        self.app.pending.room_moves[c["key"]] = dst
                self.app.toast(f"Queued {pe.cat_a['name']} + {pe.cat_b['name']} » {_room_label(dst)} (Menu to save)", ui.GOOD, 3.0)
            elif kind == "partner_cat":
                self.app.push(PartnerDetailScreen(ui, self.app, obj, self.best_stim, self.goal_idx))
            elif kind == "roster_cat":
                rooms = self.app.snap.parsed.get("rooms") or ["Attic"]
                cur_rm = self.app.pending.room_moves.get(obj["key"], obj["room"])
                ni = (rooms.index(cur_rm) + 1) % len(rooms) if cur_rm in rooms else 0
                new_rm = rooms[ni]
                if new_rm == obj["room"]:
                    self.app.pending.room_moves.pop(obj["key"], None)
                else:
                    self.app.pending.room_moves[obj["key"]] = new_rm
                self._rebuild()
                self.app.toast(f"{obj['name']} » {_room_label(new_rm)}", ui.INFO, 1.6)
        elif btn == ui.X and row:
            kind, obj = row.data
            if kind == "room_plan":
                sp: br.RoomSpec = obj.spec
                sp.max_cats = 2 if sp.max_cats >= 8 else sp.max_cats + 1
                self._run_optimizer()
                self._rebuild()
                self.app.toast(f"{_room_label(sp.name)} max cats: {sp.max_cats}", ui.INFO, 1.6)
            elif kind == "roster_cat":
                self.app.push(ui.CatEdit(self.app, [obj], 0))
            elif kind == "partner_cat":
                self.app.push(ui.CatEdit(self.app, [obj], 0))
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
        i = self.list.hit(pos)
        if i is not None:
            kind = self.list.rows[i].data[0]
            if i == self.list.sel or kind in ("apply", "rerun", "roster_cat"):
                self.list.sel = i
                self.handle(self.ui.A)
            else:
                self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        kind, obj = row.data
        if kind == "apply":
            res: br.OptimizationResult = obj
            mv = len(res.moves)
            ui.rrect(surf, (r.x + 4, r.y + 8, 8, r.h - 16), ui.GOOD if mv else ui.LINE, 4, ink=False)
            ui.text(surf, "Apply Optimized Room Plan", (r.x + 28, r.y + 16), 31, ui.GOOD if mv else ui.TEXT, "black")
            diff = res.total_ens - res.before_ens
            sign = "+" if diff >= 0 else ""
            sub = f"{mv} cat move{'s' if mv != 1 else ''} · Breeding ENS {res.before_ens:.1f} » {res.total_ens:.1f} ({sign}{diff:.1f})"
            ui.text(surf, sub, (r.x + 28, r.bottom - 16), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 220)
            ui.pill(surf, f"APPLY ({mv})" if mv else "OPTIMAL", r.right - 26, r.centery,
                    ui.GOOD if mv else ui.CARD_HI, ui.DARK if mv else ui.DIM, size=20, anchor="midright")
        elif kind == "rerun":
            ui.text(surf, "Re-Optimize (Try Fresh Layout)", (r.x + 28, r.y + 16), 29, ui.TEXT, "bold")
            ui.text(surf, f"Simulated Annealing · Goal: {br.GOALS[self.goal_idx][1]} · Seed #{self.seed}",
                    (r.x + 28, r.bottom - 16), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 180)
            ui.pill(surf, "PRESS A", r.right - 26, r.centery, ui.CARD_HI, ui.ACCENT, size=18, anchor="midright")
        elif kind == "room_plan":
            rp: br.RoomPlan = obj
            sp = rp.spec
            role_col = {"breeding": ui.GOOD, "nursery": ui.INFO, "sickbay": ui.ACCENT,
                        "quarantine": ui.BAD, "locked": ui.DIM}.get(sp.role, ui.DIM)
            ui.rrect(surf, (r.x + 4, r.y + 8, 8, r.h - 16), role_col, 4, ink=False)
            nr = ui.text(surf, _room_label(sp.name), (r.x + 28, r.y + 14), 31, ui.TEXT, "black", maxw=320)
            ui.pill(surf, br.ROOM_ROLE_LABELS[sp.role], nr.right + 12, nr.centery,
                    ui.mix(role_col, ui.CARD, .72), role_col, size=17, anchor="midleft")
            meta = f"Stim {sp.stimulation:+.0f} · Comf {sp.comfort:+.0f} · Hlth {sp.health:+.0f} · {len(rp.pairs)} pair{'s' if len(rp.pairs) != 1 else ''}"
            if rp.conflicts:
                meta += f" · {len(rp.conflicts)} FIGHT!"
            ui.text(surf, meta, (r.x + 28, r.bottom - 14), 21, ui.BAD if rp.conflicts else ui.DIM, anchor="bottomleft", maxw=r.w - 240)
            ui.pill(surf, f"{len(rp.cats)}/{sp.max_cats} cats", r.right - 26, r.y + 28, ui.CARD_HI, ui.TEXT, size=19, anchor="midright")
            if rp.pairs:
                ui.text(surf, f"Top {rp.pairs[0].ens:.1f}", (r.right - 26, r.bottom - 16), 21, ui.ACCENT, "black", anchor="bottomright")
        elif kind == "pair":
            pe: br.PairEval = obj
            a, b = pe.cat_a, pe.cat_b
            nr = ui.text(surf, f"{a['name']}  ×  {b['name']}", (r.x + 26, r.y + 14), 30, ui.TEXT, "black", maxw=r.w - 340)
            if pe.is_lovers:
                ui.pill(surf, "LOVERS", nr.right + 10, nr.centery, (235, 118, 175), ui.DARK, size=16, anchor="midleft")
            same = a["room"] == b["room"]
            loc = f"{_room_label(a['room'])}" + (" (together)" if same else f" + {_room_label(b['room'])}")
            ui.text(surf, f"Exp {pe.expected_total:.1f} ({pe.expected_sevens:.1f}×7)  ·  {loc}",
                    (r.x + 26, r.bottom - 14), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 300)
            cl, sev = br.coi_label(pe.coi)
            ccol = _coi_color(ui, sev)
            ui.pill(surf, cl, r.right - 148, r.centery, ui.mix(ccol, ui.CARD, .7), ccol, size=18, anchor="midright")
            ui.text(surf, f"ENS {pe.ens:.1f}", (r.right - 24, r.centery), 24, ui.ACCENT, "black", anchor="midright")
        elif kind in ("partner_cat", "roster_cat"):
            c = obj
            scol = ui.SEX_COL.get(c["sex"], ui.DIM)
            pygame.draw.circle(surf, ui.mix(scol, ui.CARD, .25), (r.x + 42, r.centery), 22)
            ui.text(surf, {"Male": "M", "Female": "F"}.get(c["sex"], "?"), (r.x + 42, r.centery), 22, ui.DARK, "black", anchor="center")
            nr = ui.text(surf, c["name"] or f"#{c['key']}", (r.x + 80, r.y + 14), 30, ui.TEXT, "black", maxw=320)
            cur_rm = self.app.pending.room_moves.get(c["key"], c["room"])
            moved = cur_rm != c["room"]
            ui.pill(surf, _room_label(cur_rm) + (" *" if moved else ""), nr.right + 12, nr.centery,
                    ui.ACCENT if moved else ui.CARD_HI, ui.DARK if moved else ui.DIM, size=17, anchor="midleft")
            score = br.cat_breed_score(c, br.GOALS[self.goal_idx][2])
            parts = [c["class"], f"{c['age']}d", f"Sum {br.stat_sum(c)}", f"{br.perfect_sevens(c)}×7"]
            if c.get("lover_name"):
                parts.append(f"Loves {c['lover_name']}")
            if c.get("hater_name"):
                parts.append(f"Hates {c['hater_name']}")
            ui.text(surf, " · ".join(parts), (r.x + 80, r.bottom - 14), 21, ui.DIM, anchor="bottomleft", maxw=r.w - 260)
            ui.text(surf, f"Score {score:.1f}", (r.right - 26, r.centery), 23, ui.GOOD if score >= 36 else ui.TEXT, "black", anchor="midright")

    def _draw_room_detail(self, surf, box: pygame.Rect, rp: br.RoomPlan) -> None:
        ui = self.ui
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 28, box.y + 24, box.w - 56
        sp = rp.spec
        ui.text(surf, f"{_room_label(sp.name).upper()}  ·  {br.ROOM_ROLE_LABELS[sp.role].upper()}", (x, y), 18, ui.ACCENT, "black")
        y += 30
        ui.text(surf, f"Stim {sp.stimulation:+.0f}  ·  Comf {sp.comfort:+.0f}  ·  Hlth {sp.health:+.0f}  ·  Evol {sp.evolution:+.0f}",
                (x, y), 21, ui.DIM, maxw=w)
        y += 38

        # Assigned cats
        ui.text(surf, f"ASSIGNED CATS ({len(rp.cats)}/{sp.max_cats}" + (f" + {len(rp.ey_cats)} EY" if rp.ey_cats else "") + ")",
                (x, y), 18, ui.FAINT, "black")
        y += 26
        for c in (rp.cats + rp.ey_cats)[:7]:
            moved = c["room"] != sp.name
            sx = {"Male": "M", "Female": "F"}.get(c["sex"], "?")
            tag = f"  « from {_room_label(c['room'])}" if moved else ""
            if br.has_eternal_youth(c):
                tag += "  [EY +1 Stim]"
            ui.text(surf, f"[{sx}] {c['name']}  (Sum {br.stat_sum(c)}, {br.perfect_sevens(c)}×7){tag}",
                    (x, y), 22, ui.ACCENT if moved else ui.TEXT, "bold" if moved else "semi", maxw=w)
            y += 28

        if rp.conflicts:
            y += 8
            for a, b in rp.conflicts[:2]:
                ui.text(surf, f"! Hater conflict: {a['name']} hates {b['name']}", (x, y), 21, ui.BAD, "bold", maxw=w)
                y += 28

        y += 14
        ui.text(surf, f"BREEDING PAIRS ({len(rp.pairs)})", (x, y), 18, ui.FAINT, "black")
        y += 28
        if not rp.pairs:
            ui.text(surf, "No breeding pairs in this room.", (x, y), 22, ui.FAINT)
        else:
            for pe in rp.pairs:
                if y + 54 > box.bottom - 52:
                    break
                cl, sev = br.coi_label(pe.coi)
                ccol = _coi_color(ui, sev)
                ui.text(surf, f"{pe.cat_a['name']} × {pe.cat_b['name']}", (x, y), 22, ui.TEXT, "bold", maxw=w - 150)
                ui.pill(surf, cl, box.right - 28, y + 12, ui.mix(ccol, ui.CARD, .7), ccol, size=17, anchor="midright")
                ui.text(surf, f"Exp {pe.expected_total:.1f}/49 · {pe.expected_sevens:.1f}×7 · ENS {pe.ens:.1f}",
                        (x, y + 26), 19, ui.DIM, maxw=w)
                y += 54

        ui.text(surf, "A: change room role  ·  X: adjust max cats", (x, box.bottom - 32), 20, ui.FAINT)

    def _draw_cat_summary(self, surf, box: pygame.Rect, c: dict[str, Any]) -> None:
        ui = self.ui
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 28, box.y + 24, box.w - 56
        ui.text(surf, "CAT BREEDING PROFILE", (x, y), 18, ui.FAINT, "black")
        y += 30
        ui.text(surf, f"{c['name'] or '#' + str(c['key'])}  ({c['sex']}, {c['age']}d)", (x, y), 32, ui.TEXT, "black", maxw=w)
        y += 44
        cur_rm = self.app.pending.room_moves.get(c["key"], c["room"])
        ui.text(surf, f"{c['class']} · Room: {_room_label(cur_rm)} · Base Sum {br.stat_sum(c)}/49 ({br.perfect_sevens(c)}×7)",
                (x, y), 22, ui.ACCENT, "bold", maxw=w)
        y += 38

        # Base stat bars, each led by the official in-game stat sprite
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

    def draw(self, surf, area: pygame.Rect) -> None:
        ui = self.ui
        # Row 1: Mode pills + Goal pill + Search pill
        self.chip_rects = []
        x = area.x
        for i, (_, lbl) in enumerate(self.MODES):
            sel = i == self.m
            r = ui.pill(surf, lbl, x, area.y, ui.ACCENT if sel else ui.CARD, ui.DARK if sel else ui.DIM, 23)
            self.chip_rects.append((r, i))
            x = r.right + 10
        ui.glyph(surf, "DPAD_LR", x + 8, self.chip_rects[0][0].centery)

        self.goal_rect = ui.pill(surf, f"Goal: {br.GOALS[self.goal_idx][1]} (Y)", area.right - 20,
                                 self.chip_rects[0][0].centery, ui.CARD_HI, ui.ACCENT, size=20, anchor="midright")
        if self.MODES[self.m][0] != "optimizer":
            self.search_rect = ed.draw_search_pill(ui, surf, self.goal_rect.left - 14,
                                                   self.chip_rects[0][0].centery, self.query, anchor="midright")
        else:
            self.search_rect = None

        list_w = area.w - 620
        la = pygame.Rect(area.x, area.y + 62, list_w, area.h - 62)
        if not self.list.rows:
            ui.text(surf, "No house cats available.", (la.centerx, la.y + 100), 30, ui.FAINT, "bold", anchor="midtop")
        else:
            self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 580, area.y + 62, 580, area.h - 62)
        row = self.list.current
        if not row:
            ui.rrect(surf, box, ui.CARD, 24)
            return
        kind, obj = row.data
        if kind in ("apply", "rerun") and self.result:
            self._draw_plan_summary(surf, box, self.result)
        elif kind == "room_plan":
            self._draw_room_detail(surf, box, obj)
        elif kind == "pair":
            draw_pair_simulator(ui, surf, box, obj, self.best_stim)
        elif kind in ("partner_cat", "roster_cat"):
            self._draw_cat_summary(surf, box, obj)

    def _draw_plan_summary(self, surf, box: pygame.Rect, res: br.OptimizationResult) -> None:
        ui = self.ui
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 28, box.y + 24, box.w - 56
        ui.text(surf, "OPTIMIZATION PLAN SUMMARY", (x, y), 18, ui.ACCENT, "black")
        y += 32
        diff = res.total_ens - res.before_ens
        sign = "+" if diff >= 0 else ""
        ui.text(surf, f"Breeding ENS: {res.before_ens:.1f} » {res.total_ens:.1f}", (x, y), 30, ui.GOOD if diff >= 0 else ui.TEXT, "black", maxw=w)
        y += 40
        ui.pill(surf, f"{sign}{diff:.1f} ENS gain", x, y, ui.mix(ui.GOOD, ui.CARD, .72), ui.GOOD, size=19)
        ui.pill(surf, f"{len(res.moves)} move{'s' if len(res.moves) != 1 else ''}", x + 178, y, ui.CARD_HI, ui.ACCENT, size=19)
        y += 48
        ui.text(surf, "PLANNED ROOM MOVES", (x, y), 18, ui.FAINT, "black")
        y += 28
        if not res.moves:
            ui.text(surf, "All cats are already in their best rooms for this goal.", (x, y), 22, ui.GOOD, maxw=w)
        else:
            by_key = {c["key"]: c for c in self.app.snap.parsed["cats"]}
            for ck, dst in list(res.moves.items()):
                if y + 32 > box.bottom - 52:
                    rem = len(res.moves) - max(0, (y - (box.y + 172)) // 30)
                    if rem > 0:
                        ui.text(surf, f"…and {rem} more moves", (x, y), 20, ui.FAINT)
                    break
                c = by_key.get(ck)
                nm = (c["name"] or f"#{ck}") if c else f"#{ck}"
                src = _room_label(c["room"]) if c else "?"
                ui.text(surf, nm, (x, y), 21, ui.TEXT, "bold", maxw=190)
                ui.text(surf, f"{src} » {_room_label(dst)}", (box.right - 28, y), 20, ui.ACCENT, "bold", anchor="topright", maxw=w - 200)
                y += 30
        ui.text(surf, "Press A to queue moves · then Save (Start) to write.", (x, box.bottom - 34), 20, ui.FAINT, maxw=w)

    def hint_items(self):
        mode = self.MODES[self.m][0]
        if mode == "optimizer":
            return [("A", "Apply / Role"), ("X", "Room Cap"), ("Y", "Goal"), ("DPAD_LR", "View")]
        if mode == "pairs":
            return [("A", "Pair in Best Room"), ("Y", "Goal"), ("SELECT", "Search"), ("DPAD_LR", "View")]
        if mode == "partners":
            return [("A", "Find Partners"), ("X", "Edit Cat"), ("Y", "Goal"), ("SELECT", "Search")]
        return [("A", "Cycle Room"), ("X", "Edit Cat"), ("SELECT", "Search"), ("DPAD_LR", "View")]

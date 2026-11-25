"""Gamepad-first 1080p editor screens & detail cards for MewDeck:
- Rich item detail & armor set bonus renderer
- Reusable CatalogPicker (filter chips + unobfuscated list + live detail card)
- AbilitiesScreen (Active 1-6, Passive 1-2 with Tier 1/2 toggle, Disorder 1-2)
- MutationsScreen (Coat Texture + 14 Body Parts with L<->R mirror & defect badges)
- EquipScreen (5 Cat Equipment slots with slot-scoped picker & Set Bonuses)
- FurnitureTab (Storage + Room views with live room stat totals & 633-item catalog)
"""

from __future__ import annotations

from typing import Any, Callable

import pygame

from . import catblob as cb
from . import gamedata as gd
from . import savefile as sf


def wrap_lines(font: pygame.font.Font, s: str, maxw: int, max_lines: int = 12) -> list[str]:
    """Word-wrap text preserving explicit newlines."""
    if not s:
        return []
    out: list[str] = []
    for para in s.split("\n"):
        words = para.split()
        if not words:
            if out and len(out) < max_lines:
                out.append("")
            continue
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip()
            if font.size(cand)[0] > maxw and cur:
                out.append(cur)
                cur = w
                if len(out) >= max_lines:
                    return out
            else:
                cur = cand
        if cur and len(out) < max_lines:
            out.append(cur)
    return out[:max_lines]


def draw_item_detail(ui, surf: pygame.Surface, box: pygame.Rect, item: dict[str, Any] | None) -> int:
    """Draw unobfuscated item details (name, badges, stats, description, armor set bonus).
    Returns bottom y used inside box."""
    ui.rrect(surf, box, ui.CARD, 24)
    x = box.x + 28
    w = box.w - 56
    y = box.y + 24
    if not item or not item.get("id"):
        ui.text(surf, "ITEM DETAILS", (x, y), 19, ui.FAINT, "black")
        ui.text(surf, "Select an item to inspect its stats, description, and set bonus.", (x, y + 36), 24, ui.DIM, maxw=w)
        return y + 80

    rc = ui.RARITY_COL.get(item.get("rarity", ""), ui.DIM)
    ui.text(surf, item.get("name") or item["id"], (x, y), 32, ui.TEXT, "black", maxw=w)
    y += 44

    # Badges row: rarity, kind, durability, cursed, consumable
    bx = x
    badges: list[tuple[str, tuple, tuple]] = []
    if item.get("rarity"):
        badges.append((item["rarity"].replace("_", " "), rc, ui.DARK))
    if item.get("kind"):
        badges.append((item["kind"].replace("_", " "), ui.CARD_HI, ui.TEXT))
    if item.get("cursed"):
        badges.append(("CURSED", ui.BAD, ui.DARK))
    if item.get("consumable"):
        badges.append(("CONSUMABLE", ui.INFO, ui.DARK))
    if item.get("durability"):
        badges.append((f"Uses {item['durability']}", ui.CARD_HI, ui.DIM))
    for lab, bg, fg in badges:
        r = ui.pill(surf, lab, bx, y, bg, fg, size=18)
        if r.right > box.right - 24:
            bx = x
            y += 34
            r = ui.pill(surf, lab, bx, y, bg, fg, size=18)
        bx = r.right + 8
    y += 42

    # Stat bonuses
    stats = item.get("stats") or {}
    if stats:
        sx = x
        for sk, sv in stats.items():
            col = ui.GOOD if sv > 0 else ui.BAD
            txt = f"{'+' if sv > 0 else ''}{sv} {sk}"
            r = ui.pill(surf, txt, sx, y, ui.mix(col, ui.CARD, .72), col, size=20)
            if r.right > box.right - 24:
                sx = x
                y += 36
                r = ui.pill(surf, txt, sx, y, ui.mix(col, ui.CARD, .72), col, size=20)
            sx = r.right + 8
        y += 42

    # Description
    desc = item.get("desc") or ""
    if desc:
        lines = wrap_lines(ui.F(23), desc, w, max_lines=6)
        for ln in lines:
            if y + 28 > box.bottom - 20:
                break
            if ln:
                ui.text(surf, ln, (x, y), 23, ui.TEXT if len(lines) <= 4 else ui.DIM)
            y += 28
        y += 10
    elif not stats:
        ui.text(surf, "No special effect text.", (x, y), 22, ui.FAINT)
        y += 32

    # Armor Set Bonus(es)
    sets = gd.item_set_info(item)
    for sb in sets:
        sb_h_avail = box.bottom - y - 16
        if sb_h_avail < 76:
            break
        sb_lines: list[str] = []
        st_str = gd.format_stats(sb.get("stats"))
        if st_str:
            sb_lines.append(st_str)
        if sb.get("desc"):
            sb_lines.extend(wrap_lines(ui.F(20), sb["desc"], w - 32, max_lines=3))
        card_h = min(sb_h_avail, 46 + len(sb_lines) * 25 + 12)
        sbox = pygame.Rect(x, y, w, card_h)
        ui.rrect(surf, sbox, ui.CARD_HI, 16)
        ui.rrect(surf, (sbox.x, sbox.y, 7, sbox.h), ui.ACCENT, 4)
        ui.text(surf, f"{sb['name']} ({sb.get('pieces', 3)} pcs)", (sbox.x + 18, sbox.y + 10), 21, ui.ACCENT, "black",
                maxw=sbox.w - 30)
        sy = sbox.y + 40
        for ln in sb_lines:
            if sy + 24 > sbox.bottom - 6:
                break
            ui.text(surf, ln, (sbox.x + 18, sy), 20, ui.GOOD if ln == st_str else ui.DIM, maxw=sbox.w - 30)
            sy += 25
        y = sbox.bottom + 10

    return y


# --------------------------------------------------------------------------- built-in Gamepad + Touch Search Modal

class SearchModal:
    """Built-in 1080p gamepad + touch + physical keyboard modal (powers both Search and Cat Rename)."""

    ROWS = [
        list("1234567890"),
        list("QWERTYUIOP"),
        list("ASDFGHJKL-"),
        list("ZXCVBNM'+_"),
        ["SPACE", "BACK", "CLEAR", "DONE"],
    ]

    def __init__(self, ui, app, title: str, initial: str, count_fn: Callable[[str], int] | None,
                 on_done: Callable[[str], None], done_label: str = "Search", title_case: bool = False) -> None:
        self.ui = ui
        self.app = app
        self.title = title
        self.query = initial
        self.count_fn = count_fn
        self.on_done = on_done
        self.done_label = done_label
        self.title_case = title_case
        self.r = 1
        self.c = 0
        self.key_rects: list[tuple[pygame.Rect, int, int, str]] = []

    def on_enter(self) -> None:
        pass

    def handle_raw_key(self, ev: pygame.event.Event) -> bool:
        """Accept real physical keyboard typing while SearchModal is open."""
        if ev.type != pygame.KEYDOWN:
            return False
        if ev.key == pygame.K_ESCAPE:
            self.app.pop()
            return True
        if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._finish()
            return True
        if ev.key == pygame.K_BACKSPACE:
            self.query = self.query[:-1]
            return True
        if ev.key in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
            return False  # let arrow keys navigate the on-screen grid too
        ch = ev.unicode
        if ch and ch.isprintable() and len(self.query) < 32:
            self.query += ch
            return True
        return True  # consume other keys so Q/E/X/Y/S don't fire gamepad shortcuts

    def _finish(self) -> None:
        q = self.query.strip()
        self.app.pop()
        self.on_done(q)

    def _activate_key(self, k: str) -> None:
        if k == "SPACE":
            if len(self.query) < 32 and self.query and not self.query.endswith(" "):
                self.query += " "
        elif k == "BACK":
            self.query = self.query[:-1]
        elif k == "CLEAR":
            self.query = ""
        elif k == "DONE":
            self._finish()
        else:
            if len(self.query) < 32:
                cap = self.title_case and (not self.query or self.query.endswith(" "))
                self.query += k.upper() if cap else k.lower()

    def handle(self, btn: str) -> None:
        ui = self.ui
        if btn == ui.UP:
            self.r = (self.r - 1) % len(self.ROWS)
            self.c = min(self.c, len(self.ROWS[self.r]) - 1)
        elif btn == ui.DOWN:
            self.r = (self.r + 1) % len(self.ROWS)
            self.c = min(self.c, len(self.ROWS[self.r]) - 1)
        elif btn == ui.LEFT:
            self.c = (self.c - 1) % len(self.ROWS[self.r])
        elif btn == ui.RIGHT:
            self.c = (self.c + 1) % len(self.ROWS[self.r])
        elif btn == ui.A:
            self._activate_key(self.ROWS[self.r][self.c])
        elif btn == ui.X:
            self.query = self.query[:-1]
        elif btn == ui.Y:
            self._activate_key("SPACE")
        elif btn == ui.START:
            self._finish()
        elif btn == ui.SELECT:
            self.query = ""
        elif btn == ui.B:
            self.app.pop()

    def click(self, pos) -> None:
        for rect, rr, cc, k in self.key_rects:
            if rect.collidepoint(pos):
                self.r, self.c = rr, cc
                self._activate_key(k)
                return

    def draw(self, surf) -> None:
        ui = self.ui
        self.app.stack[-2].draw(surf)
        shade = pygame.Surface((ui.W, ui.H), pygame.SRCALPHA)
        shade.fill((8, 5, 12, 210))
        surf.blit(shade, (0, 0))

        box = pygame.Rect(0, 0, 1180, 680)
        box.center = (ui.W // 2, ui.H // 2)
        ui.rrect(surf, box, ui.CARD, 28)
        ui.rrect(surf, (box.x, box.y, box.w, 8), ui.ACCENT, 4)

        ui.text(surf, self.title, (box.x + 44, box.y + 30), 34, ui.TEXT, "black")
        if self.count_fn is not None:
            matches = self.count_fn(self.query.strip())
            mcol = ui.GOOD if matches else ui.BAD
            ui.pill(surf, f"{matches} match{'es' if matches != 1 else ''}", box.right - 44, box.y + 46,
                    ui.mix(mcol, ui.CARD, .72), mcol, size=22, anchor="midright")
        else:
            ui.pill(surf, f"{len(self.query)}/32 chars", box.right - 44, box.y + 46,
                    ui.CARD_HI, ui.DIM, size=20, anchor="midright")

        # Input field
        inp = pygame.Rect(box.x + 44, box.y + 94, box.w - 88, 76)
        ui.rrect(surf, inp, ui.DARK, 18)
        pygame.draw.rect(surf, ui.ACCENT, inp, width=3, border_radius=18)
        shown = (self.query + "_") if self.query else "Type with gamepad, touch, or keyboard…"
        ui.text(surf, shown, (inp.x + 26, inp.centery), 34, ui.TEXT if self.query else ui.FAINT,
                "bold" if self.query else "semi", anchor="midleft", maxw=inp.w - 52)

        # Keyboard grid
        self.key_rects = []
        grid_x = box.x + 44
        grid_w = box.w - 88
        y = inp.bottom + 26
        gap = 10
        row_h = 80
        for rr, keys in enumerate(self.ROWS):
            if rr < 4:
                kw = (grid_w - gap * (len(keys) - 1)) // len(keys)
                x = grid_x
                for cc, k in enumerate(keys):
                    kr = pygame.Rect(x, y, kw, row_h)
                    self.key_rects.append((kr, rr, cc, k))
                    sel = rr == self.r and cc == self.c
                    ui.rrect(surf, kr, ui.ACCENT if sel else ui.CARD_HI, 14)
                    ui.text(surf, k, kr.center, 32, ui.DARK if sel else ui.TEXT, "black", anchor="center")
                    x += kw + gap
            else:
                weights = [3, 2, 2, 3]
                tot = sum(weights)
                avail = grid_w - gap * (len(keys) - 1)
                x = grid_x
                labels = {"SPACE": "Space", "BACK": "Backspace", "CLEAR": "Clear", "DONE": self.done_label}
                for cc, (k, wt) in enumerate(zip(keys, weights)):
                    kw = avail * wt // tot
                    kr = pygame.Rect(x, y, kw, row_h)
                    self.key_rects.append((kr, rr, cc, k))
                    sel = rr == self.r and cc == self.c
                    if k == "DONE":
                        bg = ui.ACCENT if sel else ui.GOOD
                        fg = ui.DARK
                    elif k == "CLEAR":
                        bg = ui.ACCENT if sel else ui.mix(ui.BAD, ui.CARD_HI, .55)
                        fg = ui.DARK if sel else ui.TEXT
                    else:
                        bg = ui.ACCENT if sel else ui.CARD_HI
                        fg = ui.DARK if sel else ui.TEXT
                    ui.rrect(surf, kr, bg, 16)
                    ui.text(surf, labels[k], kr.center, 28, fg, "black", anchor="center")
                    x += kw + gap
            y += row_h + gap

    def hint_items(self):
        return [("A", "Type"), ("X", "Backspace"), ("Y", "Space"), ("START", "Done"), ("B", "Cancel")]


def draw_search_pill(ui, surf: pygame.Surface, x: int, cy: int, query: str, anchor: str = "midright") -> pygame.Rect:
    """Draw a compact search status pill (clickable on touch/mouse, triggered with SELECT / '/')."""
    lbl = f'Search: "{query}"' if query else "Search (View / /)"
    bg = ui.ACCENT if query else ui.CARD_HI
    fg = ui.DARK if query else ui.DIM
    return ui.pill(surf, lbl, x, cy, bg, fg, size=20, anchor=anchor)


# --------------------------------------------------------------------------- reusable CatalogPicker

class CatalogPicker:
    """Full-screen gamepad picker with category chips, search, unobfuscated list, and rich detail card."""

    def __init__(
        self,
        ui,
        app,
        title: str,
        entries: list[dict[str, Any]],
        categories: list[str],
        cat_fn: Callable[[dict[str, Any]], str],
        on_pick: Callable[[dict[str, Any] | None], None],
        detail_fn: Callable[[pygame.Surface, pygame.Rect, dict[str, Any] | None], None],
        row_meta_fn: Callable[[dict[str, Any]], tuple[str, tuple]] | None = None,
        current_id: Any = None,
        allow_clear: bool = True,
        clear_label: str = "Clear (None)",
    ) -> None:
        self.ui = ui
        self.app = app
        self.title = title
        self.entries = entries
        self.categories = categories
        self.cat_fn = cat_fn
        self.on_pick = on_pick
        self.detail_fn = detail_fn
        self.row_meta_fn = row_meta_fn
        self.current_id = current_id
        self.allow_clear = allow_clear
        self.clear_label = clear_label
        self.cat_idx = 0
        self.query = ""
        self.search_rect: pygame.Rect | None = None
        self.list = ui.ListView(row_h=82, gap=8)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []
        self._rebuild(select_current=True)

    def on_enter(self) -> None:
        pass

    @staticmethod
    def _matches(e: dict[str, Any], q: str) -> bool:
        if not q:
            return True
        hay = " ".join(str(x) for x in (
            e.get("name", ""), e.get("id", ""), e.get("class", ""), e.get("desc", ""),
            e.get("desc2", ""), e.get("set", ""), gd.format_stats(e.get("stats")),
        )).lower()
        return all(tok in hay for tok in q.lower().split())

    def _filtered(self, q: str) -> list[dict[str, Any]]:
        cat = self.categories[self.cat_idx]
        return [e for e in self.entries if (cat == "All" or self.cat_fn(e) == cat) and self._matches(e, q)]

    def _rebuild(self, select_current: bool = False) -> None:
        cat = self.categories[self.cat_idx]
        rows = []
        if self.allow_clear and cat == "All" and not self.query:
            rows.append(self.ui.Row(self.clear_label, value="clear", color=self.ui.DIM, data=None))
        for e in self._filtered(self.query):
            rows.append(self.ui.Row(e["name"], data=e))
        self.list.set_rows(rows)
        if select_current and self.current_id is not None:
            for idx, r in enumerate(rows):
                if r.data and (r.data.get("id") == self.current_id or r.data.get("raw_id") == self.current_id):
                    self.list.sel = idx
                    break

    def _open_search(self) -> None:
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        self.app.push(SearchModal(self.ui, self.app, f"Search {self.title}", self.query,
                                  lambda q: len(self._filtered(q)), done))

    def handle(self, btn: str) -> None:
        ui = self.ui
        if btn in (ui.UP, ui.DOWN):
            step = 1 if self.app.input.count.get(btn, 0) < 12 else 5
            self.list.move((-1 if btn == ui.UP else 1) * step)
        elif btn in (ui.LT, ui.RT):
            self.list.move(-10 if btn == ui.LT else 10)
        elif btn in (ui.LEFT, ui.RIGHT, ui.LB, ui.RB):
            d = 1 if btn in (ui.RIGHT, ui.RB) else -1
            self.cat_idx = (self.cat_idx + d) % len(self.categories)
            self.list.sel = 0
            self._rebuild()
        elif btn == ui.SELECT:
            self._open_search()
        elif btn == ui.A and self.list.current is not None:
            picked = self.list.current.data
            self.app.pop()
            self.on_pick(picked)
        elif btn == ui.X and self.allow_clear:
            self.app.pop()
            self.on_pick(None)
        elif btn == ui.B:
            if self.query:
                self.query = ""
                self.list.sel = 0
                self._rebuild()
            else:
                self.app.pop()

    def click(self, pos) -> None:
        if self.search_rect and self.search_rect.collidepoint(pos):
            self._open_search()
            return
        for r, i in self.chip_rects:
            if r.collidepoint(pos):
                self.cat_idx = i
                self.list.sel = 0
                self._rebuild()
                return
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(self.ui.A)
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        e = row.data
        if e is None:
            ui.text(surf, row.label, (r.x + 28, r.centery), 30, ui.BAD if selected else ui.DIM, "bold", anchor="midleft")
            return
        is_cur = (e.get("id") == self.current_id or e.get("raw_id") == self.current_id)
        dot_col = ui.ACCENT if is_cur else (ui.BAD if e.get("tag") == "birth_defect" else ui.GOOD if e.get("stats") else ui.LINE)
        pygame.draw.circle(surf, dot_col, (r.x + 34, r.centery), 10)
        ui.text(surf, e["name"], (r.x + 62, r.centery), 32, ui.ACCENT if is_cur else ui.TEXT, "bold",
                anchor="midleft", maxw=r.w - 310)
        if self.row_meta_fn:
            meta, mcol = self.row_meta_fn(e)
            if meta:
                ui.text(surf, meta, (r.right - 26, r.centery), 23, mcol, "bold", anchor="midright", maxw=260)

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 18, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 18)
        # Search pill on top-right of the left pane
        self.search_rect = draw_search_pill(ui, surf, area.right - 620, area.y + 18, self.query, anchor="midright")
        chips_right = self.search_rect.left - 14
        # Category chips
        self.chip_rects = []
        widths = [ui.F(23, "bold").size(c)[0] + 34 for c in self.categories]
        start = 0
        while sum(widths[start:self.cat_idx + 1]) > (chips_right - area.x - 26) and start < self.cat_idx:
            start += 1
        x = area.x
        if start:
            ui.tri(surf, ui.DIM, (x + 8, area.y + 18), 9, "left")
            x += 26
        for i in range(start, len(self.categories)):
            sel = i == self.cat_idx
            w_est = ui.F(23, "bold").size(self.categories[i])[0] + 23
            if x + w_est > chips_right:
                break
            r = ui.pill(surf, self.categories[i], x, area.y, ui.ACCENT if sel else ui.CARD,
                        ui.DARK if sel else ui.DIM, 23)
            self.chip_rects.append((r, i))
            x = r.right + 10

        list_w = area.w - 620
        la = pygame.Rect(area.x, area.y + 58, list_w, area.h - 58)
        if not self.list.rows:
            ui.text(surf, f'No matches for "{self.query}"', (la.centerx, la.y + 100), 30, ui.FAINT, "bold", anchor="midtop")
        else:
            self.list.draw(surf, la, self._render_row)

        detail_box = pygame.Rect(area.right - 580, area.y, 580, area.h)
        cur = self.list.current.data if self.list.current else None
        self.detail_fn(surf, detail_box, cur)

    def hint_items(self):
        out = [("A", "Choose")]
        if self.allow_clear:
            out.append(("X", "Clear"))
        out += [("SELECT", "Search"), ("DPAD_LR", "Filter"), ("B", "Clear search" if self.query else "Cancel")]
        return out


# --------------------------------------------------------------------------- Abilities / Passives / Disorders Editor

class AbilitiesScreen:
    """Unobfuscated Active, Passive (+ Tier 1/2), and Disorder editor for a single cat."""

    def __init__(self, ui, cat_edit, mode: str) -> None:
        self.ui = ui
        self.cat_edit = cat_edit
        self.app = cat_edit.app
        self.mode = mode  # "active" | "passive" | "disorder"
        self.title = {"active": "Active Abilities", "passive": "Passives", "disorder": "Disorders"}[mode]
        self.list = ui.ListView(row_h=104, gap=12)
        self.build()

    def on_enter(self) -> None:
        self.build()

    def build(self) -> None:
        ab = self.cat_edit.vals["abilities"]
        rows = []
        if self.mode == "active":
            labels = ["Move", "Basic Attack", "Active 1", "Active 2", "Active 3", "Active 4"]
            for i, lab in enumerate(labels):
                aid = ab["active"][i]
                info = gd.active_info(aid)
                rows.append(self.ui.Row(lab, value=info["name"], data=("active", i, aid, info)))
        elif self.mode == "passive":
            for i in range(2):
                pid = ab["passive"][i]
                tier = ab["passive_tiers"][i]
                info = gd.passive_info(pid, tier)
                val = info.get("display_name", info["name"]) if pid else "None"
                rows.append(self.ui.Row(f"Passive {i + 1}", value=val, data=("passive", i, pid, tier, info)))
        else:
            for i in range(2):
                did = ab["disorder"][i]
                info = gd.disorder_info(did)
                rows.append(self.ui.Row(f"Disorder {i + 1}", value=info["name"], data=("disorder", i, did, info)))
        self.list.set_rows(rows)

    def _open_picker(self) -> None:
        row = self.list.current
        if not row:
            return
        ui = self.ui
        ab = self.cat_edit.vals["abilities"]
        if self.mode == "active":
            _, idx, cur_id, _ = row.data
            all_act = list(gd.actives().values())
            if idx == 0:
                entries = [a for a in all_act if a.get("slot") == "move"]
                cats = ["All"]
            elif idx == 1:
                entries = [a for a in all_act if a.get("slot") == "basic"]
                cats = ["All"]
            else:
                entries = [a for a in all_act if not a.get("extra") and a.get("slot") == "active"]
                cls_names = sorted({a.get("class") or "Other" for a in entries})
                cats = ["All"] + cls_names
            entries.sort(key=lambda a: a["name"].lower())

            def pick(e):
                ab["active"][idx] = e["id"] if e else None
                self.cat_edit.commit_local()
                self.build()

            def detail(surf, box, e):
                ui.rrect(surf, box, ui.CARD, 24)
                x, y, w = box.x + 28, box.y + 26, box.w - 56
                if not e:
                    ui.text(surf, "Empty slot (None)", (x, y), 32, ui.DIM, "black")
                    return
                ui.text(surf, e["name"], (x, y), 34, ui.TEXT, "black", maxw=w)
                y += 46
                bx = x
                if e.get("class"):
                    cc = ui.CLASS_COL.get(e["class"], ui.LINE)
                    bx = ui.pill(surf, e["class"], bx, y, cc, ui.DARK, size=20).right + 10
                if e.get("mana"):
                    bx = ui.pill(surf, f"{e['mana']} Mana", bx, y, ui.INFO, ui.DARK, size=20).right + 10
                if e.get("damage"):
                    ui.pill(surf, f"DMG {e['damage']}", bx, y, ui.BAD, ui.DARK, size=20)
                y += 48
                desc = e.get("desc") or "No description available."
                for ln in wrap_lines(ui.F(25), desc, w, max_lines=10):
                    if ln:
                        ui.text(surf, ln, (x, y), 25, ui.TEXT)
                    y += 32
                ui.text(surf, f"ID: {e['id']}", (x, box.bottom - 34), 19, ui.FAINT)

            def meta(e):
                parts = []
                if e.get("class"):
                    parts.append(e["class"])
                if e.get("mana"):
                    parts.append(f"{e['mana']}mp")
                return (" · ".join(parts), ui.DIM)

            self.app.push(CatalogPicker(ui, self.app, f"Pick {row.label}", entries, cats,
                                        lambda e: e.get("class") or "Other", pick, detail, meta,
                                        current_id=cur_id, allow_clear=(idx >= 2)))

        elif self.mode == "passive":
            _, idx, cur_id, cur_tier, _ = row.data
            entries = sorted(gd.passives().values(), key=lambda p: p["name"].lower())
            cats = ["All"] + sorted({p.get("class") or "Colorless" for p in entries})

            def pick(e):
                ab["passive"][idx] = e["id"] if e else None
                if not e:
                    ab["passive_tiers"][idx] = 1
                self.cat_edit.commit_local()
                self.build()

            def detail(surf, box, e):
                ui.rrect(surf, box, ui.CARD, 24)
                x, y, w = box.x + 28, box.y + 26, box.w - 56
                if not e:
                    ui.text(surf, "Empty passive slot", (x, y), 32, ui.DIM, "black")
                    return
                ui.text(surf, e["name"], (x, y), 34, ui.TEXT, "black", maxw=w)
                y += 46
                cc = ui.CLASS_COL.get(e.get("class", ""), ui.LINE)
                ui.pill(surf, e.get("class") or "Colorless", x, y, cc, ui.DARK, size=20)
                y += 50
                ui.text(surf, "TIER 1", (x, y), 18, ui.FAINT, "black")
                y += 26
                for ln in wrap_lines(ui.F(24), e.get("desc") or "No description.", w, max_lines=5):
                    if ln:
                        ui.text(surf, ln, (x, y), 24, ui.TEXT)
                    y += 30
                if e.get("desc2"):
                    y += 18
                    ui.text(surf, "TIER 2 (+)", (x, y), 18, ui.ACCENT, "black")
                    y += 26
                    for ln in wrap_lines(ui.F(24), e["desc2"], w, max_lines=5):
                        if ln:
                            ui.text(surf, ln, (x, y), 24, ui.GOOD)
                        y += 30
                ui.text(surf, f"ID: {e['id']}", (x, box.bottom - 34), 19, ui.FAINT)

            self.app.push(CatalogPicker(ui, self.app, f"Pick Passive {idx + 1}", entries, cats,
                                        lambda e: e.get("class") or "Colorless", pick, detail,
                                        lambda e: (e.get("class") or "Colorless", ui.CLASS_COL.get(e.get("class", ""), ui.DIM)),
                                        current_id=cur_id, allow_clear=True))

        else:
            _, idx, cur_id, _ = row.data
            entries = sorted(gd.disorders().values(), key=lambda d: d["name"].lower())

            def pick(e):
                ab["disorder"][idx] = e["id"] if e else None
                ab["disorder_tiers"][idx] = 1
                self.cat_edit.commit_local()
                self.build()

            def detail(surf, box, e):
                ui.rrect(surf, box, ui.CARD, 24)
                x, y, w = box.x + 28, box.y + 26, box.w - 56
                if not e:
                    ui.text(surf, "No disorder", (x, y), 32, ui.GOOD, "black")
                    return
                ui.text(surf, e["name"], (x, y), 34, ui.BAD, "black", maxw=w)
                y += 50
                for ln in wrap_lines(ui.F(25), e.get("desc") or "No description.", w, max_lines=10):
                    if ln:
                        ui.text(surf, ln, (x, y), 25, ui.TEXT)
                    y += 32
                ui.text(surf, f"ID: {e['id']}", (x, box.bottom - 34), 19, ui.FAINT)

            self.app.push(CatalogPicker(ui, self.app, f"Pick Disorder {idx + 1}", entries, ["All"],
                                        lambda _: "All", pick, detail, None, current_id=cur_id, allow_clear=True))

    def handle(self, btn: str) -> None:
        ui = self.ui
        ab = self.cat_edit.vals["abilities"]
        row = self.list.current
        if btn in (ui.UP, ui.DOWN):
            self.list.move(-1 if btn == ui.UP else 1)
        elif btn == ui.A:
            self._open_picker()
        elif btn in (ui.LEFT, ui.RIGHT) and self.mode == "passive" and row:
            _, idx, pid, tier, _ = row.data
            if pid:
                ab["passive_tiers"][idx] = 2 if tier == 1 else 1
                self.cat_edit.commit_local()
                self.build()
        elif btn == ui.X and row:
            idx = row.data[1]
            if self.mode == "active" and idx >= 2:
                ab["active"][idx] = None
            elif self.mode == "passive":
                ab["passive"][idx] = None
                ab["passive_tiers"][idx] = 1
            elif self.mode == "disorder":
                ab["disorder"][idx] = None
                ab["disorder_tiers"][idx] = 1
            else:
                return
            self.cat_edit.commit_local()
            self.build()
        elif btn == ui.Y and self.mode == "disorder":
            ab["disorder"] = [None, None]
            ab["disorder_tiers"] = [1, 1]
            self.cat_edit.commit_local()
            self.build()
            self.app.toast("All disorders cleared", ui.GOOD)
        elif btn == ui.START:
            self.app.write()
        elif btn == ui.B:
            self.app.pop()

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self._open_picker()
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        ui.text(surf, row.label, (r.x + 28, r.y + 16), 21, ui.FAINT, "black")
        val = row.value or "None"
        col = ui.FAINT if val == "None" else (ui.BAD if self.mode == "disorder" else ui.TEXT)
        ui.text(surf, val, (r.x + 28, r.bottom - 18), 34, col, "black", anchor="bottomleft", maxw=r.w - 220)
        if self.mode == "passive" and row.data[2]:
            tier = row.data[3]
            ui.pill(surf, f"Tier {tier}{'+' if tier == 2 else ''}", r.right - 30, r.centery,
                    ui.ACCENT if tier == 2 else ui.CARD_HI, ui.DARK if tier == 2 else ui.DIM, size=21, anchor="midright")
        elif self.mode == "active" and row.data[3].get("mana"):
            ui.pill(surf, f"{row.data[3]['mana']} MP", r.right - 30, r.centery, ui.INFO, ui.DARK, size=20, anchor="midright")

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 24, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 24)
        la = pygame.Rect(area.x, area.y, area.w - 640, area.h)
        self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 600, area.y, 600, area.h)
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 30, box.y + 28, box.w - 60
        row = self.list.current
        if not row:
            return
        if self.mode == "active":
            _, idx, aid, info = row.data
            ui.text(surf, row.label.upper(), (x, y), 18, ui.FAINT, "black")
            y += 30
            ui.text(surf, info["name"], (x, y), 36, ui.TEXT if aid else ui.DIM, "black", maxw=w)
            y += 50
            bx = x
            if info.get("class"):
                bx = ui.pill(surf, info["class"], bx, y, ui.CLASS_COL.get(info["class"], ui.LINE), ui.DARK, size=20).right + 10
            if info.get("mana"):
                bx = ui.pill(surf, f"{info['mana']} Mana", bx, y, ui.INFO, ui.DARK, size=20).right + 10
            if info.get("damage"):
                ui.pill(surf, f"DMG {info['damage']}", bx, y, ui.BAD, ui.DARK, size=20)
            y += 48
            for ln in wrap_lines(ui.F(25), info.get("desc") or "Press A to pick an ability for this slot.", w, max_lines=10):
                if ln:
                    ui.text(surf, ln, (x, y), 25, ui.TEXT if aid else ui.DIM)
                y += 32
        elif self.mode == "passive":
            _, idx, pid, tier, info = row.data
            ui.text(surf, f"{row.label.upper()}  ·  TIER {tier}", (x, y), 18, ui.ACCENT if tier == 2 else ui.FAINT, "black")
            y += 30
            ui.text(surf, info.get("display_name", info["name"]), (x, y), 36, ui.TEXT if pid else ui.DIM, "black", maxw=w)
            y += 52
            if pid:
                ui.pill(surf, info.get("class") or "Colorless", x, y,
                        ui.CLASS_COL.get(info.get("class", ""), ui.LINE), ui.DARK, size=20)
                y += 50
                desc = info.get("active_desc") or info.get("desc") or "No description."
                for ln in wrap_lines(ui.F(25), desc, w, max_lines=8):
                    if ln:
                        ui.text(surf, ln, (x, y), 25, ui.TEXT)
                    y += 32
                y += 20
                ui.text(surf, "Press Left/Right to switch Tier 1 / Tier 2 (+).", (x, y), 22, ui.DIM)
            else:
                ui.text(surf, "Press A to assign a passive.", (x, y), 25, ui.DIM)
        else:
            _, idx, did, info = row.data
            ui.text(surf, row.label.upper(), (x, y), 18, ui.FAINT, "black")
            y += 30
            ui.text(surf, info["name"], (x, y), 36, ui.BAD if did else ui.GOOD, "black", maxw=w)
            y += 52
            desc = info.get("desc") or ("No disorder in this slot." if not did else "No description.")
            for ln in wrap_lines(ui.F(25), desc, w, max_lines=10):
                if ln:
                    ui.text(surf, ln, (x, y), 25, ui.TEXT if did else ui.DIM)
                y += 32

    def hint_items(self):
        out = [("A", "Change")]
        if self.mode == "passive":
            out.append(("DPAD_LR", "Tier 1/2"))
        out.append(("X", "Clear"))
        if self.mode == "disorder":
            out.append(("Y", "Clear all"))
        out.append(("B", "Done"))
        return out + self.ui.pending_hint(self.app)


# --------------------------------------------------------------------------- Mutations Editor

class MutationsScreen:
    """Coat Texture + 14 Body Part mutation editor with unobfuscated descriptions, stats, and L<->R mirror."""

    def __init__(self, ui, cat_edit) -> None:
        self.ui = ui
        self.cat_edit = cat_edit
        self.app = cat_edit.app
        self.title = "Mutations"
        self.list = ui.ListView(row_h=84, gap=8)
        self.build()

    def on_enter(self) -> None:
        self.build()

    def build(self) -> None:
        muts = self.cat_edit.vals["mutations"]
        rows = []
        coat_val = muts.get("coat", 0)
        cinfo = gd.mutation_info("texture", coat_val)
        rows.append(self.ui.Row("Coat / Fur Texture", value=cinfo["name"], data=("coat", "texture", None, coat_val, cinfo)))
        for key, label, category, partner in gd.MUTATION_SLOTS:
            val = muts.get(key, 0)
            info = gd.mutation_info(category, val)
            rows.append(self.ui.Row(label, value=info["name"], data=(key, category, partner, val, info)))
        self.list.set_rows(rows)

    def _open_picker(self) -> None:
        row = self.list.current
        if not row:
            return
        ui = self.ui
        key, category, partner, cur_val, _ = row.data
        entries = gd.mutations_for_category(category)
        cats = ["All", "Mutations", "Birth Defects"]

        def cat_fn(e):
            return "Birth Defects" if e.get("tag") == "birth_defect" else "Mutations"

        def pick(e):
            muts = self.cat_edit.vals["mutations"]
            if e is None:
                # Reset to normal (0 for body parts; original normal coat or 1 for coat)
                orig = self.cat_edit.cat["mutations"].get(key, 0)
                muts[key] = orig if (key == "coat" and orig < 300) else (1 if key == "coat" else 0)
            else:
                muts[key] = e["raw_id"]
            self.cat_edit.commit_local()
            self.build()

        def detail(surf, box, e):
            ui.rrect(surf, box, ui.CARD, 24)
            x, y, w = box.x + 28, box.y + 26, box.w - 56
            if not e:
                ui.text(surf, "Normal (No Mutation)", (x, y), 32, ui.DIM, "black")
                ui.text(surf, "Clears any mutation or birth defect on this body part.", (x, y + 48), 24, ui.DIM, maxw=w)
                return
            is_def = e.get("tag") == "birth_defect"
            ui.text(surf, e["name"], (x, y), 34, ui.BAD if is_def else ui.GOOD, "black", maxw=w)
            y += 46
            ui.pill(surf, "BIRTH DEFECT" if is_def else f"{category.upper()} #{e['id']}", x, y,
                    ui.BAD if is_def else ui.CARD_HI, ui.DARK if is_def else ui.TEXT, size=19)
            y += 46
            stats = e.get("stats") or {}
            if stats:
                sx = x
                for sk, sv in stats.items():
                    col = ui.GOOD if sv > 0 else ui.BAD
                    r = ui.pill(surf, f"{'+' if sv > 0 else ''}{sv} {sk}", sx, y, ui.mix(col, ui.CARD, .72), col, size=20)
                    sx = r.right + 8
                y += 44
            desc = e.get("desc") or ("Cosmetic / stat mutation." if stats else "Cosmetic mutation.")
            for ln in wrap_lines(ui.F(24), desc, w, max_lines=9):
                if ln:
                    ui.text(surf, ln, (x, y), 24, ui.TEXT)
                y += 30

        def meta(e):
            st = gd.format_stats(e.get("stats"))
            if st:
                return (st, ui.GOOD)
            if e.get("tag") == "birth_defect":
                return ("defect", ui.BAD)
            return (f"#{e['id']}", ui.FAINT)

        self.app.push(CatalogPicker(ui, self.app, f"Pick {row.label}", entries, cats, cat_fn,
                                    pick, detail, meta, current_id=cur_val, allow_clear=True,
                                    clear_label="Normal (clear mutation)"))

    def handle(self, btn: str) -> None:
        ui = self.ui
        row = self.list.current
        if btn in (ui.UP, ui.DOWN):
            self.list.move(-1 if btn == ui.UP else 1)
        elif btn in (ui.LT, ui.RT):
            self.list.move(-8 if btn == ui.LT else 8)
        elif btn == ui.SELECT:
            # Clear all birth defects (-2) across all 14 body parts
            cleared = 0
            muts = self.cat_edit.vals["mutations"]
            for key, _, _, _ in gd.MUTATION_SLOTS:
                if gd.signed_mut_id(muts.get(key, 0)) == -2:
                    muts[key] = 0
                    cleared += 1
            if cleared:
                self.cat_edit.commit_local()
                self.build()
                self.app.toast(f"Cleared {cleared} birth defect{'s' if cleared != 1 else ''}", ui.GOOD)
            else:
                self.app.toast("No birth defects on this cat", ui.DIM)
        elif btn == ui.A:
            self._open_picker()
        elif btn == ui.X and row:
            key = row.data[0]
            orig = self.cat_edit.cat["mutations"].get(key, 0)
            self.cat_edit.vals["mutations"][key] = orig if (key == "coat" and orig < 300) else (1 if key == "coat" else 0)
            self.cat_edit.commit_local()
            self.build()
        elif btn == ui.Y and row:
            key, _, partner, cur_val, _ = row.data
            if partner:
                self.cat_edit.vals["mutations"][partner] = cur_val
                self.cat_edit.commit_local()
                self.build()
                self.app.toast(f"Mirrored to {gd.MUTATION_SLOT_BY_KEY[partner][0]}", ui.GOOD)
        elif btn == ui.START:
            self.app.write()
        elif btn == ui.B:
            self.app.pop()

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self._open_picker()
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        key, category, partner, raw_val, info = row.data
        is_mut = info.get("is_mutation", False)
        is_def = info.get("tag") == "birth_defect"
        col = ui.BAD if is_def else ui.GOOD if is_mut else ui.DIM
        ui.text(surf, row.label, (r.x + 28, r.centery), 28, ui.TEXT, "bold", anchor="midleft")
        ui.text(surf, info["name"], (r.right - 28, r.centery), 28, col, "black" if is_mut else "semi",
                anchor="midright", maxw=r.w - 300)

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 24, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 24)
        la = pygame.Rect(area.x, area.y, area.w - 620, area.h)
        self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 580, area.y, 580, area.h)
        ui.rrect(surf, box, ui.CARD, 24)
        x, y, w = box.x + 30, box.y + 28, box.w - 60
        row = self.list.current
        if not row:
            return
        key, category, partner, raw_val, info = row.data
        ui.text(surf, row.label.upper(), (x, y), 18, ui.FAINT, "black")
        y += 30
        is_def = info.get("tag") == "birth_defect"
        ui.text(surf, info["name"], (x, y), 36, ui.BAD if is_def else ui.GOOD if info.get("is_mutation") else ui.DIM,
                "black", maxw=w)
        y += 52
        stats = info.get("stats") or {}
        if stats:
            sx = x
            for sk, sv in stats.items():
                col = ui.GOOD if sv > 0 else ui.BAD
                r = ui.pill(surf, f"{'+' if sv > 0 else ''}{sv} {sk}", sx, y, ui.mix(col, ui.CARD, .72), col, size=21)
                sx = r.right + 8
            y += 46
        desc = info.get("desc") or ("No special effect." if info.get("is_mutation") else "Normal non-mutated body part.")
        for ln in wrap_lines(ui.F(25), desc, w, max_lines=8):
            if ln:
                ui.text(surf, ln, (x, y), 25, ui.TEXT if info.get("is_mutation") else ui.DIM)
            y += 32
        if partner:
            ui.text(surf, f"Press Y to mirror to {gd.MUTATION_SLOT_BY_KEY[partner][0]}.", (x, box.bottom - 38), 21, ui.DIM)

    def hint_items(self):
        row = self.list.current
        out = [("A", "Change"), ("X", "Clear"), ("SELECT", "Clear defects")]
        if row and row.data[2]:
            out.append(("Y", "Mirror L/R"))
        out.append(("B", "Done"))
        return out + self.ui.pending_hint(self.app)


# --------------------------------------------------------------------------- Cat Equipment Editor

class EquipScreen:
    """Edit all 5 equipment slots on a cat (Weapon, Hat, Face, Neck, Trinket) with Set Bonuses."""

    UI_ORDER = ("weapon", "head", "face", "neck", "trinket")

    def __init__(self, ui, cat_edit) -> None:
        self.ui = ui
        self.cat_edit = cat_edit
        self.app = cat_edit.app
        self.title = "Equipment"
        self.list = ui.ListView(row_h=112, gap=14)
        self.build()

    def on_enter(self) -> None:
        self.build()

    def build(self) -> None:
        eq = self.cat_edit.vals["equipment"]
        rows = []
        for sk in self.UI_ORDER:
            iid = eq.get(sk)
            info = gd.item_info(iid) if iid else None
            rows.append(self.ui.Row(cb.EQUIP_SLOT_LABELS[sk], value=info["name"] if info else "Empty",
                                    data=(sk, iid, info)))
        self.list.set_rows(rows)

    def _open_picker(self) -> None:
        row = self.list.current
        if not row:
            return
        ui = self.ui
        sk, cur_id, _ = row.data
        all_items = list(gd.items().values())
        slot_items = [it for it in all_items if it.get("kind") == sk]
        slot_items.sort(key=lambda x: x["name"].lower())
        cats = ["All", "Common", "Uncommon", "Rare", "Very Rare", "Legendary", "Cursed"]

        def cat_fn(e):
            if e.get("cursed"):
                return "Cursed"
            r = (e.get("rarity") or "").replace("_", " ").title()
            return r if r in cats else "All"

        def pick(e):
            self.cat_edit.vals["equipment"][sk] = e["id"] if e else None
            self.cat_edit.commit_local()
            self.build()

        def meta(e):
            st = gd.format_stats(e.get("stats"))
            if st:
                return (st, ui.GOOD)
            return ((e.get("rarity") or "").replace("_", " "), ui.RARITY_COL.get(e.get("rarity", ""), ui.DIM))

        self.app.push(CatalogPicker(ui, self.app, f"Equip {cb.EQUIP_SLOT_LABELS[sk]}", slot_items, cats, cat_fn,
                                    pick, lambda s, b, e: draw_item_detail(ui, s, b, e), meta,
                                    current_id=cur_id, allow_clear=True, clear_label="Unequip (Empty)"))

    def handle(self, btn: str) -> None:
        ui = self.ui
        row = self.list.current
        if btn in (ui.UP, ui.DOWN):
            self.list.move(-1 if btn == ui.UP else 1)
        elif btn == ui.A:
            self._open_picker()
        elif btn == ui.X and row:
            self.cat_edit.vals["equipment"][row.data[0]] = None
            self.cat_edit.commit_local()
            self.build()
        elif btn == ui.START:
            self.app.write()
        elif btn == ui.B:
            self.app.pop()

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self._open_picker()
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        sk, iid, info = row.data
        ui.text(surf, row.label.upper(), (r.x + 28, r.y + 16), 19, ui.FAINT, "black")
        ui.text(surf, info["name"] if info else "Empty", (r.x + 28, r.bottom - 18), 34,
                ui.TEXT if info else ui.FAINT, "black", anchor="bottomleft", maxw=r.w - 220)
        if info:
            rc = ui.RARITY_COL.get(info.get("rarity", ""), ui.DIM)
            st = gd.format_stats(info.get("stats"))
            if st:
                ui.text(surf, st, (r.right - 28, r.centery), 22, ui.GOOD, "bold", anchor="midright", maxw=260)
            elif info.get("rarity"):
                ui.pill(surf, info["rarity"].replace("_", " "), r.right - 28, r.centery, rc, ui.DARK, size=18, anchor="midright")

    def _set_progress(self) -> list[tuple[dict[str, Any], int]]:
        """Aggregate equipped items by armor set across all 5 slots."""
        counts: dict[str, int] = {}
        sb_map = gd.set_bonuses()
        for iid in self.cat_edit.vals["equipment"].values():
            if not iid:
                continue
            for sb in gd.item_set_info(gd.item_info(iid)):
                counts[sb["id"]] = counts.get(sb["id"], 0) + 1
        out = [(sb_map[sid], cnt) for sid, cnt in counts.items() if sid in sb_map]
        out.sort(key=lambda x: (-(x[1] >= x[0].get("pieces", 3)), -x[1], x[0]["name"]))
        return out

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 24, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 24)
        sets = self._set_progress()
        set_h = 156 if sets else 0
        la = pygame.Rect(area.x, area.y, area.w - 640, area.h - set_h - (16 if set_h else 0))
        self.list.draw(surf, la, self._render_row)

        if sets:
            sbox = pygame.Rect(area.x, la.bottom + 16, la.w, set_h)
            ui.rrect(surf, sbox, ui.CARD, 22)
            ui.text(surf, "EQUIPPED ARMOR SET SYNERGY", (sbox.x + 26, sbox.y + 18), 18, ui.FAINT, "black")
            sy = sbox.y + 48
            for sb, cnt in sets[:3]:
                req = sb.get("pieces", 3)
                active = cnt >= req
                col = ui.GOOD if active else ui.ACCENT if cnt >= 2 else ui.DIM
                ui.pill(surf, f"{cnt}/{req}" + (" ACTIVE" if active else ""), sbox.x + 26, sy + 14,
                        ui.mix(col, ui.CARD, .7), col, size=18, anchor="midleft")
                bonus = gd.format_stats(sb.get("stats"))
                desc = sb.get("desc", "").replace("\n", " ")
                detail = " · ".join(x for x in (bonus, desc) if x)
                ui.text(surf, f"{sb['name']}" + (f"  —  {detail}" if detail else ""),
                        (sbox.x + 178, sy + 14), 22, ui.TEXT if active else ui.DIM, "bold" if active else "semi",
                        anchor="midleft", maxw=sbox.w - 204)
                sy += 34

        box = pygame.Rect(area.right - 600, area.y, 600, area.h)
        info = self.list.current.data[2] if self.list.current else None
        draw_item_detail(ui, surf, box, info)

    def hint_items(self):
        return [("A", "Equip / Swap"), ("X", "Unequip"), ("B", "Done")] + self.ui.pending_hint(self.app)


# --------------------------------------------------------------------------- Furniture Tab

class FurnitureTab:
    """View & edit house furniture (Unplaced Storage, Placed Rooms with live stat totals, and 633-item catalog)."""

    name = "Furniture"
    EFFECT_FILTERS = ("Comfort", "Stimulation", "Health", "Appeal", "Evolution")

    def __init__(self, ui, app) -> None:
        self.ui = ui
        self.app = app
        self.catalog = sorted(gd.furniture().values(), key=lambda f: f["name"].lower())
        self.modes: list[tuple[str, str]] = []  # (key, label)
        self.m = 0
        self.query = ""
        self.search_rect: pygame.Rect | None = None
        self.list = ui.ListView(row_h=82, gap=8)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []

    def on_enter(self) -> None:
        rooms = self.app.snap.parsed.get("rooms") or []
        self.modes = [("unplaced", "Storage")]
        for rm in rooms:
            self.modes.append((f"room:{rm}", rm.replace("_", " ")))
        self.modes.append(("add:all", "Add: All"))
        for ef in self.EFFECT_FILTERS:
            self.modes.append((f"add:{ef}", f"+{ef}"))
        self.m = min(self.m, len(self.modes) - 1)
        self._rebuild()

    @staticmethod
    def _matches(info: dict[str, Any], q: str) -> bool:
        if not q:
            return True
        hay = " ".join(str(x) for x in (
            info.get("name", ""), info.get("id", ""), info.get("set", ""),
            info.get("desc", ""), gd.format_stats(info.get("effects")),
        )).lower()
        return all(tok in hay for tok in q.lower().split())

    def _rows_for(self, q: str) -> list:
        mk = self.modes[self.m][0]
        furn = self.app.snap.parsed["furniture"]
        rems = set(self.app.pending.furn_remove)
        rows = []
        if mk == "unplaced":
            for f in furn["unplaced"]:
                if f["key"] in rems:
                    continue
                info = gd.furniture_info(f["furniture_id"])
                if self._matches(info, q):
                    rows.append(self.ui.Row(info["name"], data=("owned", f, info)))
            for fid in self.app.pending.furn_add:
                info = gd.furniture_info(fid)
                if self._matches(info, q):
                    rows.append(self.ui.Row(info["name"], data=("queued", fid, info)))
        elif mk.startswith("room:"):
            rm = mk.split(":", 1)[1]
            for f in furn["placed"]:
                if f["room"] == rm and f["key"] not in rems:
                    info = gd.furniture_info(f["furniture_id"])
                    if self._matches(info, q):
                        rows.append(self.ui.Row(info["name"], data=("owned", f, info)))
        else:
            ef = mk.split(":", 1)[1]
            for info in self.catalog:
                if (ef == "all" or info.get("effects", {}).get(ef, 0) > 0) and self._matches(info, q):
                    rows.append(self.ui.Row(info["name"], data=("catalog", info["id"], info)))
        return rows

    def _rebuild(self) -> None:
        self.list.set_rows(self._rows_for(self.query))

    def _open_search(self) -> None:
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        self.app.push(SearchModal(self.ui, self.app, "Search Furniture", self.query,
                                  lambda q: len(self._rows_for(q)), done))

    def handle(self, btn: str) -> bool:
        ui = self.ui
        if btn in (ui.UP, ui.DOWN):
            step = 1 if self.app.input.count.get(btn, 0) < 12 else 5
            self.list.move((-1 if btn == ui.UP else 1) * step)
        elif btn in (ui.LT, ui.RT):
            self.list.move(-10 if btn == ui.LT else 10)
        elif btn in (ui.LEFT, ui.RIGHT):
            self.m = (self.m + (1 if btn == ui.RIGHT else -1)) % len(self.modes)
            self.list.sel = 0
            self._rebuild()
        elif btn == ui.SELECT:
            self._open_search()
        elif btn == ui.B and self.query:
            self.query = ""
            self.list.sel = 0
            self._rebuild()
        elif btn == ui.A and self.list.current:
            kind, obj, info = self.list.current.data
            if kind == "catalog":
                self.app.pending.furn_add.append(info["id"])
                self.app.toast(f"Queued {info['name']} to Storage", ui.GOOD, 2.0)
        elif btn == ui.X and self.list.current:
            kind, obj, info = self.list.current.data
            if kind == "owned":
                self.app.pending.furn_remove.append(obj["key"])
                self._rebuild()
                self.app.toast(f"Marked {info['name']} for removal", ui.ACCENT, 2.0)
            elif kind == "queued":
                if obj in self.app.pending.furn_add:
                    self.app.pending.furn_add.remove(obj)
                self._rebuild()
            elif kind == "catalog" and info["id"] in self.app.pending.furn_add:
                self.app.pending.furn_add.remove(info["id"])
        else:
            return False
        return True

    def click(self, pos) -> None:
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
            if i == self.list.sel:
                self.handle(self.ui.A)
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        kind, obj, info = row.data
        ui.text(surf, info["name"], (r.x + 28, r.centery), 30, ui.ACCENT if kind == "queued" else ui.TEXT,
                "bold", anchor="midleft", maxw=r.w - 380)
        ef_str = gd.format_stats(info.get("effects"))
        if ef_str:
            ui.text(surf, ef_str, (r.right - 90, r.centery), 21, ui.GOOD, "bold", anchor="midright", maxw=320)
        if kind == "catalog":
            n = self.app.pending.furn_add.count(info["id"])
            if n:
                ui.pill(surf, f"+{n}", r.right - 22, r.centery, ui.ACCENT, size=22, anchor="midright")
        elif kind == "queued":
            ui.pill(surf, "NEW", r.right - 22, r.centery, ui.ACCENT, size=18, anchor="midright")

    def draw(self, surf, area: pygame.Rect) -> None:
        ui = self.ui
        self.search_rect = draw_search_pill(ui, surf, area.right - 580, area.y + 18, self.query, anchor="midright")
        chips_right = self.search_rect.left - 14
        self.chip_rects = []
        widths = [ui.F(22, "bold").size(lbl)[0] + 32 for _, lbl in self.modes]
        start = 0
        while sum(widths[start:self.m + 1]) > (chips_right - area.x - 26) and start < self.m:
            start += 1
        x = area.x
        if start:
            ui.tri(surf, ui.DIM, (x + 8, area.y + 18), 9, "left")
            x += 26
        for i in range(start, len(self.modes)):
            sel = i == self.m
            w_est = ui.F(22, "bold").size(self.modes[i][1])[0] + 22
            if x + w_est > chips_right:
                break
            r = ui.pill(surf, self.modes[i][1], x, area.y, ui.ACCENT if sel else ui.CARD,
                        ui.DARK if sel else ui.DIM, 22)
            self.chip_rects.append((r, i))
            x = r.right + 10

        la = pygame.Rect(area.x, area.y + 60, area.w - 580, area.h - 60)
        if not self.list.rows:
            ui.text(surf, "No furniture here", (la.centerx, la.y + 100), 32, ui.FAINT, "bold", anchor="midtop")
        else:
            self.list.draw(surf, la, self._render_row)

        # Right card: Selected furniture details + Room stat totals
        box = pygame.Rect(area.right - 540, area.y, 540, area.h)
        ui.rrect(surf, box, ui.CARD, 24)
        bx, by, bw = box.x + 28, box.y + 26, box.w - 56
        cur = self.list.current
        if cur:
            kind, obj, info = cur.data
            ui.text(surf, info["name"], (bx, by), 32, ui.TEXT, "black", maxw=bw)
            by += 44
            px = bx
            if info.get("set"):
                px = ui.pill(surf, f"Set: {info['set']}", px, by, ui.CARD_HI, ui.DIM, size=19).right + 8
            if info.get("special"):
                ui.pill(surf, "SPECIAL", px, by, ui.ACCENT, ui.DARK, size=19)
            by += 44
            effects = info.get("effects") or {}
            if effects:
                ex = bx
                for ek, ev in effects.items():
                    col = ui.GOOD if ev > 0 else ui.BAD
                    r = ui.pill(surf, f"{'+' if ev > 0 else ''}{ev} {ek}", ex, by, ui.mix(col, ui.CARD, .72), col, size=20)
                    if r.right > box.right - 24:
                        ex = bx
                        by += 36
                        r = ui.pill(surf, f"{'+' if ev > 0 else ''}{ev} {ek}", ex, by, ui.mix(col, ui.CARD, .72), col, size=20)
                    ex = r.right + 8
                by += 44
            if info.get("desc"):
                for ln in wrap_lines(ui.F(23), info["desc"], bw, max_lines=4):
                    ui.text(surf, ln, (bx, by), 23, ui.DIM)
                    by += 28
                by += 10

        # Room totals panel at bottom of right card
        mk = self.modes[self.m][0]
        if mk.startswith("room:"):
            rm = mk.split(":", 1)[1]
            rems = set(self.app.pending.furn_remove)
            totals: dict[str, int] = {}
            for f in self.app.snap.parsed["furniture"]["placed"]:
                if f["room"] == rm and f["key"] not in rems:
                    for ek, ev in gd.furniture_info(f["furniture_id"]).get("effects", {}).items():
                        totals[ek] = totals.get(ek, 0) + ev
            rbox = pygame.Rect(bx, box.bottom - 220, bw, 196)
            ui.rrect(surf, rbox, ui.CARD_HI, 18)
            ui.text(surf, f"{rm.replace('_', ' ').upper()} TOTALS", (rbox.x + 20, rbox.y + 16), 18, ui.ACCENT, "black")
            ty = rbox.y + 48
            for ek, ev in sorted(totals.items()):
                col = ui.GOOD if ev > 0 else ui.BAD
                ui.text(surf, ek, (rbox.x + 20, ty), 22, ui.DIM)
                ui.text(surf, f"{'+' if ev > 0 else ''}{ev}", (rbox.right - 20, ty), 22, col, "black", anchor="topright")
                ty += 28
        else:
            add_n = len(self.app.pending.furn_add)
            rem_n = len(self.app.pending.furn_remove)
            summary = f"+{add_n} to storage, -{rem_n} removed" if (add_n or rem_n) else "Added furniture goes to Unplaced Storage."
            ui.text(surf, summary, (bx, box.bottom - 38), 21, ui.ACCENT if (add_n or rem_n) else ui.FAINT, maxw=bw)

    def hint_items(self):
        mk = self.modes[self.m][0]
        if mk.startswith("add:"):
            return [("A", "Add to Storage"), ("X", "Undo"), ("SELECT", "Search"), ("DPAD_LR", "Filter")]
        return [("X", "Remove"), ("SELECT", "Search"), ("DPAD_LR", "Room / Add"), ("B", "Clear" if self.query else "Saves")]


# --------------------------------------------------------------------------- Level-Up Bonus Stats Screen

class BonusStatsScreen:
    """Edit a cat's level-up bonus stats (STR..LUCK) with live effective stat totals."""

    def __init__(self, ui, cat_edit) -> None:
        self.ui = ui
        self.cat_edit = cat_edit
        self.app = cat_edit.app
        self.title = "Level-Up Bonuses"
        self.list = ui.ListView(row_h=88, gap=10)
        self.list.set_rows([ui.Row(s, data=s) for s in ui.STATS])

    def on_enter(self) -> None:
        pass

    def handle(self, btn: str) -> None:
        ui = self.ui
        row = self.list.current
        if btn in (ui.UP, ui.DOWN):
            self.list.move(-1 if btn == ui.UP else 1)
        elif btn in (ui.LEFT, ui.RIGHT) and row:
            s = row.data
            d = 1 if btn == ui.RIGHT else -1
            cur = self.cat_edit.vals["level_bonuses"][s]
            self.cat_edit.vals["level_bonuses"][s] = max(-20, min(99, cur + d))
            self.cat_edit.commit_local()
        elif btn == ui.X and row:
            self.cat_edit.vals["level_bonuses"][row.data] = 0
            self.cat_edit.commit_local()
        elif btn == ui.START:
            self.app.write()
        elif btn == ui.B:
            self.app.pop()

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        s = row.data
        base = self.cat_edit.vals["stats"][s]
        bonus = self.cat_edit.vals["level_bonuses"][s]
        inj = 0 if self.cat_edit.vals["heal"] else self.cat_edit.cat["injury_mods"][s]
        eff = max(1, base + bonus + inj)
        ui.text(surf, s, (r.x + 30, r.centery), 34, ui.TEXT, "black", anchor="midleft")
        ui.text(surf, f"Base {base}  {'+' if bonus >= 0 else ''}{bonus} bonus" + (f"  {inj} inj" if inj else "") + f"  =  {eff}",
                (r.x + 170, r.centery), 24, ui.DIM, anchor="midleft")
        ui.tri(surf, ui.DIM if selected else ui.LINE, (r.right - 200, r.centery), 11, "left")
        ui.tri(surf, ui.DIM if selected else ui.LINE, (r.right - 38, r.centery), 11, "right")
        col = ui.GOOD if bonus > 0 else ui.BAD if bonus < 0 else ui.TEXT
        ui.text(surf, f"{'+' if bonus > 0 else ''}{bonus}", (r.right - 119, r.centery), 36, col, "black", anchor="center")

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 24, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 24)
        la = pygame.Rect(area.x, area.y, area.w - 580, area.h)
        self.list.draw(surf, la, self._render_row)

        box = pygame.Rect(area.right - 540, area.y, 540, area.h)
        ui.rrect(surf, box, ui.CARD, 24)
        s = self.list.current.data if self.list.current else "STR"
        x, y, w = box.x + 30, box.y + 30, box.w - 60
        ui.text(surf, "LEVEL-UP BONUS STATS", (x, y), 18, ui.FAINT, "black")
        ui.text(surf, s, (x, y + 34), 40, ui.ACCENT, "black")
        for i, ln in enumerate(wrap_lines(ui.F(25), ui.STAT_INFO[s], w, max_lines=4)):
            ui.text(surf, ln, (x, y + 96 + i * 32), 25, ui.TEXT)
        note = "Level-up bonuses stack on top of base stats (1-7) without breaking breeding caps."
        for i, ln in enumerate(wrap_lines(ui.F(22), note, w, max_lines=3)):
            ui.text(surf, ln, (x, box.bottom - 108 + i * 28), 22, ui.DIM)

    def hint_items(self):
        return [("DPAD_LR", "Adjust"), ("X", "Zero"), ("B", "Done")] + self.ui.pending_hint(self.app)


# --------------------------------------------------------------------------- Advanced Raw Properties & Unlocks Editor

class PropertiesScreen:
    """Search and edit all integer SQLite `properties` (Tink unlocks, boss countdown, weather, etc.)."""

    def __init__(self, ui, app) -> None:
        self.ui = ui
        self.app = app
        self.title = "Advanced Properties & Unlocks"
        self.all_props = sf.raw_int_properties(app.save)
        self.orig = dict(self.all_props)
        self.query = ""
        self.search_rect: pygame.Rect | None = None
        self.list = ui.ListView(row_h=78, gap=8)
        self._rebuild()

    def on_enter(self) -> None:
        self._rebuild()

    def _filtered(self, q: str) -> list[tuple[str, int]]:
        if not q:
            return self.all_props
        toks = q.lower().split()
        return [(k, v) for k, v in self.all_props if all(t in k.lower() for t in toks)]

    def _rebuild(self) -> None:
        self.list.set_rows([self.ui.Row(k, data=(k, v)) for k, v in self._filtered(self.query)])

    def _cur_val(self, k: str) -> int:
        return self.app.pending.props.get(k, self.orig[k])

    def _set_val(self, k: str, val: int) -> None:
        if val == self.orig[k]:
            self.app.pending.props.pop(k, None)
        else:
            self.app.pending.props[k] = val

    def _open_search(self) -> None:
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        self.app.push(SearchModal(self.ui, self.app, "Search Properties", self.query,
                                  lambda q: len(self._filtered(q)), done))

    def handle(self, btn: str) -> None:
        ui = self.ui
        row = self.list.current
        if btn in (ui.UP, ui.DOWN):
            step = 1 if self.app.input.count.get(btn, 0) < 12 else 5
            self.list.move((-1 if btn == ui.UP else 1) * step)
        elif btn in (ui.LT, ui.RT):
            self.list.move(-12 if btn == ui.LT else 12)
        elif btn == ui.SELECT:
            self._open_search()
        elif btn in (ui.LEFT, ui.RIGHT) and row:
            k = row.data[0]
            d = (1 if btn == ui.RIGHT else -1) * self.app.input.mult(btn)
            self._set_val(k, self._cur_val(k) + d)
        elif btn == ui.A and row:
            k = row.data[0]
            cur = self._cur_val(k)
            if cur in (0, 1):
                self._set_val(k, 0 if cur else 1)
            else:
                self._set_val(k, cur + 1)
        elif btn == ui.X and row:
            self._set_val(row.data[0], 0)
        elif btn == ui.START:
            self.app.write()
        elif btn == ui.B:
            if self.query:
                self.query = ""
                self.list.sel = 0
                self._rebuild()
            else:
                self.app.pop()

    def click(self, pos) -> None:
        if self.search_rect and self.search_rect.collidepoint(pos):
            self._open_search()
            return
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(self.ui.A)
            self.list.sel = i

    def _render_row(self, surf, r, row, selected: bool) -> None:
        ui = self.ui
        k = row.data[0]
        val = self._cur_val(k)
        changed = val != self.orig[k]
        ui.text(surf, k, (r.x + 28, r.centery), 26, ui.ACCENT if changed else ui.TEXT, "bold",
                anchor="midleft", maxw=r.w - 320)
        ui.tri(surf, ui.DIM if selected else ui.LINE, (r.right - 220, r.centery), 10, "left")
        ui.tri(surf, ui.DIM if selected else ui.LINE, (r.right - 36, r.centery), 10, "right")
        ui.text(surf, f"{val:,}", (r.right - 128, r.centery), 30, ui.ACCENT if changed else ui.TEXT, "black", anchor="center")

    def draw(self, surf) -> None:
        ui = self.ui
        area = pygame.Rect(ui.PAD, ui.HEADER_H + 18, ui.W - 2 * ui.PAD, ui.CONTENT_BOTTOM - ui.HEADER_H - 18)
        ui.text(surf, f"{len(self.list.rows)} / {len(self.all_props)} integer properties  ·  A toggles 0/1 · Left/Right adjusts",
                (area.x, area.y + 18), 23, ui.DIM, anchor="midleft")
        self.search_rect = draw_search_pill(ui, surf, area.right - 20, area.y + 18, self.query, anchor="midright")
        la = pygame.Rect(area.x, area.y + 54, area.w - 20, area.h - 54)
        if not self.list.rows:
            ui.text(surf, f'No properties matching "{self.query}"', (la.centerx, la.y + 100), 30, ui.FAINT, "bold", anchor="midtop")
        else:
            self.list.draw(surf, la, self._render_row)

    def hint_items(self):
        return [("A", "Toggle/Inc"), ("DPAD_LR", "Adjust"), ("X", "Zero"), ("SELECT", "Search"),
                ("B", "Clear" if self.query else "Done")] + self.ui.pending_hint(self.app)

"""Gamepad-first pygame-ce front end for MewDeck.

Steam Game Mode exposes the ROG Ally / Deck controls as a virtual Xbox pad (SDL GameController).
Keyboard (arrows / Enter / Esc / Q / E / Tab) and touch work as fallbacks.

Layout: header · tab bar (LB/RB) · content · button-hint footer.
"""

from __future__ import annotations

import json
import math
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pygame

from . import savefile as sf

W, H = 1920, 1080  # native 1080p (16:9, ROG Ally native panel)
HEADER_H, TABS_H, FOOTER_H = 114, 76, 80
CONTENT_TOP = HEADER_H + TABS_H + 22
CONTENT_BOTTOM = H - FOOTER_H - 20
PAD = 54

# palette
BG_TOP, BG_BOT = (34, 24, 44), (15, 12, 21)
CARD = (40, 32, 52)
CARD_HI = (60, 47, 80)
LINE = (82, 68, 104)
TEXT = (246, 240, 252)
DIM = (166, 152, 186)
FAINT = (110, 98, 130)
ACCENT = (255, 180, 76)
GOOD = (104, 216, 148)
BAD = (248, 100, 100)
INFO = (112, 172, 255)
DARK = (20, 16, 26)

BTN_COL = {"A": (96, 196, 116), "B": (232, 88, 88), "X": (82, 146, 236), "Y": (236, 204, 74)}
CLASS_COL = {
    "Fighter": (232, 98, 82), "Hunter": (120, 196, 96), "Mage": (96, 150, 250), "Tank": (130, 150, 178),
    "Thief": (176, 120, 228), "Medic": (244, 132, 176), "Monk": (246, 166, 74), "Butcher": (196, 70, 70),
    "Druid": (156, 206, 70), "Tinkerer": (70, 196, 186), "Necromancer": (150, 98, 196),
    "Psychic": (226, 96, 210), "Jester": (240, 210, 80), "Colorless": (150, 142, 164),
}
SEX_COL = {"Male": (100, 160, 250), "Female": (246, 128, 176), "Ditto": (186, 140, 240)}
RARITY_COL = {"common": (200, 192, 214), "uncommon": GOOD, "rare": INFO, "very_rare": (196, 130, 255),
              "legendary": ACCENT}

STATS = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LUCK"]
STAT_INFO = {  # 5 is the game's baseline; base stats range 1-7
    "STR": "Strength: melee damage, roughly 1 per point.",
    "DEX": "Dexterity: ranged damage, +1 per 2 points above 5.",
    "CON": "Constitution: max HP is CON x 4, plus regen after fights.",
    "INT": "Intelligence: mana regen each turn. Spell damage is fixed.",
    "SPD": "Speed: tiles moved per turn and turn order.",
    "CHA": "Charisma: max mana is CHA x 3, plus breeding skill.",
    "LUCK": "Luck: crit chance and extra rolls; below 5 rolls go bad.",
}


def stat_col(v: int):
    return {1: (232, 92, 92), 2: (232, 92, 92), 3: (236, 120, 88), 4: (240, 156, 86), 5: (222, 196, 104),
            6: (160, 214, 118), 7: (98, 220, 150)}.get(max(1, min(7, v)), (98, 220, 150))

UP, DOWN, LEFT, RIGHT, A, B, X, Y, LB, RB, START, SELECT = (
    "UP", "DOWN", "LEFT", "RIGHT", "A", "B", "X", "Y", "LB", "RB", "START", "SELECT")
REPEATABLE = {UP, DOWN, LEFT, RIGHT}


# =========================================================================== input

def log(*a) -> None:
    print("[mewdeck]", *a, flush=True)


class Input:
    """Controller input by *polling state every frame* from every device SDL exposes.

    Polling (instead of relying on CONTROLLERBUTTONDOWN events) keeps working when gamescope's window
    focus or Steam's virtual-pad setup means events never arrive, and it treats devices SDL doesn't
    recognise as a "game controller" as plain joysticks (Xbox layout) instead of ignoring them.
    Keyboard still comes from events (Steam can map the pad to keys)."""

    JOY_BUTTONS = {0: A, 1: B, 2: X, 3: Y, 4: LB, 5: RB, 6: SELECT, 7: START}

    def __init__(self) -> None:
        self.ctrl_mod = None
        try:
            from pygame._sdl2 import controller  # type: ignore

            controller.init()
            self.ctrl_mod = controller
        except Exception as e:  # noqa: BLE001
            log("controller API unavailable, using joystick API:", e)
        pygame.joystick.init()
        pygame.event.pump()  # Linux SDL2 needs an initial pump to populate joystick devices
        self.pads: dict[int, tuple[str, Any, str]] = {}
        self.ignored: set[int] = set()
        self.prev: set[str] = set()
        self.held: dict[str, float] = {}
        self.count: dict[str, int] = {}
        self.logged_events = 0
        self.rescan()

    # devices
    def rescan(self) -> None:
        seen = set()
        for i in range(pygame.joystick.get_count()):
            try:
                j = pygame.joystick.Joystick(i)
                iid = j.get_instance_id()
                seen.add(iid)
                if iid in self.pads or iid in self.ignored:
                    continue
                name = j.get_name()
                if self.ctrl_mod and self.ctrl_mod.is_controller(i):
                    self.pads[iid] = ("ctrl", self.ctrl_mod.Controller(i), name)
                else:
                    low = name.lower()
                    if j.get_numbuttons() < 4 or any(w in low for w in ("motion", "imu", "accel", "gyro", "touchpad")):
                        self.ignored.add(iid)
                        log(f"pad #{i} '{name}' skipped (not a gamepad: buttons={j.get_numbuttons()} axes={j.get_numaxes()})")
                        continue
                    self.pads[iid] = ("joy", j, name)
                log(f"pad #{i} '{name}' guid={j.get_guid()} as {self.pads[iid][0]} "
                    f"buttons={j.get_numbuttons()} hats={j.get_numhats()} axes={j.get_numaxes()}")
            except pygame.error as e:
                log(f"pad #{i} open failed: {e}")
        self.ignored &= seen
        for iid in list(self.pads):
            if iid not in seen:
                log(f"pad '{self.pads[iid][2]}' removed")
                del self.pads[iid]

    @property
    def names(self) -> list[str]:
        return [p[2] for p in self.pads.values()]

    def _state(self, kind: str, dev: Any) -> set[str]:
        s: set[str] = set()
        if kind == "ctrl":
            for btn, name in ((pygame.CONTROLLER_BUTTON_A, A), (pygame.CONTROLLER_BUTTON_B, B),
                              (pygame.CONTROLLER_BUTTON_X, X), (pygame.CONTROLLER_BUTTON_Y, Y),
                              (pygame.CONTROLLER_BUTTON_LEFTSHOULDER, LB),
                              (pygame.CONTROLLER_BUTTON_RIGHTSHOULDER, RB),
                              (pygame.CONTROLLER_BUTTON_START, START), (pygame.CONTROLLER_BUTTON_BACK, SELECT),
                              (pygame.CONTROLLER_BUTTON_DPAD_UP, UP), (pygame.CONTROLLER_BUTTON_DPAD_DOWN, DOWN),
                              (pygame.CONTROLLER_BUTTON_DPAD_LEFT, LEFT), (pygame.CONTROLLER_BUTTON_DPAD_RIGHT, RIGHT)):
                if dev.get_button(btn):
                    s.add(name)
            ax = dev.get_axis(pygame.CONTROLLER_AXIS_LEFTX) / 32767
            ay = dev.get_axis(pygame.CONTROLLER_AXIS_LEFTY) / 32767
        else:
            n = dev.get_numbuttons()
            for idx, name in self.JOY_BUTTONS.items():
                if idx < n and dev.get_button(idx):
                    s.add(name)
            if dev.get_numhats():
                hx, hy = dev.get_hat(0)
                if hx:
                    s.add(RIGHT if hx > 0 else LEFT)
                if hy:
                    s.add(UP if hy > 0 else DOWN)
            ax = dev.get_axis(0) if dev.get_numaxes() > 0 else 0.0
            ay = dev.get_axis(1) if dev.get_numaxes() > 1 else 0.0
        if ax > 0.55:
            s.add(RIGHT)
        elif ax < -0.55:
            s.add(LEFT)
        if ay > 0.55:
            s.add(DOWN)
        elif ay < -0.55:
            s.add(UP)
        return s

    def poll(self) -> list[str]:
        cur: set[str] = set()
        for kind, dev, _ in list(self.pads.values()):
            try:
                cur |= self._state(kind, dev)
            except pygame.error:
                continue
        now = time.time()
        out = []
        for b in sorted(cur - self.prev):
            out.append(b)
            if b in REPEATABLE:
                self.held[b] = now + 0.36
                self.count[b] = 0
        for b in self.prev - cur:
            self.held.pop(b, None)
        for b, due in list(self.held.items()):
            if now >= due:
                out.append(b)
                self.count[b] = self.count.get(b, 0) + 1
                self.held[b] = now + (0.07 if self.count[b] < 12 else 0.04)
        if out and self.logged_events < 40:
            self.logged_events += 1
            log("pad input:", out)
        self.prev = cur
        return out

    # events (keyboard + hotplug)
    def translate(self, ev: pygame.event.Event) -> list[str]:
        t = ev.type
        if t in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED, getattr(pygame, "CONTROLLERDEVICEADDED", -1),
                 getattr(pygame, "CONTROLLERDEVICEREMOVED", -1)):
            self.rescan()
            return []
        if t == pygame.KEYDOWN:
            km = {pygame.K_UP: UP, pygame.K_DOWN: DOWN, pygame.K_LEFT: LEFT, pygame.K_RIGHT: RIGHT,
                  pygame.K_RETURN: A, pygame.K_SPACE: A, pygame.K_KP_ENTER: A,
                  pygame.K_ESCAPE: B, pygame.K_BACKSPACE: B, pygame.K_x: X, pygame.K_y: Y,
                  pygame.K_q: LB, pygame.K_PAGEUP: LB, pygame.K_e: RB, pygame.K_PAGEDOWN: RB,
                  pygame.K_TAB: START, pygame.K_s: START, pygame.K_BACKQUOTE: SELECT}
            name = km.get(ev.key)
            if name in REPEATABLE:
                self.count[name] = self.count.get(name, 0) + 1 if getattr(ev, "repeat", False) else 0
            if name and self.logged_events < 40:
                self.logged_events += 1
                log("key input:", name)
            return [name] if name else []
        return []

    def mult(self, name: str) -> int:
        """Value-step multiplier while a direction is held: 1 -> 10 -> 100."""
        n = self.count.get(name, 0)
        return 1 if n < 10 else 10 if n < 28 else 100


# =========================================================================== drawing kit

FONT_DIR = Path(__file__).parent / "fonts"


class Fonts:
    FILES = {"semi": "Nunito-600.ttf", "bold": "Nunito-800.ttf", "black": "Nunito-900.ttf"}

    def __init__(self) -> None:
        self.cache: dict[tuple[int, str], pygame.font.Font] = {}

    def __call__(self, size: int, weight: str = "semi") -> pygame.font.Font:
        k = (size, weight)
        if k not in self.cache:
            path = FONT_DIR / self.FILES.get(weight, self.FILES["semi"])
            try:
                f = pygame.font.Font(path, size)
            except (OSError, pygame.error):
                f = pygame.font.Font(None, int(size * 1.25))
                f.set_bold(weight != "semi")
            self.cache[k] = f
        return self.cache[k]


F: Fonts


def text(surf: pygame.Surface, s: str, pos: tuple[float, float], size: int = 28, color=TEXT,
         weight: str = "semi", anchor: str = "topleft", maxw: int | None = None, alpha: int = 255) -> pygame.Rect:
    font = F(size, weight)
    if maxw and font.size(s)[0] > maxw:
        while len(s) > 1 and font.size(s + "…")[0] > maxw:
            s = s[:-1]
        s = s.rstrip() + "…"
    img = font.render(s, True, color)
    if alpha < 255:
        img.set_alpha(alpha)
    r = img.get_rect(**{anchor: (round(pos[0]), round(pos[1]))})
    surf.blit(img, r)
    return r


def rrect(surf, rect, color, radius=14, width=0) -> None:
    pygame.draw.rect(surf, color, rect, width=width, border_radius=radius)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1, c2, t: float):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def pill(surf, label: str, x: float, y: float, bg, fg=DARK, size=20, anchor="topleft", weight="bold") -> pygame.Rect:
    img = F(size, weight).render(label, True, fg)
    r = pygame.Rect(0, 0, img.get_width() + size, img.get_height() + 4)
    setattr(r, anchor, (round(x), round(y)))
    rrect(surf, r, bg, r.h // 2)
    surf.blit(img, img.get_rect(center=r.center))
    return r


def pips(surf, x, y, value: int, maximum: int, color, w=18, h=12, gap=5, empty=LINE) -> int:
    for i in range(maximum):
        rrect(surf, (x + i * (w + gap), y, w, h), color if i < value else empty, 4)
    return x + maximum * (w + gap) - gap


def tri(surf, color, center, size, direction) -> None:
    cx, cy = center
    s = size
    pts = {"left": [(cx - s, cy), (cx + s * .6, cy - s), (cx + s * .6, cy + s)],
           "right": [(cx + s, cy), (cx - s * .6, cy - s), (cx - s * .6, cy + s)],
           "up": [(cx, cy - s), (cx - s, cy + s * .6), (cx + s, cy + s * .6)],
           "down": [(cx, cy + s), (cx - s, cy - s * .6), (cx + s, cy - s * .6)]}[direction]
    pygame.draw.polygon(surf, color, pts)


def icon_cat(surf, cx, cy, s, color=ACCENT, eye=DARK) -> None:
    pygame.draw.polygon(surf, color, [(cx - s * .78, cy - s * .1), (cx - s * .6, cy - s * .95), (cx - s * .15, cy - s * .5)])
    pygame.draw.polygon(surf, color, [(cx + s * .78, cy - s * .1), (cx + s * .6, cy - s * .95), (cx + s * .15, cy - s * .5)])
    pygame.draw.ellipse(surf, color, (cx - s * .85, cy - s * .62, s * 1.7, s * 1.45))
    for ex in (-.34, .34):
        pygame.draw.ellipse(surf, eye, (cx + ex * s - s * .11, cy - s * .1, s * .22, s * .3))
    pygame.draw.polygon(surf, eye, [(cx - s * .1, cy + s * .32), (cx + s * .1, cy + s * .32), (cx, cy + s * .44)])


def icon_coin(surf, cx, cy, r) -> None:
    pygame.draw.circle(surf, (196, 136, 40), (cx, cy + 2), r)
    pygame.draw.circle(surf, (255, 200, 80), (cx, cy), r)
    pygame.draw.circle(surf, (232, 166, 52), (cx, cy), r * .62, 3)


def icon_food(surf, cx, cy, r) -> None:
    pygame.draw.ellipse(surf, (190, 120, 90), (cx - r * .8, cy - r * .55, r * 1.6, r * .7))  # kibble
    pygame.draw.circle(surf, (214, 140, 100), (cx - r * .35, cy - r * .35), r * .28)
    pygame.draw.circle(surf, (230, 160, 110), (cx + r * .25, cy - r * .4), r * .26)
    pygame.draw.polygon(surf, (90, 160, 230), [(cx - r, cy - r * .2), (cx + r, cy - r * .2),
                                               (cx + r * .7, cy + r * .7), (cx - r * .7, cy + r * .7)])
    pygame.draw.line(surf, (150, 200, 250), (cx - r, cy - r * .2), (cx + r, cy - r * .2), 3)


def icon_status(surf, cx, cy, r, ok: bool) -> None:
    col = GOOD if ok else BAD
    pygame.draw.circle(surf, mix(col, DARK, .55), (cx, cy), r)
    pygame.draw.circle(surf, col, (cx, cy), r, 4)
    if ok:
        pygame.draw.lines(surf, col, False, [(cx - r * .42, cy + r * .02), (cx - r * .1, cy + r * .34),
                                             (cx + r * .45, cy - r * .32)], 7)
    else:
        pygame.draw.line(surf, col, (cx, cy - r * .48), (cx, cy + r * .12), 8)
        pygame.draw.circle(surf, col, (cx, cy + r * .4), 5)


def glyph(surf, name: str, x: int, cy: int) -> int:
    """Draw a controller glyph with its left edge at x, vertically centred at cy. Returns right edge."""
    if name in BTN_COL:
        r = 20
        pygame.draw.circle(surf, mix(BTN_COL[name], DARK, .45), (x + r, cy + 3), r)
        pygame.draw.circle(surf, BTN_COL[name], (x + r, cy), r)
        img = F(26, "black").render(name, True, DARK)
        surf.blit(img, img.get_rect(center=(x + r, cy)))
        return x + 2 * r
    if name in ("LB", "RB", "LBRB"):
        right = x
        for part in (["LB", "RB"] if name == "LBRB" else [name]):
            rr = pygame.Rect(right, cy - 18, 56, 36)
            rrect(surf, rr, (128, 116, 150), 12)
            img = F(22, "black").render(part, True, DARK)
            surf.blit(img, img.get_rect(center=rr.center))
            right = rr.right + 6
        return right - 6
    if name == "START":
        rr = pygame.Rect(x, cy - 18, 54, 36)
        rrect(surf, rr, (128, 116, 150), 18)
        for dy in (-8, 0, 8):
            pygame.draw.line(surf, DARK, (rr.centerx - 12, cy + dy), (rr.centerx + 12, cy + dy), 4)
        return rr.right
    if name.startswith("DPAD"):
        c = (128, 116, 150)
        hi = TEXT
        cx = x + 20
        pygame.draw.rect(surf, c, (cx - 7, cy - 20, 14, 40), border_radius=4)
        pygame.draw.rect(surf, c, (cx - 20, cy - 7, 40, 14), border_radius=4)
        if name == "DPAD_LR":
            pygame.draw.rect(surf, hi, (cx - 20, cy - 7, 11, 14), border_radius=4)
            pygame.draw.rect(surf, hi, (cx + 9, cy - 7, 11, 14), border_radius=4)
        elif name == "DPAD_UD":
            pygame.draw.rect(surf, hi, (cx - 7, cy - 20, 14, 11), border_radius=4)
            pygame.draw.rect(surf, hi, (cx - 7, cy + 9, 14, 11), border_radius=4)
        return x + 40
    return text(surf, name, (x, cy), 27, DIM, "bold", anchor="midleft").right


class Anim:
    """Critically-damped-ish follower for smooth highlight / scroll motion."""

    def __init__(self, v: float = 0.0) -> None:
        self.v = v

    def to(self, target: float, k: float = 0.32) -> float:
        self.v = target if abs(target - self.v) < 0.5 else lerp(self.v, target, k)
        return self.v


class RectAnim:
    def __init__(self) -> None:
        self.r: list[float] | None = None

    def to(self, target: pygame.Rect, k: float = 0.3) -> pygame.Rect:
        t = [target.x, target.y, target.w, target.h]
        if self.r is None:
            self.r = t
        else:
            self.r = [b if abs(b - a) < 0.5 else lerp(a, b, k) for a, b in zip(self.r, t)]
        return pygame.Rect(*[round(v) for v in self.r])


def focus_ring(surf, rect: pygame.Rect, radius=22) -> None:
    glow = rect.inflate(14, 14)
    pygame.draw.rect(surf, mix(ACCENT, BG_TOP, .7), glow, width=5, border_radius=radius + 7)
    pygame.draw.rect(surf, ACCENT, rect.inflate(3, 3), width=4, border_radius=radius + 2)


# =========================================================================== list widget

@dataclass
class Row:
    label: str
    value: str = ""
    sub: str = ""
    color: tuple = TEXT
    data: Any = None
    enabled: bool = True


class ListView:
    def __init__(self, row_h: int = 96, gap: int = 11) -> None:
        self.rows: list[Row] = []
        self.sel = 0
        self.row_h, self.gap = row_h, gap
        self.scroll = Anim()
        self.hl = Anim()
        self.rects: list[tuple[pygame.Rect, int]] = []
        self._top = 0

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self.sel = max(0, min(self.sel, len(rows) - 1))

    @property
    def current(self) -> Row | None:
        return self.rows[self.sel] if self.rows else None

    def move(self, d: int) -> None:
        if not self.rows:
            return
        n = len(self.rows)
        self.sel = (self.sel + d) % n if abs(d) == 1 else max(0, min(n - 1, self.sel + d))

    def draw(self, surf, area: pygame.Rect, render: Callable[..., None] | None = None) -> None:
        render = render or default_row
        step = self.row_h + self.gap
        visible = max(1, (area.h + self.gap) // step)
        if self.sel < self._top:
            self._top = self.sel
        elif self.sel >= self._top + visible:
            self._top = self.sel - visible + 1
        self._top = max(0, min(self._top, max(0, len(self.rows) - visible)))
        off = self.scroll.to(self._top * step)
        hl_y = self.hl.to(self.sel * step)
        clip = surf.get_clip()
        surf.set_clip(area.inflate(22, 16))
        self.rects = []
        first = max(0, int(off // step) - 1)
        for i in range(first, min(len(self.rows), first + visible + 3)):
            r = pygame.Rect(area.x, area.y + i * step - off, area.w, self.row_h)
            if r.bottom < area.y - step or r.top > area.bottom + step:
                continue
            rrect(surf, r, CARD, 18)
        hr = pygame.Rect(area.x, area.y + hl_y - off, area.w, self.row_h)
        if self.rows:
            rrect(surf, hr, CARD_HI, 18)
        for i in range(first, min(len(self.rows), first + visible + 3)):
            r = pygame.Rect(area.x, area.y + i * step - off, area.w, self.row_h)
            if r.bottom < area.y - step or r.top > area.bottom + step:
                continue
            if area.y - 6 <= r.y and r.bottom <= area.bottom + 6:
                self.rects.append((r, i))
            render(surf, r, self.rows[i], i == self.sel)
        if self.rows:
            focus_ring(surf, hr, 18)
        surf.set_clip(clip)
        if len(self.rows) > visible:
            track = pygame.Rect(area.right + 14, area.y, 7, area.h)
            rrect(surf, track, CARD, 4)
            bar_h = max(48, area.h * visible // len(self.rows))
            y = area.y + (area.h - bar_h) * off / max(1, (len(self.rows) - visible) * step)
            rrect(surf, (track.x, y, 7, bar_h), LINE, 4)

    def hit(self, pos) -> int | None:
        for r, i in self.rects:
            if r.collidepoint(pos):
                return i
        return None


def default_row(surf, r: pygame.Rect, row: Row, selected: bool) -> None:
    col = row.color if row.enabled else FAINT
    if row.sub:
        text(surf, row.label, (r.x + 30, r.y + 14), 38, col, "bold", maxw=r.w * 2 // 3)
        text(surf, row.sub, (r.x + 30, r.bottom - 14), 27, DIM if row.enabled else FAINT, anchor="bottomleft",
             maxw=r.w - 80)
    else:
        text(surf, row.label, (r.x + 30, r.centery), 38, col, "bold", anchor="midleft", maxw=r.w * 2 // 3)
    if row.value:
        text(surf, row.value, (r.right - 30, r.centery), 38, ACCENT if selected else TEXT, "bold",
             anchor="midright", maxw=r.w // 3)


# =========================================================================== screen base

class Screen:
    title = ""

    def __init__(self, app: "App") -> None:
        self.app = app

    def on_enter(self) -> None: ...
    def handle(self, btn: str) -> None: ...
    def click(self, pos) -> None: ...
    def draw(self, surf) -> None: ...

    def hint_items(self) -> list[tuple[str, str]]:
        return [("A", "Select"), ("B", "Back")]


def pending_hint(app: "App") -> list[tuple[str, str]]:
    return [("START", f"Save {app.pending.count()}")] if not app.pending.empty() else []


# =========================================================================== save picker

class SavePicker(Screen):
    title = "Saves"

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.list = ListView(row_h=150, gap=16)

    def on_enter(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        rows = []
        for p in sf.find_saves():
            try:
                snap = sf.snapshot(p)
                rows.append(Row(p.stem, data=(p, snap)))
            except Exception as e:  # noqa: BLE001
                rows.append(Row(p.stem, sub=f"unreadable: {e}", data=(p, None), enabled=False))
        self.list.set_rows(rows)

    def handle(self, btn: str) -> None:
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn == A and self.list.current and self.list.current.enabled:
            self.app.open_save(self.list.current.data[0])
        elif btn == Y:
            self.refresh()
            self.app.toast("Rescanned")
        elif btn == B:
            self.app.running = False

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(A)
            self.list.sel = i

    @staticmethod
    def render_row(surf, r, row: Row, selected: bool) -> None:
        p, snap = row.data
        icon_cat(surf, r.x + 76, r.centery + 5, 40, ACCENT if selected else LINE, CARD_HI if selected else CARD)
        x = r.x + 150
        text(surf, row.label, (x, r.y + 22), 46, TEXT, "black")
        owner = sf.save_label(p).split("(")[-1].rstrip(")")
        when = time.strftime("%b %d, %H:%M", time.localtime(p.stat().st_mtime))
        text(surf, f"Steam {owner}  ·  saved {when}", (x, r.y + 82), 27, DIM)
        if snap is None:
            text(surf, row.sub, (x, r.bottom - 18), 27, BAD, anchor="bottomleft")
            return
        b = snap.parsed["basic"]
        stats = [("DAY", b["current_day"]), ("GOLD", b["house_gold"]), ("CATS", snap.parsed["total_cats"])]
        sx = r.right - 405
        for label, val in stats:
            text(surf, str(val), (sx, r.y + 30), 40, TEXT, "black", anchor="midtop")
            text(surf, label, (sx, r.y + 86), 22, FAINT, "bold", anchor="midtop")
            sx += 124
        st = snap.steven
        pill(surf, "STEVEN ACTIVE" if st.active else "STEVEN CLEAR", r.right - 475, r.centery, BAD if st.active else GOOD,
             anchor="midright", size=24)

    def draw(self, surf) -> None:
        text(surf, "Pick a campaign", (PAD, HEADER_H + 30), 54, TEXT, "black")
        text(surf, "Read straight from your Proton prefix. Nothing is copied.", (PAD, HEADER_H + 100), 30, DIM)
        area = pygame.Rect(PAD, HEADER_H + 162, W - 2 * PAD, CONTENT_BOTTOM - HEADER_H - 162)
        if self.list.rows:
            self.list.draw(surf, area, self.render_row)
            return
        card = pygame.Rect(PAD, area.y, area.w, 510)
        rrect(surf, card, CARD, 24)
        icon_cat(surf, card.x + 120, card.y + 135, 68, LINE, CARD)
        text(surf, "No Mewgenics saves found", (card.x + 230, card.y + 72), 46, TEXT, "black")
        text(surf, "Start Mewgenics once so Proton creates its prefix, then press Y to rescan.",
             (card.x + 230, card.y + 140), 30, DIM)
        y = card.y + 230
        text(surf, "SEARCHED", (card.x + 54, y), 22, FAINT, "bold")
        for root in sf.candidate_save_roots()[:6]:
            y += 40
            text(surf, str(root), (card.x + 54, y), 24, DIM, maxw=card.w - 108)

    def hint_items(self):
        return [("A", "Open"), ("Y", "Rescan"), ("B", "Quit")]


# =========================================================================== hub + tabs

class Tab:
    name = ""

    def __init__(self, app: "App") -> None:
        self.app = app

    def on_enter(self) -> None: ...
    def handle(self, btn: str) -> bool: return False
    def click(self, pos) -> None: ...
    def draw(self, surf, area: pygame.Rect) -> None: ...
    def hint_items(self) -> list[tuple[str, str]]: return []


class Hub(Screen):
    title = "Save"

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.tabs: list[Tab] = [HomeTab(app), CatsTab(app), ItemsTab(app), BackupsTab(app)]
        self.i = 0
        self.ul = RectAnim()
        self.tab_rects: list[pygame.Rect] = []

    @property
    def tab(self) -> Tab:
        return self.tabs[self.i]

    def on_enter(self) -> None:
        for t in self.tabs:
            t.on_enter()

    def handle(self, btn: str) -> None:
        if btn in (LB, RB):
            self.i = (self.i + (1 if btn == RB else -1)) % len(self.tabs)
            self.tab.on_enter()
            return
        if self.tab.handle(btn):
            return
        if btn == START:
            self.app.write()
        elif btn == Y:
            self.app.reload()
            self.app.toast("Reloaded from disk")
        elif btn == B:
            if not self.app.pending.empty():
                self.app.confirm("Discard unsaved changes?", f"{self.app.pending.count()} pending edit(s) will be lost.",
                                 self._leave, danger=True, yes="Discard")
            else:
                self._leave()

    def _leave(self) -> None:
        self.app.pending = sf.Changes()
        self.app.pop()
        self.app.top().on_enter()

    def click(self, pos) -> None:
        for i, r in enumerate(self.tab_rects):
            if r.collidepoint(pos):
                self.i = i
                self.tab.on_enter()
                return
        self.tab.click(pos)

    def draw(self, surf) -> None:
        # tab bar
        y = HEADER_H
        x = PAD
        self.tab_rects = []
        glyph(surf, "LB", x, y + TABS_H // 2)
        x += 78
        target = None
        for i, t in enumerate(self.tabs):
            label = t.name
            badge = ""
            if isinstance(t, CatsTab):
                badge = str(self.app.snap.parsed["total_cats"])
            if isinstance(t, ItemsTab) and self.app.pending.add_items:
                badge = f"+{len(self.app.pending.add_items)}"
            sel = i == self.i
            img = F(32, "black" if sel else "bold").render(label, True, TEXT if sel else DIM)
            r = pygame.Rect(x, y + 10, img.get_width() + 48 + (46 if badge else 0), TABS_H - 20)
            self.tab_rects.append(r)
            if sel:
                target = r
            surf.blit(img, img.get_rect(midleft=(r.x + 24, r.centery)))
            if badge:
                pill(surf, badge, r.right - 18, r.centery, ACCENT if sel else LINE, DARK if sel else TEXT, 20,
                     anchor="midright")
            x = r.right + 8
        glyph(surf, "RB", x + 8, y + TABS_H // 2)
        if target:
            u = self.ul.to(target, 0.35)
            rrect(surf, (u.x + 16, u.bottom - 3, u.w - 32, 5), ACCENT, 3)
        pygame.draw.line(surf, CARD, (0, y + TABS_H), (W, y + TABS_H), 3)
        area = pygame.Rect(PAD, CONTENT_TOP, W - 2 * PAD, CONTENT_BOTTOM - CONTENT_TOP)
        self.tab.draw(surf, area)

    def hint_items(self):
        return self.tab.hint_items() + [("LBRB", "Tabs")] + pending_hint(self.app)


# --------------------------------------------------------------------------- home

class HomeTab(Tab):
    name = "Home"
    ITEMS = ["reset", "wipe", "restart", "guard", "gold", "food", "write"]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.focus = 0
        self.ring = RectAnim()
        self.rects: dict[str, pygame.Rect] = {}
        self.arrow_rects: dict[tuple[str, int], pygame.Rect] = {}

    def _val(self, key: str) -> int:
        return self.app.pending.props.get(key, self.app.snap.parsed["basic"][key])

    def _adjust(self, item: str, delta: int) -> None:
        key = {"gold": "house_gold", "food": "house_food"}[item]
        base = self.app.snap.parsed["basic"][key]
        new = max(0, min(9_999_999, self._val(key) + delta))
        if new == base:
            self.app.pending.props.pop(key, None)
        else:
            self.app.pending.props[key] = new

    def handle(self, btn: str) -> bool:
        item = self.ITEMS[self.focus]
        if btn in (UP, DOWN):
            self.focus = (self.focus + (-1 if btn == UP else 1)) % len(self.ITEMS)
            return True
        if item in ("gold", "food") and btn in (LEFT, RIGHT):
            self._adjust(item, (10 if btn == RIGHT else -10) * self.app.input.mult(btn))
            return True
        if item in ("gold", "food") and btn == X:
            self._adjust(item, 99_999 - self._val("house_gold" if item == "gold" else "house_food"))
            return True
        if btn == A:
            self.activate(item)
            return True
        return False

    def activate(self, item: str) -> None:
        app = self.app
        if item == "reset":
            app.confirm("Reset Steven's penalty?", "Sets savescumlocation to 0 and keeps 1 strike. A backup is made first.",
                        lambda: app.write(steven="cap"), yes="Reset")
        elif item == "wipe":
            app.confirm("Reset and wipe all strikes?", "Also deletes Steven's whole strike history. A backup is made first.",
                        lambda: app.write(steven="clear"), yes="Wipe", danger=True)
        elif item == "restart":
            app.confirm("Close, reset & relaunch?", "Closes Mewgenics, resets Steven, then starts the game through Steam.",
                        app.restart_cycle, yes="Do it")
        elif item == "guard":
            on = not app.snap.steven.guarded
            if on:
                app.confirm("Auto-block Steven?", "Adds a guard to this save so the game can never arm the save-scum "
                            "penalty again. Also resets it now. Turn it off here any time.",
                            lambda: app.write(steven="cap", guard=True), yes="Block")
            else:
                app.confirm("Turn off auto-block?", "Removes MewDeck's guard; Steven works normally again.",
                            lambda: app.write(guard=False), yes="Turn off")
        elif item == "write":
            app.write()

    def click(self, pos) -> None:
        for (item, d), r in self.arrow_rects.items():
            if r.collidepoint(pos):
                self.focus = self.ITEMS.index(item)
                self._adjust(item, d)
                return
        for item, r in self.rects.items():
            if r.collidepoint(pos):
                if self.ITEMS[self.focus] == item:
                    self.activate(item)
                self.focus = self.ITEMS.index(item)

    def draw(self, surf, area) -> None:
        snap = self.app.snap
        st = snap.steven
        basic = snap.parsed["basic"]
        pend = self.app.pending
        self.rects, self.arrow_rects = {}, {}

        # ---- Steven hero card
        hero = pygame.Rect(area.x, area.y, 1026, area.h)
        rrect(surf, hero, CARD, 26)
        band = pygame.Rect(hero.x, hero.y, hero.w, 202)
        tint = pygame.Surface(band.size, pygame.SRCALPHA)
        tint.fill((*(BAD if st.active else GOOD), 28))
        surf.blit(tint, band.topleft)
        text(surf, "SAVE-SCUM WATCH", (hero.x + 44, hero.y + 32), 22, FAINT, "black")
        icon_status(surf, hero.x + 94, hero.y + 124, 46, not st.active)
        text(surf, f"Penalty armed ({st.where})" if st.active else "All clear", (hero.x + 168, hero.y + 70), 50,
             BAD if st.active else GOOD, "black", maxw=hero.w - 400)
        sub = [f"{st.strikes} strike{'s' if st.strikes != 1 else ''} on record"]
        if st.guarded:
            sub.append("auto-block on")
        if st.met:
            sub.append("Steven met")
        if st.on_adventure:
            sub.append("on an adventure")
        text(surf, "  ·  ".join(sub), (hero.x + 170, hero.y + 140), 27, DIM)
        pips(surf, hero.right - 44 - max(5, st.strikes) * 31 + 7, hero.y + 46, st.strikes, max(5, st.strikes), BAD,
             24, 24, 7, mix(LINE, CARD, .4))

        btns = [("reset", "Reset penalty", "savescumlocation = 0, keep 1 strike", True),
                ("wipe", "Reset + wipe strikes", "Delete the whole strike history", False),
                ("restart", "Close game, reset & relaunch", "One button: kill, reset, start via Steam", False),
                ("guard", "Auto-block Steven", "Game can't arm the penalty anymore (save trigger)", False)]
        by = hero.y + 230
        for key, label, desc, primary in btns:
            r = pygame.Rect(hero.x + 38, by, hero.w - 76, 102)
            self.rects[key] = r
            col = (BAD if st.active else mix(GOOD, CARD, .35)) if primary else CARD_HI
            rrect(surf, r, col, 22)
            fg = DARK if primary and st.active else TEXT
            text(surf, label, (r.x + 32, r.y + 15), 36, fg, "black")
            text(surf, desc, (r.x + 32, r.bottom - 15), 24, mix(fg, col, .3), anchor="bottomleft")
            if key == "guard":
                pill(surf, "ON" if st.guarded else "OFF", r.right - 36, r.centery, GOOD if st.guarded else LINE,
                     DARK if st.guarded else TEXT, 24, anchor="midright")
            else:
                tri(surf, fg, (r.right - 40, r.centery), 12, "right")
            by += 118

        # ---- right column
        col_x = hero.right + 32
        col_w = area.right - col_x
        y = area.y
        for item, key, label in (("gold", "house_gold", "Gold"), ("food", "house_food", "Food")):
            r = pygame.Rect(col_x, y, col_w, 160)
            self.rects[item] = r
            rrect(surf, r, CARD, 24)
            (icon_coin if item == "gold" else icon_food)(surf, r.x + 62, r.y + 62, 30)
            text(surf, label.upper(), (r.x + 110, r.y + 30), 22, FAINT, "black")
            val = self._val(key)
            changed = val != basic[key]
            text(surf, f"{val:,}", (r.x + 110, r.y + 52), 54, ACCENT if changed else TEXT, "black")
            if changed:
                text(surf, f"was {basic[key]:,}", (r.x + 110, r.bottom - 16), 23, DIM, anchor="bottomleft")
            focused = self.ITEMS[self.focus] == item
            for d, direction, cx in ((-10, "left", r.right - 124), (10, "right", r.right - 54)):
                ar = pygame.Rect(0, 0, 54, 54)
                ar.center = (cx, r.centery)
                self.arrow_rects[(item, d)] = ar
                rrect(surf, ar, CARD_HI if focused else mix(CARD, CARD_HI, .4), 16)
                tri(surf, TEXT if focused else DIM, ar.center, 11, direction)
            y = r.bottom + 18

        info = pygame.Rect(col_x, y, col_w, 148)
        rrect(surf, info, mix(CARD, BG_BOT, .4), 24)
        for i, (lab, val) in enumerate((("DAY", basic["current_day"]), ("CATS", snap.parsed["total_cats"]),
                                        ("DONE", f"{basic['save_percent']}%"))):
            cx = info.x + col_w * (i * 2 + 1) // 6
            text(surf, str(val), (cx, info.y + 35), 40, TEXT, "black", anchor="midtop")
            text(surf, lab, (cx, info.y + 92), 20, FAINT, "black", anchor="midtop")
        y = info.bottom + 18

        wr = pygame.Rect(col_x, y, col_w, area.bottom - y)
        self.rects["write"] = wr
        has = not pend.empty()
        rrect(surf, wr, mix(ACCENT, CARD, .1) if has else CARD, 24)
        fg = DARK if has else FAINT
        text(surf, f"Save {pend.count()} change{'s' if pend.count() != 1 else ''}" if has else "No pending changes",
             (wr.x + 32, wr.y + 24), 38, fg, "black")
        lines = []
        for key, label in (("house_gold", "Gold"), ("house_food", "Food")):
            if key in pend.props:
                lines.append(f"{label} {basic[key]:,} » {pend.props[key]:,}")
        if pend.cats:
            lines.append(f"{len(pend.cats)} cat{'s' if len(pend.cats) != 1 else ''} edited")
        if pend.add_items:
            lines.append(f"{len(pend.add_items)} item{'s' if len(pend.add_items) != 1 else ''} to storage")
        if not lines:
            lines = ["Edits wait here until you save.", "Every save makes a backup first."]
        ly = wr.y + 78
        for ln in lines[:4]:
            if ly + 30 > wr.bottom - 10:
                break
            text(surf, ln, (wr.x + 32, ly), 26, mix(fg, DARK, .15) if has else DIM, maxw=wr.w - 64)
            ly += 34

        target = self.rects[self.ITEMS[self.focus]]
        focus_ring(surf, self.ring.to(target), 22 if self.ITEMS[self.focus] in ("reset", "wipe", "restart") else 24)

    def hint_items(self):
        item = self.ITEMS[self.focus]
        if item in ("gold", "food"):
            return [("DPAD_LR", "±10 (hold = faster)"), ("X", "99,999"), ("B", "Saves")]
        return [("A", "Select"), ("Y", "Reload"), ("B", "Saves")]


# --------------------------------------------------------------------------- cats

class CatsTab(Tab):
    name = "Cats"
    FILTERS = [("All", lambda c: True),
               ("House", lambda c: not (c["dead"] or c["donated"]) and c["room"] not in ("", "(ADVENTURE)")),
               ("Adventure", lambda c: c["room"] == "(ADVENTURE)"),
               ("Retired", lambda c: c["retired"] and not (c["dead"] or c["donated"])),
               ("Gone", lambda c: c["dead"] or c["donated"])]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.list = ListView(row_h=118, gap=11)
        self.f = 0
        self.chip_rects: list[pygame.Rect] = []

    def on_enter(self) -> None:
        cats = sorted(self.app.snap.parsed["cats"],
                      key=lambda c: (c["dead"] or c["donated"], c["room"] == "(ADVENTURE)", c["name"].lower()))
        pred = self.FILTERS[self.f][1]
        self.list.set_rows([Row(c["name"] or f"#{c['key']}", data=c) for c in cats if pred(c)])

    def handle(self, btn: str) -> bool:
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn in (LEFT, RIGHT):
            self.f = (self.f + (1 if btn == RIGHT else -1)) % len(self.FILTERS)
            self.list.sel = 0
            self.on_enter()
        elif btn == A and self.list.current:
            self.app.push(CatEdit(self.app, [r.data for r in self.list.rows], self.list.sel))
        else:
            return False
        return True

    def click(self, pos) -> None:
        for i, r in enumerate(self.chip_rects):
            if r.collidepoint(pos):
                self.f = i
                self.list.sel = 0
                self.on_enter()
                return
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(A)
            self.list.sel = i

    def render_row(self, surf, r, row: Row, selected: bool) -> None:
        c = row.data
        gone = c["dead"] or c["donated"]
        ccol = CLASS_COL.get(c["class"], (150, 142, 164))
        rrect(surf, (r.x, r.y, 11, r.h), ccol if not gone else LINE, 5)
        # sex badge
        scol = SEX_COL.get(c["sex"], DIM)
        pygame.draw.circle(surf, mix(scol, CARD, .25), (r.x + 62, r.centery), 30)
        text(surf, {"Male": "M", "Female": "F"}.get(c["sex"], "?"), (r.x + 62, r.centery), 30, DARK, "black",
             anchor="center")
        edited = c["key"] in self.app.pending.cats
        name_r = text(surf, row.label, (r.x + 114, r.y + 16), 38, DIM if gone else TEXT, "black", maxw=420)
        if edited:
            pill(surf, "EDITED", name_r.right + 14, name_r.centery, ACCENT, size=19, anchor="midleft")
        lvl = c["level"] if 0 <= c["level"] <= 99 else "?"
        room = "on adventure" if c["room"] == "(ADVENTURE)" else (c["room"] or "-").replace("_", " ")
        flags = [f for f, on in (("retired", c["retired"]), ("dead", c["dead"]), ("donated", c["donated"])) if on]
        text(surf, f"{c['class']} · Lv {lvl} · {c['age']}d · {room}" + (f" · {', '.join(flags)}" if flags else ""),
             (r.x + 114, r.bottom - 18), 26, DIM, anchor="bottomleft", maxw=760)
        # stat bars
        x0 = r.right - 40 - 7 * 46
        for i, s in enumerate(STATS):
            v = c["stats"][s]
            bx = x0 + i * 46
            bh = 60
            rrect(surf, (bx, r.y + 18, 24, bh), mix(LINE, CARD, .4), 7)
            fh = round(bh * max(0, min(7, v)) / 7)
            rrect(surf, (bx, r.y + 18 + bh - fh, 24, fh), stat_col(v) if not gone else LINE, 7)
            text(surf, s[0] if s != "LUCK" else "L", (bx + 12, r.bottom - 10), 19, FAINT, "black", anchor="midbottom")

    def draw(self, surf, area) -> None:
        x = area.x
        self.chip_rects = []
        cats = self.app.snap.parsed["cats"]
        for i, (name, pred) in enumerate(self.FILTERS):
            sel = i == self.f
            n = sum(1 for c in cats if pred(c))
            r = pill(surf, f"{name}  {n}", x, area.y, ACCENT if sel else CARD, DARK if sel else DIM, 26)
            self.chip_rects.append(r)
            x = r.right + 11
        glyph(surf, "DPAD_LR", x + 11, self.chip_rects[0].centery)
        la = pygame.Rect(area.x, area.y + 68, area.w - 20, area.h - 68)
        if not self.list.rows:
            text(surf, "No cats in this filter", (la.centerx, la.y + 108), 35, FAINT, "bold", anchor="midtop")
            return
        self.list.draw(surf, la, self.render_row)

    def hint_items(self):
        return [("A", "Edit cat"), ("DPAD_LR", "Filter"), ("B", "Saves")]


class CatEdit(Screen):
    SEXES = ["Male", "Female", "Ditto"]

    def __init__(self, app: "App", cats: list[dict[str, Any]], index: int) -> None:
        super().__init__(app)
        self.cats, self.idx = cats, index
        self.list = ListView(row_h=80, gap=8)
        self.load()

    @property
    def cat(self) -> dict[str, Any]:
        return self.cats[self.idx]

    def load(self) -> None:
        cat = self.cat
        prev = self.app.pending.cats.get(cat["key"], {})
        self.vals = {"stats": dict(prev.get("stats", cat["stats"])), "level": prev.get("level", cat["level"]),
                     "age": prev.get("age", cat["age"]), "sex": prev.get("sex", cat["sex"]),
                     "retired": prev.get("retired", cat["retired"])}
        self.title = cat["name"] or f"Cat #{cat['key']}"
        self.ok = sf.cat_editable(self.app.snap, cat)
        self.build()

    def on_enter(self) -> None:
        self.build()

    def build(self) -> None:
        v, c, ok = self.vals, self.cat, self.ok
        rows = [Row(s, data=("stat", s), enabled=ok["stats"]) for s in STATS]
        rows += [Row("Level", data=("level",), enabled=ok["level"]),
                 Row("Age", data=("age",), enabled=ok["age"]),
                 Row("Sex", data=("sex",)),
                 Row("Retired", data=("retired",)),
                 Row("Max all stats", data=("max",), enabled=ok["stats"]),
                 Row("Revert cat", data=("revert",))]
        self.list.set_rows(rows)

    def handle(self, btn: str) -> None:
        row = self.list.current
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn in (LB, RB):
            self.idx = (self.idx + (1 if btn == RB else -1)) % len(self.cats)
            self.load()
        elif btn in (LEFT, RIGHT) and row:
            if not row.enabled:
                return
            d = (1 if btn == RIGHT else -1)
            kind = row.data[0]
            if kind == "stat":
                s = row.data[1]
                self.vals["stats"][s] = max(1, min(7, self.vals["stats"][s] + d))
            elif kind == "level":
                self.vals["level"] = max(0, min(99, self.vals["level"] + d))
            elif kind == "age":
                day = self.app.snap.parsed["basic"]["current_day"]
                self.vals["age"] = max(0, min(day, self.vals["age"] + d * self.app.input.mult(btn)))
            elif kind == "sex":
                i = self.SEXES.index(self.vals["sex"]) if self.vals["sex"] in self.SEXES else 0
                self.vals["sex"] = self.SEXES[(i + d) % 3]
            elif kind == "retired":
                self.vals["retired"] = not self.vals["retired"]
            else:
                return
            self.commit_local()
        elif btn == A and row and row.enabled:
            kind = row.data[0]
            if kind == "max":
                self.vals["stats"] = {s: 7 for s in STATS}
                self.commit_local()
            elif kind == "revert":
                c = self.cat
                self.vals = {"stats": dict(c["stats"]), "level": c["level"], "age": c["age"], "sex": c["sex"],
                             "retired": c["retired"]}
                self.commit_local()
            elif kind in ("sex", "retired"):
                self.handle(RIGHT)
        elif btn == START:
            self.app.write()
        elif btn == B:
            self.app.pop()
            self.app.top().on_enter()

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(A)
            self.list.sel = i

    def commit_local(self) -> None:
        c = self.cat
        ch: dict[str, Any] = {}
        if self.vals["stats"] != c["stats"]:
            ch["stats"] = dict(self.vals["stats"])
        for k in ("level", "age", "sex", "retired"):
            if self.vals[k] != c[k]:
                ch[k] = self.vals[k]
        if ch:
            self.app.pending.cats[c["key"]] = {**sf.cat_change_base(self.app.snap, c), **ch}
        else:
            self.app.pending.cats.pop(c["key"], None)

    def render_row(self, surf, r, row: Row, selected: bool) -> None:
        v, c = self.vals, self.cat
        kind = row.data[0]
        col = TEXT if row.enabled else FAINT
        text(surf, row.label, (r.x + 30, r.centery), 32, col, "black", anchor="midleft")
        ccol = CLASS_COL.get(c["class"], ACCENT)
        if kind == "stat":
            s = row.data[1]
            new, old = v["stats"][s], c["stats"][s]
            x = r.x + 190
            for i in range(7):
                filled = i < new
                was = i < old
                colr = stat_col(new) if filled and was else ACCENT if filled else mix(BAD, CARD, .4) if was else mix(LINE, CARD, .3)
                rrect(surf, (x + i * 54, r.centery - 15, 46, 30), colr, 8)
            text(surf, str(new), (r.right - 80, r.centery), 38, ACCENT if new != old else TEXT, "black", anchor="center")
        elif kind in ("level", "age", "sex", "retired"):
            val = v[kind]
            shown = ("yes" if val else "no") if kind == "retired" else (f"{val} days" if kind == "age" else str(val))
            if kind == "level" and not (0 <= val <= 99):
                shown = "?"
            changed = val != c[kind]
            if not row.enabled:
                text(surf, "locked · field not found safely", (r.x + 190, r.centery), 26, FAINT, anchor="midleft")
            else:
                tri(surf, DIM if selected else LINE, (r.right - 202, r.centery), 11, "left")
                tri(surf, DIM if selected else LINE, (r.right - 40, r.centery), 11, "right")
            text(surf, shown, (r.right - 121, r.centery), 35, ACCENT if changed else col, "black", anchor="center")
            if changed:
                was = c[kind]
                was_s = ("yes" if was else "no") if kind == "retired" else str(was)
                text(surf, f"was {was_s}", (r.x + 190, r.centery), 24, DIM, anchor="midleft")
        else:
            text(surf, "A", (r.right - 54, r.centery), 27, DIM, "black", anchor="center")

    def draw(self, surf) -> None:
        c = self.cat
        ccol = CLASS_COL.get(c["class"], ACCENT)
        # side card
        card = pygame.Rect(W - PAD - 540, HEADER_H + 28, 540, CONTENT_BOTTOM - HEADER_H - 28)
        rrect(surf, card, CARD, 26)
        band = pygame.Rect(card.x, card.y, card.w, 202)
        tint = pygame.Surface(band.size, pygame.SRCALPHA)
        tint.fill((*ccol, 40))
        surf.blit(tint, band.topleft)
        icon_cat(surf, card.x + 94, card.y + 106, 54, ccol, CARD)
        text(surf, c["name"] or f"#{c['key']}", (card.x + 176, card.y + 48), 42, TEXT, "black", maxw=card.w - 200)
        pill(surf, c["class"], card.x + 176, card.y + 116, ccol, size=23)
        y = card.y + 230
        room = "On an adventure" if c["room"] == "(ADVENTURE)" else (c["room"] or "-").replace("_", " ")
        status = ", ".join(f for f, on in (("dead", c["dead"]), ("donated", c["donated"]), ("old", c["isOld"])) if on)
        for lab, val in (("ROOM", room), ("STATUS", status or "healthy"), ("CAT", f"{self.idx + 1} of {len(self.cats)}")):
            text(surf, lab, (card.x + 38, y), 20, FAINT, "black")
            text(surf, val, (card.x + 38, y + 26), 32, TEXT, "bold", maxw=card.w - 76)
            y += 86
        cur = self.list.current
        if cur and cur.data[0] == "stat":
            info = pygame.Rect(card.x + 22, card.bottom - 152, card.w - 44, 130)
            rrect(surf, info, CARD_HI, 18)
            s = cur.data[1]
            text(surf, s, (info.x + 22, info.y + 16), 24, stat_col(self.vals["stats"][s]), "black")
            words, lines, ln = STAT_INFO[s].split(), [], ""
            for w in words:
                if F(26).size((ln + " " + w).strip())[0] > info.w - 44:
                    lines.append(ln)
                    ln = w
                else:
                    ln = (ln + " " + w).strip()
            lines.append(ln)
            for i, l in enumerate(lines[:2]):
                text(surf, l, (info.x + 22, info.y + 54 + i * 32), 26, DIM)
        text(surf, "ABILITIES", (card.x + 38, y), 20, FAINT, "black")
        y += 32
        abil = [a for a in c["abilities"]["active"] + c["abilities"]["passive"] if a and a != "DefaultMove"]
        for a in abil[:6]:
            if y > card.bottom - (188 if cur and cur.data[0] == "stat" else 46):
                break
            text(surf, a, (card.x + 38, y), 27, DIM, maxw=card.w - 76)
            y += 36
        # stats list
        area = pygame.Rect(PAD, HEADER_H + 28, card.x - PAD - 40, CONTENT_BOTTOM - HEADER_H - 28)
        self.list.draw(surf, area, self.render_row)
        # prev/next affordance
        glyph(surf, "LB", card.x + card.w - 136, card.y + 32)
        glyph(surf, "RB", card.x + card.w - 74, card.y + 32)

    def hint_items(self):
        return [("DPAD_LR", "Adjust"), ("LBRB", "Prev/next cat"), ("B", "Done")] + pending_hint(self.app)


# --------------------------------------------------------------------------- items

class ItemsTab(Tab):
    name = "Items"

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        data = json.loads((Path(__file__).parent / "gamedata/items/items.json").read_text(encoding="utf-8"))
        self.items = sorted(data.values(), key=lambda i: (i.get("name") or i["id"]).lower())
        kinds = sorted({i.get("kind") or "other" for i in self.items})
        self.tabs = ["all"] + kinds
        self.t = 0
        self.list = ListView(row_h=84, gap=8)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []

    def on_enter(self) -> None:
        kind = self.tabs[self.t]
        self.list.set_rows([Row(it.get("name") or it["id"], data=it) for it in self.items
                            if kind == "all" or (it.get("kind") or "other") == kind])

    def handle(self, btn: str) -> bool:
        q = self.app.pending.add_items
        if btn in (UP, DOWN):
            self.list.move((-1 if btn == UP else 1) * (1 if self.app.input.count.get(btn, 0) < 14 else 4))
        elif btn in (LEFT, RIGHT):
            self.t = (self.t + (1 if btn == RIGHT else -1)) % len(self.tabs)
            self.list.sel = 0
            self.on_enter()
        elif btn == A and self.list.current:
            q.append(self.list.current.data["id"])
        elif btn == X and self.list.current:
            if self.list.current.data["id"] in q:
                q.remove(self.list.current.data["id"])
        else:
            return False
        return True

    def click(self, pos) -> None:
        for r, i in self.chip_rects:
            if r.collidepoint(pos):
                self.t = i
                self.list.sel = 0
                self.on_enter()
                return
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(A)
            self.list.sel = i

    def render_row(self, surf, r, row: Row, selected: bool) -> None:
        it = row.data
        rc = RARITY_COL.get(it.get("rarity", ""), DIM)
        pygame.draw.circle(surf, rc, (r.x + 40, r.centery), 12)
        text(surf, row.label, (r.x + 74, r.centery), 34, TEXT, "bold", anchor="midleft", maxw=r.w - 380)
        text(surf, f"{(it.get('rarity') or '').replace('_', ' ')} · {it.get('kind') or 'other'}", (r.right - 122, r.centery),
             24, FAINT, anchor="midright")
        n = self.app.pending.add_items.count(it["id"])
        if n:
            pill(surf, f"+{n}", r.right - 28, r.centery, ACCENT, size=27, anchor="midright")

    def draw(self, surf, area) -> None:
        # category chips, scrolled so the selected one is visible
        self.chip_rects = []
        widths = [F(24, "bold").size(t)[0] + 24 + 11 for t in self.tabs]
        start = 0
        while sum(widths[start:self.t + 1]) > area.w - 568 and start < self.t:
            start += 1
        x = area.x
        if start:
            tri(surf, DIM, (x + 8, area.y + 20), 9, "left")
            x += 27
        for i in range(start, len(self.tabs)):
            sel = i == self.t
            r = pill(surf, self.tabs[i].replace("_", " "), x, area.y, ACCENT if sel else CARD, DARK if sel else DIM, 24)
            if r.right > area.right - 540:
                break
            self.chip_rects.append((r, i))
            x = r.right + 11
        la = pygame.Rect(area.x, area.y + 64, area.w - 540, area.h - 64)
        self.list.draw(surf, la, self.render_row)
        # queue panel
        qp = pygame.Rect(area.right - 500, area.y, 500, area.h)
        rrect(surf, qp, CARD, 24)
        text(surf, "GOING TO STORAGE", (qp.x + 32, qp.y + 30), 20, FAINT, "black")
        q = self.app.pending.add_items
        if not q:
            text(surf, "Nothing queued yet", (qp.x + 32, qp.y + 74), 30, DIM, "bold")
            text(surf, "A adds one, X removes one.", (qp.x + 32, qp.y + 116), 26, FAINT)
            text(surf, "Menu saves everything.", (qp.x + 32, qp.y + 152), 26, FAINT)
            return
        counts: dict[str, int] = {}
        for i in q:
            counts[i] = counts.get(i, 0) + 1
        names = {it["id"]: it.get("name") or it["id"] for it in self.items}
        y = qp.y + 74
        for iid, n in list(counts.items())[-12:]:
            text(surf, names.get(iid, iid), (qp.x + 32, y), 28, TEXT, "bold", maxw=qp.w - 135)
            text(surf, f"×{n}", (qp.right - 32, y), 28, ACCENT, "black", anchor="topright")
            y += 42
        text(surf, f"{len(q)} item{'s' if len(q) != 1 else ''} total", (qp.x + 32, qp.bottom - 30), 26, DIM,
             anchor="bottomleft")

    def hint_items(self):
        return [("A", "Add"), ("X", "Remove"), ("DPAD_LR", "Category"), ("B", "Saves")]


# --------------------------------------------------------------------------- backups

class BackupsTab(Tab):
    name = "Backups"

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.list = ListView(row_h=102, gap=11)

    def on_enter(self) -> None:
        self.list.set_rows([Row(b.when.strftime("%b %d"), data=b) for b in sf.list_backups(self.app.save)])

    def handle(self, btn: str) -> bool:
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn == A and self.list.current:
            b: sf.Backup = self.list.current.data
            self.app.confirm("Restore this backup?", f"Backup from {b.when:%b %d, %H:%M}. Your current save is backed "
                             "up first, so this can be undone.", lambda: self.app.restore(b.path), yes="Restore")
        elif btn == X:
            try:
                sf.create_backup(self.app.save, "manual")
                self.app.toast("Backup created", GOOD)
            except Exception as e:  # noqa: BLE001
                self.app.toast(f"Backup failed: {e}", BAD)
            self.on_enter()
        else:
            return False
        return True

    def click(self, pos) -> None:
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(A)
            self.list.sel = i

    @staticmethod
    def render_row(surf, r, row: Row, selected: bool) -> None:
        b: sf.Backup = row.data
        text(surf, b.when.strftime("%a %b %d"), (r.x + 32, r.y + 16), 35, TEXT, "black")
        text(surf, b.when.strftime("%H:%M:%S"), (r.x + 32, r.bottom - 16), 26, DIM, anchor="bottomleft")
        reason = b.reason.replace("before-", "before ").replace("-", " ") or "backup"
        pill(surf, reason, r.x + 350, r.centery, CARD_HI if not selected else LINE, TEXT, 23, anchor="midleft")
        text(surf, f"Day {b.day if b.day is not None else '?'}", (r.right - 284, r.centery), 30, DIM, "bold",
             anchor="midright")
        on = b.penalty not in (None, 0)
        pill(surf, "Steven ON" if on else "clear", r.right - 30, r.centery, BAD if on else mix(GOOD, CARD, .3),
             size=22, anchor="midright")

    def draw(self, surf, area) -> None:
        text(surf, str(sf.backup_dir_for(self.app.save)), (area.x, area.y), 24, FAINT, maxw=area.w)
        la = pygame.Rect(area.x, area.y + 48, area.w - 20, area.h - 48)
        if not self.list.rows:
            text(surf, "No backups yet. One is made automatically before every save.", (la.centerx, la.y + 108),
                 32, DIM, "bold", anchor="midtop")
            return
        self.list.draw(surf, la, self.render_row)

    def hint_items(self):
        return [("A", "Restore"), ("X", "Back up now"), ("B", "Saves")]


# =========================================================================== modal

class Confirm(Screen):
    def __init__(self, app: "App", title: str, body: str, on_yes: Callable[[], None], yes: str = "Yes",
                 danger: bool = False) -> None:
        super().__init__(app)
        self.msg, self.body, self.on_yes, self.yes, self.danger = title, body, on_yes, yes, danger
        self.title = app.top().title
        self.t0 = time.time()
        self.btn_rects: dict[str, pygame.Rect] = {}

    def handle(self, btn: str) -> None:
        if btn == A:
            self.app.pop()
            self.on_yes()
        elif btn == B:
            self.app.pop()

    def click(self, pos) -> None:
        for k, r in self.btn_rects.items():
            if r.collidepoint(pos):
                self.handle(k)

    def draw(self, surf) -> None:
        self.app.stack[-2].draw(surf)
        k = min(1.0, (time.time() - self.t0) / 0.14)
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((8, 5, 12, int(190 * k)))
        surf.blit(shade, (0, 0))
        r = pygame.Rect(0, 0, 1026, 405)
        r.center = (W // 2, H // 2 + int((1 - k) * 40))
        rrect(surf, r, CARD, 32)
        col = BAD if self.danger else ACCENT
        rrect(surf, (r.x, r.y, r.w, 11), col, 5)
        text(surf, self.msg, (r.x + 60, r.y + 54), 48, TEXT, "black", maxw=r.w - 120)
        words, lines, cur = self.body.split(), [], ""
        for w in words:
            if F(30).size((cur + " " + w).strip())[0] > r.w - 120:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        for i, ln in enumerate(lines[:3]):
            text(surf, ln, (r.x + 60, r.y + 135 + i * 42), 30, DIM)
        self.btn_rects = {}
        bx = r.right - 60
        for key, label, bg, fg in (("A", self.yes, col, DARK), ("B", "Cancel", CARD_HI, TEXT)):
            img = F(32, "black").render(label, True, fg)
            br = pygame.Rect(0, 0, img.get_width() + 104, 76)
            br.bottomright = (bx, r.bottom - 44)
            rrect(surf, br, bg, 22)
            glyph(surf, key, br.x + 20, br.centery)
            surf.blit(img, img.get_rect(midleft=(br.x + 74, br.centery)))
            self.btn_rects[key] = br
            bx = br.x - 18

    def hint_items(self):
        return [("A", self.yes), ("B", "Cancel")]


# =========================================================================== app

class App:
    def __init__(self, windowed: bool = False, save: Path | None = None) -> None:
        global F
        for k, v in (("SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD", "1"),
                     ("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")):  # gamescope focus quirks
            os.environ.setdefault(k, v)
        pygame.init()
        pygame.display.set_caption("MewDeck")
        sizes = pygame.display.get_desktop_sizes() or [(W, H)]
        in_gamescope = "GAMESCOPE_WAYLAND_DISPLAY" in os.environ or "SteamAppId" in os.environ
        log(f"pygame {pygame.version.ver} SDL {'.'.join(map(str, pygame.get_sdl_version()))} "
            f"driver={pygame.display.get_driver()} desktop={sizes} "
            f"SteamAppId={os.environ.get('SteamAppId')} gamescope={in_gamescope}")
        dw, dh = sizes[0]
        if windowed:
            win_size = (W, H) if (dw > W and dh > H) else (1280, 720)
            self.window = self._set_mode(win_size, pygame.RESIZABLE)
        elif in_gamescope or (dw >= W and dh >= H):
            self.window = self._set_mode((W, H), pygame.FULLSCREEN)
        else:  # desktop smaller than 1080p (e.g. XWayland with display scaling): borderless desktop, never crop
            self.window = self._set_mode((0, 0), pygame.FULLSCREEN)
        self._sync_surface()
        log(f"canvas {W}x{H} window={self.window.get_size()}")
        pygame.key.set_repeat(340, 55)
        pygame.mouse.set_visible(windowed)
        F = Fonts()
        self.bg = self._make_bg()
        self.input = Input()
        self.clock = pygame.time.Clock()
        self.stack: list[Screen] = []
        self.running = True
        self.save: Path | None = None
        self.snap: sf.Snapshot | None = None
        self.pending = sf.Changes()
        self.toasts: list[tuple[str, tuple, float, float]] = []
        self._game_running = False
        self._last_poll = 0.0
        self.push(SavePicker(self))
        if save:
            self.open_save(save)
        else:
            rows = [r for r in self.stack[0].list.rows if r.enabled]
            if len(rows) == 1:  # only one campaign: skip the picker
                self.open_save(rows[0].data[0])

    @staticmethod
    def _set_mode(size: tuple[int, int], flags: int) -> pygame.Surface:
        try:
            return pygame.display.set_mode(size, flags, vsync=1)
        except pygame.error:
            try:
                return pygame.display.set_mode(size, flags)
            except pygame.error:
                return pygame.display.set_mode((0, 0), flags)

    def _sync_surface(self) -> None:
        self.screen = self.window if self.window.get_size() == (W, H) else pygame.Surface((W, H))

    @staticmethod
    def _make_bg() -> pygame.Surface:
        bg = pygame.Surface((W, H))
        for y in range(H):
            pygame.draw.line(bg, mix(BG_TOP, BG_BOT, y / H), (0, y), (W, y))
        n = 48
        small = pygame.Surface((n, n), pygame.SRCALPHA)
        for yy in range(n):
            for xx in range(n):
                d = math.hypot(xx - n / 2 + .5, yy - n / 2 + .5) / (n / 2)
                small.set_at((xx, yy), (*ACCENT, int(max(0.0, 1 - d) ** 2 * 26)))
        glow = pygame.transform.smoothscale(small, (1750, 1750))
        bg.blit(glow, (W - 216 - 875, -80 - 875))
        return bg

    # navigation
    def push(self, s: Screen) -> None:
        self.stack.append(s)
        s.on_enter()

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()

    def top(self) -> Screen:
        return self.stack[-1]

    def toast(self, msg: str, color=TEXT, secs: float = 3.0) -> None:
        now = time.time()
        self.toasts.append((msg, color, now, now + secs))

    def confirm(self, title: str, body: str, on_yes: Callable[[], None], yes: str = "Yes", danger: bool = False) -> None:
        self.push(Confirm(self, title, body, on_yes, yes, danger))

    # save ops
    def open_save(self, path: Path) -> None:
        try:
            self.snap = sf.snapshot(path)
        except Exception as e:  # noqa: BLE001
            self.toast(f"Could not read save: {e}", BAD, 6)
            return
        self.save = path
        self.pending = sf.Changes()
        self.push(Hub(self))

    def reload(self) -> None:
        assert self.save
        self.snap = sf.snapshot(self.save)
        for s in self.stack:
            if isinstance(s, (Hub, SavePicker)):
                s.on_enter()

    def _after_write(self) -> None:
        while len(self.stack) > 2 and not isinstance(self.top(), Hub):
            self.stack.pop()
        self.reload()

    def write(self, steven: str | None = None, guard: bool | None = None) -> bool:
        assert self.save
        ch = self.pending
        if steven:
            ch.steven = steven
        if guard is not None:
            ch.guard = guard
        if ch.empty():
            self.toast("Nothing to save")
            return False
        try:
            sf.apply_changes(self.save, ch, reason="steven-reset" if steven else "edit")
        except sf.GameRunningError as e:
            ch.steven, ch.guard = None, None
            self.toast(str(e), BAD, 5)
            return False
        except Exception as e:  # noqa: BLE001
            ch.steven, ch.guard = None, None
            traceback.print_exc()
            self.toast(f"Not saved, file untouched: {e}", BAD, 7)
            return False
        n = ch.count()
        self.pending = sf.Changes()
        self._after_write()
        st = self.snap.steven if self.snap else None
        if guard is not None and st is not None:
            self.toast("Auto-block on · Steven can't arm the penalty" if st.guarded else "Auto-block off",
                       GOOD if st.guarded == guard else BAD, 4)
        elif steven and st is not None:
            ok = not st.active
            self.toast("Steven penalty reset" if ok else "Reset written but flag still set?!", GOOD if ok else BAD, 4)
        else:
            self.toast(f"Saved {n} change{'s' if n != 1 else ''} · backup made", GOOD)
        return True

    def restart_cycle(self) -> None:
        if sf.game_running():
            self.toast("Closing Mewgenics…")
            self.render()
            if not sf.close_game():
                self.toast("Could not close the game", BAD, 5)
                return
        if self.write(steven="cap"):
            try:
                sf.launch_game()
                self.toast("Launching Mewgenics…", GOOD)
                self.render()
                time.sleep(1.5)
                self.running = False  # step aside so Steam focuses the game
            except Exception as e:  # noqa: BLE001
                self.toast(str(e), BAD, 5)

    def restore(self, backup: Path) -> None:
        assert self.save
        try:
            sf.restore_backup(self.save, backup)
            self.pending = sf.Changes()
            self._after_write()
            self.toast("Backup restored", GOOD)
        except Exception as e:  # noqa: BLE001
            self.toast(f"Restore failed: {e}", BAD, 6)

    # rendering
    def _header(self, s) -> None:
        icon_cat(s, PAD + 28, HEADER_H // 2 + 4, 30)
        r = text(s, "MewDeck", (PAD + 70, HEADER_H // 2), 46, TEXT, "black", anchor="midleft")
        crumbs = [x.title for x in self.stack[1:] if x.title and not isinstance(x, Confirm)]
        if self.save and crumbs:
            crumbs[0] = self.save.stem
        if crumbs:
            text(s, "  /  ".join(crumbs), (r.right + 30, HEADER_H // 2 + 3), 30, DIM, "bold", anchor="midleft",
                 maxw=760)
        self.quit_rect = pygame.Rect(W - PAD - 60, HEADER_H // 2 - 30, 60, 60)
        qr = self.quit_rect
        rrect(s, qr, CARD, 16)
        for d in (-1, 1):
            pygame.draw.line(s, DIM, (qr.centerx - 12, qr.centery - 12 * d), (qr.centerx + 12, qr.centery + 12 * d), 5)
        x = qr.x - 16
        if self._game_running:
            pr = pill(s, "GAME RUNNING · close it to save", x, HEADER_H // 2, BAD, size=24, anchor="midright")
            x = pr.x - 14
        if not self.pending.empty():
            pill(s, f"{self.pending.count()} unsaved", x, HEADER_H // 2, ACCENT, size=24, anchor="midright")

    def _footer(self, s, items) -> None:
        y = H - FOOTER_H
        pygame.draw.line(s, CARD, (0, y), (W, y), 3)
        x = PAD
        cy = y + FOOTER_H // 2
        for btn, label in items:
            x = glyph(s, btn, x, cy) + 14
            x = text(s, label, (x, cy), 27, DIM, "bold", anchor="midleft").right + 38
        names = self.input.names
        status = names[0] if names else "No controller found · touch works"
        text(s, status, (W - PAD, cy), 22, FAINT if names else BAD, "bold", anchor="midright",
             maxw=max(80, W - PAD - x - 14))

    def _toasts(self, s) -> None:
        now = time.time()
        self.toasts = [t for t in self.toasts if t[3] > now]
        y = H - FOOTER_H - 32
        for msg, col, t0, t1 in reversed(self.toasts[-3:]):
            k_in = min(1.0, (now - t0) / 0.18)
            k_out = min(1.0, (t1 - now) / 0.3)
            a = int(255 * min(k_in, k_out))
            img = F(32, "black").render(msg, True, col)
            box = pygame.Rect(0, 0, img.get_width() + 86, 72)
            box.midbottom = (W // 2, y + int((1 - k_in) * 27))
            panel = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(panel, (*DARK, 240), panel.get_rect(), border_radius=36)
            pygame.draw.rect(panel, (*col, 255), panel.get_rect(), 3, border_radius=36)
            pygame.draw.circle(panel, col, (35, box.h // 2), 8)
            panel.blit(img, img.get_rect(midleft=(56, box.h // 2)))
            panel.set_alpha(a)
            s.blit(panel, box)
            y = box.top - 14

    def render(self) -> None:
        s = self.screen
        s.blit(self.bg, (0, 0))
        top = self.top()
        self._header(s)
        top.draw(s)
        self._footer(s, top.hint_items())
        self._toasts(s)
        if self.screen is not self.window:
            pygame.transform.smoothscale(self.screen, self.window.get_size(), self.window)
        pygame.display.flip()

    def _map_pos(self, pos: tuple[float, float]) -> tuple[float, float]:
        ww, wh = self.window.get_size()
        if (ww, wh) == (W, H) or ww <= 0 or wh <= 0:
            return pos
        return (pos[0] * W / ww, pos[1] * H / wh)

    def click(self, pos) -> None:
        if getattr(self, "quit_rect", None) and self.quit_rect.collidepoint(pos):
            if not self.pending.empty():
                self.confirm("Quit MewDeck?", f"{self.pending.count()} unsaved change(s) will be lost.",
                             self.quit, yes="Quit", danger=True)
            else:
                self.quit()
            return
        self.top().click(pos)

    def quit(self) -> None:
        self.running = False

    def run(self) -> None:
        while self.running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type == pygame.VIDEORESIZE:
                    self._sync_surface()
                elif ev.type == pygame.FINGERDOWN:  # touchscreen (normalised coords)
                    self.click((ev.x * W, ev.y * H))
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and not getattr(ev, "touch", False):
                    self.click(self._map_pos(ev.pos))
                elif ev.type == pygame.MOUSEWHEEL and not getattr(ev, "touch", False):
                    self.top().handle(UP if ev.y > 0 else DOWN)
                else:
                    for b in self.input.translate(ev):
                        self.top().handle(b)
            for b in self.input.poll():
                if self.running:
                    self.top().handle(b)
            now = time.time()
            if now - self._last_poll > 2:
                self._last_poll = now
                self.input.rescan()
                self._game_running = sf.game_running()
                if self.save and self.snap and not isinstance(self.top(), Confirm):
                    try:  # the game saved behind our back -> refresh; pending offsets would be stale
                        if self.save.stat().st_mtime != self.snap.mtime and not self._game_running:
                            dropped = not self.pending.empty()
                            self.pending = sf.Changes()
                            self._after_write()
                            self.toast("Save changed on disk, reloaded" + (" · pending edits dropped" if dropped else ""),
                                       BAD if dropped else TEXT, 5)
                    except Exception:  # noqa: BLE001
                        pass
            self.render()
            self.clock.tick(60)
        pygame.quit()

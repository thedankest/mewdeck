"""Gamepad-first pygame-ce front end for MewDeck.

Steam Game Mode exposes the ROG Ally / Deck controls as a virtual Xbox pad (SDL GameController).
Keyboard (arrows / Enter / Esc / Q / E / Tab) and touch work as fallbacks.

Layout: header · tab bar (LB/RB) · content · button-hint footer.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pygame

from . import breeding as br
from . import catblob as cb
from . import editors as ed
from . import gamedata as gd
from . import organizer as org
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

UP, DOWN, LEFT, RIGHT, A, B, X, Y, LB, RB, LT, RT, START, SELECT = (
    "UP", "DOWN", "LEFT", "RIGHT", "A", "B", "X", "Y", "LB", "RB", "LT", "RT", "START", "SELECT")
REPEATABLE = {UP, DOWN, LEFT, RIGHT, LT, RT}


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
            if dev.get_axis(pygame.CONTROLLER_AXIS_TRIGGERLEFT) / 32767 > 0.55:
                s.add(LT)
            if dev.get_axis(pygame.CONTROLLER_AXIS_TRIGGERRIGHT) / 32767 > 0.55:
                s.add(RT)
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
            if dev.get_numaxes() > 5:
                if dev.get_axis(2) > 0.55:
                    s.add(LT)
                if dev.get_axis(5) > 0.55:
                    s.add(RT)
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
                  pygame.K_q: LB, pygame.K_e: RB, pygame.K_PAGEUP: LT, pygame.K_PAGEDOWN: RT,
                  pygame.K_LEFTBRACKET: LT, pygame.K_RIGHTBRACKET: RT,
                  pygame.K_TAB: START, pygame.K_s: START, pygame.K_BACKQUOTE: SELECT,
                  pygame.K_SLASH: SELECT, pygame.K_f: SELECT}
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
    if name == "SELECT":
        rr = pygame.Rect(x, cy - 18, 54, 36)
        rrect(surf, rr, (128, 116, 150), 18)
        pygame.draw.rect(surf, DARK, (rr.centerx - 12, cy - 9, 15, 12), width=3, border_radius=2)
        pygame.draw.rect(surf, DARK, (rr.centerx - 3, cy - 3, 15, 12), width=3, border_radius=2)
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

    def draw(self, surf, area: pygame.Rect, render: Callable[..., None] | None = None, show_focus: bool = True) -> None:
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
        if self.rows and show_focus:
            rrect(surf, hr, CARD_HI, 18)
        for i in range(first, min(len(self.rows), first + visible + 3)):
            r = pygame.Rect(area.x, area.y + i * step - off, area.w, self.row_h)
            if r.bottom < area.y - step or r.top > area.bottom + step:
                continue
            if area.y - 6 <= r.y and r.bottom <= area.bottom + 6:
                self.rects.append((r, i))
            render(surf, r, self.rows[i], i == self.sel and show_focus)
        if self.rows and show_focus:
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
        mod = sys.modules[__name__]
        self.tabs: list[Tab] = [
            HomeTab(app),
            CatsTab(app),
            org.BreedingTab(mod, app),
            ItemsTab(app),
            ed.FurnitureTab(mod, app),
            BackupsTab(app),
        ]
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
            elif isinstance(t, org.BreedingTab) and self.app.pending.room_moves:
                badge = str(len(self.app.pending.room_moves))
            elif isinstance(t, ItemsTab) and self.app.pending.inv_total():
                badge = f"{self.app.pending.inv_total()}"
            elif isinstance(t, ed.FurnitureTab):
                badge = str(self.app.snap.parsed["total_furniture"])
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
    ACTIONS = ["reset", "wipe", "restart", "guard", "heal_all"]
    # (prop_key, label, tag, step, max_val, quick_x_val)
    RESOURCES = [
        ("house_gold", "Home Gold", "HOME", 10, 9_999_999, 99_999),
        ("house_food", "Home Food", "HOME", 10, 9_999_999, 99_999),
        ("adventure_coins", "Adventure Coins", "ADV", 10, 9_999_999, 99_999),
        ("adventure_food", "Adventure Food", "ADV", 10, 9_999_999, 99_999),
        ("blank_collars", "Blank Collars", "HOME", 1, 999, 99),
        ("current_day", "Current Day", "META", 1, 99_999, None),
        ("house_storage_upgrades", "Storage Upgrades", "HOME", 1, 99, 10),
        ("adventure_furniture_boxes", "Furniture Boxes", "ADV", 1, 99, 20),
        ("min_strays_tomorrow", "Min Strays Tomorrow", "HOME", 1, 20, 10),
        ("save_file_percent", "Completion %", "META", 1, 100, 100),
    ]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.col = 0  # 0 = left actions, 1 = right resources/save
        self.act_idx = 0
        self.res_list = ListView(row_h=72, gap=8)
        self.ring = RectAnim()
        self.rects: dict[str, pygame.Rect] = {}
        self.arrow_rects: dict[tuple[str, int], pygame.Rect] = {}
        self._res_scroll_area = pygame.Rect(0, 0, 1, 1)

    def on_enter(self) -> None:
        rows = [Row(lbl, data=spec) for spec in self.RESOURCES for lbl in (spec[1],)]
        rows += [
            Row("Repair & refill all gear", data=("tool", "repair_all")),
            Row("Advanced Properties & Unlocks", data=("tool", "props")),
            Row("Verify Save Integrity", data=("tool", "integrity")),
            Row("Save pending changes", data=("write",)),
        ]
        self.res_list.set_rows(rows)

    def _val(self, key: str) -> int:
        basic = self.app.snap.parsed["basic"]
        return self.app.pending.props.get(key, basic.get(key, 0))

    def _adjust(self, key: str, delta: int, max_v: int) -> None:
        base = self.app.snap.parsed["basic"].get(key, 0)
        lo = 1 if key == "current_day" else 0
        new = max(lo, min(max_v, self._val(key) + delta))
        if new == base:
            self.app.pending.props.pop(key, None)
        else:
            self.app.pending.props[key] = new

    def handle(self, btn: str) -> bool:
        if btn in (UP, DOWN):
            d = -1 if btn == UP else 1
            if self.col == 0:
                nxt = self.act_idx + d
                if 0 <= nxt < len(self.ACTIONS):
                    self.act_idx = nxt
                elif nxt >= len(self.ACTIONS):
                    self.col = 1
                    self.res_list.sel = 0
                else:
                    self.col = 1
                    self.res_list.sel = len(self.res_list.rows) - 1
            else:
                nxt = self.res_list.sel + d
                if 0 <= nxt < len(self.res_list.rows):
                    self.res_list.sel = nxt
                elif nxt < 0:
                    self.col = 0
                    self.act_idx = len(self.ACTIONS) - 1
                else:
                    self.col = 0
                    self.act_idx = 0
            return True
        if self.col == 0 and btn == RIGHT:
            self.col = 1
            return True
        if self.col == 1 and btn in (LT, RT):
            self.res_list.move(-6 if btn == LT else 6)
            return True
        if self.col == 1:
            row = self.res_list.current
            if row and row.data[0] not in ("write", "tool"):
                key, _, _, step, max_v, qx = row.data
                if btn in (LEFT, RIGHT):
                    self._adjust(key, (step if btn == RIGHT else -step) * self.app.input.mult(btn), max_v)
                    return True
                if btn == X and qx is not None:
                    self._adjust(key, qx - self._val(key), max_v)
                    return True
            elif btn == LEFT:
                self.col = 0
                return True
        if btn == A:
            if self.col == 0:
                self.activate(self.ACTIONS[self.act_idx])
            elif self.res_list.current:
                kind = self.res_list.current.data[0]
                if kind == "write":
                    self.app.write()
                elif kind == "tool":
                    self._run_tool(self.res_list.current.data[1])
            return True
        return False

    def _run_tool(self, tool: str) -> None:
        app = self.app
        if tool == "repair_all":
            app.confirm("Repair & refill all gear?",
                        "Refills durability/uses and clears worn flags on all items in Home Storage & Adventure Backpack.",
                        lambda: app.write(repair_all=True), yes="Repair all")
        elif tool == "props":
            app.push(ed.PropertiesScreen(sys.modules[__name__], app))
        elif tool == "integrity":
            ok, notes = sf.check_integrity(app.save)
            app.confirm("Save Integrity: OK" if ok else "Save Integrity: Issues Found",
                        "  ·  ".join(notes), lambda: None, yes="OK", danger=not ok)

    def activate(self, item: str) -> None:
        app = self.app
        if item == "reset":
            app.confirm("Reset Steven's penalty?",
                        "Sets savescumlocation to 0, keeps 1 strike, and strips Déjà Vu from adventure cats. Backup made first.",
                        lambda: app.write(steven="cap"), yes="Reset")
        elif item == "wipe":
            app.confirm("Reset and wipe all strikes?",
                        "Deletes Steven's whole strike history and strips Déjà Vu from adventure cats. Backup made first.",
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
        elif item == "heal_all":
            inj = sum(1 for c in app.snap.parsed["cats"] if c["injured"] or c["wounded"])
            body = (f"Heals HP, injuries, stat penalties, and visual scars on all {app.snap.parsed['total_cats']} active cats "
                    f"({inj} currently injured/wounded). A backup is made first.")
            app.confirm("Heal all active cats?", body, lambda: app.write(heal_all=True), yes="Heal all")

    def click(self, pos) -> None:
        for (key, d), r in self.arrow_rects.items():
            if r.collidepoint(pos):
                for spec in self.RESOURCES:
                    if spec[0] == key:
                        self.col = 1
                        self._adjust(key, d, spec[4])
                        return
        for idx, key in enumerate(self.ACTIONS):
            r = self.rects.get(key)
            if r and r.collidepoint(pos):
                if self.col == 0 and self.act_idx == idx:
                    self.activate(key)
                self.col, self.act_idx = 0, idx
                return
        i = self.res_list.hit(pos)
        if i is not None:
            self.col = 1
            if i == self.res_list.sel:
                kind = self.res_list.rows[i].data[0]
                if kind == "write":
                    self.app.write()
                elif kind == "tool":
                    self._run_tool(self.res_list.rows[i].data[1])
            self.res_list.sel = i

    def _render_res_row(self, surf, r, row: Row, selected: bool) -> None:
        focused = self.col == 1 and selected
        if row.data[0] == "tool":
            rrect(surf, r, CARD_HI if focused else CARD, 16)
            pill(surf, "TOOL", r.x + 18, r.centery, mix(INFO, CARD, .72), INFO, size=15, anchor="midleft")
            text(surf, row.label, (r.x + 96, r.centery), 25, TEXT, "bold", anchor="midleft", maxw=r.w - 160)
            tri(surf, ACCENT if focused else DIM, (r.right - 28, r.centery), 10, "right")
            return
        if row.data[0] == "write":
            pend = self.app.pending
            has = not pend.empty()
            rrect(surf, r, mix(ACCENT, CARD, .1) if has else CARD_HI, 16)
            fg = DARK if has else DIM
            lbl = f"Save {pend.count()} pending change{'s' if pend.count() != 1 else ''}" if has else "No pending changes"
            text(surf, lbl, (r.x + 24, r.centery), 28, fg, "black", anchor="midleft")
            if has:
                tri(surf, fg, (r.right - 28, r.centery), 10, "right")
            return
        key, label, tag, step, _, _ = row.data
        val = self._val(key)
        base = self.app.snap.parsed["basic"].get(key, 0)
        changed = val != base
        tcol = INFO if tag == "ADV" else ACCENT if tag == "HOME" else FAINT
        pill(surf, tag, r.x + 18, r.centery, mix(tcol, CARD, .72), tcol, size=15, anchor="midleft")
        text(surf, label, (r.x + 96, r.centery), 25, TEXT, "bold", anchor="midleft", maxw=r.w - 340)
        text(surf, f"{val:,}", (r.right - 134, r.centery), 30, ACCENT if changed else TEXT, "black", anchor="midright")
        for d, direction, cx in ((-step, "left", r.right - 92), (step, "right", r.right - 38)):
            ar = pygame.Rect(0, 0, 44, 44)
            ar.center = (cx, r.centery)
            self.arrow_rects[(key, d)] = ar
            rrect(surf, ar, CARD_HI if focused else mix(CARD, CARD_HI, .45), 12)
            tri(surf, TEXT if focused else DIM, ar.center, 9, direction)

    def draw(self, surf, area) -> None:
        snap = self.app.snap
        st = snap.steven
        basic = snap.parsed["basic"]
        self.rects, self.arrow_rects = {}, {}
        inj_count = sum(1 for c in snap.parsed["cats"] if c["injured"] or c["wounded"])

        # ---- Left column: Steven + Heal All Cats
        hero = pygame.Rect(area.x, area.y, 980, area.h)
        rrect(surf, hero, CARD, 26)
        band = pygame.Rect(hero.x, hero.y, hero.w, 184)
        tint = pygame.Surface(band.size, pygame.SRCALPHA)
        tint.fill((*(BAD if (st.active or st.dejavu_cats) else GOOD), 28))
        surf.blit(tint, band.topleft)
        text(surf, "SAVE-SCUM WATCH & QUICK FIXES", (hero.x + 40, hero.y + 26), 20, FAINT, "black")
        icon_status(surf, hero.x + 88, hero.y + 114, 42, not (st.active or st.dejavu_cats))
        headline = f"Penalty armed ({st.where})" if st.active else (f"{st.dejavu_cats} cats have Déjà Vu" if st.dejavu_cats else "All clear")
        text(surf, headline, (hero.x + 156, hero.y + 62), 46,
             BAD if (st.active or st.dejavu_cats) else GOOD, "black", maxw=hero.w - 380)
        sub = [f"{st.strikes} strike{'s' if st.strikes != 1 else ''}"]
        if st.dejavu_cats:
            sub.append(f"{st.dejavu_cats} Déjà Vu")
        if st.guarded:
            sub.append("auto-block on")
        if st.on_adventure:
            sub.append("on adventure")
        text(surf, "  ·  ".join(sub), (hero.x + 158, hero.y + 126), 24, DIM)
        pips(surf, hero.right - 40 - max(5, st.strikes) * 29 + 6, hero.y + 42, st.strikes, max(5, st.strikes), BAD,
             23, 23, 6, mix(LINE, CARD, .4))

        btns = [
            ("reset", "Reset penalty & Déjà Vu", "savescumlocation = 0, keep 1 strike, strip Déjà Vu", True),
            ("wipe", "Reset + wipe all strikes", "Delete strike history & strip Déjà Vu", False),
            ("restart", "Close game, reset & relaunch", "One button: kill, reset, start via Steam", False),
            ("guard", "Auto-block Steven", "SQLite trigger prevents penalty from arming", False),
            ("heal_all", "Heal all active cats", "Full HP + cure injuries, stat penalties & scars", False),
        ]
        by = hero.y + 204
        for key, label, desc, primary in btns:
            r = pygame.Rect(hero.x + 34, by, hero.w - 68, 96)
            self.rects[key] = r
            if key == "heal_all":
                col = mix(GOOD, CARD, .55) if inj_count else CARD_HI
            elif primary:
                col = BAD if (st.active or st.dejavu_cats) else mix(GOOD, CARD, .35)
            else:
                col = CARD_HI
            rrect(surf, r, col, 20)
            fg = DARK if primary and (st.active or st.dejavu_cats) else TEXT
            text(surf, label, (r.x + 28, r.y + 14), 32, fg, "black")
            text(surf, desc, (r.x + 28, r.bottom - 14), 22, mix(fg, col, .28), anchor="bottomleft")
            if key == "guard":
                pill(surf, "ON" if st.guarded else "OFF", r.right - 32, r.centery, GOOD if st.guarded else LINE,
                     DARK if st.guarded else TEXT, 22, anchor="midright")
            elif key == "heal_all":
                pill(surf, f"{inj_count} injured" if inj_count else "All healthy", r.right - 32, r.centery,
                     BAD if inj_count else GOOD, DARK, 20, anchor="midright")
            else:
                tri(surf, fg, (r.right - 36, r.centery), 11, "right")
            by += 108

        # ---- Right column: Campaign summary + Home & Adventure Resources
        col_x = hero.right + 32
        col_w = area.right - col_x
        info = pygame.Rect(col_x, area.y, col_w, 114)
        rrect(surf, info, mix(CARD, BG_BOT, .35), 22)
        house_n = sum(1 for c in snap.parsed["cats"] if c["room"] != "(ADVENTURE)")
        adv_n = sum(1 for c in snap.parsed["cats"] if c["room"] == "(ADVENTURE)")
        for i, (lab, val) in enumerate((("DAY", basic["current_day"]), ("HOUSE", house_n), ("ADV PARTY", adv_n),
                                        ("DONE", f"{basic['save_percent']}%"))):
            cx = info.x + col_w * (i * 2 + 1) // 8
            text(surf, str(val), (cx, info.y + 22), 34, INFO if lab == "ADV PARTY" and adv_n else TEXT, "black", anchor="midtop")
            text(surf, lab, (cx, info.y + 72), 17, FAINT, "black", anchor="midtop")

        res_area = pygame.Rect(col_x, info.bottom + 18, col_w - 14, area.bottom - info.bottom - 18)
        self._res_scroll_area = res_area
        self.res_list.draw(surf, res_area, self._render_res_row, show_focus=(self.col == 1))
        if self.col == 0:
            target = self.rects[self.ACTIONS[self.act_idx]]
            focus_ring(surf, self.ring.to(target), 20)

    def hint_items(self):
        if self.col == 1:
            row = self.res_list.current
            if row and row.data[0] != "write":
                return [("DPAD_LR", "Adjust (hold=fast)"), ("X", "Max"), ("B", "Saves")]
            return [("A", "Save"), ("B", "Saves")]
        return [("A", "Run"), ("DPAD_LR", "Resources"), ("Y", "Reload"), ("B", "Saves")]


# --------------------------------------------------------------------------- cats

class CatsTab(Tab):
    name = "Cats"
    FILTERS = [
        ("House", "active", lambda c: c["room"] != "(ADVENTURE)"),
        ("Adventure", "active", lambda c: c["room"] == "(ADVENTURE)"),
        ("All Active", "active", lambda c: True),
        ("Injured", "active", lambda c: c["injured"] or c["wounded"]),
        ("Mutated", "active", lambda c: any(v >= 300 for k, v in c["mutations"].items() if k != "coat")),
        ("Retired", "active", lambda c: c["retired"]),
        ("Graveyard", "graveyard", lambda c: True),
    ]
    SEX_FILTERS = ("All Sex", "Male", "Female", "Ditto")
    SORTS = [
        ("Room", lambda c: (c["room"] != "(ADVENTURE)", c["room"], c["name"].lower())),
        ("Stats", lambda c: (-br.stat_sum(c), -br.perfect_sevens(c), c["name"].lower())),
        ("7s", lambda c: (-br.perfect_sevens(c), -br.stat_sum(c), c["name"].lower())),
        ("Breed Score", lambda c: (-br.cat_breed_score(c), c["name"].lower())),
        ("Level", lambda c: (-c["level"], c["name"].lower())),
        ("Age (Young)", lambda c: (c["age"], c["name"].lower())),
        ("Age (Old)", lambda c: (-c["age"], c["name"].lower())),
        ("Inbred", lambda c: (-c.get("coi", 0.0), c["name"].lower())),
        ("Class", lambda c: (c["class"], c["name"].lower())),
    ]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.list = ListView(row_h=118, gap=11)
        self.f = 0
        self.s_idx = 0
        self.sex_idx = 0
        self.query = ""
        self.search_rect: pygame.Rect | None = None
        self.sort_rect: pygame.Rect | None = None
        self.sex_rect: pygame.Rect | None = None
        self.chip_rects: list[tuple[pygame.Rect, int]] = []

    @staticmethod
    def _cat_matches(c: dict[str, Any], q: str) -> bool:
        if not q:
            return True
        ab = c["abilities"]
        names = [
            c["name"], c["class"], c["room"], c["sex"],
            gd.injury_info(c["injury"])["name"],
            c.get("lover_name", ""), c.get("hater_name", ""),
            "inbred" if c.get("coi", 0.0) >= 0.20 else "",
            "mutated" if br.mutation_count(c) else "",
        ]
        names += [gd.active_info(a)["name"] for a in ab["active"] if a]
        names += [gd.passive_info(p)["name"] for p in ab["passive"] if p]
        names += [gd.disorder_info(d)["name"] for d in ab["disorder"] if d]
        hay = " ".join(str(x) for x in names).lower()
        return all(tok in hay for tok in q.lower().split())

    def _pool(self, idx: int, q: str = "", apply_sex: bool = True) -> list[dict[str, Any]]:
        _, src, pred = self.FILTERS[idx]
        cats = self.app.snap.parsed["graveyard"] if src == "graveyard" else self.app.snap.parsed["cats"]
        sex_f = self.SEX_FILTERS[self.sex_idx] if apply_sex else "All Sex"
        return [
            c for c in cats
            if pred(c)
            and (sex_f == "All Sex" or c["sex"] == sex_f)
            and self._cat_matches(c, q)
        ]

    def on_enter(self) -> None:
        sort_fn = self.SORTS[self.s_idx][1]
        cats = sorted(self._pool(self.f, self.query), key=sort_fn)
        self.list.set_rows([Row(c["name"] or f"#{c['key']}", data=c) for c in cats])

    def _open_search(self) -> None:
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self.on_enter()
        self.app.push(ed.SearchModal(sys.modules[__name__], self.app, "Search Cats", self.query,
                                     lambda q: len(self._pool(self.f, q)), done))

    def handle(self, btn: str) -> bool:
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn in (LT, RT):
            self.list.move(-10 if btn == LT else 10)
        elif btn in (LEFT, RIGHT):
            self.f = (self.f + (1 if btn == RIGHT else -1)) % len(self.FILTERS)
            self.list.sel = 0
            self.on_enter()
        elif btn == Y:
            self.s_idx = (self.s_idx + 1) % len(self.SORTS)
            self.list.sel = 0
            self.on_enter()
            self.app.toast(f"Sort: {self.SORTS[self.s_idx][0]}", ACCENT, 1.5)
        elif btn == SELECT:
            self._open_search()
        elif btn == B and (self.query or self.sex_idx):
            self.query = ""
            self.sex_idx = 0
            self.list.sel = 0
            self.on_enter()
        elif btn == A and self.list.current:
            self.app.push(CatEdit(self.app, [r.data for r in self.list.rows], self.list.sel))
        elif btn == X and self.list.current:
            c = self.list.current.data
            if c["dead"]:
                self.app.pending.cats.setdefault(c["key"], {})["revive"] = True
                self.app.toast(f"Queued Revive » Attic for {c['name'] or '#' + str(c['key'])} (Menu to save)", GOOD, 3.0)
            elif c["injured"] or c["wounded"]:
                self.app.pending.cats.setdefault(c["key"], {})["heal"] = True
                self.app.toast(f"Queued Heal for {c['name'] or '#' + str(c['key'])} (Menu to save)", GOOD, 3.0)
            else:
                return False
        else:
            return False
        return True

    def click(self, pos) -> None:
        if self.search_rect and self.search_rect.collidepoint(pos):
            self._open_search()
            return
        if self.sort_rect and self.sort_rect.collidepoint(pos):
            self.handle(Y)
            return
        if self.sex_rect and self.sex_rect.collidepoint(pos):
            self.sex_idx = (self.sex_idx + 1) % len(self.SEX_FILTERS)
            self.list.sel = 0
            self.on_enter()
            return
        for r, i in self.chip_rects:
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
        scol = SEX_COL.get(c["sex"], DIM)
        pygame.draw.circle(surf, mix(scol, CARD, .25), (r.x + 62, r.centery), 30)
        text(surf, {"Male": "M", "Female": "F"}.get(c["sex"], "?"), (r.x + 62, r.centery), 30, DARK, "black",
             anchor="center")
        pend_c = self.app.pending.cats.get(c["key"], {})
        name_r = text(surf, row.label, (r.x + 114, r.y + 16), 38, DIM if gone else TEXT, "black", maxw=380)
        bx = name_r.right + 12
        if pend_c.get("revive"):
            bx = pill(surf, "REVIVING", bx, name_r.centery, GOOD, DARK, size=18, anchor="midleft").right + 8
        elif pend_c or c["key"] in self.app.pending.room_moves:
            bx = pill(surf, "EDITED", bx, name_r.centery, ACCENT, DARK, size=18, anchor="midleft").right + 8
        if c["room"] == "(ADVENTURE)":
            bx = pill(surf, "ADV PARTY", bx, name_r.centery, INFO, DARK, size=17, anchor="midleft").right + 8
        if (c["injured"] or c["wounded"]) and not pend_c.get("heal"):
            inj_lbl = gd.injury_info(c["injury"])["name"] if c["injury"] else "Wounded"
            bx = pill(surf, inj_lbl, bx, name_r.centery, BAD, DARK, size=17, anchor="midleft").right + 8
        if "DejaVu" in (c["abilities"]["disorder"] or []):
            bx = pill(surf, "DÉJÀ VU", bx, name_r.centery, (220, 90, 190), DARK, size=17, anchor="midleft").right + 8
        if c.get("coi", 0.0) >= 0.20:
            pill(surf, f"CoI {c['coi'] * 100:.0f}%", bx, name_r.centery, mix(BAD, CARD, .65), BAD, size=16, anchor="midleft")

        lvl = c["level"] if 0 <= c["level"] <= 99 else "?"
        rm_raw = self.app.pending.room_moves.get(c["key"], c["room"])
        room = "Adventure" if rm_raw == "(ADVENTURE)" else (rm_raw or "Graveyard").replace("_", " ")
        mut_n = br.mutation_count(c)
        parts = [f"{c['class']}", f"Lv {lvl}", f"{c['age']}d", room, f"Sum {br.stat_sum(c)}", f"HP {c['hp']}/{c['max_hp']}"]
        if mut_n:
            parts.append(f"{mut_n} mut")
        if c["retired"]:
            parts.append("retired")
        text(surf, " · ".join(parts), (r.x + 114, r.bottom - 18), 24, DIM, anchor="bottomleft", maxw=860)
        # effective stat bars
        x0 = r.right - 40 - 7 * 46
        for i, s in enumerate(STATS):
            v = c["effective_stats"][s]
            bx = x0 + i * 46
            bh = 60
            rrect(surf, (bx, r.y + 18, 24, bh), mix(LINE, CARD, .4), 7)
            fh = round(bh * max(0, min(7, v)) / 7)
            rrect(surf, (bx, r.y + 18 + bh - fh, 24, fh), stat_col(v) if not gone else LINE, 7)
            text(surf, s[0] if s != "LUCK" else "L", (bx + 12, r.bottom - 10), 19, FAINT, "black", anchor="midbottom")

    def draw(self, surf, area) -> None:
        cy = area.y + 18
        self.search_rect = ed.draw_search_pill(sys.modules[__name__], surf, area.right - 20, cy, self.query, anchor="midright")
        self.sort_rect = pill(surf, f"Sort: {self.SORTS[self.s_idx][0]} (Y)", self.search_rect.left - 10, cy,
                              ACCENT if self.s_idx else CARD_HI, DARK if self.s_idx else TEXT, size=19, anchor="midright")
        self.sex_rect = pill(surf, self.SEX_FILTERS[self.sex_idx], self.sort_rect.left - 10, cy,
                             INFO if self.sex_idx else CARD_HI, DARK if self.sex_idx else DIM, size=19, anchor="midright")
        chips_right = self.sex_rect.left - 16

        self.chip_rects = []
        labels = [f"{name} {len(self._pool(i, apply_sex=False))}" for i, (name, _, _) in enumerate(self.FILTERS)]
        widths = [F(22, "bold").size(l)[0] + 30 for l in labels]
        start = 0
        while sum(widths[start:self.f + 1]) > (chips_right - area.x - 24) and start < self.f:
            start += 1
        x = area.x
        if start:
            tri(surf, DIM, (x + 8, cy), 8, "left")
            x += 24
        for i in range(start, len(self.FILTERS)):
            sel = i == self.f
            w_est = F(22, "bold").size(labels[i])[0] + 22
            if x + w_est > chips_right:
                break
            r = pill(surf, labels[i], x, area.y, ACCENT if sel else CARD, DARK if sel else DIM, 22)
            self.chip_rects.append((r, i))
            x = r.right + 9

        la = pygame.Rect(area.x, area.y + 62, area.w - 20, area.h - 62)
        if not self.list.rows:
            msg = f'No cats matching "{self.query}"' if self.query else "No cats in this filter"
            text(surf, msg, (la.centerx, la.y + 108), 35, FAINT, "bold", anchor="midtop")
            return
        self.list.draw(surf, la, self.render_row)

    def hint_items(self):
        cur = self.list.current.data if self.list.current else None
        extra = []
        if cur and cur["dead"]:
            extra = [("X", "Revive")]
        elif cur and (cur["injured"] or cur["wounded"]):
            extra = [("X", "Heal")]
        return [("A", "Edit cat")] + extra + [("Y", "Sort"), ("SELECT", "Search"), ("DPAD_LR", "Filter"),
                                              ("B", "Clear" if (self.query or self.sex_idx) else "Saves")]


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

    @staticmethod
    def _orig_equip(cat: dict[str, Any]) -> dict[str, str | None]:
        return {sk: (cat["equipment"][sk]["item_id"] if cat["equipment"].get(sk) else None) for sk in cb.EQUIP_SLOT_KEYS}

    def load(self) -> None:
        cat = self.cat
        prev = self.app.pending.cats.get(cat["key"], {})
        orig_ab = cat["abilities"]
        prev_ab = prev.get("abilities", {})
        orig_eq = self._orig_equip(cat)
        prev_eq = prev.get("equipment", {})
        self.vals = {
            "name": prev.get("name", cat["name"]),
            "room": self.app.pending.room_moves.get(cat["key"], cat["room"]),
            "stats": dict(prev.get("stats", cat["stats"])),
            "level_bonuses": dict(prev.get("level_bonuses", cat["level_bonuses"])),
            "level": prev.get("level", cat["level"]),
            "age": prev.get("age", cat["age"]),
            "sex": prev.get("sex", cat["sex"]),
            "retired": prev.get("retired", cat["retired"]),
            "class": prev.get("class", cat["class"]),
            "heal": bool(prev.get("heal", False)),
            "revive": bool(prev.get("revive", False)),
            "abilities": {
                "active": list(prev_ab.get("active", orig_ab["active"])),
                "passive": list(prev_ab.get("passive", orig_ab["passive"])),
                "passive_tiers": list(prev_ab.get("passive_tiers", orig_ab["passive_tiers"])),
                "disorder": list(prev_ab.get("disorder", orig_ab["disorder"])),
                "disorder_tiers": list(prev_ab.get("disorder_tiers", orig_ab["disorder_tiers"])),
            },
            "mutations": {**cat["mutations"], **prev.get("mutations", {})},
            "equipment": {**orig_eq, **prev_eq},
        }
        self.title = self.vals["name"] or f"Cat #{cat['key']}"
        self.ok = sf.cat_editable(self.app.snap, cat)
        self.build()

    def on_enter(self) -> None:
        self.build()

    def build(self) -> None:
        c = self.cat
        rows: list[Row] = []
        if c["dead"]:
            rows.append(Row("Revive cat » Attic", data=("revive",)))
        elif c["injured"] or c["wounded"]:
            rows.append(Row("Heal cat (HP, injuries & scars)", data=("heal",)))
        rows += [
            Row("Active Abilities", data=("sub", "active")),
            Row("Passives (+ Tier 1/2)", data=("sub", "passive")),
            Row("Disorders", data=("sub", "disorder")),
            Row("Mutations & Coat", data=("sub", "mutations")),
            Row("Equipped Gear", data=("sub", "equip")),
            Row("Level-Up Bonuses", data=("sub", "bonuses")),
        ]
        rows += [Row(s, data=("stat", s), enabled=self.ok["stats"]) for s in STATS]
        rows.append(Row("Rename cat", data=("rename",)))
        if c["room"] and c["room"] != "(ADVENTURE)" and self.app.snap.parsed.get("rooms"):
            rows.append(Row("House Room", data=("room",)))
        rows += [
            Row("Class", data=("class",)),
            Row("Level", data=("level",), enabled=self.ok["level"]),
            Row("Age", data=("age",), enabled=self.ok["age"]),
            Row("Sex", data=("sex",)),
            Row("Retired", data=("retired",)),
            Row("Max all stats (All 7)", data=("max",), enabled=self.ok["stats"]),
            Row("Heal & clear disorders", data=("clean",)),
            Row("Revert cat", data=("revert",)),
        ]
        self.list.set_rows(rows)

    def handle(self, btn: str) -> None:
        row = self.list.current
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn in (LT, RT):
            self.list.move(-6 if btn == LT else 6)
        elif btn in (LB, RB):
            self.idx = (self.idx + (1 if btn == RB else -1)) % len(self.cats)
            self.load()
        elif btn in (LEFT, RIGHT) and row:
            if not row.enabled:
                return
            d = 1 if btn == RIGHT else -1
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
            elif kind == "room":
                rooms = self.app.snap.parsed.get("rooms") or ["Attic"]
                i = rooms.index(self.vals["room"]) if self.vals["room"] in rooms else 0
                self.vals["room"] = rooms[(i + d) % len(rooms)]
            elif kind == "class":
                old_cls = self.vals["class"]
                i = cb.CAT_CLASSES.index(old_cls) if old_cls in cb.CAT_CLASSES else 0
                new_cls = cb.CAT_CLASSES[(i + d) % len(cb.CAT_CLASSES)]
                self.vals["class"] = new_cls
                # Auto-sync default basic attack if current slot 1 was a standard class basic attack
                cur_atk = self.vals["abilities"]["active"][1]
                if cur_atk in cb.CLASS_BASIC_ATTACK.values():
                    self.vals["abilities"]["active"][1] = cb.CLASS_BASIC_ATTACK[new_cls]
            elif kind == "retired":
                self.vals["retired"] = not self.vals["retired"]
            else:
                return
            self.commit_local()
        elif btn == A and row and row.enabled:
            kind = row.data[0]
            mod = sys.modules[__name__]
            if kind == "sub":
                which = row.data[1]
                if which in ("active", "passive", "disorder"):
                    self.app.push(ed.AbilitiesScreen(mod, self, which))
                elif which == "mutations":
                    self.app.push(ed.MutationsScreen(mod, self))
                elif which == "equip":
                    self.app.push(ed.EquipScreen(mod, self))
                elif which == "bonuses":
                    self.app.push(ed.BonusStatsScreen(mod, self))
            elif kind == "rename":
                def done_rename(nm: str) -> None:
                    if nm:
                        self.vals["name"] = nm
                        self.title = nm
                        self.commit_local()
                self.app.push(ed.SearchModal(mod, self.app, "Rename Cat", self.vals["name"], None,
                                             done_rename, done_label="Rename", title_case=True))
            elif kind == "heal":
                self.vals["heal"] = not self.vals["heal"]
                self.commit_local()
                self.app.toast("Heal queued · press Menu to save" if self.vals["heal"] else "Heal cancelled", GOOD)
            elif kind == "revive":
                self.vals["revive"] = not self.vals["revive"]
                self.commit_local()
                self.app.toast("Revive » Attic queued · Menu to save" if self.vals["revive"] else "Revive cancelled", GOOD)
            elif kind == "max":
                self.vals["stats"] = {s: 7 for s in STATS}
                self.commit_local()
                self.app.toast("All base stats set to 7", GOOD)
            elif kind == "clean":
                self.vals["heal"] = True
                self.vals["abilities"]["disorder"] = [None, None]
                self.vals["abilities"]["disorder_tiers"] = [1, 1]
                self.commit_local()
                self.app.toast("Queued heal + clear disorders", GOOD)
            elif kind == "revert":
                self.app.pending.cats.pop(self.cat["key"], None)
                self.app.pending.room_moves.pop(self.cat["key"], None)
                self.load()
                self.app.toast("Reverted cat edits")
            elif kind in ("sex", "retired", "class", "room"):
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
        if self.vals["name"] != c["name"]:
            ch["name"] = self.vals["name"]
        if self.vals["stats"] != c["stats"]:
            ch["stats"] = dict(self.vals["stats"])
        if self.vals["level_bonuses"] != c["level_bonuses"]:
            ch["level_bonuses"] = dict(self.vals["level_bonuses"])
        for k in ("level", "age", "sex", "retired", "class"):
            if self.vals[k] != c[k]:
                ch[k] = self.vals[k]
        if self.vals["room"] != c["room"]:
            self.app.pending.room_moves[c["key"]] = self.vals["room"]
        else:
            self.app.pending.room_moves.pop(c["key"], None)
        if self.vals["heal"]:
            ch["heal"] = True
        if self.vals["revive"]:
            ch["revive"] = True
        ab_diff = {}
        for f in ("active", "passive", "passive_tiers", "disorder", "disorder_tiers"):
            if self.vals["abilities"][f] != c["abilities"][f]:
                ab_diff[f] = list(self.vals["abilities"][f])
        if ab_diff:
            ch["abilities"] = ab_diff
        mut_diff = {k: v for k, v in self.vals["mutations"].items() if v != c["mutations"].get(k)}
        if mut_diff:
            ch["mutations"] = mut_diff
        orig_eq = self._orig_equip(c)
        eq_diff = {k: v for k, v in self.vals["equipment"].items() if v != orig_eq.get(k)}
        if eq_diff:
            ch["equipment"] = eq_diff
        if ch:
            self.app.pending.cats[c["key"]] = ch
        else:
            self.app.pending.cats.pop(c["key"], None)

    def _sub_preview(self, which: str) -> tuple[str, tuple]:
        ab = self.vals["abilities"]
        if which == "active":
            names = [gd.active_info(a)["name"] for a in ab["active"][1:] if a]
            return (", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "") if names else "None", TEXT)
        if which == "passive":
            parts = [gd.passive_info(p, t).get("display_name", gd.passive_info(p, t)["name"])
                     for p, t in zip(ab["passive"], ab["passive_tiers"]) if p]
            return (", ".join(parts) if parts else "None", ACCENT if parts else DIM)
        if which == "disorder":
            parts = [gd.disorder_info(d)["name"] for d in ab["disorder"] if d]
            return (", ".join(parts) if parts else "None", BAD if parts else GOOD)
        if which == "mutations":
            muts = self.vals["mutations"]
            cnt = sum(1 for k, v in muts.items() if v >= 300)
            defs = sum(1 for k, v in muts.items() if gd.signed_mut_id(v) == -2)
            txt = f"{cnt} active" + (f" · {defs} defect" if defs else "")
            return (txt, BAD if defs else GOOD if cnt else DIM)
        if which == "equip":
            eq = [gd.item_info(v)["name"] for v in self.vals["equipment"].values() if v]
            return (f"{len(eq)}/5 equipped" if eq else "Empty", INFO if eq else DIM)
        if which == "bonuses":
            pos = sum(v for v in self.vals["level_bonuses"].values() if v > 0)
            neg = sum(v for v in self.vals["level_bonuses"].values() if v < 0)
            txt = f"+{pos}" + (f" / {neg}" if neg else "") if (pos or neg) else "None"
            return (txt, GOOD if pos else BAD if neg else DIM)
        return ("", DIM)

    def render_row(self, surf, r, row: Row, selected: bool) -> None:
        v, c = self.vals, self.cat
        kind = row.data[0]
        col = TEXT if row.enabled else FAINT
        text(surf, row.label, (r.x + 30, r.centery), 30, col, "black", anchor="midleft")
        if kind == "sub":
            prev, pcol = self._sub_preview(row.data[1])
            text(surf, prev, (r.right - 66, r.centery), 24, pcol, "bold", anchor="midright", maxw=r.w - 380)
            tri(surf, ACCENT if selected else DIM, (r.right - 34, r.centery), 10, "right")
        elif kind == "rename":
            text(surf, v["name"] or "(unnamed)", (r.right - 66, r.centery), 26,
                 ACCENT if v["name"] != c["name"] else TEXT, "bold", anchor="midright", maxw=r.w - 340)
            tri(surf, ACCENT if selected else DIM, (r.right - 34, r.centery), 10, "right")
        elif kind in ("heal", "revive"):
            on = v[kind]
            pill(surf, "QUEUED" if on else "PRESS A", r.right - 30, r.centery, GOOD if on else ACCENT, DARK, size=20,
                 anchor="midright")
        elif kind == "stat":
            s = row.data[1]
            new, old = v["stats"][s], c["stats"][s]
            x = r.x + 190
            for i in range(7):
                filled = i < new
                was = i < old
                colr = stat_col(new) if filled and was else ACCENT if filled else mix(BAD, CARD, .4) if was else mix(LINE, CARD, .3)
                rrect(surf, (x + i * 54, r.centery - 15, 46, 30), colr, 8)
            bon = v["level_bonuses"][s]
            val_txt = f"{new}{'+' if bon > 0 else ''}{bon}" if bon else str(new)
            text(surf, val_txt, (r.right - 80, r.centery), 34, ACCENT if new != old else TEXT, "black", anchor="center")
        elif kind in ("level", "age", "sex", "retired", "class", "room"):
            val = v[kind]
            shown = ("yes" if val else "no") if kind == "retired" else (
                f"{val} days" if kind == "age" else (str(val).replace("_", " ") if kind == "room" else str(val)))
            changed = val != c[kind]
            tri(surf, DIM if selected else LINE, (r.right - 270, r.centery), 11, "left")
            tri(surf, DIM if selected else LINE, (r.right - 40, r.centery), 11, "right")
            text(surf, shown, (r.right - 155, r.centery), 29, ACCENT if changed else col, "black", anchor="center")
            if changed:
                was = c[kind]
                was_s = ("yes" if was else "no") if kind == "retired" else str(was).replace("_", " ")
                text(surf, f"was {was_s}", (r.x + 230, r.centery), 21, DIM, anchor="midleft")
        else:
            text(surf, "A", (r.right - 54, r.centery), 27, DIM, "black", anchor="center")

    def draw(self, surf) -> None:
        c = self.cat
        cls_name = self.vals["class"]
        ccol = CLASS_COL.get(cls_name, ACCENT)
        card = pygame.Rect(W - PAD - 560, HEADER_H + 24, 560, CONTENT_BOTTOM - HEADER_H - 24)
        rrect(surf, card, CARD, 26)
        band = pygame.Rect(card.x, card.y, card.w, 188)
        tint = pygame.Surface(band.size, pygame.SRCALPHA)
        tint.fill((*ccol, 40))
        surf.blit(tint, band.topleft)
        icon_cat(surf, card.x + 88, card.y + 98, 50, ccol, CARD)
        text(surf, self.vals["name"] or f"#{c['key']}", (card.x + 164, card.y + 42), 40, TEXT, "black", maxw=card.w - 190)
        pill(surf, cls_name, card.x + 164, card.y + 106, ccol, size=22)

        y = card.y + 208
        room = "Adventure Party" if self.vals["room"] == "(ADVENTURE)" else (self.vals["room"] or "Graveyard").replace("_", " ")
        inj_name = gd.injury_info(c["injury"])["name"] if (c["injury"] and not self.vals["heal"]) else ""
        status = "Healthy" if not (c["dead"] or inj_name) else ("Dead (Graveyard)" if c["dead"] else f"Injured: {inj_name}")
        if self.vals["revive"]:
            status = "Reviving » Attic"
        elif self.vals["heal"] and (c["injured"] or c["wounded"]):
            status = "Healing » Full"

        eff_con = max(1, self.vals["stats"]["CON"] + self.vals["level_bonuses"]["CON"] + (0 if self.vals["heal"] else c["injury_mods"]["CON"]))
        eff_cha = max(1, self.vals["stats"]["CHA"] + self.vals["level_bonuses"]["CHA"] + (0 if self.vals["heal"] else c["injury_mods"]["CHA"]))
        hp_str = f"{eff_con * 4}/{eff_con * 4}" if (self.vals["heal"] or c["hp_raw"] == cb.HP_FULL) else f"{c['hp']}/{eff_con * 4}"

        for lab, val, vcol in (("LOCATION", room, INFO if c["room"] == "(ADVENTURE)" else TEXT),
                               ("STATUS", status, BAD if "Injured" in status or "Dead" in status else GOOD),
                               ("HP · MANA", f"HP {hp_str}  ·  Mana {eff_cha * 3}  ·  Cat {self.idx + 1}/{len(self.cats)}", TEXT)):
            text(surf, lab, (card.x + 34, y), 18, FAINT, "black")
            text(surf, val, (card.x + 34, y + 24), 27, vcol, "bold", maxw=card.w - 68)
            y += 68

        # Unobfuscated abilities summary
        ab = self.vals["abilities"]
        text(surf, "ABILITIES & DISORDERS", (card.x + 34, y), 18, FAINT, "black")
        y += 28
        shown_ab: list[tuple[str, tuple]] = []
        for aid in ab["active"][1:]:
            if aid:
                shown_ab.append((gd.active_info(aid)["name"], TEXT))
        for pid, tier in zip(ab["passive"], ab["passive_tiers"]):
            if pid:
                pi = gd.passive_info(pid, tier)
                shown_ab.append((f"[P] {pi.get('display_name', pi['name'])}", ACCENT))
        for did in ab["disorder"]:
            if did:
                shown_ab.append((f"[D] {gd.disorder_info(did)['name']}", BAD))

        cur = self.list.current
        reserve = 148 if (cur and cur.data[0] == "stat") else 34
        for nm, ncol in shown_ab:
            if y + 28 > card.bottom - reserve:
                break
            text(surf, nm, (card.x + 34, y), 24, ncol, maxw=card.w - 68)
            y += 30

        if cur and cur.data[0] == "stat":
            info = pygame.Rect(card.x + 22, card.bottom - 144, card.w - 44, 124)
            rrect(surf, info, CARD_HI, 18)
            s = cur.data[1]
            text(surf, s, (info.x + 20, info.y + 14), 23, stat_col(self.vals["stats"][s]), "black")
            for i, l in enumerate(ed.wrap_lines(F(24), STAT_INFO[s], info.w - 40, max_lines=2)):
                text(surf, l, (info.x + 20, info.y + 48 + i * 30), 24, DIM)

        area = pygame.Rect(PAD, HEADER_H + 24, card.x - PAD - 36, CONTENT_BOTTOM - HEADER_H - 24)
        self.list.draw(surf, area, self.render_row)
        glyph(surf, "LB", card.x + card.w - 136, card.y + 30)
        glyph(surf, "RB", card.x + card.w - 74, card.y + 30)

    def hint_items(self):
        return [("A", "Open / Run"), ("DPAD_LR", "Adjust"), ("LBRB", "Prev/next cat"), ("B", "Done")] + pending_hint(self.app)


# --------------------------------------------------------------------------- items

class ItemsTab(Tab):
    name = "Items"
    CONTAINERS = [
        ("storage", "Home Storage"),
        ("backpack", "Adventure Backpack"),
        ("trash", "Trash"),
    ]

    RARITY_RANK = {"common": 1, "uncommon": 2, "rare": 3, "very_rare": 4, "legendary": 5, "quest": 6}
    SORTS = ("A-Z", "Top Stats", "Rarity", "Set")

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.items = sorted(gd.items().values(), key=lambda i: (i.get("name") or i["id"]).lower())
        kinds = sorted({i.get("kind") or "other" for i in self.items})
        self.tabs = (
            ["owned", "all"]
            + kinds
            + ["sets", "cursed", "r:common", "r:uncommon", "r:rare", "r:very_rare",
               "+Shield", "+STR", "+DEX", "+CON", "+INT", "+SPD", "+CHA", "+LUCK"]
        )
        self.c_idx = 0  # 0=Home Storage, 1=Adventure Backpack, 2=Trash
        self.t = 1      # default to "all" catalog so browsing + adding is instant
        self.s_idx = 0
        self.query = ""
        self.search_rect: pygame.Rect | None = None
        self.sort_rect: pygame.Rect | None = None
        self.list = ListView(row_h=82, gap=8)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []
        self.container_rects: list[tuple[pygame.Rect, int]] = []

    @property
    def container(self) -> str:
        return self.CONTAINERS[self.c_idx][0]

    @property
    def container_label(self) -> str:
        return self.CONTAINERS[self.c_idx][1]

    def on_enter(self) -> None:
        self._rebuild()

    @staticmethod
    def _item_matches(it: dict[str, Any], q: str) -> bool:
        if not q:
            return True
        sets = " ".join(sb.get("name", "") + " " + sb.get("desc", "") for sb in gd.item_set_info(it))
        hay = " ".join(str(x) for x in (
            it.get("name", ""), it.get("id", ""), it.get("kind", ""), it.get("rarity", ""),
            it.get("desc", ""), it.get("set", ""), gd.format_stats(it.get("stats")), sets,
            "cursed" if it.get("cursed") else "",
        )).lower()
        return all(tok in hay for tok in q.lower().split())

    def _rows_for(self, q: str) -> list[Row]:
        grp = self.container
        mode = self.tabs[self.t]
        rows: list[Row] = []
        if mode == "owned":
            rems = set(self.app.pending.inv_remove[grp])
            moved_out = {inst for sg, _, inst in self.app.pending.inv_move if sg == grp}
            moved_in_ids = [(sg, inst) for sg, dg, inst in self.app.pending.inv_move if dg == grp]
            for raw in self.app.snap.parsed["items"][grp]:
                iid_inst = int(raw["instance_id"])
                if iid_inst in rems or iid_inst in moved_out:
                    continue
                info = gd.item_info(raw["item_id"])
                if self._item_matches(info, q):
                    rows.append(Row(info["name"], data=("owned", raw, info)))
            for sg, inst in moved_in_ids:
                raw = next((x for x in self.app.snap.parsed["items"][sg] if int(x["instance_id"]) == inst), None)
                if raw:
                    info = gd.item_info(raw["item_id"])
                    if self._item_matches(info, q):
                        rows.append(Row(info["name"], data=("moved", raw, info)))
            for iid in self.app.pending.inv_add[grp]:
                info = gd.item_info(iid)
                if self._item_matches(info, q):
                    rows.append(Row(info["name"], data=("queued", iid, info)))
        else:
            for it in self.items:
                ok = (
                    mode == "all"
                    or (mode == "sets" and bool(it.get("set")))
                    or (mode == "cursed" and bool(it.get("cursed")))
                    or (mode.startswith("r:") and (it.get("rarity") or "") == mode[2:])
                    or (mode.startswith("+") and (it.get("stats") or {}).get(mode[1:], 0) > 0)
                    or (it.get("kind") or "other") == mode
                )
                if ok and self._item_matches(it, q):
                    rows.append(Row(it.get("name") or it["id"], data=("catalog", it["id"], it)))
        if self.s_idx == 1:
            rows.sort(key=lambda r: (-sum((r.data[2].get("stats") or {}).values()), r.label.lower()))
        elif self.s_idx == 2:
            rows.sort(key=lambda r: (-self.RARITY_RANK.get(r.data[2].get("rarity") or "", 0), r.label.lower()))
        elif self.s_idx == 3:
            rows.sort(key=lambda r: (not bool(r.data[2].get("set")), str(r.data[2].get("set") or "zzz"), r.label.lower()))
        return rows

    def _rebuild(self) -> None:
        self.list.set_rows(self._rows_for(self.query))

    def _open_search(self) -> None:
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        self.app.push(ed.SearchModal(sys.modules[__name__], self.app, "Search Items", self.query,
                                     lambda q: len(self._rows_for(q)), done))

    def handle(self, btn: str) -> bool:
        grp = self.container
        adds = self.app.pending.inv_add[grp]
        rems = self.app.pending.inv_remove[grp]
        if btn in (UP, DOWN):
            self.list.move((-1 if btn == UP else 1) * (1 if self.app.input.count.get(btn, 0) < 12 else 5))
        elif btn in (LT, RT):
            self.list.move(-10 if btn == LT else 10)
        elif btn in (LEFT, RIGHT):
            self.t = (self.t + (1 if btn == RIGHT else -1)) % len(self.tabs)
            self.list.sel = 0
            self._rebuild()
        elif btn == SELECT:
            self._open_search()
        elif btn == B and self.query:
            self.query = ""
            self.list.sel = 0
            self._rebuild()
        elif btn == Y:
            self.c_idx = (self.c_idx + 1) % len(self.CONTAINERS)
            self.list.sel = 0
            self._rebuild()
        elif btn == A and self.list.current:
            kind, obj, info = self.list.current.data
            if kind == "catalog":
                adds.append(info["id"])
                self.app.toast(f"+1 {info['name']} » {self.container_label}", GOOD, 1.8)
            elif kind == "owned" and grp in ("storage", "backpack"):
                dst = "backpack" if grp == "storage" else "storage"
                dst_lbl = "Adventure Backpack" if dst == "backpack" else "Home Storage"
                self.app.pending.inv_move.append((grp, dst, int(obj["instance_id"])))
                self._rebuild()
                self.app.toast(f"Moved {info['name']} » {dst_lbl}", INFO, 2.0)
        elif btn == X and self.list.current:
            kind, obj, info = self.list.current.data
            if kind == "catalog":
                if info["id"] in adds:
                    adds.remove(info["id"])
            elif kind == "queued":
                if obj in adds:
                    adds.remove(obj)
                self._rebuild()
            elif kind == "owned":
                rems.append(int(obj["instance_id"]))
                self._rebuild()
                self.app.toast(f"Removed {info['name']} from {self.container_label}", ACCENT, 1.8)
        else:
            return False
        return True

    def click(self, pos) -> None:
        if self.search_rect and self.search_rect.collidepoint(pos):
            self._open_search()
            return
        if self.sort_rect and self.sort_rect.collidepoint(pos):
            self.s_idx = (self.s_idx + 1) % len(self.SORTS)
            self.list.sel = 0
            self._rebuild()
            return
        for r, i in self.container_rects:
            if r.collidepoint(pos):
                self.c_idx = i
                self.list.sel = 0
                self._rebuild()
                return
        for r, i in self.chip_rects:
            if r.collidepoint(pos):
                self.t = i
                self.list.sel = 0
                self._rebuild()
                return
        i = self.list.hit(pos)
        if i is not None:
            if i == self.list.sel:
                self.handle(A)
            self.list.sel = i

    def render_row(self, surf, r, row: Row, selected: bool) -> None:
        kind, obj, it = row.data
        badge = pygame.Rect(r.x + 14, r.centery - 29, 58, 58)
        ed.draw_item_badge(sys.modules[__name__], surf, it.get("id"), badge, it.get("rarity", ""))
        text(surf, row.label, (r.x + 86, r.centery), 30, ACCENT if kind == "queued" else TEXT, "bold",
             anchor="midleft", maxw=r.w - 376)
        st = gd.format_stats(it.get("stats"))
        meta = st if st else f"{(it.get('rarity') or '').replace('_', ' ')} · {it.get('kind') or 'other'}"
        text(surf, meta, (r.right - 108, r.centery), 21, GOOD if st else FAINT, "bold" if st else "semi",
             anchor="midright", maxw=260)
        if kind == "catalog":
            n = self.app.pending.inv_add[self.container].count(it["id"])
            if n:
                pill(surf, f"+{n}", r.right - 22, r.centery, ACCENT, size=24, anchor="midright")
        elif kind == "queued":
            pill(surf, "NEW", r.right - 22, r.centery, ACCENT, size=18, anchor="midright")
        elif kind == "owned" and obj.get("field_a", -1) >= 0:
            pill(surf, f"×{obj['field_a']}", r.right - 22, r.centery, CARD_HI, DIM, size=18, anchor="midright")

    def draw(self, surf, area) -> None:
        # Row 1: Home Storage / Adventure Backpack / Trash container pills + Sort pill
        self.container_rects = []
        cx = area.x
        for i, (ckey, clbl) in enumerate(self.CONTAINERS):
            sel = i == self.c_idx
            m_in = sum(1 for _, dg, _ in self.app.pending.inv_move if dg == ckey)
            m_out = sum(1 for sg, _, _ in self.app.pending.inv_move if sg == ckey)
            cnt = (len(self.app.snap.parsed["items"][ckey]) + len(self.app.pending.inv_add[ckey])
                   - len(set(self.app.pending.inv_remove[ckey])) + m_in - m_out)
            bg = (INFO if ckey == "backpack" else ACCENT) if sel else CARD
            r = pill(surf, f"{clbl}  {cnt}", cx, area.y, bg, DARK if sel else DIM, 23)
            self.container_rects.append((r, i))
            cx = r.right + 12
        glyph(surf, "Y", cx + 6, self.container_rects[0][0].centery)
        self.sort_rect = pill(surf, f"Sort: {self.SORTS[self.s_idx]}", area.right - 620, self.container_rects[0][0].centery,
                              ACCENT if self.s_idx else CARD_HI, DARK if self.s_idx else TEXT, size=19, anchor="midright")

        # Row 2: Mode / category chips + Search pill
        cy = area.y + 52
        self.search_rect = ed.draw_search_pill(sys.modules[__name__], surf, area.right - 620, cy + 16, self.query,
                                               anchor="midright")
        chips_right = self.search_rect.left - 14
        self.chip_rects = []
        labels = [
            "Owned" if t == "owned" else (
                "Add: All" if t == "all" else (
                    "Set Items" if t == "sets" else (
                        t[2:].replace("_", " ").title() if t.startswith("r:") else (
                            t if t.startswith("+") else t.replace("_", " ").title()
                        )
                    )
                )
            )
            for t in self.tabs
        ]
        widths = [F(21, "bold").size(l)[0] + 30 for l in labels]
        start = 0
        while sum(widths[start:self.t + 1]) > (chips_right - area.x - 24) and start < self.t:
            start += 1
        x = area.x
        if start:
            tri(surf, DIM, (x + 8, cy + 16), 8, "left")
            x += 24
        for i in range(start, len(self.tabs)):
            sel = i == self.t
            w_est = F(21, "bold").size(labels[i])[0] + 21
            if x + w_est > chips_right:
                break
            r = pill(surf, labels[i], x, cy, ACCENT if sel else CARD, DARK if sel else DIM, 21)
            self.chip_rects.append((r, i))
            x = r.right + 8

        list_w = area.w - 620
        la = pygame.Rect(area.x, cy + 52, list_w, area.bottom - (cy + 52))
        if not self.list.rows:
            msg = f'No items matching "{self.query}"' if self.query else f"No items in {self.container_label}"
            text(surf, msg, (la.centerx, la.y + 100), 30, FAINT, "bold", anchor="midtop")
        else:
            self.list.draw(surf, la, self.render_row)

        # Right panel: Full Item Details + Armor Set Bonus + Queue summary
        rx = area.right - 580
        q_h = 84
        detail_box = pygame.Rect(rx, area.y, 580, area.h - q_h - 14)
        cur_item = self.list.current.data[2] if self.list.current else None
        ed.draw_item_detail(sys.modules[__name__], surf, detail_box, cur_item)

        qbox = pygame.Rect(rx, detail_box.bottom + 14, 580, q_h)
        rrect(surf, qbox, CARD, 20)
        parts = []
        for ckey, clbl in self.CONTAINERS:
            a_n = len(self.app.pending.inv_add[ckey])
            r_n = len(self.app.pending.inv_remove[ckey])
            if a_n or r_n:
                short = "Home" if ckey == "storage" else "Adv" if ckey == "backpack" else "Trash"
                parts.append(f"{short}: " + " ".join(([f"+{a_n}"] if a_n else []) + ([f"-{r_n}"] if r_n else [])))
        if parts:
            text(surf, "PENDING ITEM EDITS", (qbox.x + 24, qbox.y + 14), 16, ACCENT, "black")
            text(surf, "  ·  ".join(parts), (qbox.x + 24, qbox.y + 42), 23, TEXT, "bold", maxw=qbox.w - 48)
        else:
            text(surf, f"TARGET: {self.container_label.upper()}", (qbox.x + 24, qbox.y + 14), 16, FAINT, "black")
            text(surf, "Press Y to switch Home / Adventure / Trash.", (qbox.x + 24, qbox.y + 42), 22, DIM, maxw=qbox.w - 48)

    def hint_items(self):
        if self.tabs[self.t] == "owned":
            mv = [("A", "Move to Adv" if self.container == "storage" else "Move to Home")] if self.container in ("storage", "backpack") else []
            return mv + [("X", "Remove"), ("Y", "Home/Adv"), ("SELECT", "Search")]
        return [("A", f"Add to {self.container_label.split()[0]}"), ("Y", "Home/Adv"), ("SELECT", "Search"), ("DPAD_LR", "Filter")]


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

    def write(self, steven: str | None = None, guard: bool | None = None, heal_all: bool = False,
              repair_all: bool = False) -> bool:
        assert self.save
        ch = self.pending
        if steven:
            ch.steven = steven
        if guard is not None:
            ch.guard = guard
        if heal_all:
            ch.heal_all = True
        if repair_all:
            ch.repair_all = True
        if ch.empty():
            self.toast("Nothing to save")
            return False
        reason = "steven-reset" if steven else ("heal-all" if heal_all else ("repair-all" if repair_all else "edit"))
        try:
            sf.apply_changes(self.save, ch, reason=reason)
        except sf.GameRunningError as e:
            ch.steven, ch.guard, ch.heal_all, ch.repair_all = None, None, False, False
            self.toast(str(e), BAD, 5)
            return False
        except Exception as e:  # noqa: BLE001
            ch.steven, ch.guard, ch.heal_all, ch.repair_all = None, None, False, False
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
            ok = not st.active and st.dejavu_cats == 0
            self.toast("Steven penalty & Déjà Vu reset" if ok else "Reset written", GOOD if ok else BAD, 4)
        elif heal_all:
            self.toast(f"Healed all {self.snap.parsed['total_cats']} active cats · backup made", GOOD, 4)
        elif repair_all:
            self.toast("Repaired & refilled all gear · backup made", GOOD, 4)
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
                elif ev.type == pygame.KEYDOWN and hasattr(self.top(), "handle_raw_key") and self.top().handle_raw_key(ev):
                    continue
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

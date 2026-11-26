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
from . import sfx
from . import updater as upd

W, H = 1920, 1080  # native 1080p (16:9, ROG Ally native panel)
HEADER_H, TABS_H, FOOTER_H = 114, 76, 80
CONTENT_TOP = HEADER_H + TABS_H + 22
CONTENT_BOTTOM = H - FOOTER_H - 20
PAD = 54

# Mewgenics in-game sketchbook ink & warm cardboard/paper palette
BG_TOP, BG_BOT = (40, 35, 30), (24, 21, 18)
CARD = (58, 51, 45)
CARD_HI = (82, 72, 62)
WELL = (32, 28, 25)
INK = (16, 14, 12)
LINE = (116, 104, 92)
TEXT = (246, 239, 224)
DIM = (188, 176, 158)
FAINT = (136, 124, 108)
ACCENT = (244, 182, 72)
GOOD = (108, 208, 130)
BAD = (234, 94, 86)
INFO = (114, 176, 238)
DARK = (20, 17, 15)

BTN_COL = {"A": (104, 198, 120), "B": (232, 92, 84), "X": (96, 158, 232), "Y": (242, 198, 76)}
CLASS_COL = {
    "Fighter": (228, 104, 86), "Hunter": (124, 194, 102), "Mage": (106, 154, 240), "Tank": (154, 146, 124),
    "Thief": (236, 218, 138), "Medic": (236, 228, 216), "Monk": (182, 176, 168), "Butcher": (196, 78, 86),
    "Druid": (146, 186, 84), "Tinkerer": (92, 206, 186), "Necromancer": (144, 108, 176),
    "Psychic": (196, 114, 194), "Jester": (242, 198, 82), "Colorless": (162, 152, 142),
}
SEX_COL = {"Male": (110, 168, 242), "Female": (240, 132, 168), "Ditto": (186, 142, 228)}
RARITY_COL = {"common": (208, 198, 184), "uncommon": GOOD, "rare": INFO, "very_rare": (198, 138, 240),
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
                  pygame.K_TAB: START, pygame.K_BACKQUOTE: SELECT,
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


_GLYPH_SUB = str.maketrans({"ō": "o", "Ō": "O", "ā": "a", "ē": "e", "ī": "i", "ū": "u"})


def text(surf: pygame.Surface, s: str, pos: tuple[float, float], size: int = 28, color=TEXT,
         weight: str = "semi", anchor: str = "topleft", maxw: int | None = None, alpha: int = 255) -> pygame.Rect:
    s = s.translate(_GLYPH_SUB)
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


def rrect(surf, rect, color, radius=14, width=0, ink: bool | None = None, shadow: bool = False) -> None:
    rr = pygame.Rect(rect)
    is_card = radius >= 20 and rr.w >= 160 and rr.h >= 60
    if width == 0 and (shadow or (ink is None and is_card)):
        pygame.draw.rect(surf, INK, rr.move(4, 5), border_radius=radius)
    pygame.draw.rect(surf, color, rr, width=width, border_radius=radius)
    # Subtle warm cardboard top rim highlight inside filled panels
    if width == 0 and rr.h >= 40 and rr.w >= 80 and color in (CARD, CARD_HI):
        pygame.draw.line(surf, mix(color, TEXT, .14), (rr.x + radius, rr.y + 3), (rr.right - radius, rr.y + 3), 2)
    if width == 0 and (ink or (ink is None and radius >= 14 and rr.w >= 40 and rr.h >= 24)):
        pygame.draw.rect(surf, INK, rr, width=3, border_radius=radius)


def draw_top_band(surf: pygame.Surface, rect: pygame.Rect, color: tuple, alpha: int = 44, radius: int = 22) -> None:
    """Draw a rounded-top tinted header band that hugs its parent card's ink outline cleanly."""
    band = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(
        band, (*color[:3], alpha), band.get_rect(),
        border_top_left_radius=radius, border_top_right_radius=radius,
        border_bottom_left_radius=0, border_bottom_right_radius=0,
    )
    surf.blit(band, rect.topleft)
    pygame.draw.line(surf, INK, (rect.x, rect.bottom), (rect.right, rect.bottom), 3)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1, c2, t: float):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def pill(surf, label: str, x: float, y: float, bg, fg=DARK, size=20, anchor="topleft", weight="bold") -> pygame.Rect:
    img = F(size, weight).render(label, True, fg)
    r = pygame.Rect(0, 0, img.get_width() + size, img.get_height() + 4)
    setattr(r, anchor, (round(x), round(y)))
    pygame.draw.rect(surf, bg, r, border_radius=r.h // 2)
    pygame.draw.rect(surf, INK, r, width=2, border_radius=r.h // 2)
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
    """Draw an inked Mewgenics-styled controller glyph with left edge at x. Returns right edge."""
    stone = (154, 142, 128)
    if name in BTN_COL:
        r = 20
        pygame.draw.circle(surf, INK, (x + r, cy + 2), r + 2)
        pygame.draw.circle(surf, BTN_COL[name], (x + r, cy), r)
        pygame.draw.circle(surf, INK, (x + r, cy), r, 2)
        img = F(25, "black").render(name, True, DARK)
        surf.blit(img, img.get_rect(center=(x + r, cy)))
        return x + 2 * r
    if name in ("LB", "RB", "LBRB", "LT", "RT", "LTRT"):
        right = x
        parts = ["LB", "RB"] if name == "LBRB" else (["LT", "RT"] if name == "LTRT" else [name])
        for part in parts:
            rr = pygame.Rect(right, cy - 18, 54, 36)
            pygame.draw.rect(surf, stone, rr, border_radius=12)
            pygame.draw.rect(surf, INK, rr, width=2, border_radius=12)
            img = F(21, "black").render(part, True, DARK)
            surf.blit(img, img.get_rect(center=rr.center))
            right = rr.right + 6
        return right - 6
    if name == "START":
        rr = pygame.Rect(x, cy - 18, 52, 36)
        pygame.draw.rect(surf, stone, rr, border_radius=18)
        pygame.draw.rect(surf, INK, rr, width=2, border_radius=18)
        for dy in (-7, 0, 7):
            pygame.draw.line(surf, DARK, (rr.centerx - 11, cy + dy), (rr.centerx + 11, cy + dy), 4)
        return rr.right
    if name == "SELECT":
        rr = pygame.Rect(x, cy - 18, 52, 36)
        pygame.draw.rect(surf, stone, rr, border_radius=18)
        pygame.draw.rect(surf, INK, rr, width=2, border_radius=18)
        pygame.draw.rect(surf, DARK, (rr.centerx - 12, cy - 9, 15, 12), width=3, border_radius=2)
        pygame.draw.rect(surf, DARK, (rr.centerx - 3, cy - 3, 15, 12), width=3, border_radius=2)
        return rr.right
    if name.startswith("DPAD"):
        c = stone
        hi = TEXT
        cx = x + 20
        pygame.draw.rect(surf, INK, (cx - 9, cy - 22, 18, 44), border_radius=5)
        pygame.draw.rect(surf, INK, (cx - 22, cy - 9, 44, 18), border_radius=5)
        pygame.draw.rect(surf, c, (cx - 7, cy - 20, 14, 40), border_radius=4)
        pygame.draw.rect(surf, c, (cx - 20, cy - 7, 40, 14), border_radius=4)
        if name == "DPAD_LR":
            pygame.draw.rect(surf, hi, (cx - 20, cy - 7, 11, 14), border_radius=4)
            pygame.draw.rect(surf, hi, (cx + 9, cy - 7, 11, 14), border_radius=4)
        elif name == "DPAD_UD":
            pygame.draw.rect(surf, hi, (cx - 7, cy - 20, 14, 11), border_radius=4)
            pygame.draw.rect(surf, hi, (cx - 7, cy + 9, 14, 11), border_radius=4)
        return x + 42
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
    pygame.draw.rect(surf, INK, rect.inflate(10, 10), width=4, border_radius=radius + 5)
    pygame.draw.rect(surf, ACCENT, rect.inflate(4, 4), width=5, border_radius=radius + 2)


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
        surf.set_clip(area.inflate(22, 6))
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
        elif btn == X and self.list.current and self.list.current.enabled:
            src: Path = self.list.current.data[0]
            def do_clone():
                try:
                    dst = sf.clone_save(src)
                    self.refresh()
                    self.app.toast(f"Cloned » {dst.name}", GOOD, 3.0)
                except Exception as e:  # noqa: BLE001
                    self.app.toast(f"Clone failed: {e}", BAD, 5.0)
            self.app.confirm(f"Clone {src.stem}?", "Creates an exact copy in the next free save slot so you can experiment safely.",
                             do_clone, yes="Clone slot")
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
        return [("A", "Open"), ("X", "Clone Slot"), ("Y", "Rescan"), ("B", "Quit")]


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
    TAB_ICONS = {
        "Home": "Gold",
        "Adventure": "Boss",
        "Cats": "Collar",
        "Breeding": "Heart",
        "Items": "Treasure",
        "Furniture": "Shield",
        "Backups": "Recycle",
    }

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        mod = sys.modules[__name__]
        self.tabs: list[Tab] = [
            HomeTab(app),
            AdventureTab(app),
            CatsTab(app),
            org.BreedingTab(mod, app),
            ItemsTab(app),
            ed.FurnitureTab(mod, app),
            BackupsTab(app),
        ]
        self.i = 0
        self.ul = RectAnim()
        self.tab_rects: list[pygame.Rect] = []

    def jump_to(self, name: str) -> None:
        for idx, t in enumerate(self.tabs):
            if t.name == name:
                self.i = idx
                self.tab.on_enter()
                return

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

    def hover(self, pos) -> None:
        if hasattr(self.tab, "hover"):
            self.tab.hover(pos)

    def click(self, pos) -> None:
        for i, r in enumerate(self.tab_rects):
            if r.collidepoint(pos):
                sfx.play("tab")
                self.i = i
                self.tab.on_enter()
                return
        self.tab.click(pos)

    def draw(self, surf) -> None:
        # Inked tab bar strip
        y = HEADER_H
        pygame.draw.rect(surf, (32, 28, 24), (0, y, W, TABS_H))
        pygame.draw.line(surf, INK, (0, y + TABS_H), (W, y + TABS_H), 3)
        x = PAD
        self.tab_rects = []
        glyph(surf, "LB", x, y + TABS_H // 2)
        x += 72
        target = None
        mpos = getattr(self.app, "mouse_pos", (-1, -1))
        for i, t in enumerate(self.tabs):
            label = t.name
            badge = ""
            badge_col = None
            if isinstance(t, AdventureTab):
                st = self.app.snap.steven
                adv_n = sum(1 for c in self.app.snap.parsed["cats"] if c["room"] == "(ADVENTURE)")
                if st.active or st.dejavu_cats:
                    badge = "!"
                    badge_col = BAD
                elif adv_n:
                    badge = str(adv_n)
                    badge_col = INFO
            elif isinstance(t, CatsTab):
                badge = str(self.app.snap.parsed["total_cats"])
            elif isinstance(t, org.BreedingTab) and self.app.pending.room_moves:
                badge = str(len(self.app.pending.room_moves))
                badge_col = GOOD
            elif isinstance(t, ItemsTab) and self.app.pending.inv_total():
                badge = f"{self.app.pending.inv_total()}"
                badge_col = GOOD
            elif isinstance(t, ed.FurnitureTab):
                badge = str(self.app.snap.parsed["total_furniture"])
            sel = i == self.i
            ico = ed.get_sprite("ui", self.TAB_ICONS.get(label), 26)
            img = F(27, "black" if sel else "bold").render(label, True, TEXT if sel else DIM)
            ico_w = 34 if ico is not None else 0
            badge_w = (F(18, "bold").size(badge)[0] + 28) if badge else 0
            r = pygame.Rect(x, y + 10, 32 + ico_w + img.get_width() + badge_w, TABS_H - 20)
            self.tab_rects.append(r)
            if not sel and r.collidepoint(mpos):
                rrect(surf, r, CARD, 14, ink=False)
            if sel:
                target = r
                rrect(surf, r, CARD_HI, 14, ink=True)
            tx = r.x + 16
            if ico is not None:
                surf.blit(ico, ico.get_rect(midleft=(tx, r.centery)))
                tx += ico_w
            surf.blit(img, img.get_rect(midleft=(tx, r.centery)))
            if badge:
                bg = badge_col if badge_col else (ACCENT if sel else LINE)
                fg = DARK if (badge_col or sel) else TEXT
                pill(surf, badge, r.right - 12, r.centery, bg, fg, 18, anchor="midright")
            x = r.right + 8
        glyph(surf, "RB", x + 6, y + TABS_H // 2)
        if target:
            u = self.ul.to(target, 0.35)
            rrect(surf, (u.x + 12, y + TABS_H - 6, u.w - 24, 5), ACCENT, 3, ink=False)
        area = pygame.Rect(PAD, CONTENT_TOP, W - 2 * PAD, CONTENT_BOTTOM - CONTENT_TOP)
        self.tab.draw(surf, area)

    def hint_items(self):
        return self.tab.hint_items() + [("LBRB", "Tabs")] + pending_hint(self.app)


# --------------------------------------------------------------------------- home (House)

class HomeTab(Tab):
    name = "Home"
    ACTIONS = ["heal_all", "repair_all", "strip_defects", "unlocks", "props"]
    # (prop_key, label, icon_key, step, max_val, quick_x_val)
    RESOURCES = [
        ("house_gold", "Home Gold", "Gold", 10, 9_999_999, 99_999),
        ("house_food", "Home Food", "Food", 10, 9_999_999, 99_999),
        ("blank_collars", "Blank Collars", "Collar", 1, 999, 99),
        ("house_storage_upgrades", "Storage Upgrades", "Treasure", 1, 99, 10),
        ("min_strays_tomorrow", "Min Strays Tomorrow", "Heart", 1, 20, 10),
        ("current_day", "Current Day", "Move", 1, 99_999, None),
        ("save_file_percent", "Completion %", "Damage", 1, 100, 100),
    ]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.col = 0  # 0 = left House actions, 1 = right Home resources & utilities
        self.act_idx = 0
        self.res_list = ListView(row_h=72, gap=8)
        self.ring = RectAnim()
        self.rects: dict[str, pygame.Rect] = {}
        self.arrow_rects: dict[tuple[str, int], pygame.Rect] = {}
        self._res_area = pygame.Rect(0, 0, 1, 1)

    def on_enter(self) -> None:
        rows = [Row(lbl, data=spec) for spec in self.RESOURCES for lbl in (spec[1],)]
        upd_info = getattr(self.app, "update_info", None)
        upd_lbl = f"Install Update ({upd_info['tag']})" if (upd_info and upd_info.get("newer")) else f"Check for Updates (v{upd.VERSION})"
        rows += [
            Row("Verify Save Integrity", data=("tool", "integrity")),
            Row("Refresh Steam Artwork", data=("tool", "steam_art")),
            Row(f"UI Sounds: {'ON' if sfx.enabled() else 'OFF'}", data=("tool", "sfx")),
            Row(upd_lbl, data=("tool", "update")),
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
        if btn == SELECT:
            self.col = 1 - self.col
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
        if tool == "integrity":
            ok, notes = sf.check_integrity(app.save)
            app.confirm("Save Integrity: OK" if ok else "Save Integrity: Issues Found",
                        "  ·  ".join(notes), lambda: None, yes="OK", danger=not ok)
        elif tool == "steam_art":
            from . import steam_shortcut as ss
            n, ids = ss.refresh_artwork()
            if n and ids:
                app.toast(f"Steam artwork refreshed (ID {ids[0]}) · restart Steam to view", GOOD, 4.0)
            else:
                app.toast("No Steam shortcut found — re-run installer in Desktop Mode", ACCENT, 4.0)
        elif tool == "sfx":
            sfx.set_enabled(not sfx.enabled())
            if sfx.enabled():
                sfx.play("ok")
            self.on_enter()
            app.toast(f"UI Sounds: {'ON' if sfx.enabled() else 'OFF'}", INFO, 1.8)
        elif tool == "update":
            info = getattr(app, "update_info", None) or upd.check_latest(timeout=5.0)
            app.update_info = info
            self.on_enter()
            if not info:
                app.toast("Could not reach GitHub Releases", BAD, 3.0)
            elif not info.get("newer"):
                app.toast(f"MewDeck v{upd.VERSION} is up to date ({info['tag']})", GOOD, 3.0)
            else:
                def do_upd():
                    app.toast(f"Downloading {info['tag']}…", INFO, 8.0)
                    app.render()
                    ok, msg = upd.download_and_install(info.get("asset_url", ""))
                    app.toast(msg, GOOD if ok else BAD, 6.0)
                app.confirm(f"Install MewDeck {info['tag']}?",
                            "Downloads the latest release and updates MewDeck in place (keeps all saves & backups).",
                            do_upd, yes="Update now")

    def activate(self, item: str) -> None:
        app = self.app
        p = app.pending
        if item == "heal_all":
            p.heal_all = not p.heal_all
            app.toast("Queued Heal All Active Cats · click Save to apply" if p.heal_all else "Cancelled Heal All Cats",
                      GOOD if p.heal_all else DIM, 2.6)
        elif item == "repair_all":
            p.repair_all = not p.repair_all
            app.toast("Queued Repair & Refill All Gear · click Save to apply" if p.repair_all else "Cancelled Repair All Gear",
                      GOOD if p.repair_all else DIM, 2.6)
        elif item == "strip_defects":
            p.strip_all_defects = not p.strip_all_defects
            app.toast("Queued Strip All Birth Defects · click Save to apply" if p.strip_all_defects else "Cancelled Strip Birth Defects",
                      GOOD if p.strip_all_defects else DIM, 2.6)
        elif item == "unlocks":
            app.push(ed.UnlocksScreen(sys.modules[__name__], app))
        elif item == "props":
            app.push(ed.PropertiesScreen(sys.modules[__name__], app))

    def click(self, pos) -> None:
        for (key, d), r in self.arrow_rects.items():
            if r.collidepoint(pos):
                for spec in self.RESOURCES:
                    if spec[0] == key:
                        self.col = 1
                        self._adjust(key, d, spec[4])
                        sfx.play("tick")
                        return
        for idx, key in enumerate(self.ACTIONS):
            r = self.rects.get(key)
            if r and r.collidepoint(pos):
                self.col, self.act_idx = 0, idx
                sfx.play("ok")
                self.activate(key)
                return
        i = self.res_list.hit(pos)
        if i is not None:
            self.col = 1
            self.res_list.sel = i
            kind = self.res_list.rows[i].data[0]
            if kind == "write":
                self.app.write()
            elif kind == "tool":
                sfx.play("ok")
                self._run_tool(self.res_list.rows[i].data[1])

    def _render_res_row(self, surf, r, row: Row, selected: bool) -> None:
        focused = self.col == 1 and selected
        if row.data[0] == "tool":
            tkey = row.data[1]
            ico_k = {"integrity": "Shield", "steam_art": "Treasure", "sfx": "Heart", "update": "Reroll"}.get(tkey, "Move")
            ico = ed.get_sprite("ui", ico_k, 32)
            if ico is not None:
                surf.blit(ico, ico.get_rect(midleft=(r.x + 20, r.centery)))
            text(surf, row.label, (r.x + 66, r.centery), 25, TEXT, "bold", anchor="midleft", maxw=r.w - 130)
            tri(surf, ACCENT if focused else DIM, (r.right - 28, r.centery), 10, "right")
            return
        if row.data[0] == "write":
            pend = self.app.pending
            has = not pend.empty()
            if has:
                pygame.draw.rect(surf, mix(GOOD, CARD, .2), r.inflate(-6, -6), border_radius=14)
            fg = DARK if has else DIM
            lbl = f"Save {pend.count()} pending change{'s' if pend.count() != 1 else ''}" if has else "No pending changes"
            text(surf, lbl, (r.x + 24, r.centery), 27, fg, "black", anchor="midleft")
            if has:
                tri(surf, fg, (r.right - 28, r.centery), 10, "right")
            return
        key, label, ico_k, step, _, _ = row.data
        val = self._val(key)
        base = self.app.snap.parsed["basic"].get(key, 0)
        changed = val != base
        ico = ed.get_sprite("ui", ico_k, 34)
        if ico is not None:
            surf.blit(ico, ico.get_rect(midleft=(r.x + 18, r.centery)))
        text(surf, label, (r.x + 66, r.centery), 25, TEXT, "bold", anchor="midleft", maxw=r.w - 310)
        text(surf, f"{val:,}", (r.right - 134, r.centery), 29, ACCENT if changed else TEXT, "black", anchor="midright")
        visible = self._res_area.top <= r.centery <= self._res_area.bottom
        for d, direction, cx in ((-step, "left", r.right - 92), (step, "right", r.right - 38)):
            ar = pygame.Rect(0, 0, 44, 44)
            ar.center = (cx, r.centery)
            if visible:
                self.arrow_rects[(key, d)] = ar
            pygame.draw.rect(surf, CARD_HI if focused else mix(CARD, CARD_HI, .5), ar, border_radius=11)
            pygame.draw.rect(surf, INK, ar, width=2, border_radius=11)
            tri(surf, TEXT if focused else DIM, ar.center, 9, direction)

    def draw(self, surf, area) -> None:
        snap = self.app.snap
        basic = snap.parsed["basic"]
        self.rects, self.arrow_rects = {}, {}
        house_n = sum(1 for c in snap.parsed["cats"] if c["room"] != "(ADVENTURE)")
        inj_count = sum(1 for c in snap.parsed["cats"] if c["injured"] or c["wounded"])
        rooms_n = len(snap.parsed.get("rooms") or [])
        mpos = getattr(self.app, "mouse_pos", (-1, -1))

        # ---- Left column: House Dashboard & Quick Actions
        hero = pygame.Rect(area.x, area.y, 960, area.h)
        rrect(surf, hero, CARD, 24, shadow=True)
        draw_top_band(surf, pygame.Rect(hero.x + 3, hero.y + 3, hero.w - 6, 174), ACCENT, 34, radius=21)
        h_ico = ed.get_sprite("ui", "Gold", 70)
        if h_ico is not None:
            surf.blit(h_ico, h_ico.get_rect(center=(hero.x + 82, hero.y + 92)))
        text(surf, "HOME & HOUSE TOOLS", (hero.x + 140, hero.y + 26), 19, ACCENT, "black")
        text(surf, f"Day {basic['current_day']}  ·  {house_n} House Cats", (hero.x + 140, hero.y + 56), 42, TEXT, "black", maxw=hero.w - 170)
        text(surf, f"{rooms_n} rooms  ·  {len(snap.parsed['items']['storage'])} stored items  ·  {snap.parsed['total_furniture']} furniture  ·  {basic['save_percent']}% done",
             (hero.x + 140, hero.y + 118), 22, DIM, maxw=hero.w - 170)

        p = self.app.pending
        btns = [
            ("heal_all", "Heal all active cats", "Full HP + cure injuries, stat penalties & scars", "HP", p.heal_all,
             f"{inj_count} injured" if inj_count else "All healthy", BAD if inj_count else GOOD),
            ("repair_all", "Repair & refill all gear", "Restores durability & uses in Storage & Backpack", "Shield", p.repair_all,
             "Queue", CARD_HI),
            ("strip_defects", "Strip all birth defects", "Fixes missing-part (-2) defects on all active cats", "Heart", p.strip_all_defects,
             "Queue", CARD_HI),
            ("unlocks", "Class Collars & House Unlocks", f"{len(snap.parsed['unlocks']['classes'])}/14 classes · {len(snap.parsed['house_unlocks']['upgrades'])} house tiers",
             "Collar", bool(p.unlocks_classes or p.unlocks_house), "Open", INFO),
            ("props", "Advanced Properties & Unlocks", "Search & edit all 160+ integer save flags", "Reroll", False, "Open", CARD_HI),
        ]
        by = hero.y + 196
        for key, label, desc, ico_k, queued, badge_txt, badge_col in btns:
            r = pygame.Rect(hero.x + 30, by, hero.w - 60, 96)
            self.rects[key] = r
            hov = r.collidepoint(mpos)
            col = mix(GOOD, CARD, .45) if queued else (CARD_HI if hov else mix(CARD, CARD_HI, .5))
            rrect(surf, r, col, 18)
            ib = pygame.Rect(r.x + 18, r.centery - 28, 56, 56)
            ed.draw_sprite_badge(sys.modules[__name__], surf, "ui", ico_k, ib)
            text(surf, label, (ib.right + 18, r.y + 14), 30, TEXT, "black", maxw=r.w - 260)
            text(surf, desc, (ib.right + 18, r.bottom - 14), 21, DIM, anchor="bottomleft", maxw=r.w - 260)
            if queued:
                pill(surf, "QUEUED", r.right - 26, r.centery, GOOD, DARK, 19, anchor="midright")
            elif badge_txt in ("Open", "Queue"):
                tri(surf, ACCENT if hov else DIM, (r.right - 32, r.centery), 10, "right")
            else:
                pill(surf, badge_txt, r.right - 26, r.centery, badge_col, DARK, 19, anchor="midright")
            by += 108

        # ---- Right column: Home Resources & Utilities
        col_x = hero.right + 32
        col_w = area.right - col_x
        text(surf, "HOME RESOURCES & SETTINGS", (col_x + 4, area.y + 4), 19, FAINT, "black")
        res_area = pygame.Rect(col_x, area.y + 34, col_w - 14, area.bottom - area.y - 34)
        self._res_area = res_area
        self.res_list.draw(surf, res_area, self._render_res_row, show_focus=(self.col == 1))
        if self.col == 0:
            target = self.rects[self.ACTIONS[self.act_idx]]
            focus_ring(surf, self.ring.to(target), 18)

    def hint_items(self):
        if self.col == 1:
            row = self.res_list.current
            if row and row.data[0] not in ("write", "tool"):
                qx = row.data[5]
                extra = [("X", "Max")] if qx is not None else []
                return [("DPAD_LR", "Adjust")] + extra + [("SELECT", "Switch Col"), ("B", "Saves")]
            return [("A", "Open / Run"), ("SELECT", "Switch Col"), ("B", "Saves")]
        return [("A", "Toggle / Open"), ("DPAD_LR", "Resources"), ("B", "Saves")]


# --------------------------------------------------------------------------- adventure (Steven + Party + Adv Resources)

class AdventureTab(Tab):
    name = "Adventure"
    ACTIONS = ["reset", "wipe", "restart", "guard", "clear_party_dis", "max_party"]
    RESOURCES = [
        ("adventure_coins", "Adventure Coins", "Gold", 10, 9_999_999, 99_999),
        ("adventure_food", "Adventure Food", "Food", 10, 9_999_999, 99_999),
        ("adventure_furniture_boxes", "Furniture Boxes", "Treasure", 1, 99, 20),
    ]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.col = 0  # 0 = left Steven & Party fixes, 1 = right Adv Resources & Party Roster
        self.act_idx = 0
        self.right_list = ListView(row_h=82, gap=9)
        self.ring = RectAnim()
        self.rects: dict[str, pygame.Rect] = {}
        self.arrow_rects: dict[tuple[str, int], pygame.Rect] = {}
        self._right_area = pygame.Rect(0, 0, 1, 1)

    def on_enter(self) -> None:
        rows = [Row(lbl, data=("res", spec)) for spec in self.RESOURCES for lbl in (spec[1],)]
        party = [c for c in self.app.snap.parsed["cats"] if c["room"] == "(ADVENTURE)"]
        for c in party:
            rows.append(Row(c["name"] or f"#{c['key']}", data=("cat", c)))
        rows.append(Row("Open Adventure Backpack »", data=("backpack", None)))
        self.right_list.set_rows(rows)

    def _val(self, key: str) -> int:
        basic = self.app.snap.parsed["basic"]
        return self.app.pending.props.get(key, basic.get(key, 0))

    def _adjust(self, key: str, delta: int, max_v: int) -> None:
        base = self.app.snap.parsed["basic"].get(key, 0)
        new = max(0, min(max_v, self._val(key) + delta))
        if new == base:
            self.app.pending.props.pop(key, None)
        else:
            self.app.pending.props[key] = new

    def activate(self, item: str) -> None:
        app = self.app
        p = app.pending
        if item == "reset":
            p.steven = None if p.steven == "cap" else "cap"
            app.toast("Queued Steven Reset + strip Déjà Vu · click Save" if p.steven == "cap" else "Cancelled Steven Reset",
                      GOOD if p.steven else DIM, 2.8)
        elif item == "wipe":
            p.steven = None if p.steven == "clear" else "clear"
            app.toast("Queued Wipe All Steven Strikes · click Save" if p.steven == "clear" else "Cancelled Wipe Strikes",
                      ACCENT if p.steven else DIM, 2.8)
        elif item == "restart":
            app.confirm("Close, save & relaunch?",
                        "Closes Mewgenics, saves pending changes (including Steven reset), then launches the game via Steam.",
                        app.restart_cycle, yes="Save & Relaunch")
        elif item == "guard":
            want = not app.snap.steven.guarded
            if p.guard == want:
                p.guard = None
                app.toast("Cancelled Auto-block change", DIM, 2.2)
            else:
                p.guard = want
                if want and not p.steven:
                    p.steven = "cap"
                app.toast(f"Queued Auto-block {'ON' if want else 'OFF'} · click Save", GOOD if want else ACCENT, 2.8)
        elif item == "clear_party_dis":
            p.clear_party_disorders = not p.clear_party_disorders
            app.toast("Queued Clear Party Disorders · click Save" if p.clear_party_disorders else "Cancelled Clear Party Disorders",
                      GOOD if p.clear_party_disorders else DIM, 2.6)
        elif item == "max_party":
            p.max_party_stats = not p.max_party_stats
            app.toast("Queued Max Party Stats (All 7) · click Save" if p.max_party_stats else "Cancelled Max Party Stats",
                      GOOD if p.max_party_stats else DIM, 2.6)

    def handle(self, btn: str) -> bool:
        if btn in (UP, DOWN):
            d = -1 if btn == UP else 1
            if self.col == 0:
                nxt = self.act_idx + d
                if 0 <= nxt < len(self.ACTIONS):
                    self.act_idx = nxt
                elif nxt >= len(self.ACTIONS):
                    self.col = 1
                    self.right_list.sel = 0
                else:
                    self.col = 1
                    self.right_list.sel = len(self.right_list.rows) - 1
            else:
                nxt = self.right_list.sel + d
                if 0 <= nxt < len(self.right_list.rows):
                    self.right_list.sel = nxt
                elif nxt < 0:
                    self.col = 0
                    self.act_idx = len(self.ACTIONS) - 1
                else:
                    self.col = 0
                    self.act_idx = 0
            return True
        if btn == SELECT:
            self.col = 1 - self.col
            return True
        if self.col == 0 and btn == RIGHT:
            self.col = 1
            return True
        if self.col == 1:
            row = self.right_list.current
            if row and row.data[0] == "res":
                key, _, _, step, max_v, qx = row.data[1]
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
            elif self.right_list.current:
                kind, obj = self.right_list.current.data
                if kind == "cat":
                    party = [c for c in self.app.snap.parsed["cats"] if c["room"] == "(ADVENTURE)"]
                    idx = next((i for i, c in enumerate(party) if c["key"] == obj["key"]), 0)
                    self.app.push(CatEdit(self.app, party, idx))
                elif kind == "backpack":
                    hub = self.app.top()
                    if isinstance(hub, Hub):
                        for t in hub.tabs:
                            if isinstance(t, ItemsTab):
                                t.c_idx = 1  # Adventure Backpack
                                t.t = 0      # Owned
                        hub.jump_to("Items")
            return True
        return False

    def click(self, pos) -> None:
        for (key, d), r in self.arrow_rects.items():
            if r.collidepoint(pos):
                for spec in self.RESOURCES:
                    if spec[0] == key:
                        self.col = 1
                        self._adjust(key, d, spec[4])
                        sfx.play("tick")
                        return
        for idx, key in enumerate(self.ACTIONS):
            r = self.rects.get(key)
            if r and r.collidepoint(pos):
                self.col, self.act_idx = 0, idx
                sfx.play("ok")
                self.activate(key)
                return
        i = self.right_list.hit(pos)
        if i is not None:
            self.col = 1
            self.right_list.sel = i
            kind = self.right_list.rows[i].data[0]
            if kind in ("cat", "backpack"):
                sfx.play("ok")
                self.handle(A)

    def _render_right_row(self, surf, r, row: Row, selected: bool) -> None:
        focused = self.col == 1 and selected
        kind, obj = row.data
        if kind == "res":
            key, label, ico_k, step, _, _ = obj
            val = self._val(key)
            base = self.app.snap.parsed["basic"].get(key, 0)
            changed = val != base
            ico = ed.get_sprite("ui", ico_k, 34)
            if ico is not None:
                surf.blit(ico, ico.get_rect(midleft=(r.x + 18, r.centery)))
            text(surf, label, (r.x + 66, r.centery), 25, TEXT, "bold", anchor="midleft", maxw=r.w - 310)
            text(surf, f"{val:,}", (r.right - 134, r.centery), 29, ACCENT if changed else TEXT, "black", anchor="midright")
            visible = self._right_area.top <= r.centery <= self._right_area.bottom
            for d, direction, cx in ((-step, "left", r.right - 92), (step, "right", r.right - 38)):
                ar = pygame.Rect(0, 0, 44, 44)
                ar.center = (cx, r.centery)
                if visible:
                    self.arrow_rects[(key, d)] = ar
                pygame.draw.rect(surf, CARD_HI if focused else mix(CARD, CARD_HI, .5), ar, border_radius=11)
                pygame.draw.rect(surf, INK, ar, width=2, border_radius=11)
                tri(surf, TEXT if focused else DIM, ar.center, 9, direction)
            return
        if kind == "backpack":
            ico = ed.get_sprite("ui", "Treasure", 34)
            if ico is not None:
                surf.blit(ico, ico.get_rect(midleft=(r.x + 18, r.centery)))
            bp_n = len(self.app.snap.parsed["items"]["backpack"])
            text(surf, f"Adventure Backpack ({bp_n} items)", (r.x + 66, r.centery), 25, INFO, "black", anchor="midleft", maxw=r.w - 120)
            tri(surf, ACCENT if focused else INFO, (r.right - 28, r.centery), 10, "right")
            return
        # Adventure party cat card
        c = obj
        ccol = CLASS_COL.get(c["class"], ACCENT)
        cbadge = pygame.Rect(r.x + 14, r.centery - 28, 56, 56)
        ed.draw_sprite_badge(sys.modules[__name__], surf, "class", c["class"], cbadge, border_col=ccol, fallback_col=ccol)
        nr = text(surf, c["name"] or f"#{c['key']}", (cbadge.right + 14, r.y + 12), 27, TEXT, "black", maxw=260)
        bx = nr.right + 10
        if c["injured"] or c["wounded"]:
            bx = pill(surf, gd.injury_info(c["injury"])["name"] or "Wounded", bx, nr.centery, BAD, DARK, size=15, anchor="midleft").right + 6
        if "DejaVu" in (c["abilities"]["disorder"] or []):
            pill(surf, "DÉJÀ VU", bx, nr.centery, (220, 90, 190), DARK, size=15, anchor="midleft")
        text(surf, f"{c['class']} · Lv {c['level']} · HP {c['hp']}/{c['max_hp']} · Sum {br.stat_sum(c)}",
             (cbadge.right + 14, r.bottom - 12), 20, DIM, anchor="bottomleft", maxw=r.w - 130)
        tri(surf, ACCENT if focused else DIM, (r.right - 26, r.centery), 10, "right")

    def draw(self, surf, area) -> None:
        snap = self.app.snap
        st = snap.steven
        p = self.app.pending
        self.rects, self.arrow_rects = {}, {}
        mpos = getattr(self.app, "mouse_pos", (-1, -1))

        # ---- Left column: Steven Save-Scum Watch & Adventure Party Fixes
        hero = pygame.Rect(area.x, area.y, 960, area.h)
        rrect(surf, hero, CARD, 24, shadow=True)
        alert = st.active or st.dejavu_cats > 0
        draw_top_band(surf, pygame.Rect(hero.x + 3, hero.y + 3, hero.w - 6, 154), BAD if alert else GOOD, 34, radius=21)
        icon_status(surf, hero.x + 76, hero.y + 84, 38, not alert)
        text(surf, "STEVEN SAVE-SCUM WATCH", (hero.x + 136, hero.y + 20), 18, FAINT, "black")
        headline = f"Penalty armed ({st.where})" if st.active else (f"{st.dejavu_cats} cats have Déjà Vu" if st.dejavu_cats else "All clear")
        text(surf, headline, (hero.x + 136, hero.y + 48), 40, BAD if alert else GOOD, "black", maxw=hero.w - 360)
        sub = [f"{st.strikes} strike{'s' if st.strikes != 1 else ''}"]
        if st.dejavu_cats:
            sub.append(f"{st.dejavu_cats} Déjà Vu")
        if st.guarded:
            sub.append("auto-block on")
        sub.append("in run" if st.on_adventure else "at home")
        text(surf, "  ·  ".join(sub), (hero.x + 136, hero.y + 104), 22, DIM, maxw=hero.w - 170)
        # Cap strike pips at 8 so high strike counts never collide with headline
        shown_pips = min(8, max(5, st.strikes))
        pips(surf, hero.right - 34 - shown_pips * 26 + 6, hero.y + 30, min(st.strikes, shown_pips), shown_pips, BAD,
             20, 20, 6, mix(LINE, CARD, .45))

        guard_pill = (
            ("QUEUED ON" if p.guard else "QUEUED OFF", GOOD if p.guard else ACCENT, DARK)
            if p.guard is not None else
            ("ON" if st.guarded else "OFF", GOOD if st.guarded else LINE, DARK if st.guarded else TEXT)
        )
        btns = [
            ("reset", "Reset penalty & Déjà Vu", "Clears active penalty, keeps 1 strike & strips Déjà Vu", "Recycle", p.steven == "cap", True),
            ("wipe", "Reset + wipe all strikes", "Deletes entire Steven strike history & strips Déjà Vu", "Boss", p.steven == "clear", False),
            ("restart", "Close game, reset & relaunch", "One button: close Mewgenics, save reset & start via Steam", "Move", False, False),
            ("guard", "Auto-block Steven", "SQLite trigger prevents penalty from arming again", "Shield", p.guard is not None, False),
            ("clear_party_dis", "Clear party disorders", "Removes all disorders on every Adventure Party cat", "Heart", p.clear_party_disorders, False),
            ("max_party", "Max party stats (All 7)", "Sets all 7 base stats to 7 on your Adventure Party", "STR", p.max_party_stats, False),
        ]
        by = hero.y + 174
        for key, label, desc, ico_k, queued, primary in btns:
            r = pygame.Rect(hero.x + 28, by, hero.w - 56, 82)
            self.rects[key] = r
            hov = r.collidepoint(mpos)
            if queued and key != "guard":
                col = mix(GOOD, CARD, .45)
            elif primary and alert:
                col = BAD
            elif hov:
                col = CARD_HI
            else:
                col = mix(CARD, CARD_HI, .5)
            rrect(surf, r, col, 16)
            fg = DARK if (primary and alert and not queued) else TEXT
            ib = pygame.Rect(r.x + 14, r.centery - 24, 48, 48)
            ed.draw_sprite_badge(sys.modules[__name__], surf, "ui", ico_k, ib)
            text(surf, label, (ib.right + 16, r.y + 11), 27, fg, "black", maxw=r.w - 250)
            text(surf, desc, (ib.right + 16, r.bottom - 11), 19, mix(fg, col, .24), anchor="bottomleft", maxw=r.w - 250)
            if key == "guard":
                pill(surf, guard_pill[0], r.right - 22, r.centery, guard_pill[1], guard_pill[2], 18, anchor="midright")
            elif queued:
                pill(surf, "QUEUED", r.right - 22, r.centery, GOOD, DARK, 18, anchor="midright")
            else:
                tri(surf, fg, (r.right - 28, r.centery), 10, "right")
            by += 92

        # ---- Right column: Adventure Resources & Live 4-Cat Party
        col_x = hero.right + 32
        col_w = area.right - col_x
        adv_n = sum(1 for c in snap.parsed["cats"] if c["room"] == "(ADVENTURE)")
        text(surf, f"ADVENTURE RESOURCES & PARTY ({adv_n})", (col_x + 4, area.y + 4), 19, INFO, "black")
        r_area = pygame.Rect(col_x, area.y + 34, col_w - 14, area.bottom - area.y - 34)
        self._right_area = r_area
        self.right_list.draw(surf, r_area, self._render_right_row, show_focus=(self.col == 1))
        if self.col == 0:
            target = self.rects[self.ACTIONS[self.act_idx]]
            focus_ring(surf, self.ring.to(target), 16)

    def hint_items(self):
        if self.col == 1:
            row = self.right_list.current
            if row and row.data[0] == "res":
                return [("DPAD_LR", "Adjust"), ("X", "Max"), ("SELECT", "Switch Col"), ("B", "Saves")]
            return [("A", "Edit Cat / Open"), ("SELECT", "Switch Col"), ("B", "Saves")]
        return [("A", "Toggle / Run"), ("DPAD_LR", "Resources & Party"), ("B", "Saves")]


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
                self.app.toast(f"Queued Revive » Attic for {c['name'] or '#' + str(c['key'])} · click Save", GOOD, 3.0)
            elif c["injured"] or c["wounded"]:
                self.app.pending.cats.setdefault(c["key"], {})["heal"] = True
                self.app.toast(f"Queued Heal for {c['name'] or '#' + str(c['key'])} · click Save", GOOD, 3.0)
            else:
                self.sex_idx = (self.sex_idx + 1) % len(self.SEX_FILTERS)
                self.list.sel = 0
                self.on_enter()
                self.app.toast(f"Sex filter: {self.SEX_FILTERS[self.sex_idx]}", INFO, 1.5)
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
        ccol = CLASS_COL.get(c["class"], (162, 152, 142))
        rrect(surf, (r.x + 4, r.y + 8, 8, r.h - 16), ccol if not gone else LINE, 4, ink=False)
        scol = SEX_COL.get(c["sex"], DIM)
        cbadge = pygame.Rect(r.x + 24, r.centery - 36, 72, 72)
        ed.draw_sprite_badge(sys.modules[__name__], surf, "class", c["class"], cbadge,
                             border_col=ccol if not gone else LINE, fallback_col=ccol)
        # Compact inked sex pip on bottom-right corner of class collar badge
        pygame.draw.circle(surf, INK, (cbadge.right - 6, cbadge.bottom - 6), 15)
        pygame.draw.circle(surf, scol, (cbadge.right - 6, cbadge.bottom - 6), 12)
        text(surf, {"Male": "M", "Female": "F"}.get(c["sex"], "?"), (cbadge.right - 6, cbadge.bottom - 6), 17, DARK, "black",
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
        # effective stat bars (inked Mewgenics meters)
        x0 = r.right - 40 - 7 * 46
        for i, s in enumerate(STATS):
            v = c["effective_stats"][s]
            bx = x0 + i * 46
            bh = 60
            bar_r = pygame.Rect(bx, r.y + 18, 24, bh)
            pygame.draw.rect(surf, mix(LINE, CARD, .45), bar_r, border_radius=7)
            fh = round(bh * max(0, min(7, v)) / 7)
            if fh > 0:
                pygame.draw.rect(surf, stat_col(v) if not gone else LINE, (bx, r.y + 18 + bh - fh, 24, fh), border_radius=7)
            pygame.draw.rect(surf, INK, bar_r, width=2, border_radius=7)
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
        if cur and cur["dead"]:
            extra = [("X", "Revive")]
        elif cur and (cur["injured"] or cur["wounded"]):
            extra = [("X", "Heal")]
        else:
            extra = [("X", "Sex")]
        return [("A", "Edit cat")] + extra + [("Y", "Sort"), ("SELECT", "Search"), ("DPAD_LR", "Filter"),
                                              ("B", "Clear" if (self.query or self.sex_idx) else "Saves")]


class CatEdit(Screen):
    SEXES = ["Male", "Female", "Ditto"]

    def __init__(self, app: "App", cats: list[dict[str, Any]], index: int) -> None:
        super().__init__(app)
        self.cats, self.idx = cats, index
        self.list = ListView(row_h=80, gap=8)
        self.card_mode = False
        self.card_idx = 0
        self.hover_idx: int | None = None
        self.card_rects: list[tuple[pygame.Rect, dict[str, Any]]] = []
        self.stat_rects: list[tuple[pygame.Rect, str, int]] = []
        self.adj_rects: list[tuple[pygame.Rect, str, int]] = []
        self.nav_rects: dict[str, pygame.Rect] = {}
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
            Row("Build Presets & Clone", data=("sub", "presets")),
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

    def _ability_cards(self) -> list[dict[str, Any]]:
        """Build structured visual card descriptors for the cat's Actives, Passives & Disorders."""
        ab = self.vals["abilities"]
        cards: list[dict[str, Any]] = []
        act_labels = ("Move", "Basic", "Active 1", "Active 2", "Active 3", "Active 4")
        for idx in range(1, 6):
            aid = ab["active"][idx]
            if not aid:
                continue
            info = gd.active_info(aid)
            cards.append({
                "kind": "active",
                "slot": idx,
                "sprite_kind": "ability",
                "id": aid,
                "name": info["name"],
                "tag": f"{info['mana']} MP" if str(info.get("mana") or "").strip() not in ("", "0", "None") else act_labels[idx],
                "accent": CLASS_COL.get(info.get("class", ""), INFO),
                "info": info,
                "desc": info.get("desc") or "No description available.",
            })
        for idx, (pid, tier) in enumerate(zip(ab["passive"], ab["passive_tiers"])):
            if not pid:
                continue
            pi = gd.passive_info(pid, tier)
            cards.append({
                "kind": "passive",
                "slot": idx,
                "sprite_kind": "ability",
                "id": pid,
                "name": pi.get("display_name", pi["name"]),
                "tag": f"Passive T{tier}{'+' if tier == 2 else ''}",
                "accent": ACCENT,
                "info": pi,
                "desc": pi.get("active_desc") or pi.get("desc") or "No description available.",
            })
        for idx, did in enumerate(ab["disorder"]):
            if not did:
                continue
            di = gd.disorder_info(did)
            cards.append({
                "kind": "disorder",
                "slot": idx,
                "sprite_kind": "disorder",
                "id": did,
                "name": di["name"],
                "tag": "Disorder",
                "accent": BAD,
                "info": di,
                "desc": di.get("desc") or "No description available.",
            })
        return cards

    def _gear_cards(self) -> list[dict[str, Any]]:
        """Build 5 visual card descriptors for the cat's Equipped Gear mini-bar."""
        eq = self.vals["equipment"]
        cards: list[dict[str, Any]] = []
        for idx, sk in enumerate(("weapon", "head", "face", "neck", "trinket")):
            iid = eq.get(sk)
            info = gd.item_info(iid) if iid else None
            rc = RARITY_COL.get(info.get("rarity", ""), INFO) if info else LINE
            st = gd.format_stats(info.get("stats")) if info else ""
            sets = gd.item_set_info(info) if info else []
            parts = []
            if st:
                parts.append(st)
            if info and info.get("desc"):
                parts.append(info["desc"])
            if sets:
                parts.append(f"Set: {sets[0]['name']} — {sets[0].get('desc', '')}")
            cards.append({
                "kind": "equip",
                "slot": idx,
                "sprite_kind": "item",
                "id": iid,
                "name": info["name"] if info else f"Empty {cb.EQUIP_SLOT_LABELS[sk].split()[0]}",
                "tag": cb.EQUIP_SLOT_LABELS[sk].split()[0],
                "accent": rc if info else LINE,
                "info": info or {},
                "desc": " · ".join(parts) if parts else f"No {cb.EQUIP_SLOT_LABELS[sk].lower()} equipped. Click or press A to equip.",
            })
        return cards

    def _all_inspect_cards(self) -> list[dict[str, Any]]:
        return self._ability_cards() + self._gear_cards()

    def _open_card(self, card: dict[str, Any]) -> None:
        mod = sys.modules[__name__]
        if card["kind"] == "equip":
            scr = ed.EquipScreen(mod, self)
            scr.list.sel = min(card["slot"], max(0, len(scr.list.rows) - 1))
            self.app.push(scr)
            return
        scr = ed.AbilitiesScreen(mod, self, card["kind"])
        scr.list.sel = min(card["slot"], max(0, len(scr.list.rows) - 1))
        self.app.push(scr)

    def hover(self, pos) -> None:
        self.hover_idx = None
        for idx, (r, _) in enumerate(self.card_rects):
            if r.collidepoint(pos):
                self.hover_idx = idx
                return

    def handle(self, btn: str) -> None:
        if btn in (LB, RB):
            self.idx = (self.idx + (1 if btn == RB else -1)) % len(self.cats)
            self.card_mode = False
            self.card_idx = 0
            self.hover_idx = None
            self.load()
            return
        if btn == Y:
            cards = self._all_inspect_cards()
            if cards:
                self.card_mode = not self.card_mode
                self.card_idx = min(self.card_idx, len(cards) - 1)
            return
        if self.card_mode:
            cards = self._all_inspect_cards()
            if not cards:
                self.card_mode = False
            else:
                if btn == UP:
                    self.card_idx = max(0, self.card_idx - 2)
                elif btn == DOWN:
                    self.card_idx = min(len(cards) - 1, self.card_idx + 2)
                elif btn == LEFT:
                    if self.card_idx == 0:
                        self.card_mode = False
                    else:
                        self.card_idx -= 1
                elif btn == RIGHT:
                    self.card_idx = min(len(cards) - 1, self.card_idx + 1)
                elif btn == A:
                    self._open_card(cards[self.card_idx])
                elif btn == START:
                    self.app.write()
                elif btn == B:
                    self.card_mode = False
                return

        row = self.list.current
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn in (LT, RT):
            self.list.move(-6 if btn == LT else 6)
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
                cur_atk = self.vals["abilities"]["active"][1]
                if cur_atk in cb.CLASS_BASIC_ATTACK.values():
                    self.vals["abilities"]["active"][1] = cb.CLASS_BASIC_ATTACK[new_cls]
            elif kind == "retired":
                self.vals["retired"] = not self.vals["retired"]
            elif btn == RIGHT and self._ability_cards():
                self.card_mode = True
                self.card_idx = 0
                return
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
                elif which == "presets":
                    self.app.push(ed.PresetsScreen(mod, self))
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
        for nav_btn, nr in self.nav_rects.items():
            if nr.collidepoint(pos):
                sfx.play("tab")
                self.handle(nav_btn)
                return
        for r, s, val in self.stat_rects:
            if r.collidepoint(pos):
                self.card_mode = False
                self.vals["stats"][s] = val
                self.commit_local()
                sfx.play("tick")
                return
        for r, kind, d in self.adj_rects:
            if r.collidepoint(pos):
                self.card_mode = False
                for idx, row in enumerate(self.list.rows):
                    if row.data[0] == kind:
                        self.list.sel = idx
                        self.handle(RIGHT if d > 0 else LEFT)
                        sfx.play("tick")
                        return
        for idx, (r, card) in enumerate(self.card_rects):
            if r.collidepoint(pos):
                self.card_idx = idx
                self.hover_idx = idx
                sfx.play("ok")
                self._open_card(card)
                return
        self.card_mode = False
        i = self.list.hit(pos)
        if i is not None:
            self.list.sel = i
            kind = self.list.rows[i].data[0]
            if kind in ("sub", "rename", "heal", "revive", "max", "clean", "revert"):
                sfx.play("ok")
                self.handle(A)

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
        if which == "presets":
            clip = getattr(self.app, "cat_clipboard", None)
            return (f"Clipboard: {clip['name']}" if clip else "Templates & Copy", ACCENT if clip else DIM)
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
            ico = ed.get_sprite("ui", s, 30)
            if ico is not None:
                surf.blit(ico, ico.get_rect(midleft=(r.x + 122, r.centery)))
            x = r.x + 188
            for i in range(7):
                filled = i < new
                was = i < old
                colr = stat_col(new) if filled and was else ACCENT if filled else mix(BAD, CARD, .4) if was else mix(LINE, CARD, .3)
                sr = pygame.Rect(x + i * 54, r.centery - 15, 46, 30)
                pygame.draw.rect(surf, colr, sr, border_radius=8)
                pygame.draw.rect(surf, INK, sr, width=2, border_radius=8)
                if HEADER_H + 24 <= sr.centery <= CONTENT_BOTTOM:
                    self.stat_rects.append((sr, s, i + 1))
            bon = v["level_bonuses"][s]
            val_txt = f"{new}{'+' if bon > 0 else ''}{bon}" if bon else str(new)
            text(surf, val_txt, (r.right - 74, r.centery), 32, ACCENT if new != old else TEXT, "black", anchor="center")
        elif kind in ("level", "age", "sex", "retired", "class", "room"):
            val = v[kind]
            shown = ("yes" if val else "no") if kind == "retired" else (
                f"{val} days" if kind == "age" else (str(val).replace("_", " ") if kind == "room" else str(val)))
            changed = val != c[kind]
            lr = pygame.Rect(r.right - 292, r.centery - 22, 44, 44)
            rr = pygame.Rect(r.right - 62, r.centery - 22, 44, 44)
            for ar, d, direction in ((lr, -1, "left"), (rr, 1, "right")):
                pygame.draw.rect(surf, CARD_HI if selected else mix(CARD, CARD_HI, .5), ar, border_radius=10)
                pygame.draw.rect(surf, INK, ar, width=2, border_radius=10)
                tri(surf, TEXT if selected else DIM, ar.center, 9, direction)
                if HEADER_H + 24 <= ar.centery <= CONTENT_BOTTOM:
                    self.adj_rects.append((ar, kind, d))
            text(surf, shown, (r.right - 155, r.centery), 27, ACCENT if changed else col, "black", anchor="center", maxw=172)
            if changed:
                was = c[kind]
                was_s = ("yes" if was else "no") if kind == "retired" else str(was).replace("_", " ")
                text(surf, f"was {was_s}", (r.x + 218, r.centery), 20, DIM, anchor="midleft", maxw=r.w - 525)
        else:
            text(surf, "A", (r.right - 54, r.centery), 27, DIM, "black", anchor="center")

    def draw(self, surf) -> None:
        self.stat_rects, self.adj_rects, self.nav_rects = [], [], {}
        c = self.cat
        cls_name = self.vals["class"]
        ccol = CLASS_COL.get(cls_name, ACCENT)
        card = pygame.Rect(W - PAD - 610, HEADER_H + 24, 610, CONTENT_BOTTOM - HEADER_H - 24)
        rrect(surf, card, CARD, 24, shadow=True)

        # Rounded-top inked header band with official Class Collar sprite
        band = pygame.Rect(card.x + 3, card.y + 3, card.w - 6, 128)
        draw_top_band(surf, band, ccol, 46, radius=21)

        cbadge = pygame.Rect(card.x + 22, card.y + 20, 88, 88)
        ed.draw_sprite_badge(sys.modules[__name__], surf, "class", cls_name, cbadge, border_col=ccol, fallback_col=ccol)
        text(surf, self.vals["name"] or f"#{c['key']}", (card.x + 128, card.y + 22), 36, TEXT, "black", maxw=card.w - 280)
        pill(surf, cls_name, card.x + 128, card.y + 74, ccol, size=19)
        text(surf, f"Cat {self.idx + 1}/{len(self.cats)}", (card.right - 22, card.y + 84), 18, DIM, "bold", anchor="midright")

        # Non-overlapping status & vitals bar
        room = "Adventure" if self.vals["room"] == "(ADVENTURE)" else (self.vals["room"] or "Graveyard").replace("_", " ")
        inj_name = gd.injury_info(c["injury"])["name"] if (c["injury"] and not self.vals["heal"]) else ""
        status = "Healthy" if not (c["dead"] or inj_name) else ("Dead" if c["dead"] else inj_name)
        if self.vals["revive"]:
            status = "Reviving"
        elif self.vals["heal"] and (c["injured"] or c["wounded"]):
            status = "Healing"
        eff_con = max(1, self.vals["stats"]["CON"] + self.vals["level_bonuses"]["CON"] + (0 if self.vals["heal"] else c["injury_mods"]["CON"]))
        eff_cha = max(1, self.vals["stats"]["CHA"] + self.vals["level_bonuses"]["CHA"] + (0 if self.vals["heal"] else c["injury_mods"]["CHA"]))
        hp_str = f"{eff_con * 4}/{eff_con * 4}" if (self.vals["heal"] or c["hp_raw"] == cb.HP_FULL) else f"{c['hp']}/{eff_con * 4}"

        sy = band.bottom + 12
        sx = card.x + 22
        sx = pill(surf, room, sx, sy, INFO if c["room"] == "(ADVENTURE)" else CARD_HI, DARK if c["room"] == "(ADVENTURE)" else TEXT, size=17).right + 8
        scol = BAD if (inj_name or status == "Dead") else GOOD
        pill(surf, status, sx, sy, mix(scol, CARD, .62), scol, size=17)
        hp_ico = ed.get_sprite("ui", "HP", 20)
        mp_ico = ed.get_sprite("ui", "Mana", 20)
        mp_r = text(surf, f"{eff_cha * 3}", (card.right - 22, sy + 15), 19, INFO, "black", anchor="midright")
        if mp_ico is not None:
            surf.blit(mp_ico, mp_ico.get_rect(midright=(mp_r.left - 4, sy + 15)))
        hp_r = text(surf, hp_str, (mp_r.left - 36, sy + 15), 19, GOOD, "black", anchor="midright")
        if hp_ico is not None:
            surf.blit(hp_ico, hp_ico.get_rect(midright=(hp_r.left - 4, sy + 15)))

        # Interactive Ability, Passive & Disorder Cards + 5-Slot Equipped Gear Mini-Bar
        gy = sy + 42
        text(surf, "ABILITIES & DISORDERS", (card.x + 22, gy), 16, FAINT, "black")
        text(surf, "Hover or Y to inspect · Click to edit", (card.right - 22, gy), 16, ACCENT if self.card_mode else FAINT, "bold", anchor="topright")
        gy += 24

        ab_cards = self._ability_cards()
        gear_cards = self._gear_cards()
        all_cards = ab_cards + gear_cards
        self.card_rects = []
        gap = 8
        cw = (card.w - 44 - gap) // 2
        ch = 60
        active_tip_idx: int | None = self.card_idx if self.card_mode else self.hover_idx
        if active_tip_idx is not None and active_tip_idx >= len(all_cards):
            active_tip_idx = None

        cur = self.list.current
        bottom_reserve = 186
        gear_h = 84
        max_ab_bottom = card.bottom - bottom_reserve - gear_h - 10

        if not ab_cards:
            text(surf, "No abilities or disorders equipped.", (card.centerx, gy + 30), 21, FAINT, "bold", anchor="midtop")
        else:
            for i, ac in enumerate(ab_cards):
                col_i, row_i = i % 2, i // 2
                cx = card.x + 22 + col_i * (cw + gap)
                cy = gy + row_i * (ch + gap)
                if cy + ch > max_ab_bottom:
                    break
                cr = pygame.Rect(cx, cy, cw, ch)
                self.card_rects.append((cr, ac))
                is_hi = (i == active_tip_idx)
                bg = CARD_HI if is_hi else mix(CARD, WELL, .32)
                rrect(surf, cr, bg, 13, ink=True)
                if is_hi:
                    pygame.draw.rect(surf, ACCENT, cr.inflate(2, 2), width=3, border_radius=14)
                rrect(surf, (cr.x + 4, cr.y + 6, 5, cr.h - 12), ac["accent"], 3, ink=False)
                ib = pygame.Rect(cr.x + 13, cr.centery - 22, 44, 44)
                ed.draw_sprite_badge(sys.modules[__name__], surf, ac["sprite_kind"], ac["id"], ib,
                                     border_col=ac["accent"], fallback_col=ac["accent"])
                tcol = BAD if ac["kind"] == "disorder" else (ACCENT if ac["kind"] == "passive" else TEXT)
                text(surf, ac["name"], (ib.right + 9, cr.y + 10), 20, tcol, "black", maxw=cr.w - 72)
                text(surf, ac["tag"], (ib.right + 9, cr.bottom - 10), 16, DIM, "bold", anchor="bottomleft", maxw=cr.w - 72)

        # 5-Slot Equipped Gear Mini-Bar (hoverable + clickable straight to EquipScreen!)
        gear_y = card.bottom - bottom_reserve - gear_h + 2
        text(surf, "EQUIPPED GEAR", (card.x + 22, gear_y - 20), 16, FAINT, "black")
        gw = (card.w - 44 - 4 * 8) // 5
        for j, gc in enumerate(gear_cards):
            idx_all = len(ab_cards) + j
            gr = pygame.Rect(card.x + 22 + j * (gw + 8), gear_y, gw, 72)
            self.card_rects.append((gr, gc))
            is_hi = (idx_all == active_tip_idx)
            rrect(surf, gr, CARD_HI if is_hi else mix(CARD, WELL, .35), 13, ink=True)
            if is_hi:
                pygame.draw.rect(surf, ACCENT, gr.inflate(2, 2), width=3, border_radius=14)
            ib = pygame.Rect(gr.centerx - 22, gr.y + 6, 44, 44)
            if gc["id"]:
                ed.draw_item_badge(sys.modules[__name__], surf, gc["id"], ib, gc["info"].get("rarity", ""))
            else:
                pygame.draw.rect(surf, WELL, ib, border_radius=10)
                pygame.draw.rect(surf, LINE, ib, width=2, border_radius=10)
            text(surf, gc["tag"], (gr.centerx, gr.bottom - 5), 14, TEXT if gc["id"] else FAINT, "bold", anchor="midbottom")

        # Bottom live Card Inspector Panel (hovered/focused Ability OR Gear OR selected Stat!)
        tbox = pygame.Rect(card.x + 20, card.bottom - bottom_reserve + 10, card.w - 40, bottom_reserve - 22)
        if active_tip_idx is not None:
            ac = all_cards[active_tip_idx]
            rrect(surf, tbox, CARD_HI, 18, ink=True, shadow=True)
            rrect(surf, (tbox.x + 4, tbox.y + 8, 7, tbox.h - 16), ac["accent"], 4, ink=False)
            ib = pygame.Rect(tbox.x + 20, tbox.y + 18, 66, 66)
            if ac["id"]:
                ed.draw_sprite_badge(sys.modules[__name__], surf, ac["sprite_kind"], ac["id"], ib, border_col=ac["accent"])
            else:
                pygame.draw.rect(surf, WELL, ib, border_radius=14)
                pygame.draw.rect(surf, LINE, ib, width=2, border_radius=14)
            tx = ib.right + 14
            tw = tbox.right - tx - 18
            text(surf, ac["name"], (tx, tbox.y + 14), 26, TEXT, "black", maxw=tw - 92)
            pill(surf, ac["tag"], tbox.right - 16, tbox.y + 26, ac["accent"], DARK, size=15, anchor="midright")
            dy = tbox.y + 48
            for ln in ed.wrap_lines(F(20), ac["desc"], tw, max_lines=4):
                if dy + 23 > tbox.bottom - 24:
                    break
                text(surf, ln, (tx, dy), 20, DIM)
                dy += 23
            text(surf, "Click or press A to swap this slot", (tx, tbox.bottom - 12), 16, ACCENT, "bold", anchor="bottomleft")
        elif cur and cur.data[0] == "stat":
            rrect(surf, tbox, CARD_HI, 18, ink=True)
            s = cur.data[1]
            ico = ed.get_sprite("ui", s, 40)
            if ico is not None:
                surf.blit(ico, ico.get_rect(topleft=(tbox.x + 20, tbox.y + 16)))
            text(surf, f"{s}  (Base {self.vals['stats'][s]}/7)", (tbox.x + 70, tbox.y + 22), 25, stat_col(self.vals["stats"][s]), "black")
            for i, l in enumerate(ed.wrap_lines(F(22), STAT_INFO[s], tbox.w - 44, max_lines=3)):
                text(surf, l, (tbox.x + 22, tbox.y + 68 + i * 28), 22, DIM)
        else:
            rrect(surf, tbox, WELL, 18, ink=True)
            text(surf, "CARD INSPECTOR", (tbox.x + 22, tbox.y + 18), 16, FAINT, "black")
            for i, l in enumerate(ed.wrap_lines(
                F(21),
                "Hover over any ability, passive, disorder, or equipped gear card above — or press Y on gamepad — to inspect full effects and click to swap slots.",
                tbox.w - 44, max_lines=3,
            )):
                text(surf, l, (tbox.x + 22, tbox.y + 48 + i * 27), 21, DIM)

        area = pygame.Rect(PAD, HEADER_H + 24, card.x - PAD - 32, CONTENT_BOTTOM - HEADER_H - 24)
        self.list.draw(surf, area, self.render_row, show_focus=not self.card_mode)
        lb_x = card.x + card.w - 134
        rb_x = card.x + card.w - 72
        glyph(surf, "LB", lb_x, card.y + 30)
        glyph(surf, "RB", rb_x, card.y + 30)
        self.nav_rects[LB] = pygame.Rect(lb_x, card.y + 12, 54, 36)
        self.nav_rects[RB] = pygame.Rect(rb_x, card.y + 12, 54, 36)

    def hint_items(self):
        if self.card_mode:
            return [("A", "Edit Slot"), ("DPAD", "Inspect Cards"), ("B", "Back to List"), ("LBRB", "Prev/next cat")] + pending_hint(self.app)
        return [("A", "Open / Run"), ("Y", "Inspect Cards"), ("DPAD_LR", "Adjust"), ("LBRB", "Prev/next cat"), ("B", "Done")] + pending_hint(self.app)


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
        if getattr(self, "chip_prev_rect", None) and self.chip_prev_rect.collidepoint(pos):
            self.t = (self.t - 1) % len(self.tabs)
            self.list.sel = 0
            self._rebuild()
            return
        if getattr(self, "chip_next_rect", None) and self.chip_next_rect.collidepoint(pos):
            self.t = (self.t + 1) % len(self.tabs)
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
        text(surf, row.label, (r.x + 86, r.centery), 29, ACCENT if kind == "queued" else TEXT, "bold",
             anchor="midleft", maxw=r.w - 430)
        st = gd.format_stats(it.get("stats"))
        rar = (it.get("rarity") or "").replace("_", " ")
        knd = (it.get("kind") or "other").replace("_", " ")
        meta = st if st else " · ".join(x for x in (rar, knd) if x)
        text(surf, meta, (r.right - 108, r.centery), 20, GOOD if st else FAINT, "bold" if st else "semi",
             anchor="midright", maxw=230)
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
        while sum(widths[start:self.t + 1]) > (chips_right - area.x - 56) and start < self.t:
            start += 1
        x = area.x
        self.chip_prev_rect = None
        self.chip_next_rect = None
        if start:
            pr = pygame.Rect(x, cy + 2, 26, 28)
            self.chip_prev_rect = pr
            tri(surf, ACCENT, pr.center, 8, "left")
            x += 30
        last_drawn = start - 1
        for i in range(start, len(self.tabs)):
            sel = i == self.t
            w_est = F(21, "bold").size(labels[i])[0] + 21
            if x + w_est > chips_right - 28:
                break
            r = pill(surf, labels[i], x, cy, ACCENT if sel else CARD, DARK if sel else DIM, 21)
            self.chip_rects.append((r, i))
            x = r.right + 8
            last_drawn = i
        if last_drawn < len(self.tabs) - 1:
            nr = pygame.Rect(chips_right - 24, cy + 2, 26, 28)
            self.chip_next_rect = nr
            tri(surf, ACCENT, nr.center, 8, "right")

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
        self.list = ListView(row_h=98, gap=10)
        self._diff_cache: dict[str, list[tuple[str, str, str]]] = {}

    def on_enter(self) -> None:
        self._diff_cache.clear()
        self.list.set_rows([Row(b.when.strftime("%b %d"), data=b) for b in sf.list_backups(self.app.save)])

    def _get_diff(self, b: sf.Backup) -> list[tuple[str, str, str]]:
        k = str(b.path)
        if k not in self._diff_cache:
            try:
                bsnap = sf.snapshot(b.path)
                self._diff_cache[k] = sf.diff_snapshots(self.app.snap, bsnap)
            except Exception as e:  # noqa: BLE001
                self._diff_cache[k] = [("Error", str(e), "")]
        return self._diff_cache[k]

    def handle(self, btn: str) -> bool:
        if btn in (UP, DOWN):
            self.list.move(-1 if btn == UP else 1)
        elif btn in (LT, RT):
            self.list.move(-8 if btn == LT else 8)
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
        text(surf, b.when.strftime("%a %b %d"), (r.x + 28, r.y + 14), 32, TEXT, "black")
        text(surf, b.when.strftime("%H:%M:%S"), (r.x + 28, r.bottom - 14), 24, DIM, anchor="bottomleft")
        reason = b.reason.replace("before-", "before ").replace("-", " ") or "backup"
        pill(surf, reason, r.x + 310, r.centery, CARD_HI if not selected else LINE, TEXT, 20, anchor="midleft")
        text(surf, f"Day {b.day if b.day is not None else '?'}", (r.right - 220, r.centery), 26, DIM, "bold",
             anchor="midright")
        on = b.penalty not in (None, 0)
        pill(surf, "Steven ON" if on else "clear", r.right - 24, r.centery, BAD if on else mix(GOOD, CARD, .3),
             size=20, anchor="midright")

    def draw(self, surf, area) -> None:
        bdir = str(sf.backup_dir_for(self.app.save))
        home = str(Path.home())
        if bdir.startswith(home):
            bdir = "~" + bdir[len(home):]
        text(surf, bdir, (area.x, area.y), 22, FAINT, maxw=area.w)
        la = pygame.Rect(area.x, area.y + 44, area.w - 590, area.h - 44)
        if not self.list.rows:
            text(surf, "No backups yet. One is made automatically before every save.", (area.centerx, area.y + 108),
                 32, DIM, "bold", anchor="midtop")
            return
        self.list.draw(surf, la, self.render_row)

        # Right panel: Live Diff vs Current Save
        dbox = pygame.Rect(area.right - 550, area.y + 44, 550, area.h - 44)
        rrect(surf, dbox, CARD, 24, shadow=True)
        b: sf.Backup = self.list.current.data
        x, y, w = dbox.x + 28, dbox.y + 24, dbox.w - 56
        text(surf, "BACKUP DIFF VS CURRENT SAVE", (x, y), 17, ACCENT, "black")
        y += 30
        text(surf, b.when.strftime("%b %d · %H:%M:%S"), (x, y), 30, TEXT, "black")
        y += 48
        for lbl, bval, delta in self._get_diff(b):
            if y + 32 > dbox.bottom - 50:
                break
            text(surf, lbl, (x, y), 21, DIM, "bold", maxw=220)
            text(surf, bval, (dbox.right - 160, y), 22, TEXT, "black", anchor="topright", maxw=135)
            dcol = FAINT if delta == "same" else (GOOD if delta.startswith("+") else BAD if delta.startswith("-") else ACCENT)
            text(surf, delta, (dbox.right - 28, y), 20, dcol, "bold", anchor="topright", maxw=125)
            y += 36
        text(surf, "Press A to restore (auto-backs up current first).", (x, dbox.bottom - 34), 20, FAINT, maxw=w)

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
        shade.fill((16, 13, 11, int(200 * k)))
        surf.blit(shade, (0, 0))
        lines = ed.wrap_lines(F(27), self.body, 920, max_lines=6)
        box_h = max(390, 260 + len(lines) * 38)
        r = pygame.Rect(0, 0, 1040, box_h)
        r.center = (W // 2, H // 2 + int((1 - k) * 36))
        rrect(surf, r, CARD, 28, shadow=True)
        col = BAD if self.danger else ACCENT
        draw_top_band(surf, pygame.Rect(r.x + 3, r.y + 3, r.w - 6, 108), col, 40, radius=25)
        text(surf, self.msg, (r.x + 56, r.y + 32), 44, TEXT, "black", maxw=r.w - 112)
        for i, ln in enumerate(lines):
            text(surf, ln, (r.x + 56, r.y + 132 + i * 38), 27, DIM, maxw=r.w - 112)
        self.btn_rects = {}
        bx = r.right - 56
        for key, label, bg, fg in (("A", self.yes, col, DARK), ("B", "Cancel", CARD_HI, TEXT)):
            img = F(30, "black").render(label, True, fg)
            br = pygame.Rect(0, 0, img.get_width() + 102, 72)
            br.bottomright = (bx, r.bottom - 38)
            rrect(surf, br, bg, 20)
            glyph(surf, key, br.x + 18, br.centery)
            surf.blit(img, img.get_rect(midleft=(br.x + 70, br.centery)))
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
        self.mouse_pos: tuple[float, float] = (-1.0, -1.0)
        self.update_info: dict[str, Any] | None = None
        self.cat_clipboard: dict[str, Any] | None = None
        if os.environ.get("SDL_VIDEODRIVER") != "dummy":
            upd.check_latest_async(self._on_update_checked)
        self.push(SavePicker(self))
        if save:
            self.open_save(save)
        else:
            rows = [r for r in self.stack[0].list.rows if r.enabled]
            if len(rows) == 1:  # only one campaign: skip the picker
                self.open_save(rows[0].data[0])

    def _on_update_checked(self, info: dict[str, Any]) -> None:
        self.update_info = info
        if info.get("newer"):
            self.toast(f"MewDeck {info['tag']} available · install from Home", GOOD, 5.0)

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
        """Warm Mewgenics sketchbook desk backdrop with faint graph-paper grid & desk glow."""
        bg = pygame.Surface((W, H))
        for y in range(H):
            pygame.draw.line(bg, mix(BG_TOP, BG_BOT, y / H), (0, y), (W, y))
        # Subtle hand-drawn notebook grid lines (in-game Mewgenics sketchbook texture)
        grid_col = (48, 42, 36)
        step = 48
        for x in range(0, W, step):
            pygame.draw.line(bg, grid_col, (x, 0), (x, H), 1)
        for y in range(0, H, step):
            pygame.draw.line(bg, grid_col, (0, y), (W, y), 1)
        n = 48
        small = pygame.Surface((n, n), pygame.SRCALPHA)
        for yy in range(n):
            for xx in range(n):
                d = math.hypot(xx - n / 2 + .5, yy - n / 2 + .5) / (n / 2)
                small.set_at((xx, yy), (*ACCENT, int(max(0.0, 1 - d) ** 2 * 22)))
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
              repair_all: bool = False, clear_party_disorders: bool = False,
              max_party_stats: bool = False, strip_all_defects: bool = False) -> bool:
        assert self.save
        ch = self.pending
        # Snapshot pre-existing queued flags so an error never wipes user's queued actions
        saved_flags = (ch.steven, ch.guard, ch.heal_all, ch.repair_all,
                       ch.clear_party_disorders, ch.max_party_stats, ch.strip_all_defects)
        if steven:
            ch.steven = steven
        if guard is not None:
            ch.guard = guard
        if heal_all:
            ch.heal_all = True
        if repair_all:
            ch.repair_all = True
        if clear_party_disorders:
            ch.clear_party_disorders = True
        if max_party_stats:
            ch.max_party_stats = True
        if strip_all_defects:
            ch.strip_all_defects = True
        if ch.empty():
            self.toast("Nothing to save")
            return False
        eff_steven, eff_guard = ch.steven, ch.guard
        eff_heal, eff_repair = ch.heal_all, ch.repair_all
        eff_pdis, eff_pmax, eff_def = ch.clear_party_disorders, ch.max_party_stats, ch.strip_all_defects
        reason = ("steven-reset" if eff_steven else "heal-all" if eff_heal else "repair-all" if eff_repair
                  else "party-batch" if (eff_pdis or eff_pmax) else "strip-defects" if eff_def else "edit")
        try:
            sf.apply_changes(self.save, ch, reason=reason)
        except sf.GameRunningError as e:
            (ch.steven, ch.guard, ch.heal_all, ch.repair_all,
             ch.clear_party_disorders, ch.max_party_stats, ch.strip_all_defects) = saved_flags
            sfx.play("err")
            self.toast(str(e), BAD, 5)
            return False
        except Exception as e:  # noqa: BLE001
            (ch.steven, ch.guard, ch.heal_all, ch.repair_all,
             ch.clear_party_disorders, ch.max_party_stats, ch.strip_all_defects) = saved_flags
            sfx.play("err")
            traceback.print_exc()
            self.toast(f"Not saved, file untouched: {e}", BAD, 7)
            return False
        n = ch.count()
        self.pending = sf.Changes()
        self._after_write()
        sfx.play("save")
        st = self.snap.steven if self.snap else None
        if n == 1 and eff_guard is not None and st is not None:
            self.toast("Auto-block on · Steven can't arm the penalty" if st.guarded else "Auto-block off",
                       GOOD if st.guarded == eff_guard else BAD, 4)
        elif n == 1 and eff_steven and st is not None:
            ok = not st.active and st.dejavu_cats == 0
            self.toast("Steven penalty & Déjà Vu reset · backup made" if ok else "Reset written", GOOD if ok else BAD, 4)
        elif n == 1 and eff_heal:
            self.toast(f"Healed all {self.snap.parsed['total_cats']} active cats · backup made", GOOD, 4)
        elif n == 1 and eff_repair:
            self.toast("Repaired & refilled all gear · backup made", GOOD, 4)
        elif n == 1 and eff_pdis:
            self.toast("Cleared all disorders on Adventure Party · backup made", GOOD, 4)
        elif n == 1 and eff_pmax:
            self.toast("Maxed all stats (7s) on Adventure Party · backup made", GOOD, 4)
        elif n == 1 and eff_def:
            self.toast("Stripped all birth defects on active cats · backup made", GOOD, 4)
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
        pygame.draw.rect(s, (30, 26, 22), (0, 0, W, HEADER_H))
        pygame.draw.line(s, INK, (0, HEADER_H - 2), (W, HEADER_H - 2), 3)
        mpos = self.mouse_pos
        lx = PAD
        self.back_rect: pygame.Rect | None = None
        if len(self.stack) > 1 and not isinstance(self.top(), Confirm):
            br = pygame.Rect(lx, HEADER_H // 2 - 26, 128, 52)
            self.back_rect = br
            rrect(s, br, CARD_HI if br.collidepoint(mpos) else CARD, 14)
            text(s, "« Back", br.center, 24, ACCENT if br.collidepoint(mpos) else TEXT, "black", anchor="center")
            lx = br.right + 20
        icon_cat(s, lx + 28, HEADER_H // 2 + 4, 28)
        r = text(s, "MewDeck", (lx + 68, HEADER_H // 2), 44, TEXT, "black", anchor="midleft")
        crumbs = [x.title for x in self.stack[1:] if x.title and not isinstance(x, Confirm)]
        if self.save and crumbs:
            crumbs[0] = self.save.stem
        if crumbs:
            text(s, "  /  ".join(crumbs), (r.right + 26, HEADER_H // 2 + 2), 28, DIM, "bold", anchor="midleft",
                 maxw=640)
        self.quit_rect = pygame.Rect(W - PAD - 56, HEADER_H // 2 - 28, 56, 56)
        qr = self.quit_rect
        rrect(s, qr, CARD_HI if qr.collidepoint(mpos) else CARD, 14)
        for d in (-1, 1):
            pygame.draw.line(s, TEXT if qr.collidepoint(mpos) else DIM, (qr.centerx - 11, qr.centery - 11 * d),
                             (qr.centerx + 11, qr.centery + 11 * d), 5)
        x = qr.x - 14
        self.save_rect: pygame.Rect | None = None
        self.discard_rect: pygame.Rect | None = None
        if self._game_running:
            pr = pill(s, "GAME RUNNING · close to save", x, HEADER_H // 2, BAD, DARK, size=21, anchor="midright")
            x = pr.x - 12
        if not self.pending.empty():
            self.save_rect = pill(s, f"Save ({self.pending.count()})", x, HEADER_H // 2, GOOD, DARK, size=24, anchor="midright", weight="black")
            x = self.save_rect.x - 10
            self.discard_rect = pill(s, "Discard", x, HEADER_H // 2, mix(BAD, CARD, .45), TEXT, size=20, anchor="midright")
            x = self.discard_rect.x - 12
        elif getattr(self, "update_info", None) and self.update_info.get("newer"):
            pill(s, f"Update {self.update_info['tag']}", x, HEADER_H // 2, ACCENT, DARK, size=20, anchor="midright")

    def _footer(self, s, items) -> None:
        y = H - FOOTER_H
        pygame.draw.rect(s, (26, 22, 19), (0, y, W, FOOTER_H))
        pygame.draw.line(s, INK, (0, y), (W, y), 3)
        x = PAD
        cy = y + FOOTER_H // 2
        for btn, label in items:
            if x > W - 360:
                break
            x = glyph(s, btn, x, cy) + 12
            x = text(s, label, (x, cy), 25, DIM, "bold", anchor="midleft").right + 32
        names = self.input.names
        status = names[0] if names else "Mouse · Touch · Keyboard"
        text(s, status, (W - PAD, cy), 21, GOOD if names else FAINT, "bold", anchor="midright",
             maxw=max(100, W - PAD - x - 12))

    def _toasts(self, s) -> None:
        now = time.time()
        self.toasts = [t for t in self.toasts if t[3] > now]
        y = H - FOOTER_H - 32
        for msg, col, t0, t1 in reversed(self.toasts[-3:]):
            k_in = min(1.0, (now - t0) / 0.18)
            k_out = min(1.0, (t1 - now) / 0.3)
            a = int(255 * min(k_in, k_out))
            shown = msg.translate(_GLYPH_SUB)
            font = F(29, "black")
            max_tw = W - 220
            if font.size(shown)[0] > max_tw:
                while len(shown) > 1 and font.size(shown + "…")[0] > max_tw:
                    shown = shown[:-1]
                shown = shown.rstrip() + "…"
            img = font.render(shown, True, col)
            box = pygame.Rect(0, 0, min(W - 100, img.get_width() + 86), 68)
            box.midbottom = (W // 2, y + int((1 - k_in) * 24))
            panel = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(panel, (*DARK, 245), panel.get_rect(), border_radius=34)
            pygame.draw.rect(panel, (*col, 255), panel.get_rect(), 3, border_radius=34)
            pygame.draw.circle(panel, col, (34, box.h // 2), 8)
            panel.blit(img, img.get_rect(midleft=(54, box.h // 2)))
            panel.set_alpha(a)
            s.blit(panel, box)
            y = box.top - 14

    def render(self) -> None:
        s = self.screen
        s.blit(self.bg, (0, 0))
        top = self.top()
        top.draw(s)
        self._header(s)
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
        if getattr(self, "back_rect", None) and self.back_rect.collidepoint(pos):
            self._dispatch_btn(B)
            return
        if getattr(self, "save_rect", None) and self.save_rect.collidepoint(pos) and self.save:
            self.write()
            return
        if getattr(self, "discard_rect", None) and self.discard_rect.collidepoint(pos):
            def do_disc():
                self.pending = sf.Changes()
                self.reload()
                self.toast("Pending edits discarded", DIM, 2.2)
            self.confirm("Discard pending edits?", f"Drops all {self.pending.count()} unsaved edit(s) without touching disk.",
                         do_disc, yes="Discard", danger=True)
            return
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

    def _dispatch_btn(self, b: str) -> None:
        if b in (UP, DOWN, LEFT, RIGHT, LT, RT):
            sfx.play("tick")
        elif b in (LB, RB):
            sfx.play("tab")
        elif b == A:
            sfx.play("ok")
        elif b == B:
            sfx.play("back")
        self.top().handle(b)

    def run(self) -> None:
        while self.running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type == pygame.VIDEORESIZE:
                    self._sync_surface()
                elif ev.type == pygame.MOUSEMOTION:
                    pygame.mouse.set_visible(True)
                    self.mouse_pos = self._map_pos(ev.pos)
                    if hasattr(self.top(), "hover"):
                        self.top().hover(self.mouse_pos)
                elif ev.type == pygame.FINGERDOWN:  # touchscreen (normalised coords)
                    self.click((ev.x * W, ev.y * H))
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and not getattr(ev, "touch", False):
                    self.click(self._map_pos(ev.pos))
                elif ev.type == pygame.MOUSEWHEEL and ev.y != 0 and not getattr(ev, "touch", False):
                    if not isinstance(self.top(), (Confirm, ed.SearchModal)):
                        self._dispatch_btn(UP if ev.y > 0 else DOWN)
                elif ev.type == pygame.KEYDOWN and hasattr(self.top(), "handle_raw_key") and self.top().handle_raw_key(ev):
                    continue
                else:
                    for b in self.input.translate(ev):
                        self._dispatch_btn(b)
            for b in self.input.poll():
                if self.running:
                    self._dispatch_btn(b)
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

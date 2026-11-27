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
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pygame

from . import branding
from . import breeding as br
from . import catblob as cb
from . import editors as ed
from . import gamedata as gd
from . import organizer as org
from . import presets as pr
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

def draw_stat_strip(surf, stats: dict[str, int], right: int, cy: int, faded: bool = False, hi: bool = False,
                    cell: int = 52, icon: int = 34) -> pygame.Rect:
    """Inked tray of the 7 official in-game stat icons, each over its colored value. Returns the tray rect."""
    tray = pygame.Rect(0, 0, cell * 7 + 12, icon + 52)
    tray.midright = (right, cy)
    rrect(surf, tray, mix(WELL, CARD, .35) if hi else WELL, 14, ink=True)
    for i, s in enumerate(STATS):
        v = stats.get(s, 0)
        cx = tray.x + 6 + i * cell + cell // 2
        if i:
            pygame.draw.line(surf, mix(LINE, WELL, .7), (cx - cell // 2, tray.y + 12), (cx - cell // 2, tray.bottom - 12), 1)
        ico = ed.get_stat_icon(s, icon, faded=faded)
        if ico is not None:
            surf.blit(ico, ico.get_rect(center=(cx, tray.y + 6 + icon // 2)))
        else:
            text(surf, s[:2], (cx, tray.y + 6 + icon // 2), 18, FAINT, "black", anchor="center")
        text(surf, str(v), (cx, tray.bottom - 6), 25, FAINT if faded else stat_col(v), "black", anchor="midbottom")
    return tray


def draw_stat_deltas(surf, stats: dict[str, int], right: int, cy: int, size: int = 21, icon: int = 28,
                     max_n: int = 4) -> int:
    """Right-aligned compact stat bonuses: official in-game icon + signed value (text label if no icon).

    Returns the left x of what was drawn."""
    items = [(k, v) for k, v in (stats or {}).items() if v]
    extra = 0
    if len(items) > max_n:
        items, extra = items[:max_n - 1], len(items) - (max_n - 1)
    x = right
    if extra:
        x = text(surf, f"+{extra} more", (x, cy), size - 2, FAINT, "bold", anchor="midright").left - 12
    for k, v in reversed(items):
        col = GOOD if v > 0 else BAD
        ico = ed.get_stat_icon(k, icon)
        val = f"{'+' if v > 0 else ''}{v}" if ico is not None else f"{'+' if v > 0 else ''}{v} {k}"
        x = text(surf, val, (x, cy), size, col, "black", anchor="midright").left - 4
        if ico is not None:
            ir = ico.get_rect(midright=(x, cy))
            surf.blit(ico, ir)
            x = ir.left
        x -= 14
    return x + 14


def empty_state(surf, area: pygame.Rect, icon_key: str | None, title: str, sub: str = "", top: int = 70) -> pygame.Rect:
    """Calm sketchbook empty-state card: official in-game sprite, title and a one-line hint."""
    w = min(area.w - 40, max(620, F(32, "black").size(title)[0] + 120, F(23).size(sub)[0] + 120))
    card = pygame.Rect(0, 0, w, 228 if sub else 190)
    card.midtop = (area.centerx, area.y + top)
    rrect(surf, card, WELL, 24, ink=True)
    pygame.draw.rect(surf, mix(LINE, WELL, .55), card.inflate(-18, -18), width=2, border_radius=18)
    ico = ed.get_sprite("ui", icon_key, 64) if icon_key else None
    cy = card.y + 34
    if ico is not None:
        surf.blit(ico, ico.get_rect(midtop=(card.centerx, cy)))
        cy += 76
    text(surf, title, (card.centerx, cy), 32, DIM, "black", anchor="midtop", maxw=card.w - 60)
    if sub:
        text(surf, sub, (card.centerx, cy + 48), 23, FAINT, anchor="midtop", maxw=card.w - 60)
    return card


def ellipsize_middle(s: str, font: pygame.font.Font, maxw: int) -> str:
    """Shorten a long path from the middle so both its root and its file name stay readable."""
    if font.size(s)[0] <= maxw:
        return s
    keep = len(s)
    while keep > 8:
        keep -= 2
        head, tail = s[:keep // 3], s[-(keep - keep // 3):]
        cand = f"{head}…{tail}"
        if font.size(cand)[0] <= maxw:
            return cand
    return s[-8:]


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
                self.held[b] = now + 0.24
                self.count[b] = 0
        for b in self.prev - cur:
            self.held.pop(b, None)
        for b, due in list(self.held.items()):
            if now >= due:
                out.append(b)
                self.count[b] = self.count.get(b, 0) + 1
                self.held[b] = now + (0.055 if self.count[b] < 12 else 0.032)
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

_TEXT_CACHE: OrderedDict[tuple[str, int, str, tuple, int | None], pygame.Surface] = OrderedDict()
_PILL_CACHE: OrderedDict[tuple[str, int, str, tuple, tuple], pygame.Surface] = OrderedDict()
_BAND_CACHE: OrderedDict[tuple[int, int, tuple, int, int], pygame.Surface] = OrderedDict()
_TEXT_CACHE_MAX = 1024
_PILL_CACHE_MAX = 1024
_BAND_CACHE_MAX = 64


def _clear_render_caches() -> None:
    _TEXT_CACHE.clear()
    _PILL_CACHE.clear()
    _BAND_CACHE.clear()


def _render_text_cached(s: str, size: int, weight: str, color, maxw: int | None) -> pygame.Surface:
    key = (s, size, weight, tuple(color), maxw)
    img = _TEXT_CACHE.get(key)
    if img is not None:
        _TEXT_CACHE.move_to_end(key)
        return img
    txt = s.translate(_GLYPH_SUB)
    font = F(size, weight)
    if maxw and font.size(txt)[0] > maxw:
        while len(txt) > 1 and font.size(txt + "…")[0] > maxw:
            txt = txt[:-1]
        txt = txt.rstrip() + "…"
    img = font.render(txt, True, color)
    _TEXT_CACHE[key] = img
    if len(_TEXT_CACHE) > _TEXT_CACHE_MAX:
        _TEXT_CACHE.popitem(last=False)
    return img


def text(surf: pygame.Surface, s: str, pos: tuple[float, float], size: int = 28, color=TEXT,
         weight: str = "semi", anchor: str = "topleft", maxw: int | None = None, alpha: int = 255) -> pygame.Rect:
    img = _render_text_cached(s, size, weight, color, maxw)
    if alpha < 255:
        img = img.copy()
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
    key = (rect.w, rect.h, tuple(color[:3]), alpha, radius)
    band = _BAND_CACHE.get(key)
    if band is None:
        band = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(
            band, (*color[:3], alpha), band.get_rect(),
            border_top_left_radius=radius, border_top_right_radius=radius,
            border_bottom_left_radius=0, border_bottom_right_radius=0,
        )
        _BAND_CACHE[key] = band
        if len(_BAND_CACHE) > _BAND_CACHE_MAX:
            _BAND_CACHE.popitem(last=False)
    else:
        _BAND_CACHE.move_to_end(key)
    surf.blit(band, rect.topleft)
    pygame.draw.line(surf, INK, (rect.x, rect.bottom), (rect.right, rect.bottom), 3)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1, c2, t: float):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def pill(surf, label: str, x: float, y: float, bg, fg=DARK, size=20, anchor="topleft", weight="bold") -> pygame.Rect:
    key = (label, size, weight, tuple(bg), tuple(fg))
    psurf = _PILL_CACHE.get(key)
    if psurf is None:
        img = _render_text_cached(label, size, weight, fg, None)
        pw, ph = img.get_width() + size, img.get_height() + 4
        psurf = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pr = psurf.get_rect()
        pygame.draw.rect(psurf, bg, pr, border_radius=ph // 2)
        pygame.draw.rect(psurf, INK, pr, width=2, border_radius=ph // 2)
        psurf.blit(img, img.get_rect(center=pr.center))
        _PILL_CACHE[key] = psurf
        if len(_PILL_CACHE) > _PILL_CACHE_MAX:
            _PILL_CACHE.popitem(last=False)
    else:
        _PILL_CACHE.move_to_end(key)
    r = psurf.get_rect(**{anchor: (round(x), round(y))})
    surf.blit(psurf, r)
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
        img = _render_text_cached(name, 25, "black", DARK, None)
        surf.blit(img, img.get_rect(center=(x + r, cy)))
        return x + 2 * r
    if name in ("LB", "RB", "LBRB", "LT", "RT", "LTRT"):
        right = x
        parts = ["LB", "RB"] if name == "LBRB" else (["LT", "RT"] if name == "LTRT" else [name])
        for part in parts:
            rr = pygame.Rect(right, cy - 18, 54, 36)
            pygame.draw.rect(surf, stone, rr, border_radius=12)
            pygame.draw.rect(surf, INK, rr, width=2, border_radius=12)
            img = _render_text_cached(part, 21, "black", DARK, None)
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

    def to(self, target: float, k: float = 0.44) -> float:
        self.v = target if abs(target - self.v) < 0.5 else lerp(self.v, target, k)
        return self.v


class RectAnim:
    def __init__(self) -> None:
        self.r: list[float] | None = None

    def to(self, target: pygame.Rect, k: float = 0.42) -> pygame.Rect:
        t = [target.x, target.y, target.w, target.h]
        if self.r is None:
            self.r = t
        else:
            self.r = [b if abs(b - a) < 0.5 else lerp(a, b, k) for a, b in zip(self.r, t)]
        return pygame.Rect(*[round(v) for v in self.r])


def focus_ring(surf, rect: pygame.Rect, radius=22) -> None:
    pygame.draw.rect(surf, INK, rect.inflate(10, 10), width=4, border_radius=radius + 5)
    pygame.draw.rect(surf, ACCENT, rect.inflate(4, 4), width=5, border_radius=radius + 2)


# --------------------------------------------------------------------------- navigation hierarchy
# Level 1  (LB/RB)       : full-width inked tab strip with sliding underline (Hub.draw)
# Level 2  (LT/RT, D-pad): segbar() — recessed ink-well track holding a raised paper tile
# Level 3  (filters)     : loose coloured pill() chips

SegItem = tuple  # (label, count: str | None, alert: color | None, accent: color)


def segbar(surf, items: list[SegItem], sel: int, x: int, cy: int, hint: str | None = "LTRT",
           anim: "RectAnim | None" = None, h: int = 46, size: int = 22,
           mpos: tuple[float, float] = (-1, -1)) -> tuple[list[tuple[pygame.Rect, int]], int]:
    """Level-2 sub-view switcher. Returns ([(segment_rect, index)], right_edge).

    LT/RT hints bracket the track (`LT [ a | b | c ] RT`) so the eye reads it as the shoulder-trigger
    level; D-pad switchers get their glyph after the track. Widths are measured with the heavy weight so
    the track never jitters when selection changes. Only cached text/pill surfaces + plain rect draws."""
    if hint == "LTRT":
        x = glyph(surf, "LT", x, cy) + 10
    pad = 18
    segs: list[tuple[int, SegItem]] = []
    for it in items:
        lbl, cnt, alert, _acc = it
        w = _render_text_cached(lbl, size, "black", TEXT, None).get_width() + 2 * pad
        if cnt:
            w += _render_text_cached(cnt, 16, "black", DARK, None).get_width() + 16 + 8
        if alert:
            w += 26
        segs.append((w, it))
    track = pygame.Rect(x, cy - h // 2, sum(w for w, _ in segs) + 8, h)
    pygame.draw.rect(surf, INK, track.move(0, 3), border_radius=16)
    pygame.draw.rect(surf, WELL, track, border_radius=16)
    pygame.draw.line(surf, mix(WELL, INK, .55), (track.x + 14, track.y + 4), (track.right - 14, track.y + 4), 2)
    rects: list[tuple[pygame.Rect, int]] = []
    sx = track.x + 4
    for i, (w, _) in enumerate(segs):
        rects.append((pygame.Rect(sx, track.y + 4, w, h - 8), i))
        sx += w
    sel = max(0, min(sel, len(rects) - 1)) if rects else 0
    # Hover wash + hairline dividers between idle neighbours
    for r, i in rects:
        if i != sel and r.collidepoint(mpos):
            pygame.draw.rect(surf, mix(WELL, CARD, .7), r, border_radius=12)
        if i + 1 < len(rects) and sel not in (i, i + 1):
            pygame.draw.line(surf, mix(LINE, WELL, .55), (r.right, r.y + 9), (r.right, r.bottom - 9), 2)
    if rects:
        tr = anim.to(rects[sel][0], 0.42) if anim else rects[sel][0]
        acc = segs[sel][1][3]
        pygame.draw.rect(surf, INK, tr.move(0, 3), border_radius=12)
        pygame.draw.rect(surf, CARD_HI, tr, border_radius=12)
        pygame.draw.line(surf, mix(CARD_HI, TEXT, .16), (tr.x + 10, tr.y + 3), (tr.right - 10, tr.y + 3), 2)
        pygame.draw.rect(surf, acc, (tr.x + 3, tr.bottom - 7, tr.w - 6, 5),
                         border_bottom_left_radius=10, border_bottom_right_radius=10)
        pygame.draw.rect(surf, INK, tr, width=2, border_radius=12)
    for (r, i), (w, (lbl, cnt, alert, acc)) in zip(rects, segs):
        on = i == sel
        img = _render_text_cached(lbl, size, "black" if on else "bold", TEXT if on else DIM, None)
        extra = 0
        if cnt:
            extra += _render_text_cached(cnt, 16, "black", DARK, None).get_width() + 16 + 8
        if alert:
            extra += 26
        lx = r.centerx - (img.get_width() + extra) // 2
        cyl = r.centery - 2
        surf.blit(img, img.get_rect(midleft=(lx, cyl)))
        lx += img.get_width() + 8
        if cnt:
            lx = pill(surf, cnt, lx, cyl, acc if on else mix(LINE, WELL, .45), DARK if on else TEXT, 16,
                      anchor="midleft").right
        if alert:
            warn = tuple(alert) == BAD
            rad = 10 if warn else 6
            pygame.draw.circle(surf, INK, (lx + 13, cyl + 1), rad + 2)
            pygame.draw.circle(surf, alert, (lx + 13, cyl), rad)
            if warn:
                bang = _render_text_cached("!", 16, "black", DARK, None)
                surf.blit(bang, bang.get_rect(center=(lx + 13, cyl)))
    right = track.right
    if hint == "LTRT":
        right = glyph(surf, "RT", right + 10, cy)
    elif hint:
        right = glyph(surf, hint, right + 12, cy)
    return rects, right


def empty_slot(surf, r: pygame.Rect) -> None:
    """Recessed empty gear socket with a faint inked '+' (reads as 'slot available', not 'missing art')."""
    pygame.draw.rect(surf, WELL, r, border_radius=10)
    pygame.draw.rect(surf, mix(LINE, WELL, .35), r, width=2, border_radius=10)
    c = mix(LINE, WELL, .2)
    k = max(5, r.w // 6)
    pygame.draw.line(surf, c, (r.centerx - k, r.centery), (r.centerx + k, r.centery), 3)
    pygame.draw.line(surf, c, (r.centerx, r.centery - k), (r.centerx, r.centery + k), 3)


def section_label(surf, label: str, x: int, y: int, right: int, color=FAINT, note: str = "",
                  note_color=FAINT, size: int = 16) -> int:
    """Sketchbook ruled section heading: SMALL CAPS label, hairline ink rule, optional right-aligned note.
    Returns the label's bottom edge."""
    lr = text(surf, label, (x, y), size, color, "black")
    end = right
    if note:
        end = text(surf, note, (right, lr.centery), size - 1, note_color, "bold", anchor="midright").left - 12
    if end - lr.right > 30:
        pygame.draw.line(surf, mix(LINE, CARD, .55), (lr.right + 12, lr.centery + 1), (end, lr.centery + 1), 2)
    return lr.bottom


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
            rrect(surf, track, WELL, 4)
            bar_h = max(48, area.h * visible // len(self.rows))
            y = area.y + (area.h - bar_h) * off / max(1, (len(self.rows) - visible) * step)
            rrect(surf, (track.x - 1, y, 9, bar_h), DIM, 5)

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


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


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
        for i, p in enumerate(sf.find_saves()):
            try:
                owner = sf.save_label(p).split("(")[-1].rstrip(")")
                when = time.strftime("%b %d, %H:%M", time.localtime(p.stat().st_mtime))
            except OSError:
                owner, when = "?", "?"
            meta = (owner, when, i == 0)  # resolved once here, never per frame
            try:
                snap = sf.snapshot(p)
                rows.append(Row(p.stem, data=(p, snap, meta)))
            except Exception as e:  # noqa: BLE001
                rows.append(Row(p.stem, sub=f"unreadable: {e}", data=(p, None, meta), enabled=False))
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
        p, snap, (owner, when, latest) = row.data
        branding.draw_cat_emblem(surf, r.x + 78, r.centery + 2, 30, ACCENT if selected else LINE)
        x = r.x + 150
        nr = text(surf, row.label, (x, r.y + 26), 44, TEXT, "black", maxw=r.w - 900)
        if latest:
            pill(surf, "LATEST", nr.right + 16, nr.centery + 2, mix(ACCENT, CARD, .7) if not selected else ACCENT,
                 DARK if selected else ACCENT, 17, anchor="midleft", weight="black")
        mr = text(surf, f"Last saved {when}", (x, r.y + 88), 26, DIM)
        text(surf, f"·   Steam {owner}", (mr.right + 14, r.y + 90), 23, FAINT, "bold")
        if snap is None:
            text(surf, row.sub, (r.right - 36, r.centery), 25, BAD, "bold", anchor="midright", maxw=640)
            return
        b = snap.parsed["basic"]
        stats = [("DAY", b["current_day"]), ("CATS", snap.parsed["total_cats"]), ("GOLD", b["house_gold"])]
        col_w = 132
        sx = r.right - 40 - col_w * len(stats) + col_w // 2
        for i, (label, val) in enumerate(stats):
            if i:
                pygame.draw.line(surf, LINE, (sx - col_w // 2, r.y + 38), (sx - col_w // 2, r.bottom - 38), 2)
            text(surf, f"{val:,}", (sx, r.y + 34), 40, TEXT if selected else mix(TEXT, DIM, .4), "black",
                 anchor="midtop", maxw=col_w - 10)
            text(surf, label, (sx, r.y + 88), 19, FAINT, "black", anchor="midtop")
            sx += col_w
        st = snap.steven
        stats_left = r.right - 40 - col_w * len(stats)
        pygame.draw.line(surf, LINE, (stats_left - 10, r.y + 30), (stats_left - 10, r.bottom - 30), 2)
        pill(surf, "STEVEN ACTIVE" if st.active else "STEVEN CLEAR", stats_left - 36, r.centery,
             BAD if st.active else mix(GOOD, CARD, .25), DARK, size=21, anchor="midright", weight="black")

    def draw(self, surf) -> None:
        top = HEADER_H + 34
        branding.draw_cat_emblem(surf, PAD + 52, top + 58, 40)
        tx = PAD + 128
        text(surf, "Pick a campaign", (tx, top + 6), 52, TEXT, "black")
        text(surf, "Read straight from your Proton prefix. Edits only land when you press Save.",
             (tx, top + 72), 27, DIM, maxw=W - tx - PAD - 300)
        n = len(self.list.rows)
        if n:
            text(surf, plural(n, "campaign") + " found", (W - PAD, top + 90), 22, FAINT, "black", anchor="midright")
        div_y = top + 132
        pygame.draw.line(surf, INK, (PAD, div_y), (W - PAD, div_y), 3)
        pygame.draw.line(surf, mix(CARD, TEXT, .08), (PAD, div_y + 3), (W - PAD, div_y + 3), 1)
        area = pygame.Rect(PAD, div_y + 26, W - 2 * PAD, CONTENT_BOTTOM - div_y - 26)
        if self.list.rows:
            self.list.draw(surf, area, self.render_row)
            return
        card = pygame.Rect(PAD, area.y, area.w, 510)
        rrect(surf, card, CARD, 24)
        branding.draw_cat_emblem(surf, card.x + 120, card.y + 135, 52, LINE)
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
        "System": "Recycle",
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
            SystemTab(app),
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
        elif btn == B:
            if not self.app.pending.empty():
                self.app.confirm("Discard unsaved changes?", f"{plural(self.app.pending.count(), 'pending edit')} will be lost.",
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
                house_n = sum(1 for c in self.app.snap.parsed["cats"] if c["room"] != "(ADVENTURE)")
                badge = str(house_n)
            elif isinstance(t, org.BreedingTab) and self.app.pending.room_moves:
                badge = str(len(self.app.pending.room_moves))
                badge_col = GOOD
            elif isinstance(t, ItemsTab) and self.app.pending.inv_total():
                badge = f"{self.app.pending.inv_total()}"
                badge_col = GOOD
            elif isinstance(t, ed.FurnitureTab):
                badge = str(self.app.snap.parsed["total_furniture"])
            elif isinstance(t, SystemTab):
                upd_info = getattr(self.app, "update_info", None)
                if upd_info and upd_info.get("newer"):
                    badge = "UPD"
                    badge_col = ACCENT
            sel = i == self.i
            ico = ed.get_sprite("ui", self.TAB_ICONS.get(label), 26)
            img = _render_text_cached(label, 27, "black" if sel else "bold", TEXT if sel else DIM, None)
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
            u = self.ul.to(target, 0.42)
            rrect(surf, (u.x + 12, y + TABS_H - 6, u.w - 24, 5), ACCENT, 3, ink=False)
        area = pygame.Rect(PAD, CONTENT_TOP, W - 2 * PAD, CONTENT_BOTTOM - CONTENT_TOP)
        self.tab.draw(surf, area)

    def hint_items(self):
        return self.tab.hint_items() + [("LBRB", "Tabs")] + pending_hint(self.app)


# --------------------------------------------------------------------------- shared inventory helpers

INV_RARITY_RANK = {"common": 1, "uncommon": 2, "rare": 3, "very_rare": 4, "legendary": 5, "quest": 6}
INV_SORTS = ("A-Z", "Top Stats", "Rarity", "Set")


def owned_item_locations(app: "App") -> dict[str, set[str]]:
    """Map item_id -> set of locations ('storage', 'backpack', 'equipped', 'queued')."""
    locs: dict[str, set[str]] = {}
    parsed = app.snap.parsed
    items_by_grp = parsed["items"]
    pend = app.pending

    for grp in ("storage", "backpack"):
        rems = set(pend.inv_remove[grp])
        moved_out = {inst for sg, _, inst in pend.inv_move if sg == grp}
        for raw in items_by_grp[grp]:
            inst = int(raw["instance_id"])
            if inst in rems or inst in moved_out:
                continue
            iid = raw.get("item_id")
            if iid:
                locs.setdefault(iid, set()).add(grp)

    for sg, dg, inst in pend.inv_move:
        if dg in ("storage", "backpack"):
            raw = next((x for x in items_by_grp[sg] if int(x["instance_id"]) == inst), None)
            if raw and raw.get("item_id"):
                locs.setdefault(raw["item_id"], set()).add(dg)

    for c in parsed["cats"]:
        pend_eq = (pend.cats.get(c["key"]) or {}).get("equipment") or {}
        eq = c.get("equipment") or {}
        for sk in cb.EQUIP_SLOT_KEYS:
            if sk in pend_eq:
                iid = pend_eq[sk]
            else:
                slot = eq.get(sk)
                iid = slot["item_id"] if slot else None
            if iid:
                locs.setdefault(iid, set()).add("equipped")

    for grp in ("storage", "backpack", "trash"):
        for iid in pend.inv_add[grp]:
            if iid:
                locs.setdefault(iid, set()).add("queued")

    return locs


def queued_item_counts(app: "App") -> dict[str, int]:
    counts: dict[str, int] = {}
    for grp in ("storage", "backpack", "trash"):
        for iid in app.pending.inv_add[grp]:
            counts[iid] = counts.get(iid, 0) + 1
    return counts


def container_count(app: "App", grp: str) -> int:
    m_in = sum(1 for _, dg, _ in app.pending.inv_move if dg == grp)
    m_out = sum(1 for sg, _, _ in app.pending.inv_move if sg == grp)
    return (
        len(app.snap.parsed["items"][grp])
        + len(app.pending.inv_add[grp])
        - len(set(app.pending.inv_remove[grp]))
        + m_in
        - m_out
    )


def item_matches(it: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    sets = " ".join(sb.get("name", "") + " " + sb.get("desc", "") for sb in gd.item_set_info(it))
    hay = " ".join(str(x) for x in (
        it.get("name", ""), it.get("id", ""), it.get("kind", ""), it.get("rarity", ""),
        it.get("desc", ""), it.get("set", ""), gd.format_stats(it.get("stats")), sets,
        "cursed" if it.get("cursed") else "",
    )).lower()
    return all(tok in hay for tok in q.lower().split())


def sort_item_rows(rows: list[Row], sort_idx: int) -> None:
    if sort_idx == 1:
        rows.sort(key=lambda r: (-sum((r.data[2].get("stats") or {}).values()), r.label.lower()))
    elif sort_idx == 2:
        rows.sort(key=lambda r: (-INV_RARITY_RANK.get(r.data[2].get("rarity") or "", 0), r.label.lower()))
    elif sort_idx == 3:
        rows.sort(key=lambda r: (not bool(r.data[2].get("set")), str(r.data[2].get("set") or "zzz"), r.label.lower()))
    else:
        rows.sort(key=lambda r: r.label.lower())


def container_rows(app: "App", grp: str, q: str, sort_idx: int) -> list[Row]:
    rows: list[Row] = []
    rems = set(app.pending.inv_remove[grp])
    moved_out = {inst for sg, _, inst in app.pending.inv_move if sg == grp}
    moved_in = [(sg, inst) for sg, dg, inst in app.pending.inv_move if dg == grp]
    for raw in app.snap.parsed["items"][grp]:
        iid_inst = int(raw["instance_id"])
        if iid_inst in rems or iid_inst in moved_out:
            continue
        info = gd.item_info(raw["item_id"])
        if item_matches(info, q):
            rows.append(Row(info["name"], data=("owned", raw, info)))
    for sg, inst in moved_in:
        raw = next((x for x in app.snap.parsed["items"][sg] if int(x["instance_id"]) == inst), None)
        if raw:
            info = gd.item_info(raw["item_id"])
            if item_matches(info, q):
                rows.append(Row(info["name"], data=("moved", (sg, raw), info)))
    for iid in app.pending.inv_add[grp]:
        info = gd.item_info(iid)
        if item_matches(info, q):
            rows.append(Row(info["name"], data=("queued", iid, info)))
    sort_item_rows(rows, sort_idx)
    return rows


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
        self.col = 0  # 0 = left House actions, 1 = right pure Home resources
        self.act_idx = 0
        self.res_list = ListView(row_h=84, gap=10)
        self.ring = RectAnim()
        self.rects: dict[str, pygame.Rect] = {}
        self.arrow_rects: dict[tuple[str, int], pygame.Rect] = {}
        self._res_area = pygame.Rect(0, 0, 1, 1)

    def on_enter(self) -> None:
        self.res_list.set_rows([Row(spec[1], data=spec) for spec in self.RESOURCES])

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
        if self.col == 1:
            row = self.res_list.current
            if row:
                key, _, _, step, max_v, qx = row.data
                if btn in (LEFT, RIGHT):
                    self._adjust(key, (step if btn == RIGHT else -step) * self.app.input.mult(btn), max_v)
                    return True
                if btn == X and qx is not None:
                    self._adjust(key, qx - self._val(key), max_v)
                    return True
        if btn == A and self.col == 0:
            self.activate(self.ACTIONS[self.act_idx])
            return True
        return False

    def activate(self, item: str) -> None:
        app = self.app
        p = app.pending
        if item == "heal_all":
            p.heal_all = not p.heal_all
            app.toast("Queued Heal All Active Cats · press Menu to save" if p.heal_all else "Cancelled Heal All Cats",
                      GOOD if p.heal_all else DIM, 2.6)
        elif item == "repair_all":
            p.repair_all = not p.repair_all
            app.toast("Queued Repair & Refill All Gear · press Menu to save" if p.repair_all else "Cancelled Repair All Gear",
                      GOOD if p.repair_all else DIM, 2.6)
        elif item == "strip_defects":
            p.strip_all_defects = not p.strip_all_defects
            app.toast("Queued Strip All Birth Defects · press Menu to save" if p.strip_all_defects else "Cancelled Strip Birth Defects",
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

    def _render_res_row(self, surf, r, row: Row, selected: bool) -> None:
        focused = self.col == 1 and selected
        key, label, ico_k, step, _, _ = row.data
        val = self._val(key)
        base = self.app.snap.parsed["basic"].get(key, 0)
        changed = val != base
        ico = ed.get_sprite("ui", ico_k, 36)
        if ico is not None:
            surf.blit(ico, ico.get_rect(midleft=(r.x + 20, r.centery)))
        text(surf, label, (r.x + 70, r.centery), 26, TEXT, "bold", anchor="midleft", maxw=r.w - 320)
        text(surf, f"{val:,}", (r.right - 138, r.centery), 30, ACCENT if changed else TEXT, "black", anchor="midright")
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
        adv_n = sum(1 for c in snap.parsed["cats"] if c["room"] == "(ADVENTURE)")
        inj_count = sum(1 for c in snap.parsed["cats"] if c["injured"] or c["wounded"])
        rooms_n = len(snap.parsed.get("rooms") or [])
        mpos = getattr(self.app, "mouse_pos", (-1, -1))

        # ---- Left column: House Dashboard & Campaign-Wide Tools
        hero = pygame.Rect(area.x, area.y, 960, area.h)
        rrect(surf, hero, CARD, 24, shadow=True)
        draw_top_band(surf, pygame.Rect(hero.x + 3, hero.y + 3, hero.w - 6, 174), ACCENT, 34, radius=21)
        h_ico = ed.get_sprite("ui", "Gold", 70)
        if h_ico is not None:
            surf.blit(h_ico, h_ico.get_rect(center=(hero.x + 82, hero.y + 92)))
        text(surf, "HOME & HOUSE TOOLS", (hero.x + 140, hero.y + 26), 19, ACCENT, "black")
        if adv_n:
            pill(surf, f"{adv_n} on Adventure", hero.right - 28, hero.y + 36, mix(INFO, CARD, .72), INFO, size=18, anchor="midright")
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
            ("props", "Advanced Properties", "Search & edit all 160+ integer save flags", "Reroll", False, "Open", CARD_HI),
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

        # ---- Right column: Pure Home Resources (zero scroll, zero app settings mixed in)
        col_x = hero.right + 32
        col_w = area.right - col_x
        section_label(surf, "HOME RESOURCES", col_x + 4, area.y + 4, area.right - 14, ACCENT,
                      "Hold Left/Right for ×10/×100 · X maxes", size=18)
        res_area = pygame.Rect(col_x, area.y + 34, col_w - 6, 658)
        self._res_area = res_area
        self.res_list.draw(surf, res_area, self._render_res_row, show_focus=(self.col == 1))
        note_box = pygame.Rect(col_x, res_area.bottom + 14, col_w - 6, area.bottom - (res_area.bottom + 14))
        if note_box.h >= 48:
            rrect(surf, note_box, WELL, 16, ink=True)
            text(surf, "Backups, Save Integrity, UI Sounds & Updates now live in the System tab.",
                 (note_box.x + 22, note_box.centery), 19, FAINT, "bold", anchor="midleft", maxw=note_box.w - 44)
        if self.col == 0:
            target = self.rects[self.ACTIONS[self.act_idx]]
            focus_ring(surf, self.ring.to(target), 18)

    def hint_items(self):
        if self.col == 1:
            row = self.res_list.current
            qx = row.data[5] if row else None
            extra = [("X", "Max")] if qx is not None else []
            return [("DPAD_LR", "Adjust")] + extra + [("SELECT", "Switch Col"), ("B", "Saves")]
        return [("A", "Toggle / Open"), ("DPAD_LR", "Resources"), ("B", "Saves")]


# --------------------------------------------------------------------------- adventure (Canonical Home for Active Run)

class AdventureTab(Tab):
    name = "Adventure"
    MODES = [
        ("run", "Run & Steven"),
        ("party", "Party"),
        ("backpack", "Backpack"),
    ]
    STEVEN_ACTIONS = ["reset", "wipe", "restart", "guard"]
    PARTY_ACTIONS = ["clear_party_dis", "max_party"]
    RESOURCES = [
        ("adventure_coins", "Adventure Coins", "Gold", 10, 9_999_999, 99_999),
        ("adventure_food", "Adventure Food", "Food", 10, 9_999_999, 99_999),
        ("adventure_furniture_boxes", "Furniture Boxes", "Treasure", 1, 99, 20),
    ]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.m = 0  # 0=Run & Steven, 1=Party, 2=Backpack
        self.col = 0  # in Run & Steven: 0=Steven column, 1=Run resources & party batch column
        self.act_idx = 0
        self.right_idx = 0
        self.party: list[dict[str, Any]] = []
        self.party_list = ListView(row_h=142, gap=14)
        self.bp_list = ListView(row_h=82, gap=8)
        self.bp_sort = 0
        self.bp_query = ""
        self.ring = RectAnim()
        self.mode_rects: list[tuple[pygame.Rect, int]] = []
        self.rects: dict[str, pygame.Rect] = {}
        self.right_rects: list[tuple[pygame.Rect, str, Any]] = []
        self.arrow_rects: dict[tuple[str, int], pygame.Rect] = {}
        self.bp_sort_rect: pygame.Rect | None = None
        self.bp_search_rect: pygame.Rect | None = None
        self._locs: dict[str, set[str]] = {}

    def _in_run(self) -> bool:
        return bool(self.app.snap.steven.on_adventure or self.party)

    def on_enter(self) -> None:
        self.party = [c for c in self.app.snap.parsed["cats"] if c["room"] == "(ADVENTURE)"]
        self.party_list.set_rows([Row(c["name"] or f"#{c['key']}", data=c) for c in self.party])
        if not self._in_run():
            self.col = 0
        self._rebuild_backpack()

    def _rebuild_backpack(self) -> None:
        self.bp_list.set_rows(container_rows(self.app, "backpack", self.bp_query, self.bp_sort))

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
            app.toast("Queued Steven Reset + strip Déjà Vu · press Menu to save" if p.steven == "cap" else "Cancelled Steven Reset",
                      GOOD if p.steven else DIM, 2.8)
        elif item == "wipe":
            p.steven = None if p.steven == "clear" else "clear"
            app.toast("Queued Wipe All Steven Strikes · press Menu to save" if p.steven == "clear" else "Cancelled Wipe Strikes",
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
                app.toast(f"Queued Auto-block {'ON' if want else 'OFF'} · press Menu to save", GOOD if want else ACCENT, 2.8)
        elif item == "clear_party_dis":
            p.clear_party_disorders = not p.clear_party_disorders
            app.toast("Queued Clear Party Disorders · press Menu to save" if p.clear_party_disorders else "Cancelled Clear Party Disorders",
                      GOOD if p.clear_party_disorders else DIM, 2.6)
        elif item == "max_party":
            p.max_party_stats = not p.max_party_stats
            app.toast("Queued Max Party Stats (All 7) · press Menu to save" if p.max_party_stats else "Cancelled Max Party Stats",
                      GOOD if p.max_party_stats else DIM, 2.6)

    def _open_bp_search(self) -> None:
        def done(q: str) -> None:
            self.bp_query = q
            self.bp_list.sel = 0
            self._rebuild_backpack()
        self.app.push(ed.SearchModal(sys.modules[__name__], self.app, "Search Adventure Backpack", self.bp_query,
                                     lambda q: len(container_rows(self.app, "backpack", q, self.bp_sort)), done))

    def _quick_fix_party_cat(self, c: dict[str, Any]) -> None:
        pend = self.app.pending.cats.setdefault(c["key"], {})
        nm = c["name"] or f"#{c['key']}"
        if (c["injured"] or c["wounded"] or c["hp"] < c["max_hp"]) and not pend.get("heal"):
            pend["heal"] = True
            self.app.toast(f"Queued Heal for {nm} · press Menu to save", GOOD, 2.8)
            return
        cur_dis = (pend.get("abilities") or {}).get("disorder", c["abilities"]["disorder"])
        if any(cur_dis):
            ab = pend.setdefault("abilities", {})
            ab["disorder"] = [None, None]
            ab["disorder_tiers"] = [1, 1]
            self.app.toast(f"Queued Clear Disorders for {nm} · press Menu to save", GOOD, 2.8)
            return
        if not pend:
            self.app.pending.cats.pop(c["key"], None)
        self.app.toast(f"{nm} is already healthy & disorder-free · press A to edit", DIM, 2.2)

    def handle(self, btn: str) -> bool:
        if btn in (LT, RT):
            self.m = (self.m + (1 if btn == RT else -1)) % len(self.MODES)
            if self.MODES[self.m][0] == "backpack":
                self._rebuild_backpack()
            return True

        mode = self.MODES[self.m][0]

        # ---- Sub-view 0: Run & Steven
        if mode == "run":
            in_run = self._in_run()
            right_total = len(self.RESOURCES) + len(self.PARTY_ACTIONS)
            if btn in (UP, DOWN):
                d = -1 if btn == UP else 1
                if self.col == 0 or not in_run:
                    nxt = self.act_idx + d
                    if 0 <= nxt < len(self.STEVEN_ACTIONS):
                        self.act_idx = nxt
                    elif in_run:
                        self.col = 1
                        self.right_idx = 0 if nxt >= len(self.STEVEN_ACTIONS) else right_total - 1
                    else:
                        self.act_idx = nxt % len(self.STEVEN_ACTIONS)
                else:
                    nxt = self.right_idx + d
                    if 0 <= nxt < right_total:
                        self.right_idx = nxt
                    else:
                        self.col = 0
                        self.act_idx = len(self.STEVEN_ACTIONS) - 1 if nxt < 0 else 0
                return True
            if btn == SELECT and in_run:
                self.col = 1 - self.col
                return True
            if self.col == 0 and btn == RIGHT and in_run:
                self.col = 1
                return True
            if self.col == 1 and in_run:
                if self.right_idx < len(self.RESOURCES):
                    key, _, _, step, max_v, qx = self.RESOURCES[self.right_idx]
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
                    self.activate(self.STEVEN_ACTIONS[self.act_idx])
                elif in_run and self.right_idx >= len(self.RESOURCES):
                    self.activate(self.PARTY_ACTIONS[self.right_idx - len(self.RESOURCES)])
                return True
            return False

        # ---- Sub-view 1: Party (4)
        if mode == "party":
            if btn in (UP, DOWN):
                self.party_list.move(-1 if btn == UP else 1)
                return True
            if btn == A and self.party_list.current:
                self.app.push(CatEdit(self.app, self.party, self.party_list.sel))
                return True
            if btn == X and self.party_list.current:
                self._quick_fix_party_cat(self.party_list.current.data)
                return True
            return False

        # ---- Sub-view 2: Backpack (N)
        if btn in (UP, DOWN):
            step = 1 if self.app.input.count.get(btn, 0) < 12 else 5
            self.bp_list.move((-1 if btn == UP else 1) * step)
            return True
        if btn == Y:
            self.bp_sort = (self.bp_sort + 1) % len(INV_SORTS)
            self.bp_list.sel = 0
            self._rebuild_backpack()
            self.app.toast(f"Sort: {INV_SORTS[self.bp_sort]}", ACCENT, 1.5)
            return True
        if btn == SELECT:
            self._open_bp_search()
            return True
        if btn == B and self.bp_query:
            self.bp_query = ""
            self.bp_list.sel = 0
            self._rebuild_backpack()
            return True
        if btn == A and self.bp_list.current:
            kind, obj, info = self.bp_list.current.data
            if kind == "owned":
                self.app.pending.inv_move.append(("backpack", "storage", int(obj["instance_id"])))
                self._rebuild_backpack()
                self.app.toast(f"Sent {info['name']} » Home Storage", INFO, 2.0)
            elif kind == "moved":
                sg, raw = obj
                inst = int(raw["instance_id"])
                self.app.pending.inv_move[:] = [m for m in self.app.pending.inv_move if not (m[0] == sg and m[1] == "backpack" and m[2] == inst)]
                self._rebuild_backpack()
                self.app.toast(f"Cancelled move of {info['name']}", DIM, 1.8)
            elif kind == "queued":
                if obj in self.app.pending.inv_add["backpack"]:
                    self.app.pending.inv_add["backpack"].remove(obj)
                    self.app.pending.inv_add["storage"].append(obj)
                    self._rebuild_backpack()
                    self.app.toast(f"Redirected queued {info['name']} » Home Storage", INFO, 2.0)
            return True
        if btn == X and self.bp_list.current:
            kind, obj, info = self.bp_list.current.data
            if kind == "owned":
                self.app.pending.inv_remove["backpack"].append(int(obj["instance_id"]))
                self._rebuild_backpack()
                self.app.toast(f"Removed {info['name']} from Adventure Backpack", ACCENT, 1.8)
            elif kind == "moved":
                sg, raw = obj
                inst = int(raw["instance_id"])
                self.app.pending.inv_move[:] = [m for m in self.app.pending.inv_move if not (m[0] == sg and m[1] == "backpack" and m[2] == inst)]
                self._rebuild_backpack()
                self.app.toast(f"Cancelled move of {info['name']}", DIM, 1.8)
            elif kind == "queued":
                if obj in self.app.pending.inv_add["backpack"]:
                    self.app.pending.inv_add["backpack"].remove(obj)
                self._rebuild_backpack()
                self.app.toast(f"Unqueued {info['name']}", ACCENT, 1.6)
            return True
        return False

    def click(self, pos) -> None:
        for r, i in self.mode_rects:
            if r.collidepoint(pos):
                sfx.play("tab")
                self.m = i
                if self.MODES[self.m][0] == "backpack":
                    self._rebuild_backpack()
                return
        mode = self.MODES[self.m][0]
        if mode == "run":
            for (key, d), r in self.arrow_rects.items():
                if r.collidepoint(pos):
                    for spec in self.RESOURCES:
                        if spec[0] == key:
                            self.col = 1
                            self._adjust(key, d, spec[4])
                            sfx.play("tick")
                            return
            for idx, key in enumerate(self.STEVEN_ACTIONS):
                r = self.rects.get(key)
                if r and r.collidepoint(pos):
                    self.col, self.act_idx = 0, idx
                    sfx.play("ok")
                    self.activate(key)
                    return
            for idx, (r, kind, key) in enumerate(self.right_rects):
                if r.collidepoint(pos):
                    self.col, self.right_idx = 1, idx
                    if kind == "act":
                        sfx.play("ok")
                        self.activate(key)
                    return
        elif mode == "party":
            i = self.party_list.hit(pos)
            if i is not None:
                if i == self.party_list.sel:
                    self.handle(A)
                self.party_list.sel = i
        else:
            if self.bp_search_rect and self.bp_search_rect.collidepoint(pos):
                self._open_bp_search()
                return
            if self.bp_sort_rect and self.bp_sort_rect.collidepoint(pos):
                self.handle(Y)
                return
            i = self.bp_list.hit(pos)
            if i is not None:
                if i == self.bp_list.sel:
                    self.handle(A)
                self.bp_list.sel = i

    def _render_party_row(self, surf, r, row: Row, selected: bool) -> None:
        c = row.data
        ccol = CLASS_COL.get(c["class"], ACCENT)
        rrect(surf, (r.x + 4, r.y + 8, 8, r.h - 16), ccol, 4, ink=False)
        scol = SEX_COL.get(c["sex"], DIM)
        cbadge = pygame.Rect(r.x + 24, r.centery - 38, 76, 76)
        ed.draw_sprite_badge(sys.modules[__name__], surf, "class", c["class"], cbadge, border_col=ccol, fallback_col=ccol)
        pygame.draw.circle(surf, INK, (cbadge.right - 6, cbadge.bottom - 6), 15)
        pygame.draw.circle(surf, scol, (cbadge.right - 6, cbadge.bottom - 6), 12)
        text(surf, {"Male": "M", "Female": "F"}.get(c["sex"], "?"), (cbadge.right - 6, cbadge.bottom - 6), 17, DARK, "black", anchor="center")

        pend_c = self.app.pending.cats.get(c["key"], {})
        name_r = text(surf, row.label, (r.x + 118, r.y + 18), 36, TEXT, "black", maxw=320)
        bx = name_r.right + 12
        if pend_c:
            bx = pill(surf, "EDITED", bx, name_r.centery, ACCENT, DARK, size=17, anchor="midleft").right + 8
        if (c["injured"] or c["wounded"]) and not pend_c.get("heal"):
            inj_lbl = gd.injury_info(c["injury"])["name"] if c["injury"] else "Wounded"
            bx = pill(surf, inj_lbl, bx, name_r.centery, BAD, DARK, size=17, anchor="midleft").right + 8
        cur_dis = (pend_c.get("abilities") or {}).get("disorder", c["abilities"]["disorder"])
        if "DejaVu" in (cur_dis or []):
            bx = pill(surf, "DÉJÀ VU", bx, name_r.centery, (220, 90, 190), DARK, size=16, anchor="midleft").right + 8
        other_dis = [d for d in (cur_dis or []) if d and d != "DejaVu"]
        if other_dis and bx < r.right - 430:
            pill(surf, gd.disorder_info(other_dis[0])["name"], bx, name_r.centery, mix(BAD, CARD, .55), TEXT, size=16, anchor="midleft")

        eq_n = sum(1 for v in (c.get("equipment") or {}).values() if v)
        sub = f"{c['class']} · Lv {c['level']} · HP {c['hp']}/{c['max_hp']} · Gear {eq_n}/5 · Sum {br.stat_sum(c)}"
        text(surf, sub, (r.x + 118, r.bottom - 20), 23, DIM, anchor="bottomleft", maxw=r.w - 530)
        draw_stat_strip(surf, c["effective_stats"], r.right - 22, r.centery, hi=selected, cell=50, icon=32)

    def _draw_party_dossier(self, surf, box: pygame.Rect, c: dict[str, Any]) -> None:
        rrect(surf, box, CARD, 24, shadow=True)
        ccol = CLASS_COL.get(c["class"], ACCENT)
        band = pygame.Rect(box.x + 3, box.y + 3, box.w - 6, 118)
        draw_top_band(surf, band, ccol, 44, radius=21)
        cbadge = pygame.Rect(box.x + 22, box.y + 18, 82, 82)
        ed.draw_sprite_badge(sys.modules[__name__], surf, "class", c["class"], cbadge, border_col=ccol, fallback_col=ccol)
        text(surf, c["name"] or f"#{c['key']}", (box.x + 120, box.y + 18), 34, TEXT, "black", maxw=box.w - 150)
        pr = pill(surf, f"{c['class']} · Lv {c['level']}", box.x + 120, box.y + 68, ccol, DARK, size=18)
        hp_ico = ed.get_sprite("ui", "HP", 20)
        mp_ico = ed.get_sprite("ui", "Mana", 20)
        mp_r = text(surf, f"{c['effective_stats']['CHA'] * 3}", (box.right - 22, pr.centery), 19, INFO, "black", anchor="midright")
        if mp_ico is not None:
            surf.blit(mp_ico, mp_ico.get_rect(midright=(mp_r.left - 4, pr.centery)))
        hp_r = text(surf, f"{c['hp']}/{c['max_hp']}", (mp_r.left - 34, pr.centery), 19, GOOD, "black", anchor="midright")
        if hp_ico is not None:
            surf.blit(hp_ico, hp_ico.get_rect(midright=(hp_r.left - 4, pr.centery)))

        x = box.x + 24
        w = box.w - 48
        y = band.bottom + 16
        footer = pygame.Rect(box.x + 16, box.bottom - 66, box.w - 32, 50)

        # 5-Slot Equipped Gear bar
        pend_eq = (self.app.pending.cats.get(c["key"]) or {}).get("equipment") or {}
        eq = c.get("equipment") or {}
        eq_slots = []
        for sk in ("weapon", "head", "face", "neck", "trinket"):
            iid = pend_eq[sk] if sk in pend_eq else (eq[sk]["item_id"] if eq.get(sk) else None)
            eq_slots.append((sk, iid, gd.item_info(iid) if iid else None))
        eq_cnt = sum(1 for _, iid, _ in eq_slots if iid)
        section_label(surf, "EQUIPPED GEAR", x, y, x + w, FAINT, f"{eq_cnt}/5 slots", GOOD if eq_cnt == 5 else FAINT)
        y += 26
        gw = (w - 4 * 8) // 5
        for j, (sk, iid, info) in enumerate(eq_slots):
            gr = pygame.Rect(x + j * (gw + 8), y, gw, 82)
            rrect(surf, gr, mix(CARD, WELL, .38), 13, ink=True)
            ib = pygame.Rect(gr.centerx - 24, gr.y + 8, 48, 48)
            if iid:
                ed.draw_item_badge(sys.modules[__name__], surf, iid, ib, info.get("rarity", ""))
            else:
                empty_slot(surf, ib)
            lbl = cb.EQUIP_SLOT_LABELS[sk].split()[0]
            text(surf, lbl, (gr.centerx, gr.bottom - 5), 14, TEXT if iid else FAINT, "bold", anchor="midbottom")
        y += 100

        # Skills & Passives as sprite tiles (same visual language as CatEdit's Live Loadout)
        pend_ab = (self.app.pending.cats.get(c["key"]) or {}).get("abilities") or {}
        ab = c["abilities"]
        actives = pend_ab.get("active", ab["active"])
        passives = pend_ab.get("passive", ab["passive"])
        p_tiers = pend_ab.get("passive_tiers", ab["passive_tiers"])
        disorders = pend_ab.get("disorder", ab["disorder"])
        tiles = [(aid, gd.active_info(aid)["name"], TEXT, INFO) for aid in actives[1:] if aid]
        n_act = len(tiles)
        for pid, t in zip(passives, p_tiers):
            if pid:
                pi = gd.passive_info(pid, t)
                tiles.append((pid, pi.get("display_name", pi["name"]), ACCENT, ACCENT))
        d_names = [gd.disorder_info(did)["name"] for did in disorders if did]
        injured = bool(c["injured"] or c["wounded"])
        status_h = 26 + 40 + (30 if injured else 0)
        section_label(surf, "SKILLS & PASSIVES", x, y, x + w, FAINT,
                      f"{n_act} active · {len(tiles) - n_act} passive")
        y += 26
        if not tiles:
            text(surf, "No skills learned yet", (x, y + 4), 21, FAINT, "bold")
            y += 40
        else:
            tg, th = 8, 50
            tw = (w - tg) // 2
            max_rows = max(1, (footer.y - 18 - status_h - y + tg) // (th + tg))
            shown = tiles[:max_rows * 2]
            if len(tiles) > len(shown):
                shown = shown[:-1]
            for k, (sid, nm, tcol, acc) in enumerate(shown):
                tr = pygame.Rect(x + (k % 2) * (tw + tg), y + (k // 2) * (th + tg), tw, th)
                rrect(surf, tr, mix(CARD, WELL, .32), 12, ink=True)
                rrect(surf, (tr.x + 4, tr.y + 7, 4, tr.h - 14), acc, 2, ink=False)
                ib = pygame.Rect(tr.x + 13, tr.centery - 17, 34, 34)
                ed.draw_sprite_badge(sys.modules[__name__], surf, "ability", sid, ib, border_col=acc, fallback_col=acc)
                text(surf, nm, (ib.right + 9, tr.centery), 19, tcol, "black", anchor="midleft", maxw=tr.right - ib.right - 18)
            if len(tiles) > len(shown):
                k = len(shown)
                tr = pygame.Rect(x + (k % 2) * (tw + tg), y + (k // 2) * (th + tg), tw, th)
                rrect(surf, tr, WELL, 12, ink=True)
                text(surf, f"+{len(tiles) - len(shown)} more · A to view", tr.center, 18, DIM, "bold", anchor="center")
            y += ((len(shown) + (1 if len(tiles) > len(shown) else 0) + 1) // 2) * (th + tg) + 10

        # Disorders & injury status
        section_label(surf, "DISORDERS & STATUS", x, y, x + w, FAINT)
        y += 26
        if d_names:
            dx = x
            for dn in d_names:
                bg = (220, 90, 190) if dn.lower().startswith("d") and "vu" in dn.lower() else BAD
                if dx > x + w - 90:
                    break
                dx = pill(surf, dn, dx, y, bg, DARK, size=18).right + 8
            y += 38
        else:
            pill(surf, "No disorders", x, y, mix(GOOD, CARD, .72), GOOD, size=18)
            y += 38
        if injured:
            inj = gd.injury_info(c["injury"])["name"] if c["injury"] else "Wounded"
            healed = bool((self.app.pending.cats.get(c["key"]) or {}).get("heal"))
            pygame.draw.circle(surf, INK, (x + 9, y + 13), 9)
            pygame.draw.circle(surf, GOOD if healed else BAD, (x + 9, y + 12), 7)
            text(surf, f"Injury: {inj}" + (" · Heal queued" if healed else ""), (x + 30, y + 12), 20,
                 GOOD if healed else BAD, "bold", anchor="midleft", maxw=w - 30)

        # Controller-glyph action footer
        rrect(surf, footer, WELL, 14, ink=True)
        fx = glyph(surf, "A", footer.x + 12, footer.centery) + 10
        fx = text(surf, "Edit full build", (fx, footer.centery), 20, TEXT, "bold", anchor="midleft").right + 24
        fx = glyph(surf, "X", fx, footer.centery) + 10
        text(surf, "Quick heal & cure", (fx, footer.centery), 20, TEXT, "bold", anchor="midleft",
             maxw=footer.right - fx - 14)

    def _render_bp_row(self, surf, r, row: Row, selected: bool) -> None:
        kind, obj, it = row.data
        badge = pygame.Rect(r.x + 14, r.centery - 29, 58, 58)
        ed.draw_item_badge(sys.modules[__name__], surf, it.get("id"), badge, it.get("rarity", ""))
        col = ACCENT if kind == "queued" else (INFO if kind == "moved" else TEXT)
        text(surf, row.label, (r.x + 86, r.centery), 29, col, "bold", anchor="midleft", maxw=r.w - 430)
        if any((it.get("stats") or {}).values()):
            draw_stat_deltas(surf, it["stats"], r.right - 134, r.centery, size=21, icon=28)
        else:
            rar = (it.get("rarity") or "").replace("_", " ")
            knd = (it.get("kind") or "other").replace("_", " ")
            text(surf, " · ".join(x for x in (rar, knd) if x), (r.right - 134, r.centery), 20, FAINT, "semi",
                 anchor="midright", maxw=220)
        if kind == "queued":
            pill(surf, "NEW", r.right - 22, r.centery, ACCENT, DARK, size=18, anchor="midright")
        elif kind == "moved":
            pill(surf, "FROM HOME", r.right - 22, r.centery, INFO, DARK, size=17, anchor="midright")
        elif kind == "owned" and obj.get("field_a", -1) >= 0:
            pill(surf, f"×{obj['field_a']}", r.right - 22, r.centery, CARD_HI, DIM, size=18, anchor="midright")

    def draw(self, surf, area) -> None:
        snap = self.app.snap
        st = snap.steven
        p = self.app.pending
        self.party = [c for c in snap.parsed["cats"] if c["room"] == "(ADVENTURE)"]
        in_run = self._in_run()
        bp_cnt = container_count(self.app, "backpack")
        self.mode_rects, self.rects, self.right_rects, self.arrow_rects = [], {}, [], {}
        self.bp_sort_rect = None
        self.bp_search_rect = None
        mpos = getattr(self.app, "mouse_pos", (-1, -1))

        # ---- Top Sub-View Bar (LT / RT)
        cy_bar = area.y + 18
        if not hasattr(self, "seg_anim"):
            self.seg_anim = RectAnim()
        self.mode_rects, _ = segbar(surf, [
            ("Run & Steven", None, BAD if (st.active or st.dejavu_cats) else None, BAD if (st.active or st.dejavu_cats) else ACCENT),
            ("Party", str(len(self.party)), None, INFO),
            ("Backpack", str(bp_cnt), None, INFO),
        ], self.m, area.x, cy_bar, "LTRT", self.seg_anim, mpos=mpos)

        mode = self.MODES[self.m][0]
        if mode == "run":
            pill(surf, "ACTIVE RUN" if in_run else "AT HOME", area.right - 6, cy_bar,
                 INFO if in_run else CARD_HI, DARK if in_run else DIM, size=19, anchor="midright")
        elif mode == "party":
            inj_p = sum(1 for c in self.party if c["injured"] or c["wounded"])
            dis_p = sum(1 for c in self.party if any(c["abilities"]["disorder"]))
            st_lbl = f"{inj_p} injured · {dis_p} disordered" if (inj_p or dis_p) else ("All party healthy" if self.party else "No active run")
            pill(surf, st_lbl, area.right - 6, cy_bar,
                 mix(BAD if inj_p else (ACCENT if dis_p else GOOD), CARD, .72) if self.party else CARD_HI,
                 BAD if inj_p else (ACCENT if dis_p else (GOOD if self.party else DIM)), size=19, anchor="midright")
        else:
            self.bp_search_rect = ed.draw_search_pill(sys.modules[__name__], surf, area.right - 620, cy_bar,
                                                      self.bp_query, anchor="midright")
            self.bp_sort_rect = pill(surf, f"Sort: {INV_SORTS[self.bp_sort]} (Y)", self.bp_search_rect.left - 12, cy_bar,
                                     ACCENT if self.bp_sort else CARD_HI, DARK if self.bp_sort else TEXT, size=19, anchor="midright")

        body = pygame.Rect(area.x, area.y + 58, area.w, area.h - 58)

        # ---- Sub-view 0: Run & Steven
        if mode == "run":
            hero = pygame.Rect(body.x, body.y, 960, body.h)
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
            ]
            by = hero.y + 184
            for key, label, desc, ico_k, queued, primary in btns:
                r = pygame.Rect(hero.x + 30, by, hero.w - 60, 112)
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
                rrect(surf, r, col, 18)
                fg = DARK if (primary and alert and not queued) else TEXT
                ib = pygame.Rect(r.x + 18, r.centery - 28, 56, 56)
                ed.draw_sprite_badge(sys.modules[__name__], surf, "ui", ico_k, ib)
                text(surf, label, (ib.right + 18, r.y + 18), 30, fg, "black", maxw=r.w - 260)
                text(surf, desc, (ib.right + 18, r.bottom - 18), 21, mix(fg, col, .24), anchor="bottomleft", maxw=r.w - 260)
                if key == "guard":
                    pill(surf, guard_pill[0], r.right - 26, r.centery, guard_pill[1], guard_pill[2], 19, anchor="midright")
                elif queued:
                    pill(surf, "QUEUED", r.right - 26, r.centery, GOOD, DARK, 19, anchor="midright")
                else:
                    tri(surf, fg, (r.right - 32, r.centery), 10, "right")
                by += 126

            # Right column: Run Resources & Party Quick Actions (or clean At-Home empty state)
            col_x = hero.right + 32
            col_w = body.right - col_x
            if not in_run:
                home_card = pygame.Rect(col_x, body.y, col_w, body.h)
                rrect(surf, home_card, CARD, 24, shadow=True)
                empty_state(surf, home_card, "Boss", "At Home — No Active Run",
                            "Start a run in Mewgenics to edit party, coins & backpack.", top=150)
                note_lines = ed.wrap_lines(
                    F(21),
                    "Steven Watch on the left works at home too — toggle Auto-block or wipe past strikes before departing.",
                    home_card.w - 96, max_lines=3,
                )
                for idx_l, ln in enumerate(note_lines):
                    text(surf, ln, (home_card.centerx, home_card.y + 428 + idx_l * 30), 21, DIM, anchor="midtop")
                if self.col == 0:
                    focus_ring(surf, self.ring.to(self.rects[self.STEVEN_ACTIONS[self.act_idx]]), 18)
                return

            section_label(surf, "RUN RESOURCES", col_x + 4, body.y + 4, body.right - 6, INFO,
                          "Left/Right adjusts · X maxes", size=18)
            ry = body.y + 34
            for idx, (key, label, ico_k, step, _, _) in enumerate(self.RESOURCES):
                r = pygame.Rect(col_x, ry, col_w, 82)
                self.right_rects.append((r, "res", key))
                focused = (self.col == 1 and self.right_idx == idx)
                rrect(surf, r, CARD_HI if focused else CARD, 18)
                val = self._val(key)
                base = snap.parsed["basic"].get(key, 0)
                changed = val != base
                ico = ed.get_sprite("ui", ico_k, 36)
                if ico is not None:
                    surf.blit(ico, ico.get_rect(midleft=(r.x + 20, r.centery)))
                text(surf, label, (r.x + 70, r.centery), 26, TEXT, "bold", anchor="midleft", maxw=r.w - 320)
                text(surf, f"{val:,}", (r.right - 138, r.centery), 30, ACCENT if changed else TEXT, "black", anchor="midright")
                for d, direction, cx in ((-step, "left", r.right - 92), (step, "right", r.right - 38)):
                    ar = pygame.Rect(0, 0, 44, 44)
                    ar.center = (cx, r.centery)
                    self.arrow_rects[(key, d)] = ar
                    pygame.draw.rect(surf, CARD_HI if focused else mix(CARD, CARD_HI, .5), ar, border_radius=11)
                    pygame.draw.rect(surf, INK, ar, width=2, border_radius=11)
                    tri(surf, TEXT if focused else DIM, ar.center, 9, direction)
                ry += 92

            ry += 16
            section_label(surf, "PARTY BATCH ACTIONS", col_x + 4, ry, body.right - 6, ACCENT, size=18)
            ry += 30
            dis_cnt = sum(1 for c in self.party if any(c["abilities"]["disorder"]))
            party_btns = [
                ("clear_party_dis", "Clear party disorders", "Removes all disorders on every Adventure Party cat", "Heart",
                 p.clear_party_disorders, f"{dis_cnt} affected" if dis_cnt else "Clean", BAD if dis_cnt else GOOD),
                ("max_party", "Max party stats (All 7)", "Sets all 7 base stats to 7 on your Adventure Party", "STR",
                 p.max_party_stats, "Queue", CARD_HI),
            ]
            for j, (key, label, desc, ico_k, queued, btxt, bcol) in enumerate(party_btns):
                idx = len(self.RESOURCES) + j
                r = pygame.Rect(col_x, ry, col_w, 96)
                self.right_rects.append((r, "act", key))
                focused = (self.col == 1 and self.right_idx == idx)
                hov = r.collidepoint(mpos)
                col = mix(GOOD, CARD, .45) if queued else (CARD_HI if (focused or hov) else CARD)
                rrect(surf, r, col, 18)
                ib = pygame.Rect(r.x + 18, r.centery - 28, 56, 56)
                ed.draw_sprite_badge(sys.modules[__name__], surf, "ui", ico_k, ib)
                text(surf, label, (ib.right + 16, r.y + 16), 28, TEXT, "black", maxw=r.w - 250)
                text(surf, desc, (ib.right + 16, r.bottom - 16), 20, DIM, anchor="bottomleft", maxw=r.w - 250)
                if queued:
                    pill(surf, "QUEUED", r.right - 24, r.centery, GOOD, DARK, 18, anchor="midright")
                elif btxt == "Queue":
                    tri(surf, ACCENT if (focused or hov) else DIM, (r.right - 30, r.centery), 10, "right")
                else:
                    pill(surf, btxt, r.right - 24, r.centery, bcol, DARK, 18, anchor="midright")
                ry += 108

            # Calm run summary strip at bottom of right col (pure info, no duplicate routes)
            info_box = pygame.Rect(col_x, ry + 8, col_w, body.bottom - (ry + 8))
            if info_box.h >= 66:
                rrect(surf, info_box, WELL, 18, ink=True)
                icy = info_box.centery
                text(surf, f"{plural(len(self.party), 'party cat')}  ·  {plural(bp_cnt, 'backpack item')}",
                          (info_box.x + 24, icy - 15), 23, TEXT, "black", anchor="midleft")
                text(surf, "Inspect gear, abilities & loot in Party / Backpack.",
                     (info_box.x + 24, icy + 17), 19, FAINT, "bold", anchor="midleft", maxw=info_box.w - 150)
                glyph(surf, "RT", info_box.right - 24 - 54, icy)

            target_rect = (
                self.rects[self.STEVEN_ACTIONS[self.act_idx]]
                if self.col == 0 else
                self.right_rects[self.right_idx][0]
            )
            focus_ring(surf, self.ring.to(target_rect), 18)
            return

        # ---- Sub-view 1: Party (4)
        if mode == "party":
            if not self.party:
                empty_state(surf, body, "Boss", "No active adventure party",
                            "Your cats are currently at home. Head to Cats for house cats, or start a run in Mewgenics.")
                return
            la = pygame.Rect(body.x, body.y, body.w - 620, body.h)
            self.party_list.draw(surf, la, self._render_party_row)
            dbox = pygame.Rect(body.right - 580, body.y, 580, body.h)
            if self.party_list.current:
                self._draw_party_dossier(surf, dbox, self.party_list.current.data)
            else:
                rrect(surf, dbox, CARD, 24)
            return

        # ---- Sub-view 2: Backpack (N)
        self._locs = owned_item_locations(self.app)
        la = pygame.Rect(body.x, body.y, body.w - 620, body.h)
        if not self.bp_list.rows:
            if self.bp_query:
                empty_state(surf, la, "Treasure", f'No backpack items matching "{self.bp_query}"',
                            "Press B to clear the search.")
            else:
                empty_state(surf, la, "Treasure", "Adventure Backpack is empty",
                            "Send items from Items » Home Storage, or spawn items with Target: Adv Backpack.")
        else:
            self.bp_list.draw(surf, la, self._render_bp_row)

        rx = body.right - 580
        q_h = 84
        detail_box = pygame.Rect(rx, body.y, 580, body.h - q_h - 14)
        cur_item = self.bp_list.current.data[2] if self.bp_list.current else None
        ed.draw_item_detail(sys.modules[__name__], surf, detail_box, cur_item, owned_ids=set(self._locs.keys()))

        qbox = pygame.Rect(rx, detail_box.bottom + 14, 580, q_h)
        rrect(surf, qbox, CARD, 20)
        a_n = len(p.inv_add["backpack"])
        r_n = len(p.inv_remove["backpack"])
        m_out = sum(1 for sg, _, _ in p.inv_move if sg == "backpack")
        m_in = sum(1 for _, dg, _ in p.inv_move if dg == "backpack")
        bits = ([f"+{a_n} added"] if a_n else []) + ([f"-{r_n} removed"] if r_n else []) + ([f"{m_out} » Home"] if m_out else []) + ([f"{m_in} « Home"] if m_in else [])
        if bits:
            text(surf, "PENDING BACKPACK EDITS", (qbox.x + 24, qbox.y + 14), 16, ACCENT, "black")
            text(surf, "  ·  ".join(bits), (qbox.x + 24, qbox.y + 42), 22, TEXT, "bold", maxw=qbox.w - 48)
        else:
            text(surf, "ADVENTURE BACKPACK", (qbox.x + 24, qbox.y + 14), 16, INFO, "black")
            text(surf, "A: Send to Home · X: Remove · Spawn new in Items.", (qbox.x + 24, qbox.y + 42), 19, DIM, maxw=qbox.w - 48)

    def hint_items(self):
        mode = self.MODES[self.m][0]
        if mode == "run":
            if self.col == 1 and self._in_run():
                if self.right_idx < len(self.RESOURCES):
                    return [("DPAD_LR", "Adjust"), ("X", "Max"), ("SELECT", "Switch Col"), ("LTRT", "Sub-view")]
                return [("A", "Toggle"), ("SELECT", "Switch Col"), ("LTRT", "Sub-view")]
            extra = [("DPAD_LR", "Resources")] if self._in_run() else []
            return [("A", "Toggle / Run")] + extra + [("LTRT", "Sub-view"), ("B", "Saves")]
        if mode == "party":
            if not self.party:
                return [("LTRT", "Sub-view"), ("B", "Saves")]
            cur = self.party_list.current.data if self.party_list.current else None
            x_lbl = "Heal" if (cur and (cur["injured"] or cur["wounded"] or cur["hp"] < cur["max_hp"])) else "Cure Dis."
            return [("A", "Edit Cat"), ("X", x_lbl), ("LTRT", "Sub-view"), ("B", "Saves")]
        cur_bp = self.bp_list.current.data if self.bp_list.current else None
        a_lbl = "Undo Move" if (cur_bp and cur_bp[0] == "moved") else "Send to Home"
        return ([("A", a_lbl), ("X", "Remove")] if cur_bp else []) + [("Y", "Sort"), ("SELECT", "Search"), ("LTRT", "Sub-view")]


# --------------------------------------------------------------------------- cats (Strictly House & Graveyard)

class CatsTab(Tab):
    name = "Cats"
    FILTERS = [
        ("House", "house", lambda c: True),
        ("Injured", "house", lambda c: c["injured"] or c["wounded"]),
        ("Mutated", "house", lambda c: any(v >= 300 for k, v in c["mutations"].items() if k != "coat")),
        ("Retired", "house", lambda c: c["retired"]),
        ("Graveyard", "graveyard", lambda c: True),
    ]
    SEX_FILTERS = ("All Sex", "Male", "Female", "Ditto")
    SORTS = [
        ("Room", lambda c: (c["room"], c["name"].lower())),
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
        if src == "graveyard":
            cats = self.app.snap.parsed["graveyard"]
        else:
            cats = [c for c in self.app.snap.parsed["cats"] if c["room"] != "(ADVENTURE)"]
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
        self.app.push(ed.SearchModal(sys.modules[__name__], self.app, "Search House & Graveyard Cats", self.query,
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
                self.app.toast(f"Queued Revive » Attic for {c['name'] or '#' + str(c['key'])} · press Menu to save", GOOD, 3.0)
            elif c["injured"] or c["wounded"]:
                self.app.pending.cats.setdefault(c["key"], {})["heal"] = True
                self.app.toast(f"Queued Heal for {c['name'] or '#' + str(c['key'])} · press Menu to save", GOOD, 3.0)
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
        if (c["injured"] or c["wounded"]) and not pend_c.get("heal"):
            inj_lbl = gd.injury_info(c["injury"])["name"] if c["injury"] else "Wounded"
            bx = pill(surf, inj_lbl, bx, name_r.centery, BAD, DARK, size=17, anchor="midleft").right + 8
        if "DejaVu" in (c["abilities"]["disorder"] or []):
            bx = pill(surf, "DÉJÀ VU", bx, name_r.centery, (220, 90, 190), DARK, size=17, anchor="midleft").right + 8
        if c.get("coi", 0.0) >= 0.20:
            pill(surf, f"CoI {c['coi'] * 100:.0f}%", bx, name_r.centery, mix(BAD, CARD, .65), BAD, size=16, anchor="midleft")

        lvl = c["level"] if 0 <= c["level"] <= 99 else "?"
        rm_raw = self.app.pending.room_moves.get(c["key"], c["room"])
        room = (rm_raw or "Graveyard").replace("_", " ")
        mut_n = br.mutation_count(c)
        parts = [f"{c['class']}", f"Lv {lvl}", f"{c['age']}d", room, f"Sum {br.stat_sum(c)}", f"HP {c['hp']}/{c['max_hp']}"]
        if mut_n:
            parts.append(f"{mut_n} mut")
        if c["retired"]:
            parts.append("retired")
        text(surf, " · ".join(parts), (r.x + 114, r.bottom - 18), 24, DIM, anchor="bottomleft", maxw=860)
        draw_stat_strip(surf, c["effective_stats"], r.right - 24, r.centery, faded=gone, hi=selected)

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
        x = area.x
        for i, lbl in enumerate(labels):
            sel = i == self.f
            w_est = F(22, "bold").size(lbl)[0] + 22
            if x + w_est > chips_right:
                break
            r = pill(surf, lbl, x, area.y, ACCENT if sel else CARD, DARK if sel else DIM, 22)
            self.chip_rects.append((r, i))
            x = r.right + 9

        la = pygame.Rect(area.x, area.y + 62, area.w - 20, area.h - 62)
        if not self.list.rows:
            if self.query:
                empty_state(surf, la, "Collar", f'No house cats matching "{self.query}"', "Press B to clear the search.")
            else:
                empty_state(surf, la, "Collar", "No house cats in this filter", "Use the D-pad to switch filters · Adventure party cats live in the Adventure tab.")
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
    SECTIONS = [
        ("build", "Build & Gear"),
        ("stats", "Stats"),
        ("profile", "Profile"),
    ]

    def __init__(self, app: "App", cats: list[dict[str, Any]], index: int) -> None:
        super().__init__(app)
        self.cats, self.idx = cats, index
        self.sec = 0
        self.list = ListView(row_h=78, gap=8)
        self.card_mode = False
        self.card_idx = 0
        self.hover_idx: int | None = None
        self.sec_rects: list[tuple[pygame.Rect, int]] = []
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
        sec_key = self.SECTIONS[self.sec][0]
        rows: list[Row] = []
        if sec_key == "build":
            if c["dead"]:
                rows.append(Row("Revive cat » Attic", data=("revive",)))
            elif c["injured"] or c["wounded"] or c["hp"] < c["max_hp"]:
                rows.append(Row("Heal cat (HP, injuries & scars)", data=("heal",)))
            rows += [
                Row("Active Abilities", data=("sub", "active")),
                Row("Passives (+ Tier 1/2)", data=("sub", "passive")),
                Row("Disorders", data=("sub", "disorder")),
                Row("Equipped Gear", data=("sub", "equip")),
                Row("Mutations & Coat", data=("sub", "mutations")),
                Row("Build Presets & Clone", data=("sub", "presets")),
            ]
            if not c["dead"] and any(self.vals["abilities"]["disorder"]):
                rows.append(Row("Clear all disorders", data=("clear_dis",)))
        elif sec_key == "stats":
            rows += [Row(s, data=("stat", s), enabled=self.ok["stats"]) for s in STATS]
            rows.append(Row("Level-Up Bonuses", data=("sub", "bonuses")))
            rows.append(Row("Max all stats (All 7)", data=("max",), enabled=self.ok["stats"]))
        else:
            rows.append(Row("Rename cat", data=("rename",)))
            rows.append(Row("Class", data=("class",)))
            rows.append(Row("Level", data=("level",), enabled=self.ok["level"]))
            rows.append(Row("Age", data=("age",), enabled=self.ok["age"]))
            rows.append(Row("Sex", data=("sex",)))
            if c["room"] and c["room"] != "(ADVENTURE)" and self.app.snap.parsed.get("rooms"):
                rows.append(Row("House Room", data=("room",)))
            rows.append(Row("Retired", data=("retired",)))
            rows.append(Row("Revert cat edits", data=("revert",)))
        self.list.set_rows(rows)

    def _section_dirty(self, sec_key: str) -> bool:
        c = self.cat
        if sec_key == "build":
            ab_dirty = any(self.vals["abilities"][f] != c["abilities"][f]
                           for f in ("active", "passive", "passive_tiers", "disorder", "disorder_tiers"))
            mut_dirty = any(v != c["mutations"].get(k) for k, v in self.vals["mutations"].items())
            orig_eq = self._orig_equip(c)
            eq_dirty = any(v != orig_eq.get(k) for k, v in self.vals["equipment"].items())
            return ab_dirty or mut_dirty or eq_dirty or self.vals["heal"] or self.vals["revive"]
        if sec_key == "stats":
            return self.vals["stats"] != c["stats"] or self.vals["level_bonuses"] != c["level_bonuses"]
        return (
            self.vals["name"] != c["name"]
            or self.vals["room"] != c["room"]
            or any(self.vals[k] != c[k] for k in ("level", "age", "sex", "retired", "class"))
        )

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
        if btn in (LT, RT):
            self.sec = (self.sec + (1 if btn == RT else -1)) % len(self.SECTIONS)
            self.card_mode = False
            self.list.sel = 0
            self.build()
            return
        if btn == Y:
            cards = self._all_inspect_cards()
            if cards:
                self.card_mode = not self.card_mode
                self.card_idx = min(self.card_idx, len(cards) - 1)
            return
        if self.card_mode:
            ab_cards = self._ability_cards()
            gear_cards = self._gear_cards()
            cards = ab_cards + gear_cards
            n_ab = len(ab_cards)
            if not cards:
                self.card_mode = False
            else:
                if btn == UP:
                    if self.card_idx >= n_ab:
                        self.card_idx = max(0, n_ab - 1) if n_ab else self.card_idx
                    else:
                        self.card_idx = max(0, self.card_idx - 2)
                elif btn == DOWN:
                    if self.card_idx < n_ab:
                        nxt = self.card_idx + 2
                        self.card_idx = nxt if nxt < n_ab else n_ab
                elif btn == LEFT:
                    if (self.card_idx < n_ab and self.card_idx % 2 == 0) or self.card_idx == n_ab:
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
            elif btn == RIGHT and self._all_inspect_cards():
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
                self.app.toast("Revive » Attic queued · press Menu to save" if self.vals["revive"] else "Revive cancelled", GOOD)
            elif kind == "max":
                self.vals["stats"] = {s: 7 for s in STATS}
                self.commit_local()
                self.app.toast("All base stats set to 7", GOOD)
            elif kind == "clear_dis":
                self.vals["abilities"]["disorder"] = [None, None]
                self.vals["abilities"]["disorder_tiers"] = [1, 1]
                self.commit_local()
                self.build()
                self.app.toast("Cleared all disorders on this cat", GOOD)
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
        for r, i in self.sec_rects:
            if r.collidepoint(pos):
                sfx.play("tab")
                self.sec = i
                self.card_mode = False
                self.list.sel = 0
                self.build()
                return
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
            if kind in ("sub", "rename", "heal", "revive", "max", "clear_dis", "revert"):
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
        if kind == "stat":
            ico = ed.get_stat_icon(row.data[1], 40)
            if ico is not None:
                surf.blit(ico, ico.get_rect(center=(r.x + 46, r.centery)))
            text(surf, row.label, (r.x + 78, r.centery), 30, col, "black", anchor="midleft")
        else:
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
            x = r.x + 188
            for i in range(7):
                filled = i < new
                was = i < old
                colr = stat_col(new) if filled and was else ACCENT if filled else mix(BAD, CARD, .4) if was else mix(LINE, CARD, .3)
                sr = pygame.Rect(x + i * 54, r.centery - 15, 46, 30)
                pygame.draw.rect(surf, colr, sr, border_radius=8)
                pygame.draw.rect(surf, INK, sr, width=2, border_radius=8)
                if HEADER_H + 64 <= sr.centery <= CONTENT_BOTTOM:
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
                if HEADER_H + 64 <= ar.centery <= CONTENT_BOTTOM:
                    self.adj_rects.append((ar, kind, d))
            text(surf, shown, (r.right - 155, r.centery), 27, ACCENT if changed else col, "black", anchor="center", maxw=172)
            if changed:
                was = c[kind]
                was_s = ("yes" if was else "no") if kind == "retired" else str(was).replace("_", " ")
                text(surf, f"was {was_s}", (r.x + 218, r.centery), 20, DIM, anchor="midleft", maxw=r.w - 525)
        else:
            pill(surf, "PRESS A", r.right - 30, r.centery, CARD_HI, ACCENT if selected else DIM, size=18, anchor="midright")

    def draw(self, surf) -> None:
        self.sec_rects, self.stat_rects, self.adj_rects, self.nav_rects = [], [], [], {}
        c = self.cat
        cls_name = self.vals["class"]
        ccol = CLASS_COL.get(cls_name, ACCENT)
        card = pygame.Rect(W - PAD - 610, HEADER_H + 24, 610, CONTENT_BOTTOM - HEADER_H - 24)
        rrect(surf, card, CARD, 24, shadow=True)

        # Left-pane Section Bar (Build & Gear | Stats | Profile — zero scrolling!)
        if not hasattr(self, "seg_anim"):
            self.seg_anim = RectAnim()
        cy_bar = card.y + 23
        self.sec_rects, _ = segbar(surf, [
            (slbl, None, ACCENT if self._section_dirty(sk) else None, ACCENT) for sk, slbl in self.SECTIONS
        ], self.sec, PAD, cy_bar, "LTRT", self.seg_anim, mpos=getattr(self.app, "mouse_pos", (-1, -1)))
        base_sum = sum(self.vals["stats"].values())
        pill(surf, f"Base Sum {base_sum}/49", card.x - 36, cy_bar, mix(GOOD if base_sum >= 49 else ACCENT, CARD, .72),
             GOOD if base_sum >= 49 else ACCENT, 18, anchor="midright")

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
        cur = self.list.current
        linked_kind = cur.data[1] if (not self.card_mode and self.hover_idx is None and cur and cur.data[0] == "sub") else None

        gy = sy + 44
        section_label(surf, "LIVE LOADOUT", card.x + 22, gy, card.right - 22, FAINT,
                      "Y or click any card to swap slot", ACCENT if self.card_mode else FAINT)
        gy += 26

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

        bottom_reserve = 186
        gear_h = 84
        max_ab_bottom = card.bottom - bottom_reserve - gear_h - 10

        if ab_cards:  # grow cards to fill the loadout well (no dead gap above the gear bar)
            n_rows = (len(ab_cards) + 1) // 2
            ch = max(56, min(74, (max_ab_bottom - gy - gap * (n_rows - 1)) // n_rows))
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
                is_linked = (linked_kind == ac["kind"])
                bg = CARD_HI if (is_hi or is_linked) else mix(CARD, WELL, .32)
                rrect(surf, cr, bg, 13, ink=True)
                if is_hi:
                    pygame.draw.rect(surf, ACCENT, cr.inflate(2, 2), width=3, border_radius=14)
                elif is_linked:
                    pygame.draw.rect(surf, ac["accent"], cr.inflate(1, 1), width=2, border_radius=14)
                rrect(surf, (cr.x + 4, cr.y + 6, 5, cr.h - 12), ac["accent"], 3, ink=False)
                bs = min(50, ch - 16)
                ib = pygame.Rect(cr.x + 15, cr.centery - bs // 2, bs, bs)
                ed.draw_sprite_badge(sys.modules[__name__], surf, ac["sprite_kind"], ac["id"], ib,
                                     border_col=ac["accent"], fallback_col=ac["accent"])
                tcol = BAD if ac["kind"] == "disorder" else (ACCENT if ac["kind"] == "passive" else TEXT)
                tw_c = cr.right - ib.right - 22
                text(surf, ac["name"], (ib.right + 11, cr.centery + 3), 20, tcol, "black", anchor="bottomleft", maxw=tw_c)
                text(surf, ac["tag"], (ib.right + 11, cr.centery + 2), 16, FAINT if not is_hi else DIM, "bold",
                     anchor="topleft", maxw=tw_c)

        # 5-Slot Equipped Gear Mini-Bar
        gear_y = card.bottom - bottom_reserve - gear_h + 2
        eq_n = sum(1 for gc in gear_cards if gc["id"])
        section_label(surf, "EQUIPPED GEAR", card.x + 22, gear_y - 24, card.right - 22,
                      INFO if linked_kind == "equip" else FAINT, f"{eq_n}/5 slots", GOOD if eq_n == 5 else FAINT)
        gw = (card.w - 44 - 4 * 8) // 5
        for j, gc in enumerate(gear_cards):
            idx_all = len(ab_cards) + j
            gr = pygame.Rect(card.x + 22 + j * (gw + 8), gear_y, gw, 78)
            self.card_rects.append((gr, gc))
            is_hi = (idx_all == active_tip_idx)
            is_linked = (linked_kind == "equip")
            rrect(surf, gr, CARD_HI if (is_hi or is_linked) else mix(CARD, WELL, .35), 13, ink=True)
            if is_hi:
                pygame.draw.rect(surf, ACCENT, gr.inflate(2, 2), width=3, border_radius=14)
            elif is_linked:
                pygame.draw.rect(surf, INFO, gr.inflate(1, 1), width=2, border_radius=14)
            ib = pygame.Rect(gr.centerx - 22, gr.y + 7, 44, 44)
            if gc["id"]:
                ed.draw_item_badge(sys.modules[__name__], surf, gc["id"], ib, gc["info"].get("rarity", ""))
            else:
                empty_slot(surf, ib)
            text(surf, gc["tag"], (gr.centerx, gr.bottom - 6), 14, TEXT if gc["id"] else FAINT, "bold", anchor="midbottom")

        # Bottom Context-Aware Inspector Panel (Card OR Stat OR Mutations OR Slot Group OR Profile Lineage!)
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
            base_v = self.vals["stats"][s]
            bon_v = self.vals["level_bonuses"][s]
            inj_v = 0 if self.vals["heal"] else c["injury_mods"][s]
            eff_v = max(1, base_v + bon_v + inj_v)
            ico = ed.get_stat_icon(s, 42)
            if ico is not None:
                surf.blit(ico, ico.get_rect(topleft=(tbox.x + 18, tbox.y + 14)))
            brk = f"Base {base_v}" + (f" {'+' if bon_v > 0 else ''}{bon_v} bon" if bon_v else "") + (f" {inj_v} inj" if inj_v else "") + f" = {eff_v}"
            text(surf, f"{s}  ·  {brk}", (tbox.x + 72, tbox.y + 22), 24, stat_col(base_v), "black", maxw=tbox.w - 92)
            for i, l in enumerate(ed.wrap_lines(F(21), STAT_INFO[s], tbox.w - 44, max_lines=3)):
                text(surf, l, (tbox.x + 22, tbox.y + 68 + i * 27), 21, DIM)
        elif cur and cur.data == ("sub", "bonuses"):
            rrect(surf, tbox, CARD_HI, 18, ink=True)
            text(surf, "EFFECTIVE STATS (BASE + BONUS)", (tbox.x + 22, tbox.y + 14), 16, ACCENT, "black")
            eff = {s: max(1, self.vals["stats"][s] + self.vals["level_bonuses"][s] + (0 if self.vals["heal"] else c["injury_mods"][s])) for s in STATS}
            draw_stat_strip(surf, eff, tbox.right - 22, tbox.y + 92, cell=52, icon=32)
            text(surf, "Press A to adjust level-up bonuses without breaking breeding caps.", (tbox.x + 22, tbox.bottom - 14), 17, DIM, anchor="bottomleft", maxw=tbox.w - 44)
        elif cur and cur.data == ("sub", "mutations"):
            rrect(surf, tbox, CARD_HI, 18, ink=True)
            muts = self.vals["mutations"]
            active_m: list[str] = []
            for mk, _, mcat, _ in gd.MUTATION_SLOTS:
                mv = muts.get(mk, 0)
                if mv >= 300 or gd.signed_mut_id(mv) == -2:
                    mi = gd.mutation_info(mcat, mv)
                    active_m.append(mi["name"])
            text(surf, f"MUTATIONS & COAT  ({len(active_m)} active)", (tbox.x + 22, tbox.y + 16), 17, GOOD if active_m else FAINT, "black")
            body_m = " · ".join(active_m[:6]) + (f" +{len(active_m) - 6} more" if len(active_m) > 6 else "") if active_m else "No body-part mutations or birth defects on this cat."
            for i, l in enumerate(ed.wrap_lines(F(21), body_m, tbox.w - 44, max_lines=3)):
                text(surf, l, (tbox.x + 22, tbox.y + 48 + i * 27), 21, TEXT if active_m else DIM)
            text(surf, "Press A to edit coat texture, 14 body parts, or clear birth defects.", (tbox.x + 22, tbox.bottom - 14), 17, ACCENT, "bold", anchor="bottomleft")
        elif self.SECTIONS[self.sec][0] == "profile":
            rrect(surf, tbox, WELL, 18, ink=True)
            all_cats = self.app.snap.parsed["all_cats"]
            pa = all_cats.get(c.get("parent_a"), {}).get("name") if c.get("parent_a") else ""
            pb = all_cats.get(c.get("parent_b"), {}).get("name") if c.get("parent_b") else ""
            lineage = f"{pa or '?'} × {pb or '?'}" if (pa or pb) else "Founder (wild/stray)"
            cl, sev = br.coi_label(c.get("coi", 0.0))
            ccol_coi = {"good": GOOD, "info": INFO, "warn": ACCENT, "bad": BAD}.get(sev, DIM)
            text(surf, "LINEAGE & PERSONALITY", (tbox.x + 22, tbox.y + 14), 16, FAINT, "black")
            pill(surf, f"CoI {cl}", tbox.right - 20, tbox.y + 24, mix(ccol_coi, CARD, .7), ccol_coi, size=16, anchor="midright")
            text(surf, f"Parents: {lineage}", (tbox.x + 22, tbox.y + 44), 21, TEXT, "bold", maxw=tbox.w - 44)
            text(surf, f"Libido {c['libido']:.0%}  ·  Aggression {c['aggression']:.0%}  ·  Fertility {c['fertility']:.2f}",
                 (tbox.x + 22, tbox.y + 76), 20, DIM, maxw=tbox.w - 44)
            rx = tbox.x + 22
            if c.get("lover_name"):
                rx = text(surf, f"Loves {c['lover_name']}", (rx, tbox.y + 110), 20, (235, 118, 175), "bold",
                          maxw=(tbox.w - 44) // 2).right
            if c.get("hater_name"):
                if rx > tbox.x + 22:
                    rx = text(surf, "  ·  ", (rx, tbox.y + 110), 20, FAINT).right
                text(surf, f"Hates {c['hater_name']}", (rx, tbox.y + 110), 20, BAD, "bold", maxw=tbox.right - 22 - rx)
            if not (c.get("lover_name") or c.get("hater_name")):
                text(surf, "No lover or rival recorded.", (rx, tbox.y + 110), 20, FAINT)
        else:
            rrect(surf, tbox, WELL, 18, ink=True)
            hint_map = {
                "active": ("ACTIVE ABILITIES", "Highlighted above. Press A to manage all 6 slots, or press Y / click a card directly."),
                "passive": ("PASSIVES (TIER 1 / 2)", "Highlighted above. Press A to swap passives or upgrade Tier 1 » Tier 2+."),
                "disorder": ("DISORDERS", "Highlighted above. Press A to add, edit, or clear disorders."),
                "equip": ("EQUIPPED GEAR", "Highlighted in the 5-slot bar above. Press A to swap pieces or equip a full Armor Set."),
            }
            if linked_kind in hint_map:
                ht, hb = hint_map[linked_kind]
                text(surf, ht, (tbox.x + 22, tbox.y + 18), 16, ACCENT, "black")
                for i, l in enumerate(ed.wrap_lines(F(21), hb, tbox.w - 44, max_lines=3)):
                    text(surf, l, (tbox.x + 22, tbox.y + 48 + i * 27), 21, DIM)
            else:
                text(surf, "LIVE CHARACTER SHEET", (tbox.x + 22, tbox.y + 18), 16, FAINT, "black")
                for i, l in enumerate(ed.wrap_lines(
                    F(21),
                    "Hover over any ability, passive, disorder, or gear card above — or press Y on gamepad — to inspect full effects and jump straight to that slot.",
                    tbox.w - 44, max_lines=3,
                )):
                    text(surf, l, (tbox.x + 22, tbox.y + 48 + i * 27), 21, DIM)

        area = pygame.Rect(PAD, HEADER_H + 88, card.x - PAD - 32, CONTENT_BOTTOM - (HEADER_H + 88))
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
        row = self.list.current
        adjustable = row is not None and row.data[0] in ("stat", "level", "age", "sex", "retired", "class", "room")
        mid = [("DPAD_LR", "Adjust")] if adjustable else [("A", "Open / Run")]
        return mid + [("LTRT", "Section"), ("Y", "Inspect Cards"), ("LBRB", "Prev/next cat"), ("B", "Done")] + pending_hint(self.app)


# --------------------------------------------------------------------------- items (Home Storage, Armor Sets & Item Catalog)

class ItemsTab(Tab):
    name = "Items"
    SORTS = INV_SORTS
    SET_SORTS = ("Owned %", "A-Z", "Top Stats", "Rarity")
    SLOT_ORDER = ("head", "face", "neck", "weapon", "trinket")
    KIND_ORDER = ("weapon", "head", "face", "neck", "trinket", "consumable")

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.items = sorted(gd.items().values(), key=lambda i: (i.get("name") or i["id"]).lower())
        raw_kinds = {i.get("kind") or "other" for i in self.items}
        self._ordered_kinds = [k for k in self.KIND_ORDER if k in raw_kinds] + sorted(raw_kinds - set(self.KIND_ORDER))
        self.tabs: list[str] = []
        self.t = 0                # default to Home Storage (owned home items)
        self.add_target = "storage"  # "storage" | "backpack" — ONLY applies to Armor Sets & Add Catalog!
        self.s_idx = 0
        self.query = ""
        self.search_rect: pygame.Rect | None = None
        self.sort_rect: pygame.Rect | None = None
        self.target_rect: pygame.Rect | None = None
        self.list = ListView(row_h=82, gap=8)
        self.chip_rects: list[tuple[pygame.Rect, int]] = []
        self.chip_prev_rect: pygame.Rect | None = None
        self.chip_next_rect: pygame.Rect | None = None
        self._locs: dict[str, set[str]] = {}
        self._qcounts: dict[str, int] = {}
        self._build_tabs()

    def _build_tabs(self) -> None:
        cur_mode = self.tabs[self.t] if (self.tabs and 0 <= self.t < len(self.tabs)) else "home"
        has_trash = self.app.snap is not None and container_count(self.app, "trash") > 0
        tabs = ["home"]
        if has_trash:
            tabs.append("trash")
        tabs += (
            ["sets_overview", "all"]
            + self._ordered_kinds
            + ["cursed", "r:common", "r:uncommon", "r:rare", "r:very_rare",
               "+Shield", "+STR", "+DEX", "+CON", "+INT", "+SPD", "+CHA", "+LUCK"]
        )
        self.tabs = tabs
        self.t = tabs.index(cur_mode) if cur_mode in tabs else 0

    @property
    def target_label(self) -> str:
        return "Home Storage" if self.add_target == "storage" else "Adv Backpack"

    def on_enter(self) -> None:
        self._build_tabs()
        self._rebuild()

    @staticmethod
    def _set_matches(s: dict[str, Any], q: str) -> bool:
        if not q:
            return True
        hay = " ".join([
            s.get("name", ""),
            s.get("id", ""),
            s.get("desc", ""),
            gd.format_stats(s.get("stats")),
            " ".join(
                f"{it.get('name', '')} {it.get('id', '')} {gd.format_stats(it.get('stats'))}"
                for it in s["slots"].values()
            ),
        ]).lower()
        return all(tok in hay for tok in q.lower().split())

    def _rows_for(self, q: str) -> list[Row]:
        mode = self.tabs[self.t]
        if mode == "home":
            return container_rows(self.app, "storage", q, self.s_idx)
        if mode == "trash":
            return container_rows(self.app, "trash", q, self.s_idx)
        rows: list[Row] = []
        if mode == "sets_overview":
            locs = owned_item_locations(self.app)
            for s in pr.armor_sets_catalog():
                if self._set_matches(s, q):
                    rows.append(Row(s["name"], data=("set", s, None)))
            if self.s_idx == 0:
                def _own_key(r: Row):
                    s = r.data[1]
                    tot = max(1, len(s["slots"]))
                    own = sum(1 for it in s["slots"].values() if locs.get(it["id"]))
                    return (-(own / tot), -own, s["name"].lower())
                rows.sort(key=_own_key)
            elif self.s_idx == 1:
                rows.sort(key=lambda r: r.label.lower())
            elif self.s_idx == 2:
                def _stat_key(r: Row):
                    s = r.data[1]
                    bonus = sum((s.get("stats") or {}).values())
                    pcs = sum(sum((it.get("stats") or {}).values()) for it in s["slots"].values())
                    return (-(bonus + pcs), s["name"].lower())
                rows.sort(key=_stat_key)
            elif self.s_idx == 3:
                def _rar_key(r: Row):
                    s = r.data[1]
                    top_r = max((INV_RARITY_RANK.get(it.get("rarity") or "", 0) for it in s["slots"].values()), default=0)
                    return (-top_r, s["name"].lower())
                rows.sort(key=_rar_key)
            return rows

        for it in self.items:
            ok = (
                mode == "all"
                or (mode == "cursed" and bool(it.get("cursed")))
                or (mode.startswith("r:") and (it.get("rarity") or "") == mode[2:])
                or (mode.startswith("+") and (it.get("stats") or {}).get(mode[1:], 0) > 0)
                or (it.get("kind") or "other") == mode
            )
            if ok and item_matches(it, q):
                rows.append(Row(it.get("name") or it["id"], data=("catalog", it["id"], it)))
        sort_item_rows(rows, self.s_idx)
        return rows

    def _rebuild(self) -> None:
        self._build_tabs()
        self.list.set_rows(self._rows_for(self.query))

    def _open_search(self) -> None:
        def done(q: str) -> None:
            self.query = q
            self.list.sel = 0
            self._rebuild()
        mode = self.tabs[self.t]
        title = "Search Armor Sets" if mode == "sets_overview" else ("Search Home Storage" if mode == "home" else "Search Item Catalog")
        self.app.push(ed.SearchModal(sys.modules[__name__], self.app, title, self.query,
                                     lambda q: len(self._rows_for(q)), done))

    def _cycle_sort(self) -> None:
        sorts = self.SET_SORTS if self.tabs[self.t] == "sets_overview" else self.SORTS
        self.s_idx = (self.s_idx + 1) % len(sorts)
        self.list.sel = 0
        self._rebuild()
        self.app.toast(f"Sort: {sorts[self.s_idx]}", ACCENT, 1.5)

    def _toggle_add_target(self) -> None:
        self.add_target = "backpack" if self.add_target == "storage" else "storage"
        self.app.toast(f"Add target » {self.target_label}", INFO if self.add_target == "backpack" else ACCENT, 1.8)

    def handle(self, btn: str) -> bool:
        mode = self.tabs[self.t]
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
            if mode in ("home", "trash"):
                self._cycle_sort()
            else:
                self._toggle_add_target()
        elif btn == A and self.list.current:
            kind, obj, info = self.list.current.data
            if kind == "set":
                s = obj
                adds = self.app.pending.inv_add[self.add_target]
                locs = owned_item_locations(self.app)
                pieces = [s["slots"][sk] for sk in self.SLOT_ORDER if sk in s["slots"]]
                missing = [it for it in pieces if not locs.get(it["id"])]
                if missing:
                    for it in missing:
                        adds.append(it["id"])
                    n = len(missing)
                    self.app.toast(f"Queued {n} missing pc{'s' if n != 1 else ''} of {s['name']} » {self.target_label}", GOOD, 2.2)
                else:
                    for it in pieces:
                        adds.append(it["id"])
                    self.app.toast(f"Queued full extra {s['name']} ({len(pieces)} pcs) » {self.target_label}", GOOD, 2.2)
            elif kind == "catalog":
                self.app.pending.inv_add[self.add_target].append(info["id"])
                self.app.toast(f"+1 {info['name']} » {self.target_label}", GOOD, 1.8)
            elif kind == "owned":
                if mode == "home":
                    self.app.pending.inv_move.append(("storage", "backpack", int(obj["instance_id"])))
                    self._rebuild()
                    self.app.toast(f"Sent {info['name']} » Adv Backpack", INFO, 2.0)
                elif mode == "trash":
                    self.app.pending.inv_move.append(("trash", "storage", int(obj["instance_id"])))
                    self._rebuild()
                    self.app.toast(f"Rescued {info['name']} » Home Storage", GOOD, 2.0)
            elif kind == "moved":
                sg, raw = obj
                inst = int(raw["instance_id"])
                dst_g = "storage" if mode == "home" else "trash"
                self.app.pending.inv_move[:] = [m for m in self.app.pending.inv_move if not (m[0] == sg and m[1] == dst_g and m[2] == inst)]
                self._rebuild()
                self.app.toast(f"Cancelled move of {info['name']}", DIM, 1.8)
            elif kind == "queued" and mode == "home":
                if obj in self.app.pending.inv_add["storage"]:
                    self.app.pending.inv_add["storage"].remove(obj)
                    self.app.pending.inv_add["backpack"].append(obj)
                    self._rebuild()
                    self.app.toast(f"Redirected queued {info['name']} » Adv Backpack", INFO, 2.0)
        elif btn == X and self.list.current:
            kind, obj, info = self.list.current.data
            if kind == "set":
                s = obj
                piece_ids = {it["id"] for it in s["slots"].values()}
                removed = 0
                for target_grp in (self.add_target, "storage" if self.add_target == "backpack" else "backpack"):
                    qlist = self.app.pending.inv_add[target_grp]
                    before = len(qlist)
                    qlist[:] = [iid for iid in qlist if iid not in piece_ids]
                    removed += before - len(qlist)
                    if removed:
                        break
                if removed:
                    self.app.toast(f"Unqueued {removed} pc{'s' if removed != 1 else ''} of {s['name']}", ACCENT, 1.8)
                else:
                    self.app.toast(f"No queued pieces for {s['name']}", DIM, 1.5)
            elif kind == "catalog":
                adds = self.app.pending.inv_add[self.add_target]
                if info["id"] in adds:
                    adds.remove(info["id"])
            elif kind == "queued":
                grp = "storage" if mode == "home" else "trash"
                if obj in self.app.pending.inv_add[grp]:
                    self.app.pending.inv_add[grp].remove(obj)
                self._rebuild()
            elif kind == "moved":
                sg, raw = obj
                inst = int(raw["instance_id"])
                dst_g = "storage" if mode == "home" else "trash"
                self.app.pending.inv_move[:] = [m for m in self.app.pending.inv_move if not (m[0] == sg and m[1] == dst_g and m[2] == inst)]
                self._rebuild()
                self.app.toast(f"Cancelled move of {info['name']}", DIM, 1.8)
            elif kind == "owned":
                grp = "storage" if mode == "home" else "trash"
                self.app.pending.inv_remove[grp].append(int(obj["instance_id"]))
                self._rebuild()
                lbl = "Home Storage" if grp == "storage" else "Trash"
                self.app.toast(f"Removed {info['name']} from {lbl}", ACCENT, 1.8)
        else:
            return False
        return True

    def click(self, pos) -> None:
        if self.search_rect and self.search_rect.collidepoint(pos):
            self._open_search()
            return
        if self.sort_rect and self.sort_rect.collidepoint(pos):
            self._cycle_sort()
            return
        if self.target_rect and self.target_rect.collidepoint(pos):
            self._toggle_add_target()
            return
        if self.chip_prev_rect and self.chip_prev_rect.collidepoint(pos):
            self.t = (self.t - 1) % len(self.tabs)
            self.list.sel = 0
            self._rebuild()
            return
        if self.chip_next_rect and self.chip_next_rect.collidepoint(pos):
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
        if kind == "set":
            s = obj
            ix = r.x + 14
            drawn = 0
            for sk in self.SLOT_ORDER:
                if sk in s["slots"] and drawn < 3:
                    pit = s["slots"][sk]
                    ed.draw_item_badge(sys.modules[__name__], surf, pit["id"],
                                       pygame.Rect(ix, r.centery - 25, 50, 50), pit.get("rarity", ""))
                    ix += 56
                    drawn += 1
            total_n = len(s["slots"])
            owned_n = sum(1 for pit in s["slots"].values() if self._locs.get(pit["id"]))
            full = total_n > 0 and owned_n >= total_n
            if full:
                p_lbl, p_bg, p_fg = f"{owned_n}/{total_n} FULL", GOOD, DARK
            elif owned_n > 0:
                p_lbl, p_bg, p_fg = f"{owned_n}/{total_n}", ACCENT, DARK
            else:
                p_lbl, p_bg, p_fg = f"0/{total_n}", CARD_HI, DIM
            pr_rect = pill(surf, p_lbl, r.right - 20, r.y + 28, p_bg, p_fg, size=19, anchor="midright", weight="black")
            seg_col = GOOD if full else ACCENT
            track_w = 88
            seg_w = (track_w - 4 * (total_n - 1)) // max(1, total_n)
            seg_x = pr_rect.right - track_w
            for k in range(total_n):
                pygame.draw.rect(surf, seg_col if k < owned_n else WELL,
                                 (seg_x + k * (seg_w + 4), r.bottom - 22, seg_w, 7), border_radius=3)

            tx = r.x + 190
            tw = max(160, min(pr_rect.left, seg_x) - tx - 20)
            text(surf, ed.set_display_name(s["name"]), (tx, r.y + 13), 26, TEXT, "black", maxw=tw)
            st_s = gd.format_stats(s.get("stats"))
            desc_s = " · ".join(ln.strip() for ln in (s.get("desc") or "").splitlines() if ln.strip())
            summary = " · ".join(x for x in (st_s, desc_s) if x) or "Equip all pieces for set bonus"
            text(surf, summary, (tx, r.bottom - 13), 19, GOOD if (st_s and not desc_s) else DIM,
                 anchor="bottomleft", maxw=tw)
            return
        badge = pygame.Rect(r.x + 14, r.centery - 29, 58, 58)
        ed.draw_item_badge(sys.modules[__name__], surf, it.get("id"), badge, it.get("rarity", ""))
        col = ACCENT if kind == "queued" else (INFO if kind == "moved" else TEXT)
        text(surf, row.label, (r.x + 86, r.centery), 29, col, "bold",
             anchor="midleft", maxw=r.w - 430)
        if any((it.get("stats") or {}).values()):
            draw_stat_deltas(surf, it["stats"], r.right - 130, r.centery, size=21, icon=28)
        else:
            rar = (it.get("rarity") or "").replace("_", " ")
            knd = (it.get("kind") or "other").replace("_", " ")
            text(surf, " · ".join(x for x in (rar, knd) if x), (r.right - 130, r.centery), 20, FAINT, "semi",
                 anchor="midright", maxw=220)
        if kind == "catalog":
            n = self.app.pending.inv_add[self.add_target].count(it["id"])
            if n:
                pill(surf, f"+{n}", r.right - 22, r.centery, INFO if self.add_target == "backpack" else ACCENT, DARK, size=23, anchor="midright")
        elif kind == "queued":
            pill(surf, "NEW", r.right - 22, r.centery, ACCENT, DARK, size=18, anchor="midright")
        elif kind == "moved":
            sg = obj[0]
            lbl = "FROM ADV" if sg == "backpack" else "RESCUED"
            pill(surf, lbl, r.right - 22, r.centery, INFO if sg == "backpack" else GOOD, DARK, size=17, anchor="midright")
        elif kind == "owned" and obj.get("field_a", -1) >= 0:
            pill(surf, f"×{obj['field_a']}", r.right - 22, r.centery, CARD_HI, DIM, size=18, anchor="midright")

    def draw(self, surf, area) -> None:
        self._build_tabs()
        mode = self.tabs[self.t]
        self._locs = owned_item_locations(self.app)
        self._qcounts = queued_item_counts(self.app)
        home_cnt = container_count(self.app, "storage")
        trash_cnt = container_count(self.app, "trash")

        # Row 1: Single clean Mode / Category chip bar (DPAD_LR)
        chips_right = area.right - 620
        self.chip_rects = []
        labels = [
            f"Home Storage {home_cnt}" if t == "home" else (
                f"Trash {trash_cnt}" if t == "trash" else (
                    "Armor Sets" if t == "sets_overview" else (
                        "Add: All" if t == "all" else (
                            t[2:].replace("_", " ").title() if t.startswith("r:") else (
                                t if t.startswith("+") else t.replace("_", " ").title()
                            )
                        )
                    )
                )
            )
            for t in self.tabs
        ]
        widths = [F(22, "bold").size(l)[0] + 30 for l in labels]
        start = 0
        while sum(widths[start:self.t + 1]) > (chips_right - area.x - 56) and start < self.t:
            start += 1
        x = area.x
        self.chip_prev_rect = None
        self.chip_next_rect = None
        if start:
            pr = pygame.Rect(x, area.y + 4, 26, 28)
            self.chip_prev_rect = pr
            tri(surf, ACCENT, pr.center, 8, "left")
            x += 30
        last_drawn = start - 1
        for i in range(start, len(self.tabs)):
            sel = i == self.t
            w_est = F(22, "bold").size(labels[i])[0] + 22
            if x + w_est > chips_right - 28:
                break
            r = pill(surf, labels[i], x, area.y, ACCENT if sel else CARD, DARK if sel else DIM, 22)
            self.chip_rects.append((r, i))
            x = r.right + 8
            last_drawn = i
        if last_drawn < len(self.tabs) - 1:
            nr = pygame.Rect(chips_right - 24, area.y + 4, 26, 28)
            self.chip_next_rect = nr
            tri(surf, ACCENT, nr.center, 8, "right")

        # Row 2: Context utility bar (Add Target pill ONLY in Catalog / Armor Sets + Sort + Search)
        cy = area.y + 52
        self.target_rect = None
        if mode in ("home", "trash"):
            cap = ("Home Storage · A sends to Adv Backpack · X removes" if mode == "home"
                   else "Trash · A rescues to Home Storage · X deletes")
            text(surf, cap, (area.x + 2, cy + 16), 20, DIM, "bold", anchor="midleft", maxw=620)
        else:
            t_bg = INFO if self.add_target == "backpack" else ACCENT
            self.target_rect = pill(surf, f"Add » {self.target_label} (Y)", area.x, cy + 16,
                                    t_bg, DARK, size=20, anchor="midleft")
            if self.add_target == "backpack" and not self.app.snap.steven.on_adventure:
                text(surf, "No active run (still saved to backpack)", (self.target_rect.right + 12, cy + 16),
                     18, FAINT, "bold", anchor="midleft")

        self.search_rect = ed.draw_search_pill(sys.modules[__name__], surf, area.right - 620, cy + 16, self.query,
                                               anchor="midright")
        sort_labels = self.SET_SORTS if mode == "sets_overview" else self.SORTS
        sort_lbl = sort_labels[self.s_idx % len(sort_labels)]
        sort_txt = f"Sort: {sort_lbl} (Y)" if mode in ("home", "trash") else f"Sort: {sort_lbl}"
        self.sort_rect = pill(surf, sort_txt, self.search_rect.left - 12, cy + 16,
                              ACCENT if self.s_idx else CARD_HI, DARK if self.s_idx else TEXT, size=19, anchor="midright")

        list_w = area.w - 620
        la = pygame.Rect(area.x, cy + 48, list_w, area.bottom - (cy + 48))
        if not self.list.rows:
            if self.query:
                noun = "armor sets" if mode == "sets_overview" else "items"
                empty_state(surf, la, "Treasure", f'No {noun} matching "{self.query}"', "Press B to clear the search.")
            elif mode == "home":
                empty_state(surf, la, "Treasure", "Home Storage is empty",
                            "Press Right for Armor Sets & Item Catalog · Adventure Backpack lives in the Adventure tab.")
            else:
                empty_state(surf, la, "Treasure", "No items in this filter",
                            "Use the D-pad to switch filters or clear search.")
        else:
            self.list.draw(surf, la, self.render_row)

        # Right panel: Full Armor Set Overview OR Item Details + Pending Queue summary
        rx = area.right - 580
        q_h = 84
        detail_box = pygame.Rect(rx, area.y, 580, area.h - q_h - 14)
        if mode == "sets_overview":
            cur_set = self.list.current.data[1] if (self.list.current and self.list.current.data[0] == "set") else None
            ed.draw_set_overview_detail(sys.modules[__name__], surf, detail_box, cur_set, self._locs, self._qcounts)
        else:
            cur_item = self.list.current.data[2] if self.list.current else None
            ed.draw_item_detail(sys.modules[__name__], surf, detail_box, cur_item, owned_ids=set(self._locs.keys()))

        qbox = pygame.Rect(rx, detail_box.bottom + 14, 580, q_h)
        rrect(surf, qbox, CARD, 20)
        parts = []
        for ckey, short in (("storage", "Home"), ("backpack", "Adv"), ("trash", "Trash")):
            a_n = len(self.app.pending.inv_add[ckey])
            r_n = len(self.app.pending.inv_remove[ckey])
            if a_n or r_n:
                parts.append(f"{short}: " + " ".join(([f"+{a_n}"] if a_n else []) + ([f"-{r_n}"] if r_n else [])))
        if self.app.pending.inv_move:
            parts.append(f"{len(self.app.pending.inv_move)} moved")
        if parts:
            text(surf, "PENDING ITEM EDITS", (qbox.x + 24, qbox.y + 14), 16, ACCENT, "black")
            text(surf, "  ·  ".join(parts), (qbox.x + 24, qbox.y + 42), 23, TEXT, "bold", maxw=qbox.w - 48)
        elif mode in ("home", "trash"):
            text(surf, "HOME STORAGE & CATALOG", (qbox.x + 24, qbox.y + 14), 16, FAINT, "black")
            text(surf, "Adventure Backpack is managed in Adventure » Backpack.", (qbox.x + 24, qbox.y + 42), 20, DIM, maxw=qbox.w - 48)
        else:
            text(surf, f"ADD TARGET: {self.target_label.upper()}", (qbox.x + 24, qbox.y + 14), 16,
                 INFO if self.add_target == "backpack" else ACCENT, "black")
            text(surf, "Press Y to toggle Home Storage / Adv Backpack.", (qbox.x + 24, qbox.y + 42), 21, DIM, maxw=qbox.w - 48)

    def hint_items(self):
        mode = self.tabs[self.t]
        if mode == "sets_overview":
            return [("A", "Add Missing Pieces"), ("X", "Undo Queued"), ("Y", "Home/Adv"), ("SELECT", "Search")]
        if mode == "home":
            cur = self.list.current.data if self.list.current else None
            a_lbl = "Undo Move" if (cur and cur[0] == "moved") else "Send to Adv"
            return ([("A", a_lbl), ("X", "Remove")] if cur else []) + [("Y", "Sort"), ("SELECT", "Search"), ("DPAD_LR", "Filter")]
        if mode == "trash":
            cur = self.list.current.data if self.list.current else None
            return ([("A", "Rescue » Home"), ("X", "Delete")] if cur else []) + [("Y", "Sort"), ("DPAD_LR", "Filter")]
        short = "Home" if self.add_target == "storage" else "Adv"
        return [("A", f"Add » {short}"), ("X", "Undo"), ("Y", "Home/Adv"), ("SELECT", "Search"), ("DPAD_LR", "Filter")]


# --------------------------------------------------------------------------- system (Backups & Live Diff + App & Save Utilities)

class SystemTab(Tab):
    name = "System"
    MODES = [
        ("backups", "Backups"),
        ("tools", "Settings & Tools"),
    ]
    TOOLS = [
        ("integrity", "Verify Save Integrity", "Run SQLite integrity check + full deterministic parse verification", "Shield"),
        ("reload", "Reload Save from Disk", "Re-read the .sav file from disk (confirms before dropping unsaved edits)", "Recycle"),
        ("steam_art", "Refresh Steam Artwork", "Update MewDeck's capsule, hero, logo & icon in Steam Game Mode", "Treasure"),
        ("sfx", "UI Sounds", "Toggle procedural Mewgenics sketchbook navigation & save sounds", "Heart"),
        ("update", "Check for Updates", "Check GitHub Releases and update MewDeck in place (keeps saves & backups)", "Reroll"),
    ]

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.m = 0  # 0=Backups, 1=Settings & Tools
        self.list = ListView(row_h=98, gap=10)
        self.tools_list = ListView(row_h=106, gap=12)
        self._diff_cache: dict[str, list[tuple[str, str, str]]] = {}
        self.mode_rects: list[tuple[pygame.Rect, int]] = []
        self._last_integrity: tuple[bool, list[str]] | None = None

    def on_enter(self) -> None:
        self._diff_cache.clear()
        self.list.set_rows([Row(b.when.strftime("%b %d"), data=b) for b in sf.list_backups(self.app.save)])
        self._rebuild_tools()

    def _rebuild_tools(self) -> None:
        upd_info = getattr(self.app, "update_info", None)
        rows: list[Row] = []
        for tkey, label, desc, ico_k in self.TOOLS:
            if tkey == "update" and upd_info and upd_info.get("newer"):
                lbl = f"Install Update ({upd_info['tag']})"
            elif tkey == "update":
                lbl = f"Check for Updates (v{upd.VERSION})"
            else:
                lbl = label
            rows.append(Row(lbl, sub=desc, data=(tkey, ico_k, desc)))
        self.tools_list.set_rows(rows)

    def _get_diff(self, b: sf.Backup) -> list[tuple[str, str, str]]:
        k = str(b.path)
        if k not in self._diff_cache:
            try:
                bsnap = sf.snapshot(b.path)
                self._diff_cache[k] = sf.diff_snapshots(self.app.snap, bsnap)
            except Exception as e:  # noqa: BLE001
                self._diff_cache[k] = [("Error", str(e), "")]
        return self._diff_cache[k]

    def _run_tool(self, tool: str) -> None:
        app = self.app
        if tool == "integrity":
            ok, notes = sf.check_integrity(app.save)
            self._last_integrity = (ok, notes)
            app.confirm("Save Integrity: OK" if ok else "Save Integrity: Issues Found",
                        "  ·  ".join(notes), lambda: None, yes="OK", danger=not ok)
        elif tool == "reload":
            def do_reload():
                app.pending = sf.Changes()
                app.reload()
                app.toast("Reloaded save from disk", INFO, 2.2)
            if not app.pending.empty():
                app.confirm("Reload save from disk?",
                            f"Drops {plural(app.pending.count(), 'unsaved edit')} and re-reads the current save file.",
                            do_reload, yes="Reload", danger=True)
            else:
                do_reload()
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
            self._rebuild_tools()
            app.toast(f"UI Sounds: {'ON' if sfx.enabled() else 'OFF'}", INFO, 1.8)
        elif tool == "update":
            info = getattr(app, "update_info", None) or upd.check_latest(timeout=5.0)
            app.update_info = info
            self._rebuild_tools()
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

    def handle(self, btn: str) -> bool:
        if btn in (LEFT, RIGHT):
            self.m = (self.m + (1 if btn == RIGHT else -1)) % len(self.MODES)
            return True
        if self.MODES[self.m][0] == "backups":
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
        # Settings & Tools sub-view
        if btn in (UP, DOWN):
            self.tools_list.move(-1 if btn == UP else 1)
        elif btn == A and self.tools_list.current:
            self._run_tool(self.tools_list.current.data[0])
        else:
            return False
        return True

    def click(self, pos) -> None:
        for r, i in self.mode_rects:
            if r.collidepoint(pos):
                sfx.play("tab")
                self.m = i
                return
        if self.MODES[self.m][0] == "backups":
            i = self.list.hit(pos)
            if i is not None:
                if i == self.list.sel:
                    self.handle(A)
                self.list.sel = i
        else:
            i = self.tools_list.hit(pos)
            if i is not None:
                self.tools_list.sel = i
                sfx.play("ok")
                self._run_tool(self.tools_list.rows[i].data[0])

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

    def _render_tool_row(self, surf, r, row: Row, selected: bool) -> None:
        tkey, ico_k, desc = row.data
        ib = pygame.Rect(r.x + 18, r.centery - 28, 56, 56)
        ed.draw_sprite_badge(sys.modules[__name__], surf, "ui", ico_k, ib)
        text(surf, row.label, (ib.right + 18, r.y + 16), 29, TEXT, "black", maxw=r.w - 260)
        text(surf, desc, (ib.right + 18, r.bottom - 16), 20, DIM, anchor="bottomleft", maxw=r.w - 240)
        if tkey == "sfx":
            on = sfx.enabled()
            pill(surf, "ON" if on else "OFF", r.right - 26, r.centery, GOOD if on else LINE,
                 DARK if on else TEXT, size=19, anchor="midright")
        elif tkey == "update":
            upd_info = getattr(self.app, "update_info", None)
            if upd_info and upd_info.get("newer"):
                pill(surf, upd_info["tag"], r.right - 26, r.centery, ACCENT, DARK, size=19, anchor="midright")
            else:
                pill(surf, f"v{upd.VERSION}", r.right - 26, r.centery, CARD_HI, DIM, size=18, anchor="midright")
        elif tkey == "integrity" and self._last_integrity is not None:
            ok = self._last_integrity[0]
            pill(surf, "HEALTHY" if ok else "ISSUES", r.right - 26, r.centery, GOOD if ok else BAD, DARK, size=18, anchor="midright")
        else:
            tri(surf, ACCENT if selected else DIM, (r.right - 30, r.centery), 10, "right")

    def draw(self, surf, area) -> None:
        self.mode_rects = []
        x = area.x
        upd_info = getattr(self.app, "update_info", None)
        has_upd = bool(upd_info and upd_info.get("newer"))
        cy_bar = area.y + 18
        if not hasattr(self, "seg_anim"):
            self.seg_anim = RectAnim()
        self.mode_rects, _ = segbar(surf, [
            ("Backups", str(len(self.list.rows)), None, ACCENT),
            ("Settings & Tools", "UPD" if has_upd else None, None, ACCENT),
        ], self.m, x, cy_bar, "DPAD_LR", self.seg_anim, mpos=getattr(self.app, "mouse_pos", (-1, -1)))
        ver_lbl = f"MewDeck v{upd.VERSION}" + (f" · {upd_info['tag']} available" if has_upd else "")
        text(surf, ver_lbl, (area.right - 6, cy_bar), 20, ACCENT if has_upd else FAINT, "bold", anchor="midright")

        body = pygame.Rect(area.x, area.y + 58, area.w, area.h - 58)
        bdir = str(sf.backup_dir_for(self.app.save))
        home = str(Path.home())
        if bdir.startswith(home):
            bdir = "~" + bdir[len(home):]

        if self.MODES[self.m][0] == "backups":
            lr = text(surf, "BACKUP FOLDER", (body.x, body.y + 12), 16, FAINT, "black", anchor="midleft")
            text(surf, ellipsize_middle(bdir, F(20), body.w - lr.w - 24), (lr.right + 14, body.y + 12), 20, DIM,
                 anchor="midleft")
            la = pygame.Rect(body.x, body.y + 38, body.w - 590, body.h - 38)
            if not self.list.rows:
                empty_state(surf, pygame.Rect(body.x, body.y + 38, body.w, body.h - 38), "Recycle", "No backups yet",
                            "One is made automatically before every save · press X to back up now.")
                return
            self.list.draw(surf, la, self.render_row)

            dbox = pygame.Rect(body.right - 550, body.y + 38, 550, body.h - 38)
            rrect(surf, dbox, CARD, 24, shadow=True)
            b: sf.Backup = self.list.current.data
            dx, dy, dw = dbox.x + 28, dbox.y + 24, dbox.w - 56
            text(surf, "BACKUP DIFF VS CURRENT SAVE", (dx, dy), 17, ACCENT, "black")
            dy += 30
            text(surf, b.when.strftime("%b %d · %H:%M:%S"), (dx, dy), 30, TEXT, "black")
            dy += 48
            for lbl, bval, delta in self._get_diff(b):
                if dy + 32 > dbox.bottom - 50:
                    break
                text(surf, lbl, (dx, dy), 21, DIM, "bold", maxw=220)
                text(surf, bval, (dbox.right - 160, dy), 22, TEXT, "black", anchor="topright", maxw=135)
                dcol = FAINT if delta == "same" else (GOOD if delta.startswith("+") else BAD if delta.startswith("-") else ACCENT)
                text(surf, delta, (dbox.right - 28, dy), 20, dcol, "bold", anchor="topright", maxw=125)
                dy += 36
            text(surf, "Press A to restore (auto-backs up current first).", (dx, dbox.bottom - 34), 20, FAINT, maxw=dw)
            return

        # ---- Sub-view 1: Settings & Tools
        la = pygame.Rect(body.x, body.y, body.w - 590, body.h)
        self.tools_list.draw(surf, la, self._render_tool_row)

        box = pygame.Rect(body.right - 550, body.y, 550, body.h)
        rrect(surf, box, CARD, 24, shadow=True)
        dx, dy, dw = box.x + 28, box.y + 26, box.w - 56
        cur = self.tools_list.current
        if cur:
            tkey, ico_k, desc = cur.data
            ib = pygame.Rect(box.right - 28 - 74, dy, 74, 74)
            ed.draw_sprite_badge(sys.modules[__name__], surf, "ui", ico_k, ib)
            text(surf, "UTILITY DETAILS", (dx, dy), 17, ACCENT, "black")
            dy += 28
            text(surf, cur.label, (dx, dy), 30, TEXT, "black", maxw=dw - 90)
            dy = max(dy + 46, ib.bottom + 14)
            for ln in ed.wrap_lines(F(22), desc, dw, max_lines=4):
                text(surf, ln, (dx, dy), 22, DIM)
                dy += 29
            if tkey == "integrity" and self._last_integrity is not None:
                dy += 12
                ok, notes = self._last_integrity
                text(surf, "LAST CHECK RESULT", (dx, dy), 16, GOOD if ok else BAD, "black")
                dy += 24
                for note in notes[:4]:
                    text(surf, note, (dx, dy), 19, TEXT if ok else BAD, maxw=dw)
                    dy += 26
            # Controller-glyph call to action (same footer language as the Party dossier)
            cta_verb = {
                "integrity": "Run integrity check", "reload": "Reload from disk", "steam_art": "Refresh artwork",
                "sfx": "Mute UI sounds" if sfx.enabled() else "Enable UI sounds",
                "update": cur.label.split(" (")[0],
            }.get(tkey, "Run")
            cta = pygame.Rect(dx, dy + 18, dw, 56)
            if cta.bottom < box.bottom - 262:
                rrect(surf, cta, WELL, 14, ink=True)
                gx = glyph(surf, "A", cta.x + 14, cta.centery) + 12
                text(surf, cta_verb, (gx, cta.centery), 22, TEXT, "black", anchor="midleft", maxw=cta.right - gx - 16)

        # Bottom System & Save Diagnostics card
        sbox = pygame.Rect(dx, box.bottom - 244, dw, 218)
        rrect(surf, sbox, CARD_HI, 18)
        sx, sy = sbox.x + 20, sbox.y + 16
        section_label(surf, "ACTIVE SAVE & ENVIRONMENT", sx, sy, sbox.right - 20, FAINT, size=17)
        sy += 30
        save_str = str(self.app.save) if self.app.save else "None"
        if save_str.startswith(home):
            save_str = "~" + save_str[len(home):]
        pad_str = self.app.input.names[0] if self.app.input.names else "Mouse · Touch · Keyboard"
        meta_rows = [
            ("Save File", ellipsize_middle(save_str, F(19, "bold"), sbox.w - 170), TEXT),
            ("Backups", f"{len(self.list.rows)} stored (auto before save)", GOOD if self.list.rows else DIM),
            ("UI Sounds", "Enabled" if sfx.enabled() else "Muted", GOOD if sfx.enabled() else DIM),
            ("Version", f"MewDeck v{upd.VERSION}", ACCENT if has_upd else TEXT),
            ("Input", pad_str, INFO if self.app.input.names else DIM),
        ]
        for ml, mv, mc in meta_rows:
            text(surf, ml, (sx, sy), 19, FAINT, "bold")
            text(surf, mv, (sbox.right - 20, sy), 19, mc, "bold", anchor="topright", maxw=sbox.w - 160)
            sy += 32

    def hint_items(self):
        if self.MODES[self.m][0] == "backups":
            return ([("A", "Restore")] if self.list.rows else []) + [("X", "Back up now"), ("DPAD_LR", "View"), ("B", "Saves")]
        return [("A", "Run / Toggle"), ("DPAD_LR", "View"), ("B", "Saves")]


BackupsTab = SystemTab


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
        k = min(1.0, (time.time() - self.t0) / 0.08)
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((16, 13, 11, int(200 * k)))
        surf.blit(shade, (0, 0))
        lines = ed.wrap_lines(F(27), self.body, 920, max_lines=6)
        box_h = max(330, 260 + len(lines) * 38)
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
            img = _render_text_cached(label, 30, "black", fg, None)
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
        _clear_render_caches()
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
            self.toast(f"MewDeck {info['tag']} available · install from System", GOOD, 5.0)

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
        cy = HEADER_H // 2
        branding.draw_cat_emblem(s, lx + 30, cy + 1, 22)
        r = text(s, "Mew", (lx + 66, cy), 44, TEXT, "black", anchor="midleft")
        r = text(s, "Deck", (r.right, cy), 44, ACCENT, "black", anchor="midleft")
        crumbs = [x.title for x in self.stack[1:] if x.title and not isinstance(x, Confirm)]
        if self.save and crumbs:
            crumbs[0] = self.save.stem
        if crumbs:
            # Calm breadcrumb: thin divider, save name in DIM, deepest subscreen in TEXT, faint "/" separators
            dx = r.right + 24
            pygame.draw.line(s, LINE, (dx, cy - 20), (dx, cy + 20), 2)
            cx = dx + 22
            limit = W - PAD - 56 - 14 - 420  # leave room for Save / Discard pills on the right
            for i, c in enumerate(crumbs):
                last = i == len(crumbs) - 1
                if i:
                    cx = text(s, "/", (cx + 12, cy + 1), 26, FAINT, "bold", anchor="midleft").right + 12
                cr = text(s, c, (cx, cy + 1), 28 if last else 26, TEXT if (last and i) else DIM,
                          "black" if (last and i) else "bold", anchor="midleft", maxw=max(80, limit - cx))
                cx = cr.right
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
            x = text(s, label, (x, cy), 25, TEXT, "bold", anchor="midleft").right + 32
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
            self.confirm("Discard pending edits?", f"Drops {plural(self.pending.count(), 'unsaved edit')} without touching disk.",
                         do_disc, yes="Discard", danger=True)
            return
        if getattr(self, "quit_rect", None) and self.quit_rect.collidepoint(pos):
            if not self.pending.empty():
                self.confirm("Quit MewDeck?", f"{plural(self.pending.count(), 'unsaved change')} will be lost.",
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

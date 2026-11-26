"""MewDeck brand kit: hand-inked Mewgenics sketchbook cat emblem, wordmark, badge and app icon.

Everything is drawn supersampled once and cached, so live UI use is just a blit:

    from mewdeck import branding
    logo = branding.get_emblem_surface(64)          # cached Surface, blit every frame
    branding.draw_cat_emblem(screen, x, y, 26)      # cached too (keyed by size/colours)
    icon = branding.draw_app_icon(256)              # 256x256 RGBA app / Steam / desktop icon

This module deliberately does not import `ui` (ui imports us), so it carries its own copy of the
sketchbook palette and font loader.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

import pygame

# --------------------------------------------------------------------------- palette (mirrors ui.py)

BG_TOP, BG_BOT = (40, 35, 30), (24, 21, 18)
CARD = (58, 51, 45)
CARD_HI = (82, 72, 62)
INK = (16, 14, 12)
LINE = (116, 104, 92)
TEXT = (246, 239, 224)
DIM = (188, 176, 158)
ACCENT = (244, 182, 72)
EAR_PINK = (238, 150, 132)
NOSE_PINK = (232, 124, 124)
CREAM = (255, 248, 232)

HERE = Path(__file__).parent
FONT_DIR = HERE / "fonts"
GAMEDATA = HERE / "gamedata"
_FONT_FILES = {"semi": "Nunito-600.ttf", "bold": "Nunito-800.ttf", "black": "Nunito-900.ttf"}

Color = tuple


def mix(c1: Color, c2: Color, t: float) -> tuple[int, int, int]:
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1[:3], c2[:3]))  # type: ignore[return-value]


def _ensure_font() -> None:
    if not pygame.font.get_init():
        pygame.font.init()


@lru_cache(maxsize=64)
def font(size: int, weight: str = "black") -> pygame.font.Font:
    _ensure_font()
    try:
        return pygame.font.Font(FONT_DIR / _FONT_FILES.get(weight, _FONT_FILES["black"]), size)
    except (OSError, pygame.error):
        f = pygame.font.Font(None, int(size * 1.25))
        f.set_bold(weight != "semi")
        return f


def _maybe_convert(s: pygame.Surface) -> pygame.Surface:
    """convert_alpha() when a display exists (fastest blits in the live UI); no-op headless."""
    try:
        if pygame.display.get_init() and pygame.display.get_surface() is not None:
            return s.convert_alpha()
    except pygame.error:
        pass
    return s


def _mask_to(layer: pygame.Surface, mask: pygame.Surface) -> None:
    """Clip `layer` to the opaque area of `mask` (white RGBA silhouette)."""
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)


def _dilate(sil: pygame.Surface, radius: float) -> pygame.Surface:
    """Grow an alpha silhouette by `radius` px (used for the thick hand-inked outline)."""
    out = pygame.Surface(sil.get_size(), pygame.SRCALPHA)
    steps = 24
    for ring in (radius, radius * 0.66, radius * 0.33):
        for i in range(steps):
            a = 2 * math.pi * i / steps
            out.blit(sil, (round(math.cos(a) * ring), round(math.sin(a) * ring)))
    out.blit(sil, (0, 0))
    return out


def _tinted(sil: pygame.Surface, color: Color, alpha: int = 255) -> pygame.Surface:
    s = sil.copy()
    s.fill((*color[:3], alpha), special_flags=pygame.BLEND_RGBA_MULT)
    return s


# --------------------------------------------------------------------------- cat emblem

def _emblem_render(r: int, color: Color, ink: Color, eye: Color, detailed: bool) -> pygame.Surface:
    """Draw the emblem supersampled; returns an RGBA surface whose centre is the emblem origin."""
    r = max(6, int(r))
    k = 4 if r < 60 else 3 if r < 130 else 2
    half = int(math.ceil(r * 1.42)) + 8
    D = half * 2
    S = D * k
    R = r * k
    C = S / 2
    whiskers = detailed and r >= 34
    mouth = detailed and r >= 22

    def P(x: float, y: float) -> tuple[float, float]:
        return (C + x * R, C + y * R)

    def poly(points):
        return [P(x, y) for x, y in points]

    def ell(cx, cy, rx, ry):
        return pygame.Rect(round(C + (cx - rx) * R), round(C + (cy - ry) * R), round(2 * rx * R), round(2 * ry * R))

    head = ell(0, 0.14, 0.86, 0.70)
    ear_l = poly([(-0.86, -0.04), (-0.76, -0.98), (-0.14, -0.50)])
    ear_r = poly([(0.86, -0.04), (0.14, -0.50), (0.64, -1.0), (0.685, -0.86), (0.56, -0.76), (0.725, -0.66)])
    tuft_l = poly([(-0.80, 0.16), (-1.02, 0.36), (-0.74, 0.44)])
    tuft_r = poly([(0.80, 0.16), (1.02, 0.36), (0.74, 0.44)])

    def silhouette(col, dx=0.0, dy=0.0) -> pygame.Surface:
        s = pygame.Surface((S, S), pygame.SRCALPHA)
        off = (dx * R, dy * R)
        pygame.draw.ellipse(s, col, head.move(*off))
        for shape in (ear_l, ear_r, tuft_l, tuft_r):
            pygame.draw.polygon(s, col, [(x + off[0], y + off[1]) for x, y in shape])
        return s

    mask = silhouette((255, 255, 255, 255))
    stroke = R * (0.085 if detailed else 0.11)
    inked = _dilate(mask, stroke)

    canvas = pygame.Surface((S, S), pygame.SRCALPHA)
    # soft ink drop shadow (sketchbook sticker depth)
    canvas.blit(_tinted(inked, ink, 105), (round(R * 0.05), round(R * 0.08)))
    canvas.blit(_tinted(inked, ink), (0, 0))

    # body fill with warm rim highlight crescent + lower shading, clipped to the silhouette
    body = pygame.Surface((S, S), pygame.SRCALPHA)
    body.blit(silhouette(mix(color, ink, 0.16)), (0, 0))         # chin/underside shade
    body.blit(silhouette(mix(color, CREAM, 0.42), 0, -0.07), (0, 0))  # warm rim light
    body.blit(silhouette(color, 0.045, -0.01), (0, 0))
    if r >= 16:  # tabby forehead stripes
        stripe = mix(color, ink, 0.34)
        for pts in (
            [(-0.07, -0.58), (0.07, -0.58), (0.0, -0.30)],
            [(-0.33, -0.50), (-0.20, -0.56), (-0.20, -0.33)],
            [(0.33, -0.50), (0.20, -0.56), (0.20, -0.33)],
        ):
            pygame.draw.polygon(body, stripe, poly(pts))
    _mask_to(body, mask)
    canvas.blit(body, (0, 0))

    # inner ears
    inner = mix(EAR_PINK, color, 0.18)
    pygame.draw.polygon(canvas, inner, poly([(-0.70, -0.16), (-0.70, -0.80), (-0.32, -0.47)]))
    pygame.draw.polygon(canvas, inner, poly([(0.70, -0.16), (0.32, -0.47), (0.52, -0.71), (0.67, -0.60)]))

    # cream muzzle patch
    pygame.draw.ellipse(canvas, mix(color, CREAM, 0.62), ell(0, 0.44, 0.31, 0.21))
    if detailed and r >= 22:  # cheek blush
        blush = pygame.Surface((S, S), pygame.SRCALPHA)
        for sx in (-1, 1):
            pygame.draw.ellipse(blush, (*EAR_PINK, 120), ell(sx * 0.58, 0.36, 0.12, 0.07))
        canvas.blit(blush, (0, 0))

    # eyes: big inked ovals, warm glint
    for sx in (-1, 1):
        ex = sx * 0.34
        pygame.draw.ellipse(canvas, eye, ell(ex, 0.08, 0.135, 0.175))
        pygame.draw.circle(canvas, CREAM, P(ex + 0.05, 0.015), R * 0.052)
        if detailed and r >= 22:
            pygame.draw.circle(canvas, CREAM, P(ex - 0.045, 0.15), R * 0.022)

    # nose
    nose = poly([(-0.085, 0.29), (0.085, 0.29), (0.0, 0.385)])
    pygame.draw.polygon(canvas, NOSE_PINK, nose)
    pygame.draw.polygon(canvas, ink, nose, max(2, round(stroke * 0.34)))

    lw = max(2, round(stroke * (0.42 if r >= 60 else 0.5)))

    def stroke_line(pts):
        pp = poly(pts)
        pygame.draw.lines(canvas, ink, False, pp, lw)
        for p in pp:
            pygame.draw.circle(canvas, ink, p, lw / 2)

    if mouth:  # little 'w' cat mouth
        stroke_line([(0, 0.385), (0, 0.43)])
        stroke_line([(-0.16, 0.43), (-0.12, 0.49), (-0.055, 0.50), (0, 0.43), (0.055, 0.50), (0.12, 0.49), (0.16, 0.43)])
    if whiskers:
        for sx in (-1, 1):
            for y0, y1, x1 in ((0.38, 0.27, 1.26), (0.44, 0.45, 1.30), (0.50, 0.63, 1.20)):
                stroke_line([(sx * 0.42, y0), (sx * x1, y1)])

    return pygame.transform.smoothscale(canvas, (D, D))


@lru_cache(maxsize=96)
def _emblem_cached(r: int, color: Color, ink: Color, eye: Color, detailed: bool) -> pygame.Surface:
    return _maybe_convert(_emblem_render(r, color, ink, eye, detailed))


def emblem_surface(r: int, color: Color = ACCENT, ink: Color = INK, eye: Color = INK, detailed: bool = True) -> pygame.Surface:
    """Cached RGBA emblem; its centre is the emblem origin (r = head radius)."""
    return _emblem_cached(int(round(r)), tuple(color[:3]), tuple(ink[:3]), tuple(eye[:3]), bool(detailed))


def draw_cat_emblem(surf: pygame.Surface, cx: float, cy: float, r: float, color: Color = ACCENT,
                    ink: Color = INK, eye: Color = INK, detailed: bool = True) -> pygame.Rect:
    """Blit the hand-inked MewDeck cat emblem centred at (cx, cy). Cached after the first call."""
    img = emblem_surface(r, color, ink, eye, detailed)
    rect = img.get_rect(center=(round(cx), round(cy)))
    surf.blit(img, rect)
    return rect


@lru_cache(maxsize=32)
def get_emblem_surface(size: int, color: Color = ACCENT, detailed: bool | None = None) -> pygame.Surface:
    """A size x size transparent surface with the emblem fitted inside (for headers, icons...)."""
    size = int(size)
    r = max(6, int((size - 6) / 2.5))
    det = size >= 48 if detailed is None else detailed
    img = _emblem_render(r, tuple(color[:3]), INK, INK, det)
    out = pygame.Surface((size, size), pygame.SRCALPHA)
    # nudge down a touch: ears make the visual mass sit high
    out.blit(img, img.get_rect(center=(size // 2, size // 2 + round(r * 0.07))))
    return _maybe_convert(out)


# --------------------------------------------------------------------------- typography

def _render_tracked(text: str, size: int, color: Color, weight: str, tracking: float) -> pygame.Surface:
    f = font(size, weight)
    if not tracking:
        return f.render(text, True, color)
    glyphs = [f.render(ch, True, color) for ch in text]
    w = sum(g.get_width() for g in glyphs) + int(tracking * (len(glyphs) - 1))
    out = pygame.Surface((max(1, w), f.get_height()), pygame.SRCALPHA)
    x = 0.0
    for g in glyphs:
        out.blit(g, (round(x), 0))
        x += g.get_width() + tracking
    return out


@lru_cache(maxsize=64)
def inked_text(text: str, size: int, color: Color = TEXT, weight: str = "black", outline: int = 0,
               ink: Color = INK, shadow: tuple[int, int] = (0, 0), tracking: float = 0) -> pygame.Surface:
    """Text with a thick ink outline + offset ink shadow, like Mewgenics' sticker captions."""
    fill = _render_tracked(text, size, color, weight, tracking)
    pad = outline + max(abs(shadow[0]), abs(shadow[1])) + 2
    w, h = fill.get_width() + pad * 2, fill.get_height() + pad * 2
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    if outline or shadow != (0, 0):
        sil = pygame.Surface((w, h), pygame.SRCALPHA)
        sil.blit(_render_tracked(text, size, ink, weight, tracking), (pad, pad))
        grown = _dilate(sil, outline) if outline else sil
        if shadow != (0, 0):
            out.blit(grown, shadow)
        out.blit(grown, (0, 0))
    out.blit(fill, (pad, pad))
    return out


def blit_anchored(surf: pygame.Surface, img: pygame.Surface, pos, anchor: str = "center") -> pygame.Rect:
    r = img.get_rect(**{anchor: (round(pos[0]), round(pos[1]))})
    surf.blit(img, r)
    return r


@lru_cache(maxsize=16)
def wordmark(size: int, outline: int | None = None, shadow: int | None = None,
             mew: Color = TEXT, deck: Color = ACCENT) -> pygame.Surface:
    """Two-tone 'MewDeck' wordmark (cream 'Mew' + gold 'Deck') with ink outline and drop shadow."""
    o = max(2, round(size * 0.07)) if outline is None else outline
    sh = max(2, round(size * 0.05)) if shadow is None else shadow
    a = inked_text("Mew", size, mew, "black", o, INK, (sh, sh))
    b = inked_text("Deck", size, deck, "black", o, INK, (sh, sh))
    pad = o + sh + 2
    gap = -pad * 2 + round(size * 0.02)
    out = pygame.Surface((a.get_width() + b.get_width() + gap, max(a.get_height(), b.get_height())), pygame.SRCALPHA)
    out.blit(b, (a.get_width() + gap, 0))  # Deck first so Mew's outline never cuts into it
    out.blit(a, (0, 0))
    return out


# --------------------------------------------------------------------------- decorative pieces

@lru_cache(maxsize=64)
def class_tag(name: str, size: int = 64, angle: float = 0.0) -> pygame.Surface | None:
    """Official Mewgenics class collar-tag sprite (from gamedata/class_icons.png), scaled/rotated."""
    try:
        import json
        meta = json.loads((GAMEDATA / "class_icons.json").read_text())
        sheet = pygame.image.load(str(GAMEDATA / "class_icons.png"))
    except (OSError, ValueError, pygame.error):
        return None
    t, idx = meta["tile"], meta["icons"].get(name)
    if idx is None:
        return None
    tile = pygame.Surface((t, t), pygame.SRCALPHA)
    tile.blit(sheet, (0, 0), pygame.Rect((idx % meta["cols"]) * t, (idx // meta["cols"]) * t, t, t))
    img = pygame.transform.smoothscale(tile, (size, size)) if size != t else tile
    if angle:
        img = pygame.transform.rotozoom(img, angle, 1.0)
    return img


def draw_paw(surf: pygame.Surface, cx: float, cy: float, s: float, color, angle: float = 0.0) -> None:
    """Small paw print (alpha colours allowed)."""
    size = int(s * 2.6) + 4
    k = 3
    layer = pygame.Surface((size * k, size * k), pygame.SRCALPHA)
    c = size * k / 2
    S = s * k
    pygame.draw.ellipse(layer, color, (c - S * 0.55, c - S * 0.05, S * 1.1, S * 0.9))
    for dx, dy, rr in ((-0.72, -0.38, 0.24), (-0.26, -0.78, 0.26), (0.26, -0.78, 0.26), (0.72, -0.38, 0.24)):
        pygame.draw.ellipse(layer, color, (c + (dx - rr) * S, c + (dy - rr * 1.2) * S, rr * 2 * S, rr * 2.4 * S))
    layer = pygame.transform.smoothscale(layer, (size, size))
    if angle:
        layer = pygame.transform.rotozoom(layer, angle, 1.0)
    surf.blit(layer, layer.get_rect(center=(round(cx), round(cy))))


def _radial(n: int, color: Color, peak: int, power: float = 2.0) -> pygame.Surface:
    small = pygame.Surface((n, n), pygame.SRCALPHA)
    for yy in range(n):
        for xx in range(n):
            d = math.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5) / (n / 2)
            small.set_at((xx, yy), (*color[:3], int(max(0.0, 1 - d) ** power * peak)))
    return small


def glow(surf: pygame.Surface, cx: float, cy: float, rad: int, color: Color = ACCENT, peak: int = 70) -> None:
    g = pygame.transform.smoothscale(_radial(48, color, peak), (rad * 2, rad * 2))
    surf.blit(g, (round(cx) - rad, round(cy) - rad))


def sketch_canvas(w: int, h: int, seed: int = 7, grid: int = 44) -> pygame.Surface:
    """Warm dark cardboard gradient + faint sketchbook grid + paper speckle + soft edge vignette."""
    s = pygame.Surface((w, h))
    for y in range(h):
        pygame.draw.line(s, mix(BG_TOP, BG_BOT, y / max(1, h - 1)), (0, y), (w, y))
    grid_col = mix(BG_TOP, LINE, 0.20)
    for x in range(grid // 2, w, grid):
        pygame.draw.line(s, grid_col, (x, 0), (x, h))
    for y in range(grid // 2, h, grid):
        pygame.draw.line(s, grid_col, (0, y), (w, y))
    rnd = random.Random(seed)
    speck = pygame.Surface((w, h), pygame.SRCALPHA)
    for _ in range(w * h // 520):
        c = (255, 240, 210, rnd.randint(6, 18)) if rnd.random() < 0.5 else (0, 0, 0, rnd.randint(14, 34))
        speck.fill(c, (rnd.randrange(w), rnd.randrange(h), rnd.choice((1, 1, 2)), rnd.choice((1, 1, 2))))
    s.blit(speck, (0, 0))
    vign = pygame.Surface((32, 32), pygame.SRCALPHA)
    for yy in range(32):
        for xx in range(32):
            d = max(abs(xx - 15.5), abs(yy - 15.5)) / 16
            vign.set_at((xx, yy), (*INK, int(max(0.0, d - 0.55) / 0.45 * 150)))
    s.blit(pygame.transform.smoothscale(vign, (w, h)), (0, 0))
    return s


def draw_badge(surf: pygame.Surface, cx: float, cy: float, R: int, emblem_r: int | None = None,
               fill: Color = CARD_HI, rim: Color = ACCENT) -> None:
    """Round sketchbook medallion: ink ring, gold rim, stitched cardboard disk, inked cat emblem."""
    k = 3
    D = (R + 16) * 2
    big = pygame.Surface((D * k, D * k), pygame.SRCALPHA)
    c = D * k / 2
    Rk = R * k
    pygame.draw.circle(big, (*INK, 120), (c + 7 * k, c + 10 * k), Rk + 6 * k)
    pygame.draw.circle(big, INK, (c, c), Rk + 6 * k)
    pygame.draw.circle(big, rim, (c, c), Rk)
    pygame.draw.circle(big, INK, (c, c), Rk * 0.905)
    inner = Rk * 0.88
    disk = pygame.Surface((D * k, D * k), pygame.SRCALPHA)
    steps = 48
    for i in range(steps):  # top-lit cardboard gradient
        y0 = c - inner + 2 * inner * i / steps
        pygame.draw.rect(disk, mix(mix(fill, CREAM, 0.10), mix(fill, INK, 0.28), i / steps),
                         (0, y0, D * k, 2 * inner / steps + 2))
    gcol = (*mix(fill, LINE, 0.55), 90)
    step = max(10, int(Rk * 0.16))
    for x in range(int(c) % step, D * k, step):
        pygame.draw.line(disk, gcol, (x, 0), (x, D * k), max(1, k))
    for y in range(int(c) % step, D * k, step):
        pygame.draw.line(disk, gcol, (0, y), (D * k, y), max(1, k))
    m = pygame.Surface((D * k, D * k), pygame.SRCALPHA)
    pygame.draw.circle(m, (255, 255, 255, 255), (c, c), inner)
    _mask_to(disk, m)
    big.blit(disk, (0, 0))
    # stitched dashed ring
    sr = inner * 0.92
    n = max(24, int(R / 5))
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = a0 + math.pi / n * 0.95
        pygame.draw.line(big, (*mix(fill, CREAM, 0.35), 170),
                         (c + math.cos(a0) * sr, c + math.sin(a0) * sr),
                         (c + math.cos(a1) * sr, c + math.sin(a1) * sr), max(2, int(R * k / 90)))
    # gold rim highlight arc (top-left)
    pygame.draw.arc(big, mix(rim, CREAM, 0.55), pygame.Rect(c - Rk * 0.96, c - Rk * 0.96, Rk * 1.92, Rk * 1.92),
                    math.radians(105), math.radians(165), max(2, int(Rk * 0.035)))
    img = pygame.transform.smoothscale(big, (D, D))
    surf.blit(img, img.get_rect(center=(round(cx), round(cy))))
    er = emblem_r if emblem_r is not None else int(R * 0.6)
    draw_cat_emblem(surf, cx, cy + er * 0.06, er)


# --------------------------------------------------------------------------- app icon

@lru_cache(maxsize=8)
def _app_icon(size: int) -> pygame.Surface:
    k = 4 if size <= 256 else 2
    S = size * k
    u = S / 256.0  # design units
    big = pygame.Surface((S, S), pygame.SRCALPHA)
    m = round(8 * u)
    body = pygame.Rect(m, m, S - 2 * m - round(4 * u), S - 2 * m - round(4 * u))
    rad = round(54 * u)
    pygame.draw.rect(big, (*INK, 130), body.move(round(5 * u), round(6 * u)), border_radius=rad)
    pygame.draw.rect(big, INK, body, border_radius=rad)
    rim = body.inflate(-round(9 * u), -round(9 * u))
    pygame.draw.rect(big, ACCENT, rim, border_radius=rad - round(5 * u))
    pygame.draw.rect(big, mix(ACCENT, CREAM, 0.45), rim, width=round(3 * u), border_radius=rad - round(5 * u))
    inner_ink = rim.inflate(-round(14 * u), -round(14 * u))
    pygame.draw.rect(big, INK, inner_ink, border_radius=rad - round(12 * u))
    face = inner_ink.inflate(-round(6 * u), -round(6 * u))

    layer = pygame.Surface((S, S), pygame.SRCALPHA)
    for y in range(face.top, face.bottom):
        t = (y - face.top) / face.h
        pygame.draw.line(layer, mix(mix(CARD_HI, CREAM, 0.08), mix(CARD, INK, 0.25), t), (face.left, y), (face.right, y))
    gcol = (*mix(CARD_HI, LINE, 0.6), 80)
    step = round(22 * u)
    for x in range(face.left + step // 2, face.right, step):
        pygame.draw.line(layer, gcol, (x, face.top), (x, face.bottom), max(1, round(u)))
    for y in range(face.top + step // 2, face.bottom, step):
        pygame.draw.line(layer, gcol, (face.left, y), (face.right, y), max(1, round(u)))
    g = pygame.transform.smoothscale(_radial(48, ACCENT, 110), (round(150 * u), round(150 * u)))
    layer.blit(g, g.get_rect(center=(S // 2, round(134 * u))))
    mask = pygame.Surface((S, S), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), face, border_radius=rad - round(14 * u))
    _mask_to(layer, mask)
    big.blit(layer, (0, 0))
    # stitched dashes just inside the face edge
    st = face.inflate(-round(14 * u), -round(14 * u))
    dash, gap, w = round(10 * u), round(7 * u), max(2, round(2.2 * u))
    col = (*mix(CARD_HI, CREAM, 0.30), 150)
    for x0 in range(st.left + rad // 2, st.right - rad // 2, dash + gap):
        for yy in (st.top, st.bottom):
            pygame.draw.line(big, col, (x0, yy), (min(x0 + dash, st.right - rad // 2), yy), w)
    for y0 in range(st.top + rad // 2, st.bottom - rad // 2, dash + gap):
        for xx in (st.left, st.right):
            pygame.draw.line(big, col, (xx, y0), (xx, min(y0 + dash, st.bottom - rad // 2)), w)

    out = pygame.transform.smoothscale(big, (size, size))
    cx = size * (124 / 256)
    draw_cat_emblem(out, cx, size * (140 / 256), size * (78 / 256), detailed=size >= 96)
    return out


def draw_app_icon(size: int = 256) -> pygame.Surface:
    """Rounded-square MewDeck app icon (RGBA). Use for docs/icon.png, .desktop and Steam _icon.png."""
    return _app_icon(int(size)).copy()


if __name__ == "__main__":  # python -m mewdeck.branding out.png  -> writes the 256px icon
    import os
    import sys

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.image.save(draw_app_icon(256), sys.argv[1] if len(sys.argv) > 1 else "icon.png")

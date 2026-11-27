"""MewDeck brand kit: hand-inked Mewgenics SVG cat emblem, MEWDECK! wordmark, Chewy display font & app icon.

Assets in `mewdeck/assets/` (`mewdeck_cat.svg`, `mewdeck_title.svg`) are hand-crafted SVGs rasterized
offline into bundled PNGs (`mewdeck_cat.png`, `mewdeck_title.png`) so runtime requires only pygame
with zero extra dependencies and ~3 us cached blits in the live UI.

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
CREAM = (255, 248, 232)
PARCHMENT = (232, 221, 198)

HERE = Path(__file__).parent
FONT_DIR = HERE / "fonts"
ASSETS_DIR = HERE / "assets"
GAMEDATA = HERE / "gamedata"
_FONT_FILES = {
    "semi": "Nunito-600.ttf",
    "bold": "Nunito-800.ttf",
    "black": "Nunito-900.ttf",
    "display": "Chewy-Regular.ttf",
}

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


def display_font(size: int) -> pygame.font.Font:
    """Chunky hand-brushed Mewgenics-style display font (Chewy Regular, SIL OFL/Apache)."""
    return font(size, "display")


def _maybe_convert(s: pygame.Surface) -> pygame.Surface:
    """convert_alpha() when a display exists (fastest blits in the live UI); no-op headless."""
    try:
        if pygame.display.get_init() and pygame.display.get_surface() is not None:
            return s.convert_alpha()
    except pygame.error:
        pass
    return s


def _mask_to(layer: pygame.Surface, mask: pygame.Surface) -> None:
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)


def _dilate(sil: pygame.Surface, radius: float) -> pygame.Surface:
    out = pygame.Surface(sil.get_size(), pygame.SRCALPHA)
    steps = 24
    for ring in (radius, radius * 0.66, radius * 0.33):
        for i in range(steps):
            a = 2 * math.pi * i / steps
            out.blit(sil, (round(math.cos(a) * ring), round(math.sin(a) * ring)))
    out.blit(sil, (0, 0))
    return out


# --------------------------------------------------------------------------- bundled SVG-rendered PNG loaders

@lru_cache(maxsize=4)
def _load_asset_png(name: str) -> pygame.Surface | None:
    p = ASSETS_DIR / name
    if not p.is_file():
        return None
    try:
        return pygame.image.load(str(p))
    except pygame.error:
        return None


def _tint_surface(img: pygame.Surface, color: Color) -> pygame.Surface:
    """Subtly mute/tint the scruffy cat when caller passes a subdued color (e.g. empty state)."""
    if tuple(color[:3]) in (ACCENT, CREAM, TEXT, PARCHMENT):
        return img
    # Muted grayscale-tinted variant for empty states (e.g. color=LINE)
    out = img.copy()
    out.fill((*mix(color, CREAM, 0.25), 255), special_flags=pygame.BLEND_RGBA_MULT)
    return out


# --------------------------------------------------------------------------- cat emblem (SVG-backed)

@lru_cache(maxsize=96)
def _emblem_cached(r: int, color: Color) -> pygame.Surface:
    r = max(6, int(r))
    size = max(16, int(round(r * 2.55)))
    raw = _load_asset_png("mewdeck_cat.png")
    if raw is not None:
        scaled = pygame.transform.smoothscale(raw, (size, size))
        if color != ACCENT:
            scaled = _tint_surface(scaled, color)
        return _maybe_convert(scaled)
    # Minimal fallback if asset PNG is somehow absent
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(s, PARCHMENT, (size // 2, size // 2), r)
    pygame.draw.circle(s, INK, (size // 2, size // 2), r, max(2, r // 8))
    return _maybe_convert(s)


def emblem_surface(r: int, color: Color = ACCENT, ink: Color = INK, eye: Color = INK, detailed: bool = True) -> pygame.Surface:
    """Cached RGBA Mewgenics SVG cat emblem; its centre is the emblem origin."""
    return _emblem_cached(int(round(r)), tuple(color[:3]))


def draw_cat_emblem(surf: pygame.Surface, cx: float, cy: float, r: float, color: Color = ACCENT,
                    ink: Color = INK, eye: Color = INK, detailed: bool = True) -> pygame.Rect:
    """Blit the hand-inked Mewgenics SVG cat emblem centred at (cx, cy). Cached after first call."""
    img = emblem_surface(r, color, ink, eye, detailed)
    rect = img.get_rect(center=(round(cx), round(cy)))
    surf.blit(img, rect)
    return rect


@lru_cache(maxsize=32)
def get_emblem_surface(size: int, color: Color = ACCENT, detailed: bool | None = None) -> pygame.Surface:
    """A size x size transparent surface with the SVG cat emblem fitted inside."""
    size = max(16, int(size))
    raw = _load_asset_png("mewdeck_cat.png")
    if raw is not None:
        scaled = pygame.transform.smoothscale(raw, (size, size))
        if tuple(color[:3]) != ACCENT:
            scaled = _tint_surface(scaled, tuple(color[:3]))
        return _maybe_convert(scaled)
    return emblem_surface(max(6, size // 2), color)


# --------------------------------------------------------------------------- typography & SVG MEWDECK! wordmark

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


@lru_cache(maxsize=32)
def wordmark_surface(height: int) -> pygame.Surface:
    """Hand-crafted SVG 'MEWDECK!' logo scaled cleanly to `height` px and cached."""
    height = max(20, int(height))
    raw = _load_asset_png("mewdeck_title.png")
    if raw is not None:
        rw, rh = raw.get_size()
        w = max(1, round(rw * height / max(1, rh)))
        return _maybe_convert(pygame.transform.smoothscale(raw, (w, height)))
    return _maybe_convert(inked_text("MEWDECK!", height, ACCENT, "display", max(2, height // 14), INK, (3, 4)))


@lru_cache(maxsize=32)
def wordmark_fit(max_w: int, max_h: int) -> pygame.Surface:
    """Hand-crafted SVG 'MEWDECK!' logo fitted cleanly inside (max_w, max_h)."""
    raw = _load_asset_png("mewdeck_title.png")
    if raw is not None:
        rw, rh = raw.get_size()
        scale = min(max_w / max(1, rw), max_h / max(1, rh))
        w, h = max(1, round(rw * scale)), max(1, round(rh * scale))
        return _maybe_convert(pygame.transform.smoothscale(raw, (w, h)))
    return wordmark_surface(max_h)


@lru_cache(maxsize=16)
def wordmark(size: int, outline: int | None = None, shadow: int | None = None,
             mew: Color = TEXT, deck: Color = ACCENT) -> pygame.Surface:
    """Backwards-compatible entry point returning the SVG MEWDECK! wordmark sized to ~`size` px tall."""
    return wordmark_surface(int(round(size * 1.15)))


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
    """Gritty dark cardboard sketchbook paper + faint ink graph grid + paper speckle + vignette."""
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
    for _ in range(w * h // 420):
        c = (242, 232, 210, rnd.randint(6, 18)) if rnd.random() < 0.45 else (0, 0, 0, rnd.randint(16, 38))
        speck.fill(c, (rnd.randrange(w), rnd.randrange(h), rnd.choice((1, 1, 2)), rnd.choice((1, 1, 2))))
    s.blit(speck, (0, 0))
    vign = pygame.Surface((32, 32), pygame.SRCALPHA)
    for yy in range(32):
        for xx in range(32):
            d = max(abs(xx - 15.5), abs(yy - 15.5)) / 16
            vign.set_at((xx, yy), (*INK, int(max(0.0, d - 0.50) / 0.50 * 165)))
    s.blit(pygame.transform.smoothscale(vign, (w, h)), (0, 0))
    return s


def draw_badge(surf: pygame.Surface, cx: float, cy: float, R: int, emblem_r: int | None = None,
               fill: Color = CARD_HI, rim: Color = ACCENT) -> None:
    """Hand-inked gritty Mewgenics medallion: heavy ink ring, warm bone-gold rim, cardboard disk & SVG cat."""
    k = 3
    D = (R + 18) * 2
    big = pygame.Surface((D * k, D * k), pygame.SRCALPHA)
    c = D * k / 2
    Rk = R * k
    pygame.draw.circle(big, (*INK, 140), (c + 8 * k, c + 11 * k), Rk + 7 * k)
    pygame.draw.circle(big, INK, (c, c), Rk + 7 * k)
    pygame.draw.circle(big, rim, (c, c), Rk)
    pygame.draw.circle(big, INK, (c, c), Rk * 0.90)
    inner = Rk * 0.87
    disk = pygame.Surface((D * k, D * k), pygame.SRCALPHA)
    steps = 48
    for i in range(steps):
        y0 = c - inner + 2 * inner * i / steps
        pygame.draw.rect(disk, mix(mix(fill, PARCHMENT, 0.08), mix(fill, INK, 0.34), i / steps),
                         (0, y0, D * k, 2 * inner / steps + 2))
    gcol = (*mix(fill, LINE, 0.55), 85)
    step = max(10, int(Rk * 0.16))
    for x in range(int(c) % step, D * k, step):
        pygame.draw.line(disk, gcol, (x, 0), (x, D * k), max(1, k))
    for y in range(int(c) % step, D * k, step):
        pygame.draw.line(disk, gcol, (0, y), (D * k, y), max(1, k))
    m = pygame.Surface((D * k, D * k), pygame.SRCALPHA)
    pygame.draw.circle(m, (255, 255, 255, 255), (c, c), inner)
    _mask_to(disk, m)
    big.blit(disk, (0, 0))
    # subtle sketchy cross-stitch ring
    sr = inner * 0.92
    n = max(24, int(R / 5))
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = a0 + math.pi / n * 0.95
        pygame.draw.line(big, (*mix(fill, CREAM, 0.28), 160),
                         (c + math.cos(a0) * sr, c + math.sin(a0) * sr),
                         (c + math.cos(a1) * sr, c + math.sin(a1) * sr), max(2, int(R * k / 90)))
    img = pygame.transform.smoothscale(big, (D, D))
    surf.blit(img, img.get_rect(center=(round(cx), round(cy))))
    er = emblem_r if emblem_r is not None else int(R * 0.68)
    draw_cat_emblem(surf, cx, cy + er * 0.02, er)


# --------------------------------------------------------------------------- app icon

@lru_cache(maxsize=8)
def _app_icon(size: int) -> pygame.Surface:
    k = 4 if size <= 256 else 2
    S = size * k
    u = S / 256.0
    big = pygame.Surface((S, S), pygame.SRCALPHA)
    m = round(8 * u)
    body = pygame.Rect(m, m, S - 2 * m - round(4 * u), S - 2 * m - round(4 * u))
    rad = round(54 * u)
    pygame.draw.rect(big, (*INK, 145), body.move(round(5 * u), round(7 * u)), border_radius=rad)
    pygame.draw.rect(big, INK, body, border_radius=rad)
    rim = body.inflate(-round(10 * u), -round(10 * u))
    pygame.draw.rect(big, ACCENT, rim, border_radius=rad - round(5 * u))
    inner_ink = rim.inflate(-round(14 * u), -round(14 * u))
    pygame.draw.rect(big, INK, inner_ink, border_radius=rad - round(12 * u))
    face = inner_ink.inflate(-round(7 * u), -round(7 * u))

    layer = pygame.Surface((S, S), pygame.SRCALPHA)
    for y in range(face.top, face.bottom):
        t = (y - face.top) / max(1, face.h)
        pygame.draw.line(layer, mix(mix(CARD_HI, PARCHMENT, 0.06), mix(CARD, INK, 0.30), t), (face.left, y), (face.right, y))
    gcol = (*mix(CARD_HI, LINE, 0.55), 85)
    step = round(22 * u)
    for x in range(face.left + step // 2, face.right, step):
        pygame.draw.line(layer, gcol, (x, face.top), (x, face.bottom), max(1, round(u)))
    for y in range(face.top + step // 2, face.bottom, step):
        pygame.draw.line(layer, gcol, (face.left, y), (face.right, y), max(1, round(u)))
    mask = pygame.Surface((S, S), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), face, border_radius=rad - round(14 * u))
    _mask_to(layer, mask)
    big.blit(layer, (0, 0))

    # stitched dashes inside cardboard face
    st = face.inflate(-round(14 * u), -round(14 * u))
    dash, gap, w = round(10 * u), round(7 * u), max(2, round(2.2 * u))
    col = (*mix(CARD_HI, CREAM, 0.25), 145)
    for x0 in range(st.left + rad // 2, st.right - rad // 2, dash + gap):
        for yy in (st.top, st.bottom):
            pygame.draw.line(big, col, (x0, yy), (min(x0 + dash, st.right - rad // 2), yy), w)
    for y0 in range(st.top + rad // 2, st.bottom - rad // 2, dash + gap):
        for xx in (st.left, st.right):
            pygame.draw.line(big, col, (xx, y0), (xx, min(y0 + dash, st.bottom - rad // 2)), w)

    out = pygame.transform.smoothscale(big, (size, size))
    # Big unmistakable Mewgenics SVG cat face filling the icon cleanly
    cat = get_emblem_surface(round(size * 0.78))
    out.blit(cat, cat.get_rect(center=(round(size * 0.49), round(size * 0.51))))
    return out


def draw_app_icon(size: int = 256) -> pygame.Surface:
    """Rounded-square MewDeck app icon (RGBA). Use for docs/icon.png, .desktop and Steam _icon.png."""
    return _app_icon(int(size)).copy()


if __name__ == "__main__":  # python -m mewdeck.branding out.png  -> writes the 256px icon
    import os
    import sys

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.image.save(draw_app_icon(256), sys.argv[1] if len(sys.argv) > 1 else "icon.png")

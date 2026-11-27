"""Add MewDeck to Steam as a non-Steam game by editing userdata/<id>/config/shortcuts.vdf directly,
plus library artwork in config/grid/. Steam must not be running while shortcuts.vdf is written
(it rewrites shortcuts.vdf on exit), so `install` closes Steam first and restarts it after.
Grid artwork (`config/grid/<appid>*.png`) can be written safely even while Steam is running.

    python -m mewdeck.steam_shortcut install --exe /path/run.sh [--no-restart] [--artwork-if-exists]
    python -m mewdeck.steam_shortcut artwork [--exe /path/run.sh]
    python -m mewdeck.steam_shortcut remove  --exe /path/run.sh
"""

from __future__ import annotations

import argparse
import binascii
import json
import math
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

APP_NAME = "MewDeck"
ART_SUFFIXES = ("p.png", ".png", "_hero.png", "_logo.png", "_logo.json", "_icon.png")

# --------------------------------------------------------------------------- binary VDF

T_MAP, T_STR, T_INT, T_FLOAT, T_PTR, T_WSTR, T_COLOR, T_U64, T_END = 0, 1, 2, 3, 4, 5, 6, 7, 8


def _cstr(b: bytes, i: int) -> tuple[str, int]:
    j = b.index(b"\0", i)
    return b[i:j].decode("utf-8", "surrogateescape"), j + 1


def vdf_load(b: bytes) -> dict[str, Any]:
    """Parse binary VDF into {key: value | dict}, remembering value types for an exact round trip."""

    def read_map(i: int) -> tuple[dict, int]:
        out: dict[str, Any] = {}
        while True:
            t = b[i]
            i += 1
            if t == T_END:
                return out, i
            key, i = _cstr(b, i)
            if t == T_MAP:
                out[key], i = read_map(i)
            elif t == T_STR:
                s, i = _cstr(b, i)
                out[key] = (T_STR, s)
            elif t in (T_INT, T_PTR, T_COLOR):
                out[key] = (t, struct.unpack_from("<i", b, i)[0])
                i += 4
            elif t == T_FLOAT:
                out[key] = (t, struct.unpack_from("<f", b, i)[0])
                i += 4
            elif t == T_U64:
                out[key] = (t, struct.unpack_from("<Q", b, i)[0])
                i += 8
            else:
                raise ValueError(f"unsupported VDF type {t} at {i}")

    root, _ = read_map(0)
    return root


def vdf_dump(d: dict[str, Any]) -> bytes:
    out = bytearray()

    def enc(s: str) -> bytes:
        return s.encode("utf-8", "surrogateescape") + b"\0"

    def write_map(m: dict) -> None:
        for k, v in m.items():
            if isinstance(v, dict):
                out.append(T_MAP)
                out.extend(enc(k))
                write_map(v)
                continue
            t, val = v
            out.append(t)
            out.extend(enc(k))
            if t == T_STR:
                out.extend(enc(val))
            elif t in (T_INT, T_PTR, T_COLOR):
                out.extend(struct.pack("<i", val))
            elif t == T_FLOAT:
                out.extend(struct.pack("<f", val))
            elif t == T_U64:
                out.extend(struct.pack("<Q", val))
        out.append(T_END)

    write_map(d)
    return bytes(out)


# --------------------------------------------------------------------------- steam locations

def steam_roots() -> list[Path]:
    home = Path.home()
    cands = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]
    seen, out = set(), []
    for c in cands:
        if (c / "userdata").is_dir():
            r = c.resolve()
            if r not in seen:
                seen.add(r)
                out.append(c)
    return out


def user_dirs() -> list[Path]:
    """All Steam accounts that have logged in on this machine (id '0' and 'anonymous' skipped)."""
    out = []
    for root in steam_roots():
        for d in sorted((root / "userdata").iterdir()):
            if d.is_dir() and d.name.isdigit() and d.name != "0":
                out.append(d)
    return out


def shortcut_appid(exe: str, name: str) -> int:
    """Unsigned 32-bit id Steam derives for a non-Steam shortcut (used for grid artwork filenames)."""
    return (binascii.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def _signed(u: int) -> int:
    return struct.unpack("<i", struct.pack("<I", u & 0xFFFFFFFF))[0]


def _unsigned(v: int) -> int:
    return v & 0xFFFFFFFF


# --------------------------------------------------------------------------- steam process

def steam_running() -> bool:
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            comm = (d / "comm").read_text().strip()
        except OSError:
            continue
        if comm in ("steam", "steamwebhelper"):
            return True
    return False


def _steam_cmd() -> list[str] | None:
    if shutil.which("steam"):
        return ["steam"]
    if shutil.which("flatpak") and subprocess.run(
        ["flatpak", "info", "com.valvesoftware.Steam"], capture_output=True
    ).returncode == 0:
        return ["flatpak", "run", "com.valvesoftware.Steam"]
    return None


def stop_steam(timeout: float = 45) -> bool:
    if not steam_running():
        return True
    cmd = _steam_cmd()
    if cmd:
        subprocess.Popen(cmd + ["-shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + timeout
    while time.time() < end:
        if not steam_running():
            time.sleep(2)
            return True
        time.sleep(0.5)
    return False


def start_steam() -> None:
    cmd = _steam_cmd()
    if cmd:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


# --------------------------------------------------------------------------- artwork

def _draw_art_surfaces() -> dict[str, Any]:
    """Render all 5 MewDeck Steam library images in memory once (safe headless or in-process)."""
    import pygame

    if not pygame.get_init():
        # Force dummy video/audio in standalone CLI runs so Gamescope/Wayland env vars never block
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    if not pygame.font.get_init():
        pygame.font.init()
    from . import branding as b

    def tags(surf, spots, alpha):
        """Scatter official class collar-tag sprites as faint sketchbook stickers."""
        for name, x, y, size, ang in spots:
            img = b.class_tag(name, size, ang)
            if img is None:
                continue
            img = img.copy()
            img.set_alpha(alpha)
            surf.blit(img, img.get_rect(center=(x, y)))

    def sticker(surf, name, x, y, size, ang):
        """Bold class tag with an ink drop shadow (foreground decoration)."""
        img = b.class_tag(name, size, ang)
        if img is None:
            return
        sh = img.copy()
        sh.fill((*b.INK, 150), special_flags=pygame.BLEND_RGBA_MULT)
        r = img.get_rect(center=(x, y))
        surf.blit(sh, r.move(max(3, size // 18), max(4, size // 14)))
        surf.blit(img, r)

    def subtitle(text, size, color=b.TEXT):
        return b.inked_text(text, size, color, "display", max(2, size // 8), b.INK, (0, max(2, size // 10)), size * 0.08)

    def rule(surf, cx, cy, half, gapw):
        for sx in (-1, 1):
            x0, x1 = cx + sx * gapw, cx + sx * (gapw + half)
            pygame.draw.line(surf, b.INK, (x0, cy + 3), (x1, cy + 3), 5)
            pygame.draw.line(surf, b.ACCENT, (x0, cy), (x1, cy), 4)
            d = cx + sx * (gapw + half + 10)
            pygame.draw.polygon(surf, b.ACCENT, [(d, cy - 7), (d + 7, cy), (d, cy + 7), (d - 7, cy)])

    def fit_wordmark(maxw, maxh=150):
        return b.wordmark_fit(maxw, maxh)

    def scallop_strip(surf, top, h, color=b.ACCENT, bump=18):
        w = surf.get_width()
        pygame.draw.rect(surf, b.INK, (0, top - 6, w, h + 6))
        for x in range(-bump, w + bump, bump * 2):
            pygame.draw.circle(surf, b.INK, (x + bump, top), bump + 6)
        pygame.draw.rect(surf, color, (0, top, w, h))
        for x in range(-bump, w + bump, bump * 2):
            pygame.draw.circle(surf, color, (x + bump, top), bump)

    out: dict[str, Any] = {}

    # 1. Portrait capsule 600x900 (<appid>p.png) — Big Picture & Library main tile
    s = b.sketch_canvas(600, 900, seed=11)
    b.glow(s, 300, 315, 390, b.ACCENT, 55)
    tags(s, [("Mage", 68, 92, 82, 14), ("Fighter", 536, 118, 78, -12), ("Necromancer", 58, 500, 74, -8),
             ("Druid", 548, 490, 76, 10), ("Jester", 546, 746, 60, -16), ("Thief", 54, 748, 60, 12)], 68)
    for x, y in ((118, 238), (148, 190), (454, 238), (482, 190)):
        b.draw_paw(s, x, y, 11, (*b.LINE, 70), 20 if x < 300 else -20)
    b.draw_badge(s, 300, 312, 212, 144)
    b.blit_anchored(s, fit_wordmark(528, 134), (300, 606))
    b.blit_anchored(s, subtitle("MEWGENICS SAVE EDITOR", 30, b.TEXT), (300, 696))
    rule(s, 300, 744, 72, 34)
    b.draw_paw(s, 300, 744, 13, b.ACCENT)
    scallop_strip(s, 808, 92)
    b.blit_anchored(s, b.inked_text("STEAM DECK  ·  GAMEPAD READY", 26, b.INK, "display", 0, b.INK, (0, 0), 1.8), (300, 856))
    for x in (38, 562):
        b.draw_paw(s, x, 858, 11, (*b.INK, 210))
    out["p.png"] = s

    # 2. Wide / recent capsule 920x430 (<appid>.png) — Home "Most Recent" tile
    s = b.sketch_canvas(920, 430, seed=12)
    b.glow(s, 212, 206, 310, b.ACCENT, 62)
    tags(s, [("Mage", 868, 64, 70, -12), ("Necromancer", 860, 358, 64, 10), ("Hunter", 442, 48, 54, 8)], 54)
    b.draw_badge(s, 210, 204, 158, 108)
    b.blit_anchored(s, fit_wordmark(494, 126), (398, 152), "midleft")
    b.blit_anchored(s, subtitle("MEWGENICS SAVE EDITOR", 28, b.TEXT), (410, 240), "midleft")
    x = 434
    for name in ("Fighter", "Mage", "Hunter", "Necromancer", "Druid", "Thief", "Jester"):
        sticker(s, name, x, 322, 58, 0)
        x += 68
    pygame.draw.rect(s, b.INK, (0, 410, 920, 20))
    pygame.draw.rect(s, b.ACCENT, (0, 414, 920, 16))
    out[".png"] = s

    # 3. Hero banner 1920x620 (<appid>_hero.png — no text; Steam overlays the logo bottom-left)
    s = b.sketch_canvas(1920, 620, seed=13, grid=48)
    b.glow(s, 1460, 300, 680, b.ACCENT, 70)
    tags(s, [("Tank", 960, 96, 84, 12), ("Medic", 1020, 300, 76, -10), ("Psychic", 1870, 560, 80, 14),
             ("Monk", 1880, 64, 70, -8), ("Colorless", 840, 470, 70, 6)], 58)
    for i in range(7):
        x = 880 + i * 78
        y = 560 - i * 22 + (14 if i % 2 else -14)
        b.draw_paw(s, x, y, 13, (*b.mix(b.LINE, b.ACCENT, 0.3), 70 + i * 12), -70)
    cx, cy = 1460, 300
    orbit = [("Fighter", -160), ("Mage", -120), ("Hunter", -80), ("Necromancer", -40), ("Druid", 0),
             ("Thief", 40), ("Jester", 80), ("Tinkerer", 120), ("Butcher", 160)]
    for name, deg in orbit:
        a = math.radians(deg)
        sticker(s, name, round(cx + math.cos(a) * 345), round(cy + math.sin(a) * 250), 92, -deg / 8)
    b.draw_badge(s, cx, cy, 234, 160)
    ramp = pygame.Surface((256, 1), pygame.SRCALPHA)
    for i in range(256):
        ramp.set_at((i, 0), (*b.INK, int(218 * (1 - i / 255) ** 1.6)))
    s.blit(pygame.transform.smoothscale(ramp, (1050, 620)), (0, 0))
    floor = pygame.Surface((1, 64), pygame.SRCALPHA)
    for i in range(64):
        floor.set_at((0, i), (*b.INK, int(145 * (i / 63) ** 2)))
    s.blit(pygame.transform.smoothscale(floor, (1920, 260)), (0, 360))
    out["_hero.png"] = s

    # 4. Transparent logo 800x260 (<appid>_logo.png + <appid>_logo.json)
    s = pygame.Surface((800, 260), pygame.SRCALPHA)
    b.draw_cat_emblem(s, 118, 132, 86)
    b.blit_anchored(s, fit_wordmark(556, 146), (224, 108), "midleft")
    b.blit_anchored(s, subtitle("MEWGENICS SAVE EDITOR", 32, b.TEXT), (238, 204), "midleft")
    out["_logo.png"] = s
    out["_logo.json"] = json.dumps(
        {"logoPosition": {"pinnedPosition": "BottomLeft", "widthPct": 44, "heightPct": 50}}
    )

    # 5. Icon 256x256 (<appid>_icon.png)
    out["_icon.png"] = b.draw_app_icon(256)
    return out


def make_artwork(grid: Path, appid: int | list[int]) -> Path:
    """Write capsule, wide capsule, hero, logo (+ layout json) and icon for one or more AppIDs."""
    ids = [appid] if isinstance(appid, int) else list(dict.fromkeys(appid))
    grid.mkdir(parents=True, exist_ok=True)
    primary = ids[0]
    icon_path = grid / f"{primary}_icon.png"
    try:
        import pygame
        assets = _draw_art_surfaces()
    except Exception as e:
        import sys
        print(f"  [mewdeck] artwork generation failed: {e}", file=sys.stderr)
        return icon_path

    for aid in ids:
        for suf, val in assets.items():
            dst = grid / f"{aid}{suf}"
            if isinstance(val, str):
                dst.write_text(val, encoding="utf-8")
            else:
                pygame.image.save(val, str(dst))
    return icon_path


# --------------------------------------------------------------------------- shortcuts.vdf edit

def _entries(root: dict) -> dict:
    for k in list(root.keys()):
        if k.lower() == "shortcuts" and isinstance(root[k], dict):
            return root[k]
    return root.setdefault("shortcuts", {})


def _entry_str(entry: dict, *keys: str) -> str:
    low = {k.lower(): v for k, v in entry.items()}
    for k in keys:
        v = low.get(k.lower())
        if v and isinstance(v, tuple) and len(v) == 2:
            return str(v[1])
    return ""


def _entry_appid(entry: dict) -> int | None:
    for k, v in entry.items():
        if k.lower() == "appid" and isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], int):
            u = _unsigned(v[1])
            if u:
                return u
    return None


def _norm_exe(p: str) -> str:
    s = p.strip().strip('"').strip()
    if not s:
        return ""
    try:
        return str(Path(s).resolve())
    except OSError:
        return s


def _find_all(entries: dict, exe: Path | None = None) -> list[str]:
    target_raw = str(exe) if exe else ""
    target_res = _norm_exe(target_raw) if target_raw else ""
    found = []
    for k, e in entries.items():
        if not isinstance(e, dict):
            continue
        exe_v = _entry_str(e, "Exe", "exe").strip().strip('"')
        name_v = _entry_str(e, "AppName", "appname").strip()
        if name_v.lower() == APP_NAME.lower():
            found.append(k)
        elif exe_v and (
            (target_raw and exe_v == target_raw)
            or (target_res and _norm_exe(exe_v) == target_res)
            or ("mewdeck" in exe_v.lower() and exe_v.endswith("run.sh"))
        ):
            found.append(k)
    return found


def _art_ids_for_entry(entry: dict, fallback_exe: Path | None = None) -> list[int]:
    """Collect every AppID Steam Desktop or Big Picture could look up in config/grid/."""
    ids: list[int] = []
    stored = _entry_appid(entry)
    if stored:
        ids.append(stored)
    exe_raw = _entry_str(entry, "Exe", "exe")
    name_raw = _entry_str(entry, "AppName", "appname") or APP_NAME
    if exe_raw:
        ids.append(shortcut_appid(exe_raw, name_raw))
        unq = exe_raw.strip().strip('"')
        if unq and unq != exe_raw:
            ids.append(shortcut_appid(unq, name_raw))
        q = f'"{unq}"'
        if unq and q != exe_raw:
            ids.append(shortcut_appid(q, name_raw))
    if fallback_exe is not None:
        ids.append(shortcut_appid(f'"{fallback_exe}"', APP_NAME))
    return list(dict.fromkeys(ids))


def refresh_artwork_for_user(user: Path, exe: Path | None = None) -> list[int]:
    """Read shortcuts.vdf (read-only) and write Big Picture + Desktop grid artwork.
    Completely safe to call while Steam is running (never modifies shortcuts.vdf)."""
    cfg = user / "config"
    vdf = cfg / "shortcuts.vdf"
    ids: list[int] = []
    if vdf.exists() and vdf.stat().st_size > 0:
        try:
            root = vdf_load(vdf.read_bytes())
            entries = _entries(root)
            for k in _find_all(entries, exe):
                ids.extend(_art_ids_for_entry(entries[k], exe))
        except Exception:
            pass
    if not ids and exe is not None:
        ids.append(shortcut_appid(f'"{exe}"', APP_NAME))
    ids = list(dict.fromkeys(ids))
    if ids:
        make_artwork(cfg / "grid", ids)
    return ids


def refresh_artwork(exe: Path | None = None) -> tuple[int, list[int]]:
    """Refresh Steam grid artwork for every Steam account on this machine (safe in Game Mode)."""
    if exe is None:
        default_exe = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewdeck/app/run.sh"
        if default_exe.exists():
            exe = default_exe
    users = user_dirs()
    all_ids: list[int] = []
    updated = 0
    for u in users:
        ids = refresh_artwork_for_user(u, exe)
        if ids:
            updated += 1
            all_ids.extend(ids)
    return updated, list(dict.fromkeys(all_ids))


def has_shortcut(user: Path, exe: Path | None = None) -> bool:
    vdf = user / "config/shortcuts.vdf"
    if not (vdf.exists() and vdf.stat().st_size > 0):
        return False
    try:
        root = vdf_load(vdf.read_bytes())
        return bool(_find_all(_entries(root), exe))
    except Exception:
        return False


def add_shortcut(user: Path, exe: Path, icon: Path | None) -> tuple[int, bool]:
    """Returns (appid, added). Idempotent: updates existing MewDeck entry AND preserves its AppID."""
    cfg = user / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    vdf = cfg / "shortcuts.vdf"
    root = vdf_load(vdf.read_bytes()) if vdf.exists() and vdf.stat().st_size > 0 else {"shortcuts": {}}
    entries = _entries(root)
    exe_q = f'"{exe}"'
    matches = _find_all(entries, exe)
    added = not matches

    # Preserve Steam's existing AppID if MewDeck is already registered (crucial for Big Picture art!)
    existing_ids: list[int] = []
    if matches:
        for mk in matches:
            aid = _entry_appid(entries[mk])
            if aid:
                existing_ids.append(aid)
    computed_id = shortcut_appid(exe_q, APP_NAME)
    appid = existing_ids[0] if existing_ids else computed_id

    art_ids = list(dict.fromkeys([appid, computed_id] + existing_ids))
    if matches:
        art_ids = list(dict.fromkeys(art_ids + _art_ids_for_entry(entries[matches[0]], exe)))

    grid = cfg / "grid"
    grid_icon = make_artwork(grid, art_ids)
    chosen_icon = str(grid_icon) if grid_icon.exists() else (str(icon) if icon else "")
    entry = {
        "appid": (T_INT, _signed(appid)),
        "AppName": (T_STR, APP_NAME),
        "Exe": (T_STR, exe_q),
        "StartDir": (T_STR, f'"{exe.parent}"'),
        "icon": (T_STR, chosen_icon),
        "ShortcutPath": (T_STR, ""),
        "LaunchOptions": (T_STR, ""),
        "IsHidden": (T_INT, 0),
        "AllowDesktopConfig": (T_INT, 1),
        "AllowOverlay": (T_INT, 1),
        "OpenVR": (T_INT, 0),
        "Devkit": (T_INT, 0),
        "DevkitGameID": (T_STR, ""),
        "DevkitOverrideAppID": (T_INT, 0),
        "LastPlayTime": (T_INT, 0),
        "FlatpakAppID": (T_STR, ""),
        "tags": {},
    }
    if matches:
        old = entries[matches[0]]
        for keep in ("LastPlayTime", "tags", "LaunchOptions", "IsHidden"):
            if keep in old:
                entry[keep] = old[keep]
        keep_key = matches[0]
        for dup in matches[1:]:
            entries.pop(dup, None)
        entries[keep_key] = entry
        root["shortcuts"] = {str(i): v for i, v in enumerate(entries.values())}
    else:
        key = str(max([int(k) for k in entries if k.isdigit()], default=-1) + 1)
        entries[key] = entry
    if vdf.exists():
        shutil.copy2(vdf, vdf.with_name("shortcuts.vdf.mewdeck-bak"))
    tmp = vdf.with_name("shortcuts.vdf.tmp")
    tmp.write_bytes(vdf_dump(root))
    vdf_load(tmp.read_bytes())  # sanity: must parse back
    os.replace(tmp, vdf)
    return appid, added


def remove_shortcut(user: Path, exe: Path) -> bool:
    vdf = user / "config/shortcuts.vdf"
    if not vdf.exists():
        return False
    root = vdf_load(vdf.read_bytes())
    entries = _entries(root)
    matches = _find_all(entries, exe)
    if not matches:
        return False
    match_set = set(matches)
    removed_ids = {shortcut_appid(f'"{exe}"', APP_NAME)}
    for k in matches:
        removed_ids.update(_art_ids_for_entry(entries[k], exe))
    vals = [v for k, v in entries.items() if k not in match_set]
    root["shortcuts"] = {str(i): v for i, v in enumerate(vals)}
    vdf.write_bytes(vdf_dump(root))
    for appid in removed_ids:
        for suffix in ART_SUFFIXES:
            (user / "config/grid" / f"{appid}{suffix}").unlink(missing_ok=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog="mewdeck.steam_shortcut")
    ap.add_argument("action", choices=("install", "artwork", "remove"))
    ap.add_argument("--exe", type=Path)
    ap.add_argument("--icon", type=Path)
    ap.add_argument("--no-restart", action="store_true", help="don't close/restart Steam (it must already be closed)")
    ap.add_argument(
        "--artwork-if-exists",
        action="store_true",
        help="if MewDeck is already in Steam and Steam is running, only refresh grid artwork without restarting Steam",
    )
    args = ap.parse_args()
    # Keep user's logical path (/home/...) instead of resolving Bazzite's /home -> /var/home symlink
    exe = Path(os.path.abspath(str(args.exe))) if args.exe else None

    users = user_dirs()
    if not users:
        print("  No Steam account found (log into Steam once first).")
        return 1

    if args.action == "artwork":
        for u in users:
            ids = refresh_artwork_for_user(u, exe)
            if ids:
                print(f"  Refreshed grid artwork (AppID {', '.join(map(str, ids))}) for Steam account {u.name}")
            else:
                print(f"  MewDeck shortcut not found for Steam account {u.name}")
        return 0

    if exe is None:
        print("  --exe is required for install/remove.")
        return 1

    was_running = steam_running()
    if args.action == "install" and was_running and args.artwork_if_exists and all(has_shortcut(u, exe) for u in users):
        for u in users:
            ids = refresh_artwork_for_user(u, exe)
            print(f"  Refreshed grid artwork (AppID {', '.join(map(str, ids))}) for Steam account {u.name} (Steam kept running)")
        return 0

    if was_running and args.no_restart:
        if args.action == "install":
            for u in users:
                refresh_artwork_for_user(u, exe)
        print("  Steam is running; refreshed artwork only (close Steam to update shortcuts.vdf).")
        return 1
    if was_running:
        print("  Closing Steam so the shortcut isn't overwritten…")
        if not stop_steam():
            if args.action == "install":
                for u in users:
                    ids = refresh_artwork_for_user(u, exe)
                    if ids:
                        print(f"  Refreshed grid artwork (AppID {', '.join(map(str, ids))}) for account {u.name}")
            print("  Steam didn't close in time; close it manually and re-run ./install.sh")
            return 1
    rc = 0
    for u in users:
        try:
            if args.action == "install":
                appid, added = add_shortcut(u, exe, args.icon)
                print(f"  {'Added' if added else 'Updated'} MewDeck (AppID {appid}) + grid artwork for Steam account {u.name}")
            else:
                print(f"  {'Removed' if remove_shortcut(u, exe) else 'Not found'} for account {u.name}")
        except Exception as e:  # noqa: BLE001
            print(f"  Failed for account {u.name}: {e}")
            rc = 1
    if was_running:
        print("  Restarting Steam…")
        start_steam()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

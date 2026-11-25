"""Add MewDeck to Steam as a non-Steam game by editing userdata/<id>/config/shortcuts.vdf directly,
plus library artwork in config/grid/. Steam must not be running while the file is written
(it rewrites shortcuts.vdf on exit), so `install` closes Steam first and restarts it after.

    python -m mewdeck.steam_shortcut install --exe /path/run.sh [--no-restart]
    python -m mewdeck.steam_shortcut remove  --exe /path/run.sh
"""

from __future__ import annotations

import argparse
import binascii
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

APP_NAME = "MewDeck"

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
    cands = [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
             home / ".var/app/com.valvesoftware.Steam/data/Steam"]
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
    return (binascii.crc32((exe + name).encode()) & 0xFFFFFFFF) | 0x80000000


def _signed(u: int) -> int:
    return struct.unpack("<i", struct.pack("<I", u))[0]


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
    if shutil.which("flatpak") and subprocess.run(["flatpak", "info", "com.valvesoftware.Steam"],
                                                  capture_output=True).returncode == 0:
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
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True)


# --------------------------------------------------------------------------- artwork

def make_artwork(grid: Path, appid: int) -> None:
    """Library capsule, wide capsule, hero, logo and icon, drawn with pygame (no external assets)."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    try:
        import pygame
    except ImportError:
        return
    from . import ui

    pygame.init()
    ui.F = ui.Fonts()
    grid.mkdir(parents=True, exist_ok=True)

    def canvas(w: int, h: int) -> pygame.Surface:
        s = pygame.Surface((w, h))
        for y in range(h):
            pygame.draw.line(s, ui.mix(ui.BG_TOP, ui.BG_BOT, y / h), (0, y), (w, y))
        return s

    def title(s: pygame.Surface, cx: int, cy: int, size: int, sub: bool = True) -> None:
        ui.text(s, "MewDeck", (cx, cy), size, ui.TEXT, "black", anchor="center")
        if sub:
            ui.text(s, "Mewgenics save editor", (cx, cy + size * 0.75), max(14, size // 3), ui.ACCENT, "black",
                    anchor="center")

    # portrait capsule 600x900
    s = canvas(600, 900)
    ui.icon_cat(s, 300, 360, 170)
    title(s, 300, 640, 96)
    pygame.image.save(s, str(grid / f"{appid}p.png"))
    # wide capsule 920x430
    s = canvas(920, 430)
    ui.icon_cat(s, 230, 225, 120)
    ui.text(s, "MewDeck", (400, 190), 96, ui.TEXT, "black", anchor="midleft")
    ui.text(s, "Mewgenics save editor", (404, 268), 34, ui.ACCENT, "black", anchor="midleft")
    pygame.image.save(s, str(grid / f"{appid}.png"))
    # hero 1920x620 (no text; Steam overlays the logo)
    s = canvas(1920, 620)
    for i, (x, y, r) in enumerate(((260, 380, 150), (1640, 250, 190), (980, 520, 90), (1250, 120, 70))):
        ui.icon_cat(s, x, y, r, ui.mix(ui.ACCENT, ui.BG_TOP, 0.55 + i * 0.1), ui.mix(ui.BG_TOP, ui.BG_BOT, .5))
    pygame.image.save(s, str(grid / f"{appid}_hero.png"))
    # logo (transparent) 800x260
    s = pygame.Surface((800, 260), pygame.SRCALPHA)
    ui.icon_cat(s, 120, 140, 95)
    ui.text(s, "MewDeck", (250, 130), 120, ui.TEXT, "black", anchor="midleft")
    pygame.image.save(s, str(grid / f"{appid}_logo.png"))
    # icon 256x256
    s = pygame.Surface((256, 256), pygame.SRCALPHA)
    pygame.draw.rect(s, ui.CARD, (0, 0, 256, 256), border_radius=48)
    ui.icon_cat(s, 128, 136, 92)
    pygame.image.save(s, str(grid / f"{appid}_icon.png"))


# --------------------------------------------------------------------------- shortcuts.vdf edit

def _entries(root: dict) -> dict:
    return root.setdefault("shortcuts", {})


def _find(entries: dict, exe_q: str) -> str | None:
    for k, e in entries.items():
        if isinstance(e, dict):
            v = e.get("Exe") or e.get("exe")
            if v and v[1].strip('"') == exe_q.strip('"'):
                return k
    return None


def add_shortcut(user: Path, exe: Path, icon: Path | None) -> tuple[int, bool]:
    """Returns (appid, added). Idempotent: an existing MewDeck entry is updated, not duplicated."""
    cfg = user / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    vdf = cfg / "shortcuts.vdf"
    root = vdf_load(vdf.read_bytes()) if vdf.exists() and vdf.stat().st_size > 0 else {"shortcuts": {}}
    entries = _entries(root)
    exe_q = f'"{exe}"'
    appid = shortcut_appid(exe_q, APP_NAME)
    entry = {
        "appid": (T_INT, _signed(appid)),
        "AppName": (T_STR, APP_NAME),
        "Exe": (T_STR, exe_q),
        "StartDir": (T_STR, f'"{exe.parent}"'),
        "icon": (T_STR, str(icon) if icon else ""),
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
    key = _find(entries, exe_q)
    added = key is None
    if added:
        key = str(max([int(k) for k in entries if k.isdigit()], default=-1) + 1)
    else:  # keep user-set fields like LastPlayTime / tags
        old = entries[key]
        for keep in ("LastPlayTime", "tags", "LaunchOptions", "IsHidden"):
            if keep in old:
                entry[keep] = old[keep]
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
    key = _find(entries, f'"{exe}"')
    if key is None:
        return False
    vals = [v for k, v in entries.items() if k != key]
    root["shortcuts"] = {str(i): v for i, v in enumerate(vals)}
    vdf.write_bytes(vdf_dump(root))
    appid = shortcut_appid(f'"{exe}"', APP_NAME)
    for suffix in ("p.png", ".png", "_hero.png", "_logo.png", "_icon.png"):
        (user / "config/grid" / f"{appid}{suffix}").unlink(missing_ok=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog="mewdeck.steam_shortcut")
    ap.add_argument("action", choices=("install", "remove"))
    ap.add_argument("--exe", required=True, type=Path)
    ap.add_argument("--icon", type=Path)
    ap.add_argument("--no-restart", action="store_true", help="don't close/restart Steam (it must already be closed)")
    args = ap.parse_args()
    exe = args.exe.resolve()

    users = user_dirs()
    if not users:
        print("  No Steam account found (log into Steam once first).")
        return 1
    was_running = steam_running()
    if was_running and args.no_restart:
        print("  Steam is running; close it first or drop --no-restart.")
        return 1
    if was_running:
        print("  Closing Steam so the shortcut isn't overwritten…")
        if not stop_steam():
            print("  Steam didn't close in time; close it manually and re-run ./install.sh")
            return 1
    rc = 0
    for u in users:
        try:
            if args.action == "install":
                appid, added = add_shortcut(u, exe, args.icon)
                make_artwork(u / "config/grid", appid)
                print(f"  {'Added' if added else 'Updated'} MewDeck in Steam library of account {u.name}")
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
    sys.exit(main())

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
    from . import ui

    if not pygame.get_init():
        # Force dummy video/audio in standalone CLI runs so Gamescope/Wayland env vars never block
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        pygame.font.init()
    elif not pygame.font.get_init():
        pygame.font.init()
    if getattr(ui, "F", None) is None:
        ui.F = ui.Fonts()

    def canvas(w: int, h: int, gx: float = 0.82, gy: float = 0.15, gr: int | None = None) -> pygame.Surface:
        s = pygame.Surface((w, h))
        for y in range(h):
            pygame.draw.line(s, ui.mix(ui.BG_TOP, ui.BG_BOT, y / h), (0, y), (w, y))
        # Subtle sketchbook grid matching MewDeck v1.5
        grid_col = ui.mix(ui.BG_TOP, ui.LINE, 0.24)
        for gx_i in range(0, w, 44):
            pygame.draw.line(s, grid_col, (gx_i, 0), (gx_i, h), 1)
        for gy_i in range(0, h, 44):
            pygame.draw.line(s, grid_col, (0, gy_i), (w, gy_i), 1)
        n = 48
        small = pygame.Surface((n, n), pygame.SRCALPHA)
        for yy in range(n):
            for xx in range(n):
                d = math.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5) / (n / 2)
                small.set_at((xx, yy), (*ui.ACCENT, int(max(0.0, 1 - d) ** 2 * 64)))
        rad = gr or int(max(w, h) * 0.9)
        glow = pygame.transform.smoothscale(small, (rad * 2, rad * 2))
        s.blit(glow, (int(w * gx) - rad, int(h * gy) - rad))
        return s

    out: dict[str, Any] = {}

    # 1. Portrait capsule 600x900 (<appid>p.png) — Big Picture & Library main tile
    s = canvas(600, 900, gx=0.5, gy=0.34, gr=460)
    pygame.draw.circle(s, ui.INK, (300, 336), 204)
    pygame.draw.circle(s, ui.mix(ui.CARD_HI, ui.ACCENT, 0.18), (300, 330), 196)
    pygame.draw.circle(s, ui.ACCENT, (300, 330), 196, 6)
    ui.icon_cat(s, 300, 344, 152)
    ui.text(s, "MewDeck", (304, 614), 104, ui.INK, "black", anchor="center")
    ui.text(s, "MewDeck", (300, 610), 104, ui.TEXT, "black", anchor="center")
    ui.text(s, "Mewgenics Save Editor", (300, 690), 34, ui.ACCENT, "black", anchor="center")
    ui.pill(s, "GAMEPAD READY · STEAM DECK", 300, 778, ui.CARD_HI, ui.DIM, size=21, anchor="center")
    out["p.png"] = s

    # 2. Wide / recent capsule 920x430 (<appid>.png) — Home "Most Recent" tile
    s = canvas(920, 430, gx=0.26, gy=0.5, gr=380)
    pygame.draw.circle(s, ui.INK, (210, 220), 150)
    pygame.draw.circle(s, ui.mix(ui.CARD_HI, ui.ACCENT, 0.18), (210, 215), 144)
    pygame.draw.circle(s, ui.ACCENT, (210, 215), 144, 5)
    ui.icon_cat(s, 210, 226, 112)
    ui.text(s, "MewDeck", (396, 178), 102, ui.INK, "black", anchor="midleft")
    ui.text(s, "MewDeck", (392, 174), 102, ui.TEXT, "black", anchor="midleft")
    ui.text(s, "Mewgenics Save Editor", (396, 256), 36, ui.ACCENT, "black", anchor="midleft")
    ui.pill(s, "STEVEN RESET · BREEDING · ITEMS", 396, 318, ui.CARD_HI, ui.DIM, size=20, anchor="midleft")
    out[".png"] = s

    # 3. Hero banner 1920x620 (<appid>_hero.png — no text; Steam overlays the logo)
    s = canvas(1920, 620, gx=0.78, gy=0.35, gr=760)
    for i, (x, y, r) in enumerate(((1520, 310, 210), (1180, 470, 115), (1760, 140, 90), (260, 180, 100))):
        ui.icon_cat(s, x, y, r, ui.mix(ui.ACCENT, ui.BG_TOP, 0.54 + i * 0.08), ui.mix(ui.BG_TOP, ui.BG_BOT, 0.5))
    out["_hero.png"] = s

    # 4. Transparent logo 800x260 (<appid>_logo.png + <appid>_logo.json)
    s = pygame.Surface((800, 260), pygame.SRCALPHA)
    ui.icon_cat(s, 116, 138, 92)
    ui.text(s, "MewDeck", (245, 107), 116, ui.INK, "black", anchor="midleft")
    ui.text(s, "MewDeck", (240, 102), 116, ui.TEXT, "black", anchor="midleft")
    ui.text(s, "Mewgenics Save Editor", (244, 194), 38, ui.ACCENT, "black", anchor="midleft")
    out["_logo.png"] = s
    out["_logo.json"] = json.dumps(
        {"logoPosition": {"pinnedPosition": "BottomLeft", "widthPct": 44, "heightPct": 50}}
    )

    # 5. Icon 256x256 (<appid>_icon.png)
    s = pygame.Surface((256, 256), pygame.SRCALPHA)
    pygame.draw.rect(s, ui.CARD, (0, 0, 256, 256), border_radius=52)
    pygame.draw.rect(s, ui.ACCENT, (0, 0, 256, 256), width=7, border_radius=52)
    ui.icon_cat(s, 128, 138, 92)
    out["_icon.png"] = s
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

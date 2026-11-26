"""In-app GitHub Release update checker & self-updater for ROG Ally / Steam Deck Game Mode."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any

VERSION = "1.6.0"
REPO = "thedankest/mewdeck"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = f"MewDeck/{VERSION}"


def _ver_tuple(tag: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", tag)]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def is_newer(tag: str, current: str = VERSION) -> bool:
    return _ver_tuple(tag) > _ver_tuple(current)


def check_latest(timeout: float = 5.0) -> dict[str, Any] | None:
    """Query GitHub latest release. Returns dict with tag, url, notes, newer, or None on network error."""
    req = urllib.request.Request(API_URL, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        tag = str(d.get("tag_name") or "")
        asset_url = ""
        for a in d.get("assets") or []:
            if str(a.get("name", "")).endswith(".run"):
                asset_url = str(a.get("browser_download_url") or "")
                break
        return {
            "tag": tag,
            "newer": bool(tag) and is_newer(tag),
            "asset_url": asset_url,
            "html_url": str(d.get("html_url") or ""),
            "title": str(d.get("name") or tag),
        }
    except Exception:
        return None


def check_latest_async(on_result) -> None:
    def _run():
        res = check_latest(timeout=4.5)
        if res is not None:
            on_result(res)
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def download_and_install(asset_url: str) -> tuple[bool, str]:
    """Download MewDeck-Installer.run and run a headless in-place update."""
    if not asset_url:
        return False, "No installer asset URL in latest release"
    try:
        tmp = Path(tempfile.mkdtemp(prefix="mewdeck-upd-"))
        run_file = tmp / "MewDeck-Installer.run"
        req = urllib.request.Request(asset_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=45) as r:
            run_file.write_bytes(r.read())
        os.chmod(run_file, 0o755)
        # Run headless CLI install path (preserves Steam shortcut & backups, skips GUI dialog)
        env = dict(os.environ)
        env["MEWDECK_HEADLESS"] = "1"
        proc = subprocess.run(
            ["bash", str(run_file), "--cli"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if proc.returncode == 0:
            return True, "Updated! Restart MewDeck to use the new version."
        err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip().splitlines()[-1]
        return False, f"Update failed: {err}"
    except Exception as e:  # noqa: BLE001
        return False, f"Update failed: {e}"

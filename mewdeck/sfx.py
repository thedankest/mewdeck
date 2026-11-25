"""Cozy procedural UI sound effects & persistent user preferences for MewDeck.

Generates tiny warm tactile PCM sounds in memory (zero external audio files,
gracefully silent if no audio device is present).
"""

from __future__ import annotations

import array
import json
import math
import os
from pathlib import Path
from typing import Any

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewdeck"
CONFIG_PATH = DATA_HOME / "config.json"

_CFG: dict[str, Any] | None = None
_SOUNDS: dict[str, Any] = {}
_INIT_DONE = False


def load_config() -> dict[str, Any]:
    global _CFG
    if _CFG is None:
        _CFG = {"sfx": True}
        try:
            if CONFIG_PATH.is_file():
                _CFG.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return _CFG


def save_config() -> None:
    if _CFG is None:
        return
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(_CFG, indent=2), encoding="utf-8")
    except Exception:
        pass


def enabled() -> bool:
    return bool(load_config().get("sfx", True))


def set_enabled(on: bool) -> None:
    load_config()["sfx"] = bool(on)
    save_config()


def _synth_tone(freqs: list[tuple[float, float, float]], sr: int = 22050, vol: float = 0.16) -> bytes:
    """Build a short warm triangle/sine envelope PCM (16-bit signed mono)."""
    buf = array.array("h")
    for f0, f1, dur in freqs:
        n = max(1, int(sr * dur))
        phase = 0.0
        for i in range(n):
            t = i / n
            freq = f0 + (f1 - f0) * t
            phase += 2.0 * math.pi * freq / sr
            # Soft fast attack + smooth exponential decay
            env = min(1.0, t * 18.0) * ((1.0 - t) ** 1.8)
            val = (0.72 * math.sin(phase) + 0.28 * math.sin(phase * 2.0)) * env * vol
            buf.append(max(-32000, min(32000, int(val * 32767))))
    return buf.tobytes()


def _init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    _INIT_DONE = True
    try:
        import pygame
        if os.environ.get("SDL_VIDEODRIVER") == "dummy":
            return
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
        specs = {
            "tick": [(520.0, 460.0, 0.022)],
            "ok": [(540.0, 740.0, 0.055)],
            "back": [(420.0, 300.0, 0.055)],
            "tab": [(440.0, 620.0, 0.042)],
            "save": [(523.25, 523.25, 0.065), (659.25, 783.99, 0.12)],
            "err": [(240.0, 190.0, 0.07), (200.0, 160.0, 0.09)],
        }
        for name, spec in specs.items():
            _SOUNDS[name] = pygame.mixer.Sound(buffer=_synth_tone(spec))
    except Exception:
        _SOUNDS.clear()


def play(name: str) -> None:
    if not enabled():
        return
    _init()
    snd = _SOUNDS.get(name)
    if snd is not None:
        try:
            snd.play()
        except Exception:
            pass

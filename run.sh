#!/usr/bin/env bash
# MewDeck launcher: creates a private venv with pygame-ce on first run, then starts the editor.
# Usage: ./run.sh [--windowed] [--list] [--reset-steven SAV]
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/mewdeck"
VENV="$DATA/venv"
LOG="$DATA/mewdeck.log"
mkdir -p "$DATA"

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 not found" >&2; exit 1; }

if ! "$VENV/bin/python" -c 'import pygame' >/dev/null 2>&1; then
  echo "[mewdeck] setting up venv in $VENV (one-time, needs internet)…"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet 'pygame-ce>=2.5.2'
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
export SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD=1
# Interactive terminal: show output. Launched from Steam/desktop: log to file.
if [ -t 1 ]; then
  exec "$VENV/bin/python" -m mewdeck "$@"
else
  exec "$VENV/bin/python" -m mewdeck "$@" >>"$LOG" 2>&1
fi

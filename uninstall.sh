#!/usr/bin/env bash
# Remove MewDeck: Steam shortcut + artwork, app menu entry, app and venv. Backups are kept.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/mewdeck/app"
if [ -x "$DATA/mewdeck/venv/bin/python" ] && [ -d "$APP" ]; then
  PYTHONPATH="$APP" "$DATA/mewdeck/venv/bin/python" -m mewdeck.steam_shortcut remove --exe "$APP/run.sh" || true
fi
rm -f "$DATA/applications/mewdeck.desktop" "$DATA/icons/hicolor/256x256/apps/mewdeck.png"
rm -rf "$APP" "$DATA/mewdeck/venv"
echo "MewDeck removed. Backups kept in $DATA/mewdeck/backups"

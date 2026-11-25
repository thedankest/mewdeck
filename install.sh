#!/usr/bin/env bash
# Install MewDeck for the current user (Bazzite / SteamOS / any Linux with python3 >= 3.11).
#  - copies the app to ~/.local/share/mewdeck/app
#  - builds the venv (pygame-ce)
#  - adds a desktop entry + icon
#  - tries to add it to Steam as a non-Steam game (so it shows up in Game Mode)
set -euo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/mewdeck/app"

python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
  || { echo "Need python3 >= 3.11" >&2; exit 1; }

echo "==> Copying app to $APP"
rm -rf "$APP"
mkdir -p "$APP"
cp -r "$SRC/mewdeck" "$SRC/run.sh" "$SRC/LICENSE" "$SRC/README.md" "$APP/"
find "$APP" -name '__pycache__' -prune -exec rm -rf {} +
chmod +x "$APP/run.sh"

echo "==> Setting up Python environment"
"$APP/run.sh" --list || true

echo "==> Creating icon + desktop entry"
ICON="$DATA/icons/hicolor/256x256/apps/mewdeck.png"
mkdir -p "$(dirname "$ICON")" "$DATA/applications"
"$DATA/mewdeck/venv/bin/python" - "$ICON" <<'PY'
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
import pygame
pygame.init()
s = pygame.Surface((256, 256), pygame.SRCALPHA)
pygame.draw.rect(s, (38, 32, 48), (0, 0, 256, 256), border_radius=48)
o = (255, 176, 72)
pygame.draw.polygon(s, o, [(48, 110), (70, 36), (112, 84)])
pygame.draw.polygon(s, o, [(208, 110), (186, 36), (144, 84)])
pygame.draw.ellipse(s, o, (40, 70, 176, 150))
for x in (92, 164):
    pygame.draw.ellipse(s, (24, 20, 30), (x - 14, 120, 28, 36))
pygame.draw.polygon(s, (24, 20, 30), [(118, 172), (138, 172), (128, 184)])
pygame.image.save(s, sys.argv[1])
PY
DESKTOP="$DATA/applications/mewdeck.desktop"
cat >"$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=MewDeck
Comment=Gamepad-friendly Mewgenics save editor (Steven / save-scum reset)
Exec=$APP/run.sh
Icon=$ICON
Terminal=false
Categories=Game;Utility;
DESK
chmod +x "$DESKTOP"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true

echo "==> Adding to Steam"
STEAM_OK=0
if PYTHONPATH="$APP" "$DATA/mewdeck/venv/bin/python" -m mewdeck.steam_shortcut install --exe "$APP/run.sh" --icon "$ICON"; then
  STEAM_OK=1
fi

cat <<MSG

Done.
  Launch:          $APP/run.sh            (or 'MewDeck' in the app menu)
  Steam:           $( [ "$STEAM_OK" = 1 ] && echo "added - find MewDeck under Non-Steam in Game Mode" || echo "not added - Steam > Games > Add a Non-Steam Game > MewDeck" )
  Backups:         $DATA/mewdeck/backups
  Log:             $DATA/mewdeck/mewdeck.log
MSG

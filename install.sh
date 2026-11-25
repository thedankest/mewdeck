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
if command -v steamos-add-to-steam >/dev/null 2>&1; then
  steamos-add-to-steam "$DESKTOP" && ADDED=1 || ADDED=0
elif pgrep -x steam >/dev/null 2>&1 && command -v xdg-open >/dev/null; then
  ENC="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1], safe=""))' "$DESKTOP")"
  touch /tmp/addnonsteamgamefile
  xdg-open "steam://addnonsteamgame/$ENC" >/dev/null 2>&1 && ADDED=1 || ADDED=0
else
  ADDED=0
fi
if [ "$ADDED" = 1 ]; then
  echo "    Sent to Steam. If it does not appear in your library, add it manually (below)."
fi
cat <<MSG

Done.
  Launch:          $APP/run.sh            (or 'MewDeck' in the app menu)
  Add to Steam:    Steam (Desktop Mode) > Games > Add a Non-Steam Game > tick 'MewDeck'
  Backups:         $DATA/mewdeck/backups
  Log:             $DATA/mewdeck/mewdeck.log
MSG

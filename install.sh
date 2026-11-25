#!/usr/bin/env bash
# Install MewDeck for the current user (Bazzite / SteamOS / any Linux with python3 >= 3.11).
# Supports native KDE (kdialog) / GNOME (zenity) GUI wizard automatically when double-clicked,
# or with --gui, or clean CLI output in a terminal (--cli).
set -euo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/mewdeck/app"
ICON="$DATA/icons/hicolor/256x256/apps/mewdeck.png"
DESKTOP="$DATA/applications/mewdeck.desktop"

MODE="auto"
for arg in "$@"; do
  case "$arg" in
    --gui) MODE="gui" ;;
    --cli) MODE="cli" ;;
  esac
done

GUI_TOOL=""
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  if command -v kdialog >/dev/null 2>&1; then GUI_TOOL="kdialog"
  elif command -v zenity >/dev/null 2>&1; then GUI_TOOL="zenity"
  fi
fi

USE_GUI=0
if [ "$MODE" = "gui" ] && [ -n "$GUI_TOOL" ]; then
  USE_GUI=1
elif [ "$MODE" = "auto" ] && [ ! -t 1 ] && [ -n "$GUI_TOOL" ]; then
  USE_GUI=1
fi

do_install() {
  python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
    || { echo "Need python3 >= 3.11" >&2; exit 1; }

  echo "15"
  echo "# Copying MewDeck & game data…"
  rm -rf "$APP"
  mkdir -p "$APP"
  cp -r "$SRC/mewdeck" "$SRC/run.sh" "$SRC/LICENSE" "$SRC/README.md" "$APP/"
  find "$APP" -name '__pycache__' -prune -exec rm -rf {} +
  chmod +x "$APP/run.sh"

  echo "45"
  echo "# Setting up Python environment (pygame-ce)…"
  "$APP/run.sh" --list >/dev/null 2>&1 || true

  echo "70"
  echo "# Creating desktop icon & app menu entry…"
  mkdir -p "$(dirname "$ICON")" "$DATA/applications"
  "$DATA/mewdeck/venv/bin/python" - "$ICON" <<'PY'
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
import pygame
pygame.init()
s = pygame.Surface((256, 256), pygame.SRCALPHA)
pygame.draw.rect(s, (38, 32, 48), (0, 0, 256, 256), border_radius=52)
pygame.draw.rect(s, (255, 180, 76), (0, 0, 256, 256), width=6, border_radius=52)
o = (255, 180, 76)
pygame.draw.polygon(s, o, [(48, 110), (70, 36), (112, 84)])
pygame.draw.polygon(s, o, [(208, 110), (186, 36), (144, 84)])
pygame.draw.ellipse(s, o, (40, 70, 176, 150))
for x in (92, 164):
    pygame.draw.ellipse(s, (20, 16, 26), (x - 14, 120, 28, 36))
pygame.draw.polygon(s, (20, 16, 26), [(118, 172), (138, 172), (128, 184)])
pygame.image.save(s, sys.argv[1])
PY
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
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true

  echo "85"
  echo "# Adding MewDeck to Steam with full artwork…"
  STEAM_OK=0
  if PYTHONPATH="$APP" "$DATA/mewdeck/venv/bin/python" -m mewdeck.steam_shortcut install --exe "$APP/run.sh" --icon "$ICON" >&2; then
    STEAM_OK=1
  fi
  echo "$STEAM_OK" >"$DATA/mewdeck/.steam_ok"
  echo "100"
  echo "# Done!"
}

if [ "$USE_GUI" = 1 ]; then
  if [ "$GUI_TOOL" = "kdialog" ]; then
    ACTION=$(kdialog --title "MewDeck Installer" \
      --yesnocancel "Install or update MewDeck (Mewgenics Save Editor)?

This will:
  • Install MewDeck & unobfuscated game data
  • Set up Python (pygame-ce)
  • Add MewDeck to Steam with full library artwork
    (Steam will briefly close and reopen)

Backups live safely in ~/.local/share/mewdeck/backups." \
      --yes-label "Install / Update" --no-label "Uninstall" --cancel-label "Cancel" && echo "install" || echo "$?")
    if [ "$ACTION" = "2" ]; then exit 0; fi
    if [ "$ACTION" = "1" ]; then
      "$SRC/uninstall.sh" >/dev/null 2>&1 || true
      kdialog --title "MewDeck Installer" --msgbox "MewDeck has been uninstalled. Your save backups were kept."
      exit 0
    fi
    QDBUS="$(command -v qdbus-qt6 || command -v qdbus6 || command -v qdbus || true)"
    DBUS_REF=$(kdialog --title "MewDeck Installer" --progressbar "Installing MewDeck…" 100)
    ERR_LOG=$(mktemp)
    if (do_install 2>"$ERR_LOG") | while IFS= read -r line; do
      [ -n "$QDBUS" ] || continue
      case "$line" in
        \#*) $QDBUS $DBUS_REF setLabelText "${line#\# }" >/dev/null 2>&1 || true ;;
        [0-9]*) $QDBUS $DBUS_REF Set "" value "$line" >/dev/null 2>&1 || true ;;
      esac
    done; then
      [ -n "$QDBUS" ] && $QDBUS $DBUS_REF close >/dev/null 2>&1 || true
      STEAM_OK="$(cat "$DATA/mewdeck/.steam_ok" 2>/dev/null || echo 0)"
      STEAM_MSG="Added to Steam with capsule, hero, logo & icon artwork!"
      [ "$STEAM_OK" = 1 ] || STEAM_MSG="Steam shortcut could not be added automatically (add 'MewDeck' via Steam → Games → Add a Non-Steam Game)."
      if kdialog --title "MewDeck Installed" --yesno "MewDeck is ready!

$STEAM_MSG

Switch to Game Mode and launch MewDeck under Non-Steam." --yes-label "Launch MewDeck Now" --no-label "Done"; then
        nohup "$APP/run.sh" --windowed >/dev/null 2>&1 &
      fi
    else
      [ -n "$QDBUS" ] && $QDBUS $DBUS_REF close >/dev/null 2>&1 || true
      kdialog --title "MewDeck Install Failed" --error "Installation failed:\n\n$(tail -n 20 "$ERR_LOG")"
      rm -f "$ERR_LOG"
      exit 1
    fi
    rm -f "$ERR_LOG"
  else
    # zenity (GNOME)
    CHOICE=$(zenity --list --radiolist --title="MewDeck Installer" \
      --text="Choose an action for MewDeck (Mewgenics Save Editor):" \
      --column="" --column="Action" --column="Description" \
      TRUE "Install" "Install/update MewDeck + Steam shortcut & artwork" \
      FALSE "Uninstall" "Remove MewDeck (keeps backups)" --width=540 --height=260 || exit 0)
    if [ "$CHOICE" = "Uninstall" ]; then
      "$SRC/uninstall.sh" >/dev/null 2>&1 || true
      zenity --info --title="MewDeck" --text="MewDeck uninstalled. Backups were kept."
      exit 0
    fi
    ERR_LOG=$(mktemp)
    if do_install 2>"$ERR_LOG" | zenity --progress --title="Installing MewDeck" --auto-close --no-cancel --width=460; then
      if zenity --question --title="MewDeck Installed" --ok-label="Launch Now" --cancel-label="Done" \
        --text="MewDeck is installed and added to Steam with full artwork!\nSwitch to Game Mode and launch it under Non-Steam."; then
        nohup "$APP/run.sh" --windowed >/dev/null 2>&1 &
      fi
    else
      zenity --error --title="Install Failed" --text="$(tail -n 20 "$ERR_LOG")"
      rm -f "$ERR_LOG"
      exit 1
    fi
    rm -f "$ERR_LOG"
  fi
  exit 0
fi

# CLI mode
do_install | while IFS= read -r line; do
  case "$line" in
    \#*) echo "==> ${line#\# }" ;;
  esac
done
STEAM_OK="$(cat "$DATA/mewdeck/.steam_ok" 2>/dev/null || echo 0)"
cat <<MSG

Done.
  Launch:          $APP/run.sh            (or 'MewDeck' in the app menu)
  Steam:           $( [ "$STEAM_OK" = 1 ] && echo "added with full grid artwork - find MewDeck under Non-Steam in Game Mode" || echo "not added - Steam > Games > Add a Non-Steam Game > MewDeck" )
  Backups:         $DATA/mewdeck/backups
  Log:             $DATA/mewdeck/mewdeck.log
MSG

#!/usr/bin/env bash
# Builds a single self-extracting one-click installer (MewDeck-Installer.run)
# that runs the native KDE/GNOME GUI wizard on Bazzite/SteamOS with zero manual extraction.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/MewDeck-Installer.run"

TMP_TAR="$(mktemp)"
trap 'rm -f "$TMP_TAR"' EXIT

COPYFILE_DISABLE=1 tar -czf "$TMP_TAR" -C "$ROOT" \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  mewdeck run.sh install.sh uninstall.sh LICENSE README.md

cat >"$OUT" <<'HEADER'
#!/usr/bin/env bash
# MewDeck One-Click Installer (self-extracting archive + KDE/GNOME wizard)
set -euo pipefail
TMPDIR_MEW="$(mktemp -d "${TMPDIR:-/tmp}/mewdeck-install.XXXXXX")"
trap 'rm -rf "$TMPDIR_MEW"' EXIT

ARCHIVE_LINE=$(awk '/^__MEWDECK_ARCHIVE_BELOW__$/ {print NR + 1; exit 0}' "$0")
tail -n +"$ARCHIVE_LINE" "$0" | tar -xzf - -C "$TMPDIR_MEW"
chmod +x "$TMPDIR_MEW/install.sh" "$TMPDIR_MEW/uninstall.sh" "$TMPDIR_MEW/run.sh"

# If launched with no args on a desktop session without a terminal, install.sh auto-opens the KDE/GNOME wizard.
exec "$TMPDIR_MEW/install.sh" "$@"
__MEWDECK_ARCHIVE_BELOW__
HEADER

cat "$TMP_TAR" >>"$OUT"
chmod +x "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"

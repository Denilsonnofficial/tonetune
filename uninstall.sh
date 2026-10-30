#!/usr/bin/env bash
# Remove os atalhos e o ícone do Tonetune. O projeto e os ritmos ficam.
set -euo pipefail

APP_ID="io.github.denilsonnofficial.Tonetune"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="$HOME/.local/bin"

rm -f "$BIN_HOME/tonetune"
rm -f "$DATA_HOME/applications/$APP_ID.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
rm -f "$DATA_HOME/mime/packages/tonetune.xml"

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$DATA_HOME/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" || true
command -v update-mime-database >/dev/null 2>&1 \
    && update-mime-database "$DATA_HOME/mime" || true

echo "Atalhos do Tonetune removidos."

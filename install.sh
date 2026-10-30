#!/usr/bin/env bash
# Instala o Tonetune no menu de aplicativos do usuário atual.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APP_ID="io.github.denilsonnofficial.Tonetune"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="$HOME/.local/bin"

chmod +x "$ROOT/run.sh" "$ROOT/rodar.sh"
mkdir -p "$BIN_HOME" "$DATA_HOME/applications" \
    "$DATA_HOME/icons/hicolor/scalable/apps" "$DATA_HOME/mime/packages"

ln -sfn "$ROOT/run.sh" "$BIN_HOME/tonetune"

sed -e "s|@EXEC@|$ROOT/run.sh|g" \
    -e "s|@ROOT@|$ROOT|g" \
    "$ROOT/packaging/$APP_ID.desktop.in" \
    > "$DATA_HOME/applications/$APP_ID.desktop"
chmod 644 "$DATA_HOME/applications/$APP_ID.desktop"

install -m 644 \
    "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
    "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m 644 \
    "$ROOT/packaging/tonetune-mime.xml" \
    "$DATA_HOME/mime/packages/tonetune.xml"

# A versão antiga do editor deixa de aparecer no menu. O código-fonte em
# ~/ctx800 e o repositório do GitHub não são tocados.
rm -f "$DATA_HOME/applications/br.com.ctx800.Editor.desktop"
rm -f "$BIN_HOME/ctx800-editor"

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$DATA_HOME/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" || true
command -v update-mime-database >/dev/null 2>&1 \
    && update-mime-database "$DATA_HOME/mime" || true
command -v xdg-mime >/dev/null 2>&1 \
    && xdg-mime default "$APP_ID.desktop" application/x-casio-ac7 || true

echo "Tonetune instalado."
echo "  Menu de aplicativos: procure por Tonetune"
echo "  Terminal: tonetune [arquivo.AC7]"
echo "  Atalho: $BIN_HOME/tonetune"

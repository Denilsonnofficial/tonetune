#!/usr/bin/env bash
# Inicia o Tonetune.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
exec python3 main.py "$@"

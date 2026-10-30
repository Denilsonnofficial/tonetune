#!/usr/bin/env bash
# Atalho em português para o mesmo inicializador.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
exec ./run.sh "$@"

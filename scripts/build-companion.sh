#!/usr/bin/env bash
# Builds vcinject Link, the app on the 3DS that vcinject connects to, into
# src/vcinject/data/vcinject-link.cia (shipped with vcinject).
#
# Compiling needs devkitARM: either installed (DEVKITARM set) or through
# Docker (the devkitpro/devkitarm image). Packaging the CIA uses makerom and
# bannertool from ./bin (scripts/build-tools.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/companion"
OUT="$ROOT/src/vcinject/data"
BIN="${VCINJECT_BIN_DIR:-$ROOT/bin}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

log "Compiling"
if [ -n "${DEVKITARM:-}" ]; then
    make -C "$APP"
else
    docker run --rm -v "$APP:/app" -w /app devkitpro/devkitarm make
fi

log "Packaging the CIA"
build="$APP/build"
"$BIN/bannertool" makebanner -i "$APP/meta/banner.png" -a "$APP/meta/silence.wav" \
    -o "$build/banner.bnr" >/dev/null
"$BIN/bannertool" makesmdh -s "vcinject Link" -l "vcinject Link: connects this 3DS to vcinject" \
    -p "vcinject" -i "$APP/meta/icon.png" -o "$build/icon.smdh" >/dev/null
mkdir -p "$OUT"
"$BIN/makerom" -f cia -o "$OUT/vcinject-link.cia" -target t -exefslogo \
    -elf "$APP/vcinject-link.elf" -rsf "$APP/meta/app.rsf" \
    -icon "$build/icon.smdh" -banner "$build/banner.bnr"
log "Built $OUT/vcinject-link.cia"

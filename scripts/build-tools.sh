#!/usr/bin/env bash
# Builds the external 3DS tools vcinject needs (makerom, bannertool) from
# source for the host platform and installs them into ./bin.
#
# Requirements: git, make, a C11 compiler and a C++17 compiler.
#   macOS:  xcode-select --install
#   Debian/Ubuntu: sudo apt install build-essential git
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${VCINJECT_BIN_DIR:-$ROOT/bin}"
WORK_DIR="${VCINJECT_BUILD_DIR:-$ROOT/build/tools}"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"

# Pinned upstream revisions so builds are reproducible.
PROJECT_CTR_REPO="https://github.com/3DSGuy/Project_CTR.git"
PROJECT_CTR_REV="d52c2b0e788c2d085763d7c5dcc4b2edd9ffb7d8"
BANNERTOOL_REPO="https://github.com/carstene1ns/3ds-bannertool.git"
BANNERTOOL_REV="734d33be79fd3f8c29c6296158f06ac7c5ca9dcb"

# Windows (MSYS2 / MinGW): .exe files, linked statically so no MinGW DLLs are
# needed (makerom's makefile links statically on Windows by itself).
EXE=""
STATIC=""
DEFAULT_CC=cc
DEFAULT_CXX=c++
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) EXE=".exe"; STATIC="-static -static-libgcc -static-libstdc++"
                          DEFAULT_CC=gcc; DEFAULT_CXX=g++ ;;
esac
CC="${CC:-$DEFAULT_CC}"
CXX="${CXX:-$DEFAULT_CXX}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

checkout() { # repo rev dir
    local repo="$1" rev="$2" dir="$3"
    if [ ! -d "$dir/.git" ]; then
        git clone --quiet "$repo" "$dir"
    fi
    git -C "$dir" fetch --quiet origin "$rev" 2>/dev/null || git -C "$dir" fetch --quiet origin
    git -C "$dir" checkout --quiet --force "$rev"
}

build_makerom() {
    log "Building makerom ($PROJECT_CTR_REV)"
    local src="$WORK_DIR/Project_CTR"
    checkout "$PROJECT_CTR_REPO" "$PROJECT_CTR_REV" "$src"
    make -C "$src/makerom" deps -j"$JOBS" CC="$CC" CXX="$CXX" >/dev/null
    make -C "$src/makerom" -j"$JOBS" CC="$CC" CXX="$CXX" >/dev/null
    install -m 755 "$src/makerom/bin/makerom$EXE" "$BIN_DIR/makerom$EXE"
}

build_bannertool() {
    # Upstream uses CMake >= 3.28; the sources are simple enough to compile
    # directly so we don't force a CMake install on users.
    log "Building bannertool ($BANNERTOOL_REV)"
    local src="$WORK_DIR/bannertool"
    checkout "$BANNERTOOL_REPO" "$BANNERTOOL_REV" "$src"
    # clang rejects initialising a variable-length array; padLength is < 4.
    sed -i.orig 's/u8 pad\[padLength\] = {0};/u8 pad[4] = {0};/' "$src/source/3ds/lz11.cpp"
    local version
    version="$(sed -n 's/^project(bannertool VERSION \([0-9.]*\).*/\1/p' "$src/CMakeLists.txt")"
    "$CXX" -std=c++17 -O2 -w \
        -I"$src/source" -I"$src/source/pc" \
        -DVERSION="\"${version:-unknown}\"" \
        -DSTBI_ONLY_PNG -DSTBI_NO_LINEAR -DSTBI_NO_STDIO \
        -DSTB_VORBIS_NO_PUSHDATA_API -DSTB_VORBIS_NO_STDIO \
        -DDR_WAV_NO_STDIO \
        "$src"/source/*.cpp "$src"/source/3ds/*.cpp "$src"/source/pc/*.cpp \
        $STATIC -o "$BIN_DIR/bannertool$EXE"
}

main() {
    mkdir -p "$BIN_DIR" "$WORK_DIR"
    local targets=("$@")
    [ ${#targets[@]} -eq 0 ] && targets=(makerom bannertool)
    for t in "${targets[@]}"; do
        case "$t" in
            makerom) build_makerom ;;
            bannertool) build_bannertool ;;
            *) echo "unknown tool: $t" >&2; exit 2 ;;
        esac
    done
    log "Installed into $BIN_DIR:"
    ls -1 "$BIN_DIR"
}

main "$@"

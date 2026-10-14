#!/usr/bin/env bash
# One-step install: builds the 3DS tools, creates .venv and installs vcinject.
#   ./scripts/install.sh            GUI + command line
#   ./scripts/install.sh --cli      command line only (no Qt)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
EXTRAS="gui"
[ "${1:-}" = "--cli" ] && EXTRAS=""

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null || fail "python3 not found. macOS: xcode-select --install; Linux: install python3 and python3-venv."
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' || fail "Python 3.9 or newer is required."
for tool in git make cc c++; do
    command -v "$tool" >/dev/null || fail "'$tool' not found. macOS: xcode-select --install; Debian/Ubuntu: sudo apt install build-essential git"
done

if [ -x bin/makerom ] && [ -x bin/bannertool ]; then
    say "makerom and bannertool already built"
else
    say "Building makerom and bannertool (takes a minute)"
    ./scripts/build-tools.sh
fi

if [ ! -x .venv/bin/python ]; then
    say "Creating .venv"
    "$PYTHON" -m venv .venv || fail "Could not create a virtual environment (Debian/Ubuntu: sudo apt install python3-venv)."
fi
say "Installing vcinject"
.venv/bin/python -m pip install --quiet --upgrade pip   # old pips can't install pyproject-only packages
.venv/bin/python -m pip install --quiet -e ".${EXTRAS:+[$EXTRAS]}"

say "Done. Start the GUI with ./start-gui.command or .venv/bin/vcinject gui"

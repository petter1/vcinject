#!/usr/bin/env bash
# Double-click in Finder (macOS) or run from a terminal to open the vcinject GUI.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/vcinject ]; then
    echo "vcinject is not installed yet. See README.md -> Installation."
    read -r -p "Press Enter to close."
    exit 1
fi
exec .venv/bin/vcinject gui

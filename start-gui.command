#!/usr/bin/env bash
# Double-click in Finder (macOS) or run from a terminal to open the vcinject GUI.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/vcinject ]; then
    echo "vcinject is not installed yet."
    read -r -p "Install it now (builds the tools, needs a minute)? [Y/n] " answer
    case "$answer" in
        [nN]*) exit 1 ;;
    esac
    ./scripts/install.sh || { read -r -p "Installation failed. Press Enter to close."; exit 1; }
fi
exec .venv/bin/vcinject gui

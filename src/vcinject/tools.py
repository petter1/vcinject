"""Locating and running the external 3DS tools (makerom, bannertool)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import InjectError

TOOLS = ("makerom", "bannertool")

_REPO_BIN = Path(__file__).resolve().parents[2] / "bin"


def search_dirs() -> list[Path]:
    from .template import data_dir

    dirs = []
    if os.environ.get("VCINJECT_BIN_DIR"):
        dirs.append(Path(os.environ["VCINJECT_BIN_DIR"]))
    dirs += [_REPO_BIN, data_dir() / "bin"]
    return dirs


def find_tool(name: str) -> Path | None:
    for directory in search_dirs():
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def require_tool(name: str) -> Path:
    path = find_tool(name)
    if path is None:
        raise InjectError(f"'{name}' not found. Build it with scripts/build-tools.sh "
                          f"or put it on your PATH.")
    return path


def run_tool(name: str, *args: str | Path) -> str:
    cmd = [str(require_tool(name)), *map(str, args)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise InjectError(f"{name} failed (exit {result.returncode}):\n{output}")
    return output

"""Locating and running the external 3DS tools (makerom, bannertool)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import InjectError, MissingResource

TOOLS = ("makerom", "bannertool")

_REPO_BIN = Path(__file__).resolve().parents[2] / "bin"
EXE = ".exe" if os.name == "nt" else ""


# Windows: run the console tools without flashing a console window over the GUI.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def frozen() -> bool:
    """True inside the packaged app (PyInstaller), which ships the tools itself."""
    return bool(getattr(sys, "frozen", False))


def search_dirs() -> list[Path]:
    from .template import data_dir

    dirs = []
    if os.environ.get("VCINJECT_BIN_DIR"):
        dirs.append(Path(os.environ["VCINJECT_BIN_DIR"]))
    if frozen():
        dirs.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "bin")
    dirs += [_REPO_BIN, data_dir() / "bin"]
    return dirs


def find_tool(name: str) -> Path | None:
    for directory in search_dirs():
        candidate = directory / f"{name}{EXE}"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def require_tool(name: str) -> Path:
    path = find_tool(name)
    if path is None:
        raise MissingResource(f"tool:{name}", f"'{name}' not found. Build it with scripts/build-tools.sh "
                          f"or put it on your PATH.")
    return path


def run_tool(name: str, *args: str | Path) -> str:
    cmd = [str(require_tool(name)), *map(str, args)]
    result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            **NO_WINDOW)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise InjectError(f"{name} failed (exit {result.returncode}):\n{output}")
    return output

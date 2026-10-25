"""Which games of the library are installed on which 3DS.

Each time vcinject reads the titles of a 3DS (vcinject Link), it keeps their IDs
in that console's folder. The title ID register knows which ROM each inject was
built from, so a ROM is "on" a 3DS when one of its injects is installed there.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import registry, remote
from .template import data_dir

# what fits on a cover badge, by model
_SHORT = {"3DS": "3DS", "3DS XL": "XL", "New 3DS": "New", "2DS": "2DS", "New 3DS XL": "New XL",
          "New 2DS XL": "2DS XL"}


@dataclass(frozen=True)
class Console:
    key: str
    label: str      # "Petter1 (3DS XL)"
    short: str      # "XL", for a badge


def remember(console: remote.Console, titles: list[remote.Title]) -> None:
    """Keeps the IDs of the games and apps installed on ``console``."""
    path = console.folder / "titles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"label": console.label, "short": _SHORT.get(console.model, console.model),
                                "at": time.time(),
                                "ids": sorted(f"{t.id:016X}" for t in titles if t.is_app)}))
    _cache.pop("stamp", None)


_cache: dict = {}


def _known() -> dict[Console, set[int]]:
    """Every 3DS with a remembered title list, and those titles."""
    folder = data_dir() / "3ds"
    files = sorted(folder.glob("*/titles.json")) if folder.is_dir() else []
    stamp = tuple((str(f), f.stat().st_mtime) for f in files)
    if "known" not in _cache or _cache.get("stamp") != stamp:
        known = {}
        for path in files:
            try:
                data = json.loads(path.read_text())
                console = Console(path.parent.name, data["label"], data["short"])
                known[console] = {int(i, 16) for i in data["ids"]}
            except (OSError, ValueError, KeyError):
                continue
        _cache.update(stamp=stamp, known=known)
    return _cache["known"]


def known_consoles() -> list[Console]:
    """Every 3DS whose titles vcinject has read at some point."""
    return list(_known())


def _built() -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Title IDs of the injects built per ROM SHA-1 and per ROM file name."""
    path = registry.registry_path()
    stamp = path.stat().st_mtime if path.is_file() else 0.0
    if "built" not in _cache or _cache.get("registry") != stamp:
        by_sha1: dict[str, set[int]] = {}
        by_name: dict[str, set[int]] = {}
        for entry in registry.load().values():
            if entry.rom_sha1:
                by_sha1.setdefault(entry.rom_sha1, set()).add(entry.title_id)
            if entry.rom_name:
                by_name.setdefault(entry.rom_name, set()).add(entry.title_id)
        _cache.update(registry=stamp, built=(by_sha1, by_name))
    return _cache["built"]


def on_consoles(sha1: str, rom_name: str = "") -> list[Console]:
    """The 3DSs on which an inject of this ROM (by SHA-1, else file name) is installed."""
    by_sha1, by_name = _built()
    ids = by_sha1.get(sha1, set()) | by_name.get(rom_name, set())
    if not ids:
        return []
    return sorted((c for c, titles in _known().items() if ids & titles), key=lambda c: c.label.lower())

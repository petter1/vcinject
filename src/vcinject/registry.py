"""Local register of built injects, so title IDs never collide.

Each build is recorded with its title ID and ROM hash. When a new ROM's
derived title ID is already taken by a different ROM, the next free ID is used
instead. Rebuilding the same ROM keeps its ID and so updates the installed
title in place.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .template import data_dir

_TITLE_ID_HIGH = 0x00040000
UNIQUE_ID_BASE = 0xF0000
UNIQUE_ID_SPAN = 0x8000


@dataclass
class Entry:
    title_id: int
    title: str
    rom_sha1: str
    rom_name: str
    product_code: str
    save_type: str
    output: str
    built_at: float
    source: str = "vcinject"  # or "tickets" for IDs imported from the console

    @property
    def imported(self) -> bool:
        return self.source == "tickets"

    def to_json(self) -> dict:
        data = asdict(self)
        data["title_id"] = f"{self.title_id:016X}"
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Entry":
        known = {f for f in cls.__dataclass_fields__}
        fields = {k: v for k, v in data.items() if k in known}
        return cls(**{**fields, "title_id": int(data["title_id"], 16)})


def registry_path() -> Path:
    return data_dir() / "registry.json"


def load() -> dict[int, Entry]:
    path = registry_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {e.title_id: e for e in map(Entry.from_json, raw.get("titles", []))}
    except (ValueError, KeyError, TypeError):
        # A damaged register must never block building; start over.
        backup = path.with_suffix(f".broken-{int(time.time())}.json")
        path.replace(backup)
        return {}


def _save(entries: dict[int, Entry]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"titles": [e.to_json() for e in sorted(entries.values(), key=lambda e: e.title_id)]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".registry-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def title_id_for_unique(unique_id: int) -> int:
    return (_TITLE_ID_HIGH << 32) | (unique_id << 8)


def preferred_title_id(rom_sha1: str) -> int:
    """The ID a ROM gets when nothing else is in the way (stable per ROM)."""
    return title_id_for_unique(UNIQUE_ID_BASE + int(rom_sha1[:8], 16) % UNIQUE_ID_SPAN)


def suggest_title_id(rom_sha1: str, entries: dict[int, Entry] | None = None) -> int:
    """Most recent ID used for this ROM, else the first free ID from its preferred one."""
    entries = load() if entries is None else entries
    previous = [e for e in entries.values() if e.rom_sha1 == rom_sha1]
    if previous:
        return max(previous, key=lambda e: e.built_at).title_id
    start = (preferred_title_id(rom_sha1) >> 8) & 0xFFFFF
    for step in range(UNIQUE_ID_SPAN):
        unique_id = UNIQUE_ID_BASE + (start - UNIQUE_ID_BASE + step) % UNIQUE_ID_SPAN
        title_id = title_id_for_unique(unique_id)
        if title_id not in entries:
            return title_id
    raise RuntimeError("All inject title IDs are in use.")


def conflict(title_id: int, rom_sha1: str) -> Entry | None:
    """The entry of a *different* ROM that already uses ``title_id``."""
    entry = load().get(title_id)
    return entry if entry is not None and entry.rom_sha1 != rom_sha1 else None


def record(entry: Entry) -> None:
    entries = load()
    entries[entry.title_id] = entry
    _save(entries)


def import_title_ids(title_ids: set[int], label: str = "(installed on 3DS)") -> int:
    """Register IDs that are taken on the console; returns how many were new."""
    entries = load()
    now = time.time()
    added = 0
    for title_id in sorted(title_ids - entries.keys()):
        entries[title_id] = Entry(title_id=title_id, title=label, rom_sha1="",
                                  rom_name="", product_code="", save_type="", output="",
                                  built_at=now, source="tickets")
        added += 1
    if added:
        _save(entries)
    return added


def forget(title_id: int) -> bool:
    entries = load()
    if entries.pop(title_id, None) is None:
        return False
    _save(entries)
    return True

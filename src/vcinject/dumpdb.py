"""Second source for verifying Game Boy / Color / Advance dumps: the database of
FlashGBX (https://github.com/lesserkuma/FlashGBX), the GBxCart RW software.

It finds a cartridge by the SHA-1 of its header (the first 0x180 bytes; 0x100
for GBA game codes starting with "M") and then checks the whole dump by CRC32
and size – the same way FlashGBX checks a fresh dump. Entries come from No-Intro
and its authors' own verified dumps, with name, game code and save type. It is
downloaded once (about 400 KB) and refreshed monthly like the game lists.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from .errors import InjectError

URL = "https://raw.githubusercontent.com/lesserkuma/FlashGBX/master/FlashGBX/res/config.zip"
_MAX_AGE = 30 * 24 * 3600
_FILES = {"gba": "db_AGB.json", "gb": "db_DMG.json", "gbc": "db_DMG.json"}
_LOADED: dict[tuple[Path, str], dict] = {}


def _path() -> Path:
    from .artwork import _cache_dir
    return _cache_dir() / "flashgbx" / "config.zip"


def database(system_key: str) -> dict[str, dict]:
    """SHA-1 -> entry for a system ({} for systems FlashGBX does not cover)."""
    name = _FILES.get(system_key)
    if name is None:
        return {}
    key = (_path(), name)
    if key not in _LOADED:
        from .artwork import cached_download
        data = cached_download(_path(), URL, max_age=_MAX_AGE)
        if not data:
            raise InjectError("The FlashGBX database is not available.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                _LOADED[key] = {k.lower(): v for k, v in json.loads(zf.read(name)).items()}
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise InjectError(f"The FlashGBX database could not be read ({exc}).") from exc
    return _LOADED[key]


def header_key(system_key: str, rom: bytes) -> str:
    """The SHA-1 FlashGBX looks a cartridge up by."""
    if system_key == "gba" and rom[0xAC:0xAD] == b"M":
        return hashlib.sha1(rom[:0x100]).hexdigest()
    return hashlib.sha1(rom[:0x180]).hexdigest()


def check(system_key: str, header_sha1: str, crc: str, size: int) -> tuple[str, dict | None]:
    """("good" | "mismatch" | "", entry): the cartridge is known and the dump
    matches it, the cartridge is known but the dump differs, or it is unknown."""
    entry = database(system_key).get(header_sha1) if header_sha1 else None
    if entry is None:
        return "", None
    good = entry.get("rc") == int(crc or "0", 16) and entry.get("rs") == size
    return ("good" if good else "mismatch"), entry


def title(entry: dict) -> str:
    return f"{entry.get('gn', '')} {entry.get('ne', '')}".strip()

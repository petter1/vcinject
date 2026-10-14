"""Supported consoles: file extensions, database names and ROM header quirks."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import InjectError


@dataclass(frozen=True)
class System:
    key: str
    name: str
    database: str          # libretro-database / thumbnail system name
    extensions: tuple[str, ...]
    forwarder: str | None  # forwarder template that runs it (None = native GBA)
    product_prefix: str

    def strip_header(self, rom: bytes) -> bytes:
        """ROM data as No-Intro hashes it (without copier/iNES headers)."""
        if self.key == "nes" and rom[:4] == b"NES\x1a":
            return rom[16:]
        if self.key == "snes" and len(rom) % 1024 == 512:
            return rom[512:]
        return rom


SYSTEMS = {
    s.key: s for s in (
        System("gba", "Game Boy Advance", "Nintendo - Game Boy Advance", (".gba", ".agb"), None, "CTR-N"),
        System("snes", "Super Nintendo", "Nintendo - Super Nintendo Entertainment System",
               (".sfc", ".smc"), "snes", "SNES-P"),
        System("nes", "NES", "Nintendo - Nintendo Entertainment System", (".nes",), "nes", "NES-P"),
        System("gb", "Game Boy", "Nintendo - Game Boy", (".gb",), "gbc", "GB-P"),
        System("gbc", "Game Boy Color", "Nintendo - Game Boy Color", (".gbc",), "gbc", "GBC-P"),
        System("gen", "Mega Drive / Genesis", "Sega - Mega Drive - Genesis",
               (".md", ".gen", ".smd"), "gen", "GEN-P"),
    )
}

# Forwarder templates and which systems they can run.
FORWARDER_KINDS = {
    "snes": "Super Nintendo (snes9x)",
    "nes": "NES",
    "gbc": "Game Boy / Color",
    "gen": "Mega Drive / Genesis",
}


def _system_for(name: str) -> System | None:
    ext = Path(name).suffix.lower()
    return next((s for s in SYSTEMS.values() if ext in s.extensions), None)


def is_zip(path: Path) -> bool:
    return Path(path).suffix.lower() == ".zip"


def _zip_member(path: Path) -> zipfile.ZipInfo:
    """The ROM inside a .zip: the only (else the largest) file of a supported type."""
    try:
        with zipfile.ZipFile(path) as zf:
            members = [i for i in zf.infolist() if not i.is_dir() and _system_for(i.filename)
                       and not i.filename.startswith("__MACOSX/")
                       and not Path(i.filename).name.startswith(".")]
    except (OSError, zipfile.BadZipFile) as exc:
        raise InjectError(f"{Path(path).name} is not a readable zip file ({exc}).") from exc
    if not members:
        raise InjectError(f"{Path(path).name} contains no supported ROM.")
    return max(members, key=lambda i: i.file_size)


def rom_name(path: Path) -> str:
    """File name of the ROM itself (the zip member for zipped ROMs)."""
    return Path(_zip_member(path).filename).name if is_zip(path) else Path(path).name


def read_rom(path: Path) -> bytes:
    """The ROM bytes, unpacked when the ROM is zipped."""
    path = Path(path)
    if not is_zip(path):
        return path.read_bytes()
    member = _zip_member(path)
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read(member)
    except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise InjectError(f"Could not unpack {member.filename} from {path.name} ({exc}).") from exc


def read_rom_start(path: Path, length: int) -> bytes:
    """The first ``length`` bytes of the ROM (cheap, also inside a zip)."""
    path = Path(path)
    if not is_zip(path):
        with open(path, "rb") as f:
            return f.read(length)
    member = _zip_member(path)
    try:
        with zipfile.ZipFile(path) as zf, zf.open(member) as f:
            return f.read(length)
    except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise InjectError(f"Could not unpack {member.filename} from {path.name} ({exc}).") from exc


def for_rom(path: Path) -> System:
    system = _system_for(rom_name(path))
    if system is None:
        known = ", ".join(e for s in SYSTEMS.values() for e in s.extensions)
        raise InjectError(f"Unknown ROM type '{Path(path).suffix.lower()}'. "
                          f"Supported: {known} (also zipped)")
    return system


def rom_filter() -> str:
    """File dialog filter covering every supported system."""
    patterns = " ".join(f"*{e}" for s in SYSTEMS.values() for e in s.extensions)
    return f"ROMs ({patterns} *.zip);;All files (*)"

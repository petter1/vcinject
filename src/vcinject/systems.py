"""Supported consoles: file extensions, database names and ROM header quirks."""
from __future__ import annotations

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


def for_rom(path: Path) -> System:
    ext = Path(path).suffix.lower()
    for system in SYSTEMS.values():
        if ext in system.extensions:
            return system
    known = ", ".join(e for s in SYSTEMS.values() for e in s.extensions)
    raise InjectError(f"Unknown ROM type '{ext}'. Supported: {known}")


def rom_filter() -> str:
    """File dialog filter covering every supported system."""
    patterns = " ".join(f"*{e}" for s in SYSTEMS.values() for e in s.extensions)
    return f"ROMs ({patterns});;All files (*)"

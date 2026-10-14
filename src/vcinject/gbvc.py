"""Game Boy / Game Boy Color with Nintendo's own Virtual Console emulator.

An official GB/GBC VC title (e.g. Pokémon Blue or Gold from the eShop, dumped with
GodMode9) holds Nintendo's emulator in its ExeFS and the game in its RomFS:
/rom/<name>, next to config.ini (emulator settings), the menu layout and a
<name>.patch per game – Pokémon's patches adjust trading and save limits, so a
new game gets an empty one. Nothing else needs to change; saves go to the
title's own save data like on a cartridge. Nothing of Nintendo is bundled:
the emulator comes from a title the user owns.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from . import ncch, romfs
from .errors import InjectError, MissingResource
from .template import data_dir

KINDS = {"gb": "Game Boy (Nintendo VC)", "gbc": "Game Boy Color (Nintendo VC)"}
_EMPTY_PATCH = b";Format Sample\r\n;no patches for this game\r\n"
# cartridge RAM size code (header 0x149) -> bytes
_RAM = {0: 0, 1: 0x800, 2: 0x2000, 3: 0x8000, 4: 0x20000, 5: 0x10000}


def templates_dir() -> Path:
    return data_dir() / "templates" / "gbvc"


def is_color(rom: bytes) -> bool:
    """Game Boy Color game (also the ones that run on a Game Boy)."""
    return len(rom) > 0x143 and rom[0x143] in (0x80, 0xC0)


@dataclass
class GbVcTemplate:
    kind: str           # "gb" (DMG emulator build) or "gbc" (CGB build)
    exheader: bytes
    code: bytes
    logo: bytes
    files: dict[str, bytes]   # RomFS without the game and its patches
    rom_name: str             # "/rom/DMGAAUD0.K00": the emulator loads the file in /rom/

    def save(self) -> Path:
        folder = templates_dir() / self.kind
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "exheader.bin").write_bytes(self.exheader)
        (folder / "code.bin").write_bytes(self.code)
        (folder / "logo.bin").write_bytes(self.logo)
        (folder / "romfs.bin").write_bytes(romfs.build(self.files))
        (folder / "meta.json").write_text(json.dumps({"kind": self.kind, "rom_name": self.rom_name},
                                                     indent=2))
        return folder

    @classmethod
    def load(cls, kind: str) -> "GbVcTemplate":
        folder = templates_dir() / kind
        if not (folder / "meta.json").is_file():
            raise MissingResource(f"gbvc:{kind}", f"Nintendo's {KINDS[kind]} emulator is not set "
                                  "up. Add an official Game Boy VC title you own with "
                                  "'vcinject setup --gbvc-donor <title.cia>' or in the GUI.")
        try:
            meta = json.loads((folder / "meta.json").read_text())
            template = cls(meta["kind"], (folder / "exheader.bin").read_bytes(),
                           (folder / "code.bin").read_bytes(), (folder / "logo.bin").read_bytes(),
                           romfs.read((folder / "romfs.bin").read_bytes()), meta["rom_name"])
            if len(template.exheader) != 0x800 or not template.code:
                raise ValueError("exheader or code damaged")
        except (OSError, ValueError, KeyError, InjectError, struct.error) as exc:
            from .template import quarantine
            quarantine(folder)
            raise MissingResource(f"gbvc:{kind}", f"The {KINDS[kind]} template was damaged ({exc}) "
                                  "and has been set aside; set it up again.") from exc
        return template

    @classmethod
    def from_title(cls, path: Path) -> "GbVcTemplate":
        """Take the emulator out of an official GB/GBC Virtual Console title."""
        data = Path(path).read_bytes()
        cxi = ncch.first_cia_content(data) if data[0x100:0x104] != b"NCCH" else data
        parts = ncch.parse_ncch(cxi)
        files = romfs.read(parts.romfs) if parts.romfs else {}
        games = [name for name in files if name.startswith("/rom/")]
        if "/config.ini" not in files or len(games) != 1 or ".code" not in parts.exefs_files:
            raise InjectError(f"{Path(path).name} is not an official Game Boy Virtual Console "
                              "title (no /rom/ and config.ini in its RomFS).")
        game = files[games[0]]
        kind = "gbc" if is_color(game) else "gb"
        kept = {name: content for name, content in files.items()
                if not name.startswith("/rom/") and ".patch" not in name}
        return cls(kind, parts.exheader, parts.exefs_files[".code"],
                   parts.exefs_files.get("logo", b""), kept, games[0])

    def build(self, rom: bytes, title_id: int, app_title: str) -> tuple[bytes, bytes]:
        """Exheader and RomFS image for a new game."""
        files = dict(self.files)
        files[self.rom_name] = rom
        files["/" + self.rom_name.rsplit("/", 1)[-1] + ".patch"] = _EMPTY_PATCH
        exheader = ncch.patch_exheader(self.exheader, title_id, app_title,
                                       save_data_size=save_size(self.exheader, rom))
        return exheader, romfs.build(files)


def save_size(exheader: bytes, rom: bytes) -> int:
    """The donor's save data size, more for cartridges with a lot of RAM."""
    size = struct.unpack_from("<Q", exheader, 0x1C0)[0]
    ram = _RAM.get(rom[0x149], 0) if len(rom) > 0x149 else 0
    needed = 2 * ram + 0x1000  # the RAM, a backup and the clock
    while size < needed:
        size *= 2
    return size


def installed_kinds() -> list[str]:
    return [k for k in KINDS if (templates_dir() / k / "meta.json").is_file()]


def template_for(rom: bytes) -> GbVcTemplate:
    """The emulator to use: the matching build, else the other one (Game Boy games
    run on the Color build; Color games need it, so the DMG build is a last resort)."""
    wanted = ["gbc", "gb"] if is_color(rom) else ["gb", "gbc"]
    for kind in wanted:
        if kind in installed_kinds():
            return GbVcTemplate.load(kind)
    return GbVcTemplate.load(wanted[0])  # raises MissingResource


def install_from(path: Path) -> GbVcTemplate:
    template = GbVcTemplate.from_title(path)
    template.save()
    return template

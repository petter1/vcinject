"""Emulator forwarders (RetroArch, snes9x_3ds, VirtuaNES) for non-GBA systems.

A forwarder is a homebrew emulator whose RomFS holds the game: the ROM file
named in rom_path.txt, emulator settings, a bottom-screen image and
internal_name.txt, which names the folder on the SD card where the emulator
keeps saves (sdmc:/nsui_formwarders_data/<internal name>/...). Two injects of
the same emulator differ only in the ROM, the internal name and the title ID,
so any existing inject (e.g. made with NSUI) works as template. Nothing of
the emulator is bundled with vcinject.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from . import ncch, romfs
from .errors import InjectError, MissingResource
from .systems import FORWARDER_KINDS
from .template import data_dir

_PREFIXES = {"snes": "snes", "gen": "gen", "md": "gen", "gb": "gbc", "gbc": "gbc", "nes": "nes",
             "fds": "nes"}
_ROM_KIND = {".sfc": "snes", ".smc": "snes", ".md": "gen", ".gen": "gen", ".smd": "gen",
             ".bin": "gen", ".gb": "gbc", ".gbc": "gbc", ".nes": "nes"}
_TEXT_SUFFIXES = (".cfg", ".txt", ".ini")


def templates_dir() -> Path:
    return data_dir() / "templates" / "forwarders"


@dataclass
class ForwarderTemplate:
    kind: str
    exheader: bytes
    code: bytes
    logo: bytes
    files: dict[str, bytes]      # RomFS without the ROM
    rom_name: str                # e.g. "/rom.sfc"
    internal_name: str           # the donor's, replaced in every text file
    patch_access_desc: bool      # homebrew AD (not Nintendo-signed) carries the title ID too

    # --- storage -----------------------------------------------------------------

    def save(self) -> Path:
        folder = templates_dir() / self.kind
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "exheader.bin").write_bytes(self.exheader)
        (folder / "code.bin").write_bytes(self.code)
        (folder / "logo.bin").write_bytes(self.logo)
        (folder / "romfs.bin").write_bytes(romfs.build(self.files))
        (folder / "meta.json").write_text(json.dumps({
            "kind": self.kind, "rom_name": self.rom_name, "internal_name": self.internal_name,
            "patch_access_desc": self.patch_access_desc}, indent=2))
        return folder

    @classmethod
    def load(cls, kind: str) -> "ForwarderTemplate":
        folder = templates_dir() / kind
        if not (folder / "meta.json").is_file():
            raise MissingResource(f"forwarder:{kind}", f"No {FORWARDER_KINDS.get(kind, kind)} template "
                                  f"set up. Add one of your existing injects for this system with "
                                  f"'vcinject setup --forwarder-donor <inject.cia>' or in the GUI.")
        try:
            meta = json.loads((folder / "meta.json").read_text())
            template = cls(kind=meta["kind"], exheader=(folder / "exheader.bin").read_bytes(),
                           code=(folder / "code.bin").read_bytes(),
                           logo=(folder / "logo.bin").read_bytes(),
                           files=romfs.read((folder / "romfs.bin").read_bytes()),
                           rom_name=meta["rom_name"], internal_name=meta["internal_name"],
                           patch_access_desc=meta["patch_access_desc"])
            if len(template.exheader) != 0x800 or not template.code:
                raise ValueError("exheader or code damaged")
        except (OSError, ValueError, KeyError, InjectError, struct.error) as exc:
            from .template import quarantine
            quarantine(folder)
            raise MissingResource(f"forwarder:{kind}", f"The {FORWARDER_KINDS.get(kind, kind)} template "
                                  f"was damaged ({exc}) and has been set aside; set it up again.") from exc
        return template

    # --- extraction --------------------------------------------------------------

    @classmethod
    def from_inject(cls, path: Path) -> "ForwarderTemplate":
        data = Path(path).read_bytes()
        cxi = ncch.first_cia_content(data) if data[0x100:0x104] != b"NCCH" else data
        parts = ncch.parse_ncch(cxi)
        if not parts.romfs:
            raise InjectError("This title has no RomFS, so it is not an emulator forwarder.")
        files = romfs.read(parts.romfs)
        rom_path = files.get("/rom_path.txt", b"").decode("utf-8", "replace").strip()
        if not rom_path.startswith("romfs:/"):
            raise InjectError("Not a supported forwarder (no rom_path.txt). Official Virtual "
                              "Console emulators come in a later version.")
        rom_name = rom_path[len("romfs:"):]
        internal = files.get("/internal_name.txt", b"").decode("utf-8", "replace").strip()
        kind = _PREFIXES.get(internal.split("_", 1)[0]) or _ROM_KIND.get(Path(rom_name).suffix.lower())
        if kind is None:
            raise InjectError(f"Can't tell which system this forwarder runs ({rom_name}).")
        files.pop(rom_name, None)
        own_id = struct.unpack_from("<Q", parts.exheader, 0x200)[0]
        ad_id = struct.unpack_from("<Q", parts.exheader, 0x600)[0]
        return cls(kind=kind, exheader=parts.exheader, code=parts.exefs_files[".code"],
                   logo=parts.exefs_files.get("logo", b""), files=files, rom_name=rom_name,
                   internal_name=internal, patch_access_desc=ad_id == own_id)

    # --- building ----------------------------------------------------------------

    def build(self, rom: bytes, rom_ext: str, title_id: int, title: str) -> tuple[bytes, bytes, str]:
        """Exheader, RomFS image and internal name for a new game."""
        internal = internal_name(self.internal_name.split("_", 1)[0] or self.kind, title_id, title)
        rom_name = f"/rom{rom_ext.lower()}"
        files = {}
        for path, data in self.files.items():
            if path.endswith(_TEXT_SUFFIXES) and self.internal_name:
                data = data.replace(self.internal_name.encode(), internal.encode())
            files[path] = data
        files["/internal_name.txt"] = internal.encode()
        files["/rom_path.txt"] = f"romfs:{rom_name}".encode()
        files[rom_name] = rom

        exheader = bytearray(self.exheader)
        struct.pack_into("<Q", exheader, 0x1C8, title_id)
        struct.pack_into("<Q", exheader, 0x200, title_id)
        if self.patch_access_desc:
            struct.pack_into("<Q", exheader, 0x600, title_id)
        return bytes(exheader), romfs.build(files), internal


def internal_name(prefix: str, title_id: int, title: str) -> str:
    """'snes_3bdb9_super_metroid': save folder name, unique per title ID."""
    slug = re.sub(r"[^a-z0-9._]", "", title.lower().replace(" ", "_"))
    return f"{prefix}_{(title_id >> 8) & 0xFFFFF:x}_{slug[:40]}"


def installed_kinds() -> list[str]:
    return [k for k in FORWARDER_KINDS if (templates_dir() / k / "meta.json").is_file()]


def install_from(path: Path) -> ForwarderTemplate:
    template = ForwarderTemplate.from_inject(path)
    template.save()
    return template

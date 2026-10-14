"""GBA VC base template: the signed exheader, empty RomFS and boot logo of an
official GBA VC title.

None of these files are bundled. They are either downloaded from the vcoven
project (pinned commit, SHA-256 verified) or extracted from a decrypted GBA VC
title the user dumped themselves - the same "donor" approach NSUI uses.
"""
from __future__ import annotations

import hashlib
import os
import platform
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import ncch
from .errors import InjectError, MissingResource

_VCOVEN_BASE = ("https://raw.githubusercontent.com/vedoot/vcoven/"
                "9aed5e2b31d5f19eb76098153bfbfa38ca6e3dd5/template/")
_DOWNLOADS = {
    "exheader.bin": ("exheader.bin", "44f8f254992ba7355b85d41262336ec10e1252dc0750a1628a609a2143cb32a9"),
    "romfs.bin": ("romfs.bin", "afcd2d8fd818654d196a385f7207d8011644f89b168dbf6fa9c4904515088f3d"),
    "logo.bin": ("logo.darc.lz", "2a98c49d919e254e15dc213cab47a800ed63b248dcd43119e8fb82d9e62ae51c"),
}
_FILES = tuple(_DOWNLOADS)


def data_dir() -> Path:
    override = os.environ.get("VCINJECT_DATA_DIR")
    if override:
        return Path(override)
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "vcinject"
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "vcinject"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "vcinject"


def template_dir() -> Path:
    return data_dir() / "templates" / "gba"


@dataclass(frozen=True)
class GbaTemplate:
    exheader: bytes
    romfs: bytes
    logo: bytes

    @classmethod
    def load(cls, directory: Path | None = None) -> "GbaTemplate":
        directory = directory or template_dir()
        missing = [name for name in _FILES if not (directory / name).is_file()]
        if missing:
            raise MissingResource("gba-template", "No GBA VC template installed. Run 'vcinject "
                                  "setup' (download) or 'vcinject setup --donor <title.cia>'.")
        template = cls(*((directory / name).read_bytes() for name in _FILES))
        problem = template.problem()
        if problem:
            quarantine(directory)
            raise MissingResource("gba-template", f"The GBA VC template was damaged ({problem}) "
                                  "and has been set aside; set it up again.")
        return template

    def problem(self) -> str | None:
        """What is wrong with the files, or None when they look usable."""
        if len(self.exheader) != 0x800:
            return "exheader has the wrong size"
        if self.romfs[:4] != b"IVFC":
            return "RomFS is not an IVFC image"
        if not self.logo:
            return "boot logo is empty"
        return None

    def save(self, directory: Path | None = None) -> Path:
        directory = directory or template_dir()
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in zip(_FILES, (self.exheader, self.romfs, self.logo)):
            (directory / name).write_bytes(data)
        return directory


def is_installed() -> bool:
    return all((template_dir() / name).is_file() for name in _FILES)


def quarantine(path: Path) -> Path | None:
    """Move a damaged file or folder aside (``*.broken-<time>``) so it gets set up
    again instead of failing every time; returns where it went."""
    import time

    path = Path(path)
    if not path.exists():
        return None
    target = path.with_name(f"{path.name}.broken-{int(time.time())}")
    path.rename(target)
    return target


def download() -> GbaTemplate:
    parts = []
    for name, (remote, sha256) in _DOWNLOADS.items():
        url = _VCOVEN_BASE + remote
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
        except OSError as exc:
            raise InjectError(f"Download of {url} failed: {exc}") from exc
        if hashlib.sha256(data).hexdigest() != sha256:
            raise InjectError(f"Checksum mismatch for {name}; refusing to use it.")
        parts.append(data)
    template = GbaTemplate(*parts)
    template.save()
    return template


def from_donor(path: Path) -> GbaTemplate:
    """Extract the template from a decrypted GBA VC .cia, .cxi or .app."""
    data = path.read_bytes()
    cxi = ncch.first_cia_content(data) if data[0x100:0x104] != b"NCCH" else data
    parts = ncch.parse_ncch(cxi)

    code = parts.exefs_files.get(".code", b"")
    if code[-0x10:-0xC] != b".CAA":
        raise InjectError("Donor is not a GBA Virtual Console title (no AGB_FIRM footer).")
    logo = parts.exefs_files.get("logo") or parts.logo_region
    if not logo:
        raise InjectError("Donor has no boot logo.")

    template = GbaTemplate(exheader=parts.exheader, romfs=parts.romfs, logo=logo)
    template.save()
    return template

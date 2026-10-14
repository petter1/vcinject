"""ROM library: find every supported ROM below a folder and describe it."""
from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import artwork, registry, systems
from .errors import InjectError

_SKIP_DIRS = {".git", "__pycache__", ".Trashes", ".Spotlight-V100", ".fseventsd"}


@dataclass
class LibraryRom:
    path: Path
    system: systems.System
    size: int
    folder: str = ""                       # sub folder relative to the library root
    release: str | None = None             # No-Intro name when identified
    year: str | None = None
    cover: Image.Image | None = None
    sha1: str = ""
    built: list[registry.Entry] = field(default_factory=list)

    @property
    def title(self) -> str:
        return (self.release or self.path.stem).split(" (", 1)[0]


def scan(root: Path) -> list[LibraryRom]:
    """All supported ROMs below ``root``, sorted by folder and name (fast: no reading)."""
    root = Path(root)
    if not root.is_dir():
        raise InjectError(f"Not a folder: {root}")
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in files:
            if name.startswith("."):
                continue
            path = Path(folder) / name
            try:
                system = systems.for_rom(path)
            except InjectError:
                continue
            relative = Path(folder).relative_to(root)
            found.append(LibraryRom(path, system, path.stat().st_size,
                                    "" if relative == Path(".") else str(relative)))
    return sorted(found, key=lambda r: (r.folder.lower(), r.path.name.lower()))


def analyze(rom: LibraryRom, online: bool = True, cover: bool = True) -> LibraryRom:
    """Identify the ROM, look up its cover and whether it was built before."""
    data = systems.read_rom(rom.path)
    rom.sha1 = hashlib.sha1(data).hexdigest()
    rom.built = sorted((e for e in registry.load().values() if e.rom_sha1 == rom.sha1),
                       key=lambda e: e.built_at)
    if not online:
        return rom
    try:
        db = artwork.ReleaseDatabase.load(system=rom.system.database)
        game_code = data[0xAC:0xB0].decode("ascii", "replace") if rom.system.key == "gba" else ""
        match = db.identify(rom.system.strip_header(data), game_code, systems.rom_name(rom.path))
        if match and artwork.is_probably_hack(match, systems.rom_name(rom.path)):
            game, hack_cover = artwork.nexus_cover([Path(systems.rom_name(rom.path)).stem],
                                                   rom.system.database)
            if game:
                match = None
                rom.release, rom.year, rom.cover = game.name, game.year, hack_cover
        if match:
            rom.release = match.name
            crcs = db.sibling_crcs(match.name)
            rom.year = artwork._metadata("releaseyear", crcs, rom.system.database)
            if cover:
                for name in (match.name, *db.siblings(match.name)[:3]):
                    rom.cover = artwork.fetch_image(name, "boxart", rom.system.database)
                    if rom.cover is not None:
                        break
        if cover and rom.cover is None and not (rom.release and not match):  # hacks: RetroGamesNexus
            game, rom.cover = artwork.nexus_cover(
                [match.name if match else None, Path(systems.rom_name(rom.path)).stem],
                rom.system.database)
            if game and not match:
                rom.release, rom.year = game.name, game.year
        if rom.cover is not None:
            rom.cover.thumbnail((128, 128))
    except InjectError:
        pass  # offline: the library still lists the file
    return rom


def crc32(path: Path) -> str:
    return f"{zlib.crc32(systems.read_rom(path)) & 0xFFFFFFFF:08X}"


# --- zipping: one ROM per .zip, as No-Intro sets are usually shared -----------------

def unzipped(roms: list[LibraryRom]) -> list[LibraryRom]:
    return [r for r in roms if not systems.is_zip(r.path)]


def zip_rom(path: Path) -> Path:
    """Pack ``path`` into ``<name>.zip`` next to it and delete the original once
    the zip has been read back and verified. Returns the zip."""
    path = Path(path)
    target = path.with_suffix(".zip")
    if target.exists():
        raise InjectError(f"{target.name} already exists; {path.name} was left as it is.")
    data = path.read_bytes()
    partial = target.with_name(f".{target.name}.part")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            info = zipfile.ZipInfo.from_file(path, path.name)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
        with zipfile.ZipFile(partial) as zf:  # never delete a ROM without a good copy
            if zf.read(path.name) != data:
                raise InjectError(f"Verifying {target.name} failed.")
        partial.replace(target)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InjectError(f"Could not zip {path.name} ({exc}).") from exc
    finally:
        if partial.exists():
            partial.unlink()
    shutil.copystat(path, target)
    path.unlink()
    return target


# --- collection: every game of a system, owned or not ------------------------------

@dataclass
class CatalogGame:
    title: str                 # display name (preferred region)
    releases: list[str]        # all No-Intro releases of the game
    system: systems.System
    year: str | None = None
    owned: bool = False


_PREFERRED_TAGS = {"Germany", "De", "Europe"}
# Not part of a collection: unlicensed, pirate and aftermarket releases, tools, BIOS ...
_NOT_GAMES = ("(Unl", "(Pirate", "(Aftermarket", "(Program", "(Tech Demo", "(Hack", "[BIOS]",
              "(Test Program", "(Enhancement Chip")


# Country tag whose release names a game, per UI language.
LANGUAGE_REGION = {"de": "Germany", "fr": "France", "es": "Spain", "it": "Italy",
                   "nl": "Netherlands", "pt": "Brazil", "ja": "Japan"}


def preferred_order(name: str, prefer: tuple[str, ...]) -> tuple:
    """Earlier entries of ``prefer`` win: ("Germany", "Europe") puts a German
    release before an "(USA, Europe)" one."""
    rank = next((i for i, p in enumerate(prefer) if f"({p}" in name or f", {p}" in name),
                len(prefer))
    return rank, artwork._release_rank(name, _PREFERRED_TAGS)


def catalog(system: systems.System, prefer: tuple[str, ...] = ()) -> list[CatalogGame]:
    """Every retail game of ``system`` (downloads the No-Intro list once), one entry
    per game. Regional releases are joined when they share a serial family (e.g.
    "Blaue Edition" and "Blue Version") or their title without tags."""
    db = artwork.ReleaseDatabase.load(system=system.database)
    names = [n for n in db.names
             if not artwork._release_rank(n)[0] and not any(t in n for t in _NOT_GAMES)]
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    first_with: dict[str, str] = {}
    for name in names:
        keys = {f"title:{artwork._normalize(name)}"}
        keys |= {f"serial:{s[:3]}" for s in db.serials.get(name, ()) if len(s) == 4}
        for key in keys:
            if key in first_with:
                parent[find(name)] = find(first_with[key])
            else:
                first_with[key] = name
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    games = []
    for releases in groups.values():
        releases.sort(key=lambda n: preferred_order(n, prefer))
        games.append(CatalogGame(releases[0].split(" (", 1)[0], releases, system))
    return sorted(games, key=lambda g: g.title.lower())


def mark_owned(games: list[CatalogGame], roms: list[LibraryRom]) -> int:
    owned_names = {rom.release for rom in roms if rom.release}
    count = 0
    for game in games:
        game.owned = any(name in owned_names for name in game.releases)
        count += game.owned
    return count

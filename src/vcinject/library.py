"""ROM library: find every supported ROM below a folder and describe it."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import artwork, dumpdb, registry, systems
from .errors import InjectError
from .template import data_dir

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
    method: str = ""                       # how release was found: crc, serial, ... retrogamesnexus
    family: str = ""                       # serial without region letter (GBA "BPE")
    crc: str = ""                          # CRC32 as No-Intro / libretro identify the dump
    serial: str = ""                       # cartridge serial ("BPED"), if the system has one
    dump_sha1: str = ""                    # SHA-1 as dump databases list it (no copier header)
    dump_size: int = 0                     # size of the dump without copier header
    header_sha1: str = ""                  # SHA-1 of the header (FlashGBX looks carts up by it)
    verified: str = ""                     # "good", "mismatch", "unknown" or "" (not checked)
    verified_by: str = ""                  # "No-Intro", "FlashGBX" or both

    @property
    def title(self) -> str:
        return (self.release or plain_stem(self.path)).split(" (", 1)[0]

    @property
    def self_dumped(self) -> bool:
        return is_self_dumped(self.path)

    @property
    def exact(self) -> bool:
        """Identified as one specific release (checksum or a known hack), so its
        file can be named after it."""
        return bool(self.release) and self.method in ("crc", "retrogamesnexus")


# "selfdumped" in a file name marks a ROM dumped from one's own cartridge.
_SELF_DUMPED = re.compile(r"self-?dumped", re.I)
_SELF_DUMPED_PART = re.compile(r"\s*(?:-\s*)?[\[(]?\s*self-?dumped\s*[\])]?", re.I)
SELF_DUMPED_SUFFIX = " - selfdumped"


def is_self_dumped(path: Path) -> bool:
    return bool(_SELF_DUMPED.search(Path(path).stem))


def plain_stem(path: Path) -> str:
    """File name without extension and without the "selfdumped" mark."""
    return _SELF_DUMPED_PART.sub("", Path(path).stem).strip() or Path(path).stem


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


# --- index: what was found out about each file, so the next start is instant ------------
#
# A file that has not changed (same size and time) is shown at once with its
# cover, without reading it; a renamed or moved file is recognised by its
# checksum. Covers are kept as small pictures next to the index. Nothing
# expires: refresh() checks a ROM online again (the GUI does that when a ROM is
# selected, and on request for the whole library).

THUMB = 256  # pixels; large enough for the grid and Cover Flow
_index: dict | None = None
_index_lock = threading.Lock()
_index_dirty = False


def _index_dir() -> Path:
    return data_dir() / "library"


_index_home: Path | None = None   # data folder the loaded index belongs to


def _load_index() -> dict:
    global _index, _index_home, _index_dirty
    with _index_lock:
        if _index is None or _index_home != _index_dir():
            try:
                _index = json.loads((_index_dir() / "index.json").read_text(encoding="utf-8"))
                if _index.get("format") != 1:
                    raise ValueError
            except (OSError, ValueError):
                _index = {"format": 1, "files": {}}
            _index_home, _index_dirty = _index_dir(), False
        return _index


def save_index() -> None:
    """Write the index if anything changed (cheap to call often)."""
    global _index_dirty
    with _index_lock:
        if _index is None or not _index_dirty:
            return
        folder = _index_home
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / "index.json.part"
        partial.write_text(json.dumps(_index, ensure_ascii=False), encoding="utf-8")
        partial.replace(folder / "index.json")
        _index_dirty = False


def _thumb_path(name: str) -> Path:
    return _index_dir() / "covers" / f"{name}.jpg"  # small: thousands of them add up


def _load_thumb(name: str) -> Image.Image | None:
    for path in (_thumb_path(name), _thumb_path(name).with_suffix(".png")):  # .png: older versions
        try:
            with Image.open(path) as img:
                img.load()
                return img.copy()
        except OSError:
            continue
    return None


def _save_thumb(name: str, image: Image.Image) -> None:
    path = _thumb_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, quality=88, optimize=True)
    path.with_suffix(".png").unlink(missing_ok=True)


_FIELDS = ("sha1", "release", "method", "family", "year", "crc", "serial", "dump_sha1",
           "dump_size", "header_sha1", "verified", "verified_by")
_TEXT_FIELDS = ("sha1", "method", "family", "crc", "serial", "dump_sha1", "verified",
                "verified_by")  # "" rather than None


def from_index(rom: LibraryRom) -> bool:
    """Fill ``rom`` from the index when its file is unchanged. Returns True when it was."""
    try:
        stat = rom.path.stat()
    except OSError:
        return False
    record = _load_index()["files"].get(str(rom.path))
    if not record or record.get("size") != stat.st_size or record.get("mtime") != stat.st_mtime \
            or "crc" not in record:  # entries from before CRC32 was kept: read once more
        return False
    for name in _FIELDS:
        setattr(rom, name, record.get(name) or ("" if name in _TEXT_FIELDS else None))
    rom.cover = _load_thumb(rom.sha1) if record.get("cover") else None
    rom.built = _built(rom.sha1)
    return True


def _remember(rom: LibraryRom) -> None:
    global _index_dirty
    try:
        stat = rom.path.stat()
    except OSError:
        return
    if rom.cover is not None:
        _save_thumb(rom.sha1, rom.cover)
    record = {name: getattr(rom, name) for name in _FIELDS}
    record.update(size=stat.st_size, mtime=stat.st_mtime, checked=time.time(),
                  cover=rom.cover is not None)
    index = _load_index()
    with _index_lock:
        index["files"][str(rom.path)] = record
        _index_dirty = True


def _by_checksum(sha1: str) -> dict | None:
    """An entry of the same data under another name (the file was renamed or moved)."""
    for record in _load_index()["files"].values():
        if record.get("sha1") == sha1:
            return record
    return None


def _built(sha1: str) -> list:
    return sorted((e for e in registry.load().values() if e.rom_sha1 == sha1),
                  key=lambda e: e.built_at)


def analyze(rom: LibraryRom, online: bool = True, cover: bool = True,
            refresh: bool = False) -> LibraryRom:
    """Identify the ROM, look up its cover and whether it was built before.
    Unchanged files come from the index without being read, unless ``refresh``
    asks to check everything online again."""
    if not refresh and from_index(rom):
        return rom
    data = systems.read_rom(rom.path)
    hash_rom(rom, data)
    rom.built = _built(rom.sha1)
    known = None if refresh else _by_checksum(rom.sha1)
    if known is not None and "crc" in known and (known.get("release") or not online):
        for name in _FIELDS[1:]:
            setattr(rom, name, known.get(name) or ("" if name in _TEXT_FIELDS else None))
        rom.cover = _load_thumb(rom.sha1) if known.get("cover") else None
        _remember(rom)
        return rom
    if not online:
        return rom
    verify(rom)
    _identify(rom, cover, refresh)
    return rom


def hash_rom(rom: LibraryRom, data: bytes) -> None:
    """Checksums and serial of the ROM's data."""
    rom.sha1 = hashlib.sha1(data).hexdigest()
    dump = rom.system.strip_header(data)
    rom.crc = f"{zlib.crc32(dump) & 0xFFFFFFFF:08X}"
    rom.dump_sha1 = rom.sha1 if len(dump) == len(data) else hashlib.sha1(dump).hexdigest()
    rom.dump_size = len(dump)
    rom.header_sha1 = dumpdb.header_key(rom.system.key, dump)
    code = data[0xAC:0xB0].decode("ascii", "replace")
    if rom.system.key == "gba" and re.fullmatch(r"[A-Z0-9]{4}", code):
        rom.serial = code


def verify_data(path: Path, system: systems.System, data: bytes) -> LibraryRom:
    """Check ROM data that is not (yet) in the library; see verify()."""
    rom = LibraryRom(Path(path), system, len(data))
    hash_rom(rom, data)
    verify(rom)
    return rom


def fix_hack(rom: LibraryRom) -> bool:
    """A ROM hack indexed as its base game (by an older version): identify it
    again. Returns True when it changed."""
    if rom.method != "serial" or not rom.release:
        return False
    if not artwork.is_probably_hack(artwork.Match(rom.release, "serial"), systems.rom_name(rom.path)):
        return False
    return refresh(rom)


def ensure_verified(rom: LibraryRom) -> bool:
    """Verify a ROM that came from the index before dumps were checked.
    Returns True when the result is new."""
    if rom.verified:
        return False
    try:
        verify(rom)
    except (OSError, InjectError, zipfile.BadZipFile):
        return False
    if rom.verified:
        _remember(rom)
    return bool(rom.verified)


def verify(rom: LibraryRom) -> str:
    """Check the dump against the known good dumps of No-Intro (via libretro) and,
    for Game Boy systems, FlashGBX. Sets and returns ``rom.verified``:
    "good" (the SHA-1 is listed), "mismatch" (the CRC32 of a release matches but
    not the SHA-1: probably a bad dump), "unknown" (in no list: a hack,
    translation, homebrew, or a trimmed or bad dump) or "" when offline."""
    if not rom.dump_sha1 or not rom.dump_size:  # entries from before verification existed
        if rom.system.key in ("nes", "snes") or not rom.sha1:  # copier headers: read it all
            dump = rom.system.strip_header(systems.read_rom(rom.path))
            rom.dump_sha1, rom.dump_size = hashlib.sha1(dump).hexdigest(), len(dump)
            rom.header_sha1 = dumpdb.header_key(rom.system.key, dump)
        else:
            rom.dump_sha1 = rom.sha1
            rom.dump_size = _rom_size(rom.path)
            rom.header_sha1 = dumpdb.header_key(rom.system.key,
                                                systems.read_rom_start(rom.path, 0x180))
    sources, differs = [], False
    try:
        db = artwork.ReleaseDatabase.load(system=rom.system.database)
        if rom.dump_sha1 in db.by_sha1:
            sources.append("No-Intro")
        differs = rom.crc in db.by_crc  # a release's CRC32, but not its SHA-1
    except InjectError:
        return rom.verified  # offline: keep what was found before
    try:
        state, _entry = dumpdb.check(rom.system.key, rom.header_sha1, rom.crc, rom.dump_size)
        if state == "good":
            sources.append("FlashGBX")
        differs = differs or state == "mismatch"  # a known cartridge, other contents
    except InjectError:
        pass  # FlashGBX is a second opinion only
    rom.verified = "good" if sources else "mismatch" if differs else "unknown"
    rom.verified_by = ", ".join(sources)
    return rom.verified


def _rom_size(path: Path) -> int:
    if systems.is_zip(path):
        return systems._zip_member(path).file_size
    return Path(path).stat().st_size


_REGION_CODES = {"Europe": "EU", "USA": "US", "Japan": "JP", "World": "W", "Germany": "DE",
                 "France": "FR", "Spain": "ES", "Italy": "IT", "Netherlands": "NL", "Sweden": "SE",
                 "Scandinavia": "SC", "UK": "UK", "Australia": "AU", "Korea": "KR", "China": "CN",
                 "Brazil": "BR", "Canada": "CA", "Asia": "AS", "Taiwan": "TW", "Hong Kong": "HK",
                 "Russia": "RU", "Denmark": "DK", "Norway": "NO", "Finland": "FI", "Portugal": "PT"}


def region_code(release: str | None) -> str:
    """Short region of a release: "(USA, Europe)" -> "US/EU", "(Germany)" -> "DE"."""
    match = re.search(r"\(([^)]*)\)", release or "")
    if not match:
        return ""
    codes = [_REGION_CODES.get(part.strip()) for part in match.group(1).split(",")]
    return "/".join(c for c in codes if c)


def version_codes(roms: list[LibraryRom]) -> list[str]:
    """The regions of the versions of a game one has, with counts for copies:
    ["DE×2", "US"]. ROMs that are not identified count as "?"."""
    counts: dict[str, int] = {}
    for rom in roms:
        code = region_code(rom.release) or "?"
        counts[code] = counts.get(code, 0) + 1
    return [code + (f"×{n}" if n > 1 else "") for code, n in counts.items()]


HACK_METHODS = ("retrogamesnexus", "hack")


def dump_is_bad(rom: LibraryRom) -> bool:
    """The checksum matches no known good dump (and it is not a ROM hack)."""
    return rom.verified in ("mismatch", "unknown") and rom.method not in HACK_METHODS


def verified_label(rom: LibraryRom) -> str:
    """How the dump check reads for people ("" when not checked)."""
    if rom.verified == "good":
        return f"✓ verified good dump ({rom.verified_by})"
    if rom.method in HACK_METHODS:
        return "ROM hack"
    if rom.verified == "mismatch":  # a known cartridge, but other contents
        return "⚠ differs from the known dump (bad dump or modified)"
    if rom.verified == "unknown":
        return "unverified (not in No-Intro / FlashGBX)"
    return ""


def _identify(rom: LibraryRom, cover: bool = True, refresh: bool = False) -> None:
    """Find release, year and cover online for a ROM whose CRC32 and serial are
    known, and remember the result. ``refresh`` asks the servers whether the
    cached pictures changed."""
    name_in_folder = plain_stem(systems.rom_name(rom.path)) + Path(systems.rom_name(rom.path)).suffix
    try:
        db = artwork.ReleaseDatabase.load(system=rom.system.database)
        game_code = rom.serial if rom.system.key == "gba" else ""
        match = db.identify_crc(rom.crc, game_code, name_in_folder)
        if match and artwork.is_probably_hack(match, systems.rom_name(rom.path)):
            # a ROM hack keeps its base game's serial; never list it as that game
            game, hack_cover = artwork.nexus_cover([plain_stem(systems.rom_name(rom.path))],
                                                   rom.system.database, refresh,
                                                   context=match.name)
            match = None
            if game:
                rom.release, rom.year, rom.cover = game.name, game.year, hack_cover
                rom.method = "retrogamesnexus"
            else:
                rom.method = "hack"  # not on RetroGamesNexus: named after its file
        if match:
            rom.release, rom.method = match.name, match.method
            families = sorted(s[:3] for s in db.serials.get(match.name, ()) if len(s) == 4)
            rom.family = families[0] if families else ""
            if not rom.family and rom.system.key in ("gb", "gbc"):
                try:  # no serials in libretro's Game Boy lists: FlashGBX's product code
                    rom.family = dumpdb.families(rom.system.key).get(rom.crc, "")
                except InjectError:
                    pass
            rom.serial = rom.serial or (sorted(db.serials.get(match.name, ())) or [""])[0]
            crcs = db.sibling_crcs(match.name)
            rom.year = artwork._metadata("releaseyear", crcs, rom.system.database)
            if cover:
                for name in (match.name, *db.siblings(match.name)[:3]):
                    rom.cover = artwork.fetch_image(name, "boxart", rom.system.database, refresh)
                    if rom.cover is not None:
                        break
        if cover and rom.cover is None and not (rom.release and not match) \
                and rom.method != "hack":  # games libretro has no cover for: RetroGamesNexus
            game, rom.cover = artwork.nexus_cover(
                [match.name if match else None, plain_stem(systems.rom_name(rom.path))],
                rom.system.database, refresh)
            if game and not match:
                rom.release, rom.year, rom.method = game.name, game.year, "retrogamesnexus"
        if rom.cover is not None:
            rom.cover.thumbnail((THUMB, THUMB))
    except InjectError:
        if refresh:
            raise
        return  # offline: the library still lists the file, try again next time
    _remember(rom)


def refresh(rom: LibraryRom) -> bool:
    """Check ``rom`` online again: release, year and cover. Pictures are only
    downloaded when they changed on the server; a file the index knows is not
    read again. ``rom`` only changes when that worked (offline it keeps what it had)."""
    fresh = LibraryRom(rom.path, rom.system, rom.size, rom.folder)
    try:
        if from_index(fresh) and fresh.crc:
            fresh.release = fresh.year = fresh.cover = None
            fresh.method = fresh.family = ""
            verify(fresh)
            _identify(fresh, refresh=True)
        else:
            analyze(fresh, refresh=True)
    except (InjectError, OSError, ValueError, zipfile.BadZipFile):
        return False
    for name in ("sha1", "release", "year", "method", "family", "crc", "serial", "dump_sha1",
                 "dump_size", "header_sha1", "verified", "verified_by", "cover", "built"):
        setattr(rom, name, getattr(fresh, name))
    save_index()
    return True


def catalog_cover(game: "CatalogGame", refresh: bool = False) -> Image.Image | None:
    """Box art of a game from the catalog (missing games), as a small cached picture.

    Only the small picture is kept (not the original), with the server's ETag, so
    ``refresh`` merely asks whether it changed. Games without any cover are noted
    and not asked for again until a refresh."""
    name = _catalog_thumb_name(game)
    path = _thumb_path(name)
    none = path.with_suffix(".none")
    if not refresh:
        cover = _load_thumb(name)
        if cover is not None or none.is_file():
            return cover
    tag_file = path.with_suffix(".etag")
    known_url, etag = "", None
    if refresh and tag_file.is_file() and path.is_file():
        known_url, _, etag = tag_file.read_text().partition("\n")
    for release in game.releases[:4]:
        url = artwork.thumbnail_url(release, "boxart", game.system.database)
        data, new_tag = artwork.conditional_get(url, etag if url == known_url else None)  # may raise
        if data is artwork.NOT_MODIFIED:
            return _load_thumb(name)
        if data is None:
            continue
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                cover = img.convert("RGB")
        except OSError:
            continue
        cover.thumbnail((THUMB, THUMB))
        _save_thumb(name, cover)
        tag_file.write_text(f"{url}\n{new_tag or ''}")
        none.unlink(missing_ok=True)
        return cover
    path.unlink(missing_ok=True)
    tag_file.unlink(missing_ok=True)
    none.parent.mkdir(parents=True, exist_ok=True)
    none.touch()
    return None


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
    unreleased: bool = False   # only betas, prototypes or demos exist


_PREFERRED_TAGS = {"Germany", "De", "Europe"}
_UNRELEASED = ("(Beta", "(Proto", "(Demo", "(Sample", "(Kiosk", "(Debug", "[b]")
# Not part of a collection: unlicensed, pirate and aftermarket releases, tools, BIOS ...
_NOT_GAMES = ("(Unl", "(Pirate", "(Aftermarket", "(Program", "(Tech Demo", "(Hack", "[BIOS]",
              "(Test Program", "(Enhancement Chip")


def for_region(games: list[CatalogGame], roms: list[LibraryRom],
               tags: tuple[str, ...]) -> list[CatalogGame]:
    """Mark what is owned, then keep the games released in a region (``tags``,
    e.g. "Europe", "Germany"; empty: all) named after their release there. Owned
    games stay even when they are another region's version (a USA cartridge is
    not "missing" in a Europe view)."""
    mark_owned(games, roms)
    if not tags:
        return games
    kept = []
    for game in games:
        regional = [n for n in game.releases if any(t in n for t in tags)]
        if regional:
            game.releases = regional
            game.title = regional[0].split(" (", 1)[0]
        if regional or game.owned:
            kept.append(game)
    return kept


# Country tag whose release names a game, per UI language.
LANGUAGE_REGION = {"de": "Germany", "fr": "France", "es": "Spain", "it": "Italy",
                   "nl": "Netherlands", "pt": "Brazil", "ja": "Japan"}


def preferred_order(name: str, prefer: tuple[str, ...]) -> tuple:
    """Earlier entries of ``prefer`` win: ("Germany", "Europe") puts a German
    release before an "(USA, Europe)" one."""
    rank = next((i for i, p in enumerate(prefer) if f"({p}" in name or f", {p}" in name),
                len(prefer))
    return rank, artwork._release_rank(name, _PREFERRED_TAGS)


def _title_key(name: str) -> str:
    """A title for comparing releases: without tags and case, but with its word
    boundaries ("Dragon Quest I & II" is not "Dragon Quest III")."""
    name = re.sub(r"\s*[\(\[].*?[\)\]]", "", name).lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", name))


def catalog(system: systems.System, prefer: tuple[str, ...] = (),
            unreleased: bool = False) -> list[CatalogGame]:
    """Every retail game of ``system`` (downloads the No-Intro list once), one entry
    per game. Regional releases are joined when they share a serial family (e.g.
    "Blaue Edition" and "Blue Version") or their title without tags."""
    db = artwork.ReleaseDatabase.load(system=system.database)
    # revisions ("Rev 1"), betas and re-releases belong to their game, so a dump of
    # any of them counts as owned; they are only never the name shown for a game
    names = [n for n in db.names if not any(t in n for t in _NOT_GAMES)]
    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    try:  # Game Boy lists have no serials: FlashGBX's product codes join languages
        extra = dumpdb.families(system.key) if system.key in ("gb", "gbc") else {}
    except InjectError:
        extra = {}
    first_with: dict[str, str] = {}
    for name in names:
        keys = {f"title:{_title_key(name)}"}
        keys |= {f"serial:{s[:3]}" for s in db.serials.get(name, ()) if len(s) == 4}
        keys |= {f"serial:{extra[c]}" for c in db.crcs.get(name, ()) if c in extra}
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
        never_released = all(any(t in n for t in _UNRELEASED) for n in releases)
        if never_released and not unreleased:
            continue  # only betas, prototypes, demos ... (shown on request)
        releases.sort(key=lambda n: (any(t in n for t in _UNRELEASED), preferred_order(n, prefer)))
        year = artwork._metadata("releaseyear", [c for n in releases for c in db.crcs.get(n, [])],
                                 system.database)
        games.append(CatalogGame(releases[0].split(" (", 1)[0], releases, system, year,
                                 unreleased=never_released))
    return sorted(games, key=lambda g: g.title.lower())


def mark_owned(games: list[CatalogGame], roms: list[LibraryRom]) -> int:
    owned_names = {rom.release for rom in roms if rom.release}
    count = 0
    for game in games:
        game.owned = any(name in owned_names for name in game.releases)
        count += game.owned
    return count


# --- series ("collections") and compilations ------------------------------------------

# Words that start many unrelated titles: never a series on their own.
_GENERIC_STARTS = {"super", "the", "new", "my", "disney", "disneys", "classic", "dr", "mr", "ms",
                   "pro", "world", "championship", "international", "ultimate", "mega", "real",
                   "little", "big", "great", "crazy", "battle", "power", "space", "star", "jurassic"}
_ROMAN = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}
# Games named after a person whose first name is also a series ("Mario Andretti
# Racing" is no Mario game): never part of the one-word series.
_PEOPLE = {"mario andretti", "mario lemieux", "michael jordan", "michael jackson",
           "james bond", "james pond", "john madden", "tony hawk", "tony hawks"}
_COMPILATION = re.compile(r"\b(\d+\s*(games\s*)?in\s*1|double\s*pack|combo\s*pack|compilation|"
                          r"collection|2-pack|triple\s*pack)\b|\s\+\s", re.I)


def is_compilation(title: str) -> bool:
    """Several games on one cartridge ("2 Games in 1 - ...", "... Collection")."""
    return bool(_COMPILATION.search(title))


def _head_words(title: str) -> list[str]:
    """Words of the main title (before " - " or ":"), without "The" and numbering."""
    head = re.split(r"\s+-\s+|:\s", title.split(" (", 1)[0], maxsplit=1)[0]
    head = re.sub(r",\s*the$", "", head, flags=re.I)  # "Legend of Zelda, The"
    head = unicodedata.normalize("NFKD", head).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", head.replace("'", ""))
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and (words[-1].isdigit() or words[-1] in _ROMAN):
        words = words[:-1]  # Sonic Advance 2 -> Sonic Advance
    return words


def series(titles: list[str]) -> dict[str, str]:
    """Series of each title that belongs to one ("Pokemon - Emerald Version" and
    "Pokemon Mystery Dungeon" -> "Pokemon"). Titles of no series are left out."""
    heads = {t: _head_words(t) for t in titles}
    full = {tuple(w) for w in heads.values() if w}
    counts: dict[tuple, int] = {}
    for words in heads.values():
        for k in range(1, len(words) + 1):
            counts[tuple(words[:k])] = counts.get(tuple(words[:k]), 0) + 1
    display: dict[tuple, str] = {}
    for title in titles:  # readable name of each start: the words as first written
        head = re.split(r"\s+-\s+|:\s", title.split(" (", 1)[0], maxsplit=1)[0]
        head = re.sub(r",\s*the$", "", head, flags=re.I)
        # the same word runs as _head_words, so "F-Zero" stays "F-Zero"
        runs = list(re.finditer(r"[^\W_]+(?:'[^\W_]+)?", head))
        if runs and runs[0].group().lower() == "the":
            head, runs = head[runs[0].end():].lstrip(), list(re.finditer(r"[^\W_]+(?:'[^\W_]+)?",
                                                                         head[runs[0].end():].lstrip()))
        for k in range(1, min(len(runs), len(heads[title])) + 1):
            display.setdefault(tuple(heads[title][:k]), head[:runs[k - 1].end()].strip(" ,:-"))
    result = {}
    for title, words in heads.items():
        chosen = None
        for k in range(1, len(words) + 1):  # a whole other title's main part: "Pokemon"
            prefix = tuple(words[:k])
            if k == 1 and (prefix[0] in _GENERIC_STARTS or " ".join(words[:2]) in _PEOPLE):
                continue
            if prefix in full and counts[prefix] >= 2:
                chosen = prefix
                break
        if chosen is None:  # or the shortest specific start several titles share
            for k in range(2, len(words) + 1):
                prefix = tuple(words[:k])
                if counts[prefix] >= 2 and not all(w in _GENERIC_STARTS for w in prefix):
                    chosen = prefix
                    break
        if chosen:
            result[title] = display.get(chosen) or " ".join(chosen).title()
    return result


# --- versions, duplicates and file names ----------------------------------------------

def group_versions(roms: list[LibraryRom]) -> list[list[LibraryRom]]:
    """ROMs of the same game together: copies, language and regional versions
    (same serial apart from the region letter, or the same title without tags)."""
    parent = list(range(len(roms)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[tuple, int] = {}
    for i, rom in enumerate(roms):
        keys = [(rom.system.key, "title", artwork._normalize(rom.release or plain_stem(rom.path)))]
        if not rom.family and rom.crc and rom.system.key in ("gb", "gbc"):
            try:  # indexed before Game Boy product codes were looked up
                rom.family = dumpdb.families(rom.system.key).get(rom.crc, "")
            except InjectError:
                pass
        if rom.family:
            keys.append((rom.system.key, "serial", rom.family))
        if rom.sha1:
            keys.append(("sha1", rom.sha1))
        for key in keys:
            if key in first:
                parent[find(i)] = find(first[key])
            else:
                first[key] = i
    groups: dict[int, list[LibraryRom]] = {}
    for i, rom in enumerate(roms):
        groups.setdefault(find(i), []).append(rom)
    return list(groups.values())


@dataclass
class Duplicate:
    copies: list[LibraryRom]   # the same release more than once
    identical: bool            # all bit-identical (after unpacking zips)

    @property
    def name(self) -> str:
        return self.copies[0].release or plain_stem(self.copies[0].path)


def duplicates(group: list[LibraryRom]) -> list[Duplicate]:
    """Copies of the same release (or the very same data) within one game."""
    by_release: dict[str, list[LibraryRom]] = {}
    for rom in group:
        key = f"sha1:{rom.sha1}" if rom.sha1 else f"name:{plain_stem(rom.path).lower()}"
        if rom.release and rom.method == "crc":
            key = f"release:{rom.release}"
        by_release.setdefault(key, []).append(rom)
    found = []
    for copies in by_release.values():
        if len(copies) > 1:
            hashes = {c.sha1 for c in copies}
            found.append(Duplicate(copies, len(hashes) == 1 and "" not in hashes))
    return found


_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|]')


def clean_stem(rom: LibraryRom, self_dumped: bool | None = None) -> str | None:
    """The tidy file name (without extension): the release name, plus the
    "selfdumped" mark when set. None when the ROM is not exactly identified."""
    if self_dumped is None:
        self_dumped = rom.self_dumped
    base = rom.release if rom.exact else None
    if base is None:
        if self_dumped == rom.self_dumped:
            return None
        base = plain_stem(rom.path)
    base = _UNSAFE_NAME.sub("_", base).strip(" .")
    return base + (SELF_DUMPED_SUFFIX if self_dumped else "")


def rename(rom: LibraryRom, stem: str) -> Path:
    """Give ``rom`` the file name ``stem`` (zips: the ROM inside as well)."""
    path = rom.path
    target = path.with_name(stem + path.suffix)
    if target == path:
        return path
    if target.exists() and target.name.lower() != path.name.lower():
        raise InjectError(f"{target.name} already exists; {path.name} was not renamed.")
    if systems.is_zip(path):
        inner = systems.rom_name(path)
        data = systems.read_rom(path)
        partial = path.with_name(f".{path.name}.part")
        try:
            member = stem + Path(inner).suffix
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                zf.writestr(member, data)
            with zipfile.ZipFile(partial) as zf:
                if zf.read(member) != data:
                    raise InjectError(f"Verifying the renamed {path.name} failed.")
            shutil.copystat(path, partial)
            partial.replace(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise InjectError(f"Could not rename inside {path.name} ({exc}).") from exc
        finally:
            if partial.exists():
                partial.unlink()
    path.rename(target)
    rom.path = target
    _forget_path(path)
    if rom.sha1:
        _remember(rom)
    return target


def _forget_path(path: Path) -> None:
    global _index_dirty
    index = _load_index()
    with _index_lock:
        if index["files"].pop(str(path), None) is not None:
            _index_dirty = True


def clean_names(roms: list[LibraryRom]) -> list[tuple[LibraryRom, str]]:
    """ROMs whose file name differs from the tidy one, with that name."""
    todo = []
    for rom in roms:
        stem = clean_stem(rom)
        if stem and stem != rom.path.stem:
            todo.append((rom, stem))
    return todo


def set_self_dumped(rom: LibraryRom, flag: bool) -> Path:
    stem = clean_stem(rom, flag) or (plain_stem(rom.path) + (SELF_DUMPED_SUFFIX if flag else ""))
    return rename(rom, stem)


# --- links ------------------------------------------------------------------------

def libretro_url(release: str | None, system: systems.System) -> str | None:
    """The release's entry in libretro-database (No-Intro data)."""
    return artwork.database_url(release, system.database) if release else None


_NEXUS_PAGES: dict[tuple, str | None] = {}  # (system, title, release) -> slug or None


def _nexus_titles(title: str, system: systems.System, release: str | None) -> list[str]:
    titles = [title]
    if release:
        try:
            db = artwork.ReleaseDatabase.load(system=system.database)
            titles += [n.split(" (", 1)[0] for n in db.siblings(release)[:8]]
        except InjectError:
            pass
    return list(dict.fromkeys(titles))


def nexus_url(title: str, system: systems.System, language: str = "",
              release: str | None = None) -> str | None:
    """The game's page on RetroGamesNexus (may download its game list once). The
    site uses English titles, so other regions' names of ``release`` are tried too
    ("Smaragd-Edition" is listed as "Pokemon Emerald"). Answers are remembered."""
    from . import nexus
    key = (system.key, title, release)
    if key not in _NEXUS_PAGES:
        try:
            game = nexus.find(_nexus_titles(title, system, release), system.key)
        except InjectError:
            return None  # offline: ask again later
        _NEXUS_PAGES[key] = game.slug if game else None
    slug = _NEXUS_PAGES[key]
    return nexus.page_url(slug, language) if slug else None


def nexus_page_exists(title: str, system: systems.System, release: str | None = None) -> bool:
    """Quick answer for menus, without going online: True when the page is known
    or the site lists a game of a similar name (checked on opening); False when
    it surely has none."""
    from . import nexus
    key = (system.key, title, release)
    if key in _NEXUS_PAGES:
        return _NEXUS_PAGES[key] is not None
    if system.key not in nexus.PLATFORMS:
        return False
    slugs = nexus.known_slugs()
    if slugs is None:
        return True  # the site's game list is not loaded yet: offer it
    return any(nexus.candidates(t, slugs) for t in _nexus_titles(title, system, release))


# --- downloading all covers at once -------------------------------------------------

def _catalog_thumb_name(game: "CatalogGame") -> str:
    key = f"{game.system.key}:{game.releases[0] if game.releases else game.title}"
    return "missing-" + hashlib.sha1(key.encode()).hexdigest()


def missing_games(roms: list[LibraryRom], prefer: tuple[str, ...] = ()) -> list["CatalogGame"]:
    """Every game of the library's systems that is not in the folder."""
    games = []
    for key in sorted({rom.system.key for rom in roms}):
        try:
            games += catalog(systems.SYSTEMS[key], prefer)
        except InjectError:
            continue
    mark_owned(games, roms)
    return [g for g in games if not g.owned]


def download_covers(roms: list[LibraryRom], games: list["CatalogGame"], stop: threading.Event,
                    progress=lambda done, total, name: None, workers: int = 8) -> tuple[int, int]:
    """Identify ``roms`` and cache their covers, and cache the covers of ``games``,
    several at a time. Whatever is cached already is skipped, so an interrupted
    run continues where it stopped. Returns (done, failed)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = []
    for rom in roms:
        if not from_index(rom):
            jobs.append((rom.path.name, lambda rom=rom: analyze(rom)))
    for game in games:
        thumb = _thumb_path(_catalog_thumb_name(game))
        if not thumb.is_file() and not thumb.with_suffix(".none").is_file():
            jobs.append((game.title, lambda game=game: catalog_cover(game)))
    total = len(roms) + len(games)
    done = total - len(jobs)
    failed = 0
    progress(done, total, "")
    if not jobs:
        return done, failed
    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(job): name for name, job in jobs}
    try:
        for count, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except Exception:  # one bad file or picture must not stop the rest
                failed += 1
            done += 1
            progress(done, total, futures[future])
            if count % 25 == 0:
                save_index()
            if stop.is_set():
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        save_index()
    return done, failed

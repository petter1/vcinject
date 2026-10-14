"""ROM library: find every supported ROM below a folder and describe it."""
from __future__ import annotations

import hashlib
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

from . import artwork, registry, systems
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
    return _index_dir() / "covers" / f"{name}.png"


def _load_thumb(name: str) -> Image.Image | None:
    try:
        with Image.open(_thumb_path(name)) as img:
            img.load()
            return img.copy()
    except OSError:
        return None


def _save_thumb(name: str, image: Image.Image) -> None:
    path = _thumb_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, optimize=True)


_FIELDS = ("sha1", "release", "method", "family", "year", "crc", "serial")
_TEXT_FIELDS = ("sha1", "method", "family", "crc", "serial")  # "" rather than None


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
    rom.sha1 = hashlib.sha1(data).hexdigest()
    rom.crc = f"{zlib.crc32(rom.system.strip_header(data)) & 0xFFFFFFFF:08X}"
    if rom.system.key == "gba" and re.fullmatch(r"[A-Z0-9]{4}", data[0xAC:0xB0].decode("ascii", "replace")):
        rom.serial = data[0xAC:0xB0].decode("ascii")
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
    try:
        db = artwork.ReleaseDatabase.load(system=rom.system.database)
        game_code = data[0xAC:0xB0].decode("ascii", "replace") if rom.system.key == "gba" else ""
        match = db.identify(rom.system.strip_header(data), game_code, systems.rom_name(rom.path))
        if match and artwork.is_probably_hack(match, systems.rom_name(rom.path)):
            game, hack_cover = artwork.nexus_cover([plain_stem(systems.rom_name(rom.path))],
                                                   rom.system.database, refresh)
            if game:
                match = None
                rom.release, rom.year, rom.cover = game.name, game.year, hack_cover
                rom.method = "retrogamesnexus"
        if match:
            rom.release, rom.method = match.name, match.method
            families = sorted(s[:3] for s in db.serials.get(match.name, ()) if len(s) == 4)
            rom.family = families[0] if families else ""
            rom.serial = rom.serial or (sorted(db.serials.get(match.name, ())) or [""])[0]
            crcs = db.sibling_crcs(match.name)
            rom.year = artwork._metadata("releaseyear", crcs, rom.system.database)
            if cover:
                for name in (match.name, *db.siblings(match.name)[:3]):
                    rom.cover = artwork.fetch_image(name, "boxart", rom.system.database, refresh)
                    if rom.cover is not None:
                        break
        if cover and rom.cover is None and not (rom.release and not match):  # hacks: RetroGamesNexus
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
        return rom  # offline: the library still lists the file, try again next time
    _remember(rom)
    return rom


def refresh(rom: LibraryRom) -> bool:
    """Check ``rom`` online again: release, year and cover are downloaded anew.
    ``rom`` only changes when that worked (offline it keeps what it had)."""
    fresh = LibraryRom(rom.path, rom.system, rom.size, rom.folder)
    try:
        analyze(fresh, refresh=True)
    except (InjectError, OSError):
        return False
    for name in ("sha1", "release", "year", "method", "family", "crc", "serial", "cover", "built"):
        setattr(rom, name, getattr(fresh, name))
    save_index()
    return True


def catalog_cover(game: "CatalogGame", refresh: bool = False) -> Image.Image | None:
    """Box art of a game from the catalog (missing games), as a small cached picture."""
    name = "missing-" + hashlib.sha1(f"{game.system.key}:{game.releases[0] if game.releases else game.title}"
                                     .encode()).hexdigest()
    cover = None if refresh else _load_thumb(name)
    if cover is not None:
        return cover
    for release in game.releases[:4]:
        cover = artwork.fetch_image(release, "boxart", game.system.database, refresh)  # may raise
        if cover is not None:
            cover.thumbnail((THUMB, THUMB))
            _save_thumb(name, cover)
            return cover
    if refresh:
        _thumb_path(name).unlink(missing_ok=True)  # the cover is gone upstream
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
        year = artwork._metadata("releaseyear", [c for n in releases for c in db.crcs.get(n, [])],
                                 system.database)
        games.append(CatalogGame(releases[0].split(" (", 1)[0], releases, system, year))
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
            if k == 1 and prefix[0] in _GENERIC_STARTS:
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


def nexus_url(title: str, system: systems.System, language: str = "",
              release: str | None = None) -> str | None:
    """The game's page on RetroGamesNexus (may download its game list once). The
    site uses English titles, so other regions' names of ``release`` are tried too
    ("Smaragd-Edition" is listed as "Pokemon Emerald")."""
    from . import nexus
    titles = [title]
    if release:
        try:
            db = artwork.ReleaseDatabase.load(system=system.database)
            titles += [n.split(" (", 1)[0] for n in db.siblings(release)[:8]]
        except InjectError:
            pass
    try:
        game = nexus.find(list(dict.fromkeys(titles)), system.key)
    except InjectError:
        return None
    return nexus.page_url(game.slug, language) if game else None

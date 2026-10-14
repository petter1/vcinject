"""Online artwork lookup via the libretro thumbnail database.

ROMs are identified against the No-Intro DAT shipped in libretro-database:
by CRC32 first (exact dump), then by the cartridge serial (hacks, translations
and other revisions), then by file name. The matching release name is the key
for https://thumbnails.libretro.com, which hosts box art, title screens,
in-game screenshots and logos for most releases. Nothing needs an API key.
ROMs it does not know or has no box art for (mostly ROM hacks) are looked up
on RetroGamesNexus as a fallback (see nexus.py).
"""
from __future__ import annotations

import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from . import art
from .errors import InjectError
from .template import data_dir

SYSTEM = "Nintendo - Game Boy Advance"  # default; see systems.SYSTEMS[...].database
_DAT_URL = ("https://raw.githubusercontent.com/libretro/libretro-database/master/"
            "metadat/no-intro/{system}.dat")
_THUMB_URL = "https://thumbnails.libretro.com/{system}/{kind}/{name}.png"
_DAT_MAX_AGE = 30 * 24 * 3600
_TIMEOUT = 20
_USER_AGENT = "vcinject"

KINDS = {
    "boxart": "Named_Boxarts",
    "title": "Named_Titles",
    "snap": "Named_Snaps",
    "logo": "Named_Logos",
}

# Characters libretro replaces with "_" in thumbnail file names.
_THUMB_UNSAFE = re.compile(r'[&*/:`<>?\\|"]')
_GAME_RE = re.compile(r'game \(\s*name "(?P<name>[^"]+)"(?P<body>.*?)\n\)', re.S)
_CRC_RE = re.compile(r"\bcrc ([0-9A-Fa-f]{8})\b")
_SERIAL_RE = re.compile(r'\bserial "([^"]+)"')
_SHA1_RE = re.compile(r"\bsha1 ([0-9A-Fa-f]{40})\b")
# Prefer the clean retail release when several share a serial.
_PENALTIES = ("(Beta", "(Proto", "(Demo", "(Sample", "(Kiosk", "(Virtual Console", "[b]", "(Rev")


@dataclass
class Match:
    name: str
    method: str  # "crc", "serial", "filename" or "retrogamesnexus"


@dataclass
class Artwork:
    match: Match | None = None
    images: dict[str, Image.Image] = field(default_factory=dict)
    year: str | None = None
    publisher: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.images)


def _cache_dir() -> Path:
    return data_dir() / "artwork"


def _download(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise InjectError(f"Artwork server error {exc.code} for {url}") from exc
    except OSError as exc:
        raise InjectError(f"Could not reach the artwork server: {exc}") from exc


NOT_MODIFIED = object()  # a conditional request found the cached copy still current


def conditional_get(url: str, etag: str | None = None, headers: dict | None = None):
    """GET ``url``; with ``etag`` the server answers "not modified" when the cached
    copy is still current. Returns (data | None | NOT_MODIFIED, new etag)."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read(), response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return NOT_MODIFIED, etag
        if exc.code in (404, 410):
            return None, None
        raise InjectError(f"Server error {exc.code} for {url}") from exc
    except OSError as exc:
        raise InjectError(f"Could not reach {urllib.parse.urlsplit(url).netloc}: {exc}") from exc


def cached_download(path: Path, url: str, refresh: bool = False, max_age: float | None = None,
                    get=conditional_get) -> bytes | None:
    """``url`` through a file cache next to ``path``; None when it does not exist.

    The server's ETag is kept beside the file, so ``refresh`` only asks whether the
    file changed and downloads it only then. ``max_age`` lets a copy expire.
    Offline, a cached copy is used rather than failing."""
    missing = path.with_name(path.name + ".missing")
    tag = path.with_name(path.name + ".etag")
    fresh = lambda p: max_age is None or time.time() - p.stat().st_mtime < max_age
    if not refresh and path.is_file() and fresh(path):
        return path.read_bytes()
    if not refresh and missing.is_file() and fresh(missing):
        return None
    etag = tag.read_text().strip() if tag.is_file() and path.is_file() else None
    try:
        data, new_tag = get(url, etag)
    except InjectError:
        if path.is_file():
            return path.read_bytes()
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is NOT_MODIFIED:
        path.touch()  # checked just now
        return path.read_bytes()
    if data is None:
        missing.touch()
        path.unlink(missing_ok=True)
        tag.unlink(missing_ok=True)
        return None
    path.write_bytes(data)
    missing.unlink(missing_ok=True)
    if new_tag:
        tag.write_text(new_tag)
    else:
        tag.unlink(missing_ok=True)
    return data


_DB_CACHE: dict[str, "ReleaseDatabase"] = {}


class ReleaseDatabase:
    def __init__(self, text: str):
        self.by_crc: dict[str, str] = {}
        self.by_serial: dict[str, list[str]] = {}
        self.crcs: dict[str, list[str]] = {}
        self.serials: dict[str, set[str]] = {}
        self.names: list[str] = []
        self.lines: dict[str, int] = {}  # line of each entry in the DAT (for links)
        self.by_sha1: dict[str, str] = {}  # SHA-1 of a verified dump -> release
        line, last = 1, 0
        for game in _GAME_RE.finditer(text):
            name, body = game.group("name"), game.group("body")
            line += text.count("\n", last, game.start())
            last = game.start()
            self.lines[name] = line
            self.names.append(name)
            self.crcs[name] = [crc.upper() for crc in _CRC_RE.findall(body)]
            for crc in self.crcs[name]:
                self.by_crc.setdefault(crc, name)
            for sha1 in _SHA1_RE.findall(body):
                self.by_sha1.setdefault(sha1.lower(), name)
            self.serials[name] = {s.upper() for s in _SERIAL_RE.findall(body)}
            for serial in self.serials[name]:
                self.by_serial.setdefault(serial, []).append(name)

    def siblings(self, name: str) -> list[str]:
        """Other releases of the same game: same serial apart from the region
        letter, or the same title without region/language tags."""
        families = {s[:3] for s in self.serials.get(name, ()) if len(s) == 4}
        base = _normalize(name)
        found = [other for other, serials in self.serials.items() if other != name
                 and ({s[:3] for s in serials} & families or _normalize(other) == base)]
        return sorted(found, key=_release_rank)

    def sibling_crcs(self, name: str) -> list[str]:
        """CRCs of the release itself first, then of its other regional versions
        (same serial apart from the region letter)."""
        own = list(self.crcs.get(name, []))
        families = {s[:3] for s in self.serials.get(name, ()) if len(s) == 4}
        others = [crc for other, serials in self.serials.items() if other != name
                  and {s[:3] for s in serials} & families for crc in self.crcs[other]]
        return own + others

    @classmethod
    def load(cls, refresh: bool = False, system: str = SYSTEM) -> "ReleaseDatabase":
        if not refresh and system in _DB_CACHE:
            return _DB_CACHE[system]
        path = _cache_dir() / f"{system}.dat"
        fresh = path.is_file() and time.time() - path.stat().st_mtime < _DAT_MAX_AGE
        if refresh or not fresh:
            try:
                data = _download(_DAT_URL.format(system=urllib.parse.quote(system)))
            except InjectError:
                if not path.is_file():
                    raise
                data = None  # offline: keep using the stale copy
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        db = cls(path.read_text(encoding="utf-8", errors="replace"))
        _DB_CACHE[system] = db
        return db

    def identify(self, rom: bytes, game_code: str = "", filename: str = "") -> Match | None:
        return self.identify_crc(f"{zlib.crc32(rom) & 0xFFFFFFFF:08X}", game_code, filename)

    def identify_crc(self, crc: str, game_code: str = "", filename: str = "") -> Match | None:
        """identify() for a ROM whose CRC32 is already known (no need to read it)."""
        if crc in self.by_crc:
            return Match(self.by_crc[crc], "crc")
        candidates = self.by_serial.get(game_code.upper()) if game_code else None
        if candidates:
            return Match(min(candidates, key=_release_rank), "serial")
        if filename:
            stem = Path(filename).stem
            exact = {n.lower(): n for n in self.names}.get(stem.lower())
            if exact:
                return Match(exact, "filename")
            wanted = _normalize(stem)
            hits = [n for n in self.names if _normalize(n) == wanted]
            if hits:
                return Match(min(hits, key=lambda n: _release_rank(n, _tags(stem))), "filename")
        return None


_PREFERRED_REGIONS = ("World", "USA, Europe", "Europe", "USA")


def _tags(name: str) -> set[str]:
    return {t.strip() for group in re.findall(r"\(([^)]*)\)", name) for t in group.split(",")}


def _release_rank(name: str, wanted_tags: set[str] = frozenset()) -> tuple[int, int, int, int]:
    penalty = sum(p in name for p in _PENALTIES)
    tag_misses = -len(_tags(name) & wanted_tags)
    region = next((i for i, r in enumerate(_PREFERRED_REGIONS) if f"({r}" in name),
                  len(_PREFERRED_REGIONS))
    return penalty, tag_misses, region, len(name)


def _normalize(name: str) -> str:
    name = re.sub(r"\s*[\(\[].*?[\)\]]", "", name)  # drop (Region) [tags]
    return re.sub(r"[^a-z0-9]", "", name.lower())


def database_url(name: str, system: str = SYSTEM) -> str | None:
    """The release's entry in libretro-database on GitHub (libretro has no game
    pages; this is where the release, its checksums and serial are listed)."""
    try:
        line = ReleaseDatabase.load(system=system).lines.get(name)
    except InjectError:
        return None
    if line is None:
        return None
    return ("https://github.com/libretro/libretro-database/blob/master/metadat/no-intro/"
            f"{urllib.parse.quote(system)}.dat#L{line}")


def serial_of(name: str, system: str = SYSTEM) -> str:
    try:
        serials = ReleaseDatabase.load(system=system).serials.get(name, ())
    except InjectError:
        return ""
    return sorted(serials)[0] if serials else ""


def thumbnail_url(name: str, kind: str, system: str = SYSTEM) -> str:
    return _THUMB_URL.format(system=urllib.parse.quote(system),
                             kind=KINDS[kind],
                             name=urllib.parse.quote(_THUMB_UNSAFE.sub("_", name)))


def fetch_image(name: str, kind: str, system: str = SYSTEM,
                refresh: bool = False) -> Image.Image | None:
    """An image from the thumbnail server, cached on disk. ``refresh`` asks the
    server whether it changed (e.g. a cover was replaced or added upstream)."""
    folder = _cache_dir() if system == SYSTEM else _cache_dir() / system
    cache = folder / KINDS[kind] / f"{_THUMB_UNSAFE.sub('_', name)}.png"
    old_marker = cache.with_suffix(".missing")  # older versions wrote "<name>.missing"
    if old_marker.is_file():
        old_marker.rename(cache.with_name(cache.name + ".missing"))
    data = cached_download(cache, thumbnail_url(name, kind, system), refresh,
                           get=lambda url, etag: conditional_get(url, etag))
    if data is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return img.copy()
    except OSError:
        return None


def lookup(rom: bytes, game_code: str = "", filename: str = "",
           kinds: tuple[str, ...] = tuple(KINDS), system: str = SYSTEM) -> Artwork:
    """Identify the ROM and download all available artwork for it."""
    db = ReleaseDatabase.load(system=system)
    match = db.identify(rom, game_code, filename)
    if match is None or is_probably_hack(match, filename):
        # a ROM hack is never shown as its base game, found on RetroGamesNexus or not
        context = match.name if match else ""
        return nexus_artwork(filename, system, context=context, rom=rom) \
            if "boxart" in kinds else Artwork()
    images = {}
    for kind in kinds:
        image = fetch_image(match.name, kind, system)
        if image is not None:
            images[kind] = image
    # Fallback: images missing for this release often exist for another region.
    missing = [kind for kind in kinds if kind not in images]
    for other in db.siblings(match.name)[:6] if missing else []:
        for kind in list(missing):
            try:
                image = fetch_image(other, kind, system)
            except InjectError:
                continue
            if image is not None:
                images[kind] = image
                missing.remove(kind)
        if not missing:
            break
    crcs = db.sibling_crcs(match.name)
    found = Artwork(match, images, year=_metadata("releaseyear", crcs, system),
                    publisher=_metadata("publisher", crcs, system))
    if "boxart" in kinds and "boxart" not in images:
        extra = nexus_artwork(filename, system, known=match.name)
        if "boxart" in extra.images:
            images["boxart"] = extra.images["boxart"]
            found.year = found.year or extra.year
            found.publisher = found.publisher or extra.publisher
    return found


def is_probably_hack(match: Match, filename: str) -> bool:
    """Matched only by the cartridge serial, but named like a different game: a ROM
    hack keeps its base game's serial (Pokémon Clover is BPRE like FireRed)."""
    if match.method != "serial" or not filename:
        return False
    import difflib
    from .nexus import words
    mine, base = " ".join(words(Path(filename).stem)), " ".join(words(match.name))
    return bool(mine) and difflib.SequenceMatcher(None, mine, base).ratio() < 0.8 \
        and base not in mine


def _system_key(database: str) -> str | None:
    from . import systems
    return next((s.key for s in systems.SYSTEMS.values() if s.database == database), None)


def nexus_cover(titles: list[str], system: str = SYSTEM, refresh: bool = False,
                context: str = ""):
    """(game, box art) from RetroGamesNexus, or (None, None). Never raises.
    ``context``: the base game's name when looking for a ROM hack."""
    from . import nexus
    key = _system_key(system)
    if not key:
        return None, None
    try:
        game = nexus.find([t for t in titles if t], key, refresh=refresh, context=context)
        return (game, nexus.cover(game, refresh=refresh)) if game else (None, None)
    except InjectError:
        return None, None


def nexus_from_rom_text(rom: bytes, system: str = SYSTEM, refresh: bool = False):
    """(game, box art) for a Pokémon ROM hack named in its own text, or (None, None)."""
    from . import nexus, romtext
    key = _system_key(system)
    if key != "gba" or not romtext.is_gen3_pokemon(rom):
        return None, None
    try:
        game = nexus.find_in_text(romtext.gen3_text(rom), key, refresh=refresh)
        return (game, nexus.cover(game, refresh=refresh)) if game else (None, None)
    except InjectError:
        return None, None


def nexus_artwork(filename: str, system: str = SYSTEM, known: str | None = None,
                  context: str = "", rom: bytes | None = None) -> Artwork:
    """Box art, name and year from RetroGamesNexus (fallback, mostly ROM hacks);
    with ``rom``, a hack whose file name says nothing is looked for by its text."""
    game, image = nexus_cover([known, Path(filename).stem if filename else None], system,
                              context=context)
    if game is None and rom is not None and context:
        game, image = nexus_from_rom_text(rom, system)
    if game is None or image is None:
        return Artwork()
    return Artwork(Match(game.name, "retrogamesnexus"), {"boxart": image}, year=game.year,
                   publisher=game.developer)


_META_RE = re.compile(r'game \((?P<body>.*?)\n\)', re.S)
_META_CACHE: dict[tuple[str, str], dict[str, str]] = {}


def _metadata(field_name: str, crcs: list[str], system: str = SYSTEM) -> str | None:
    """First value of a libretro-database metadat field for any of ``crcs``."""
    table = _META_CACHE.get((system, field_name))
    if table is None:
        folder = _cache_dir() / "metadat" if system == SYSTEM else _cache_dir() / "metadat" / system
        path = folder / f"{field_name}.dat"
        fresh = path.is_file() and time.time() - path.stat().st_mtime < _DAT_MAX_AGE
        if not fresh:
            url = (f"https://raw.githubusercontent.com/libretro/libretro-database/master/"
                   f"metadat/{field_name}/{urllib.parse.quote(system)}.dat")
            try:
                data = _download(url)
            except InjectError:
                data = None
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        table = {}
        if path.is_file():
            value_re = re.compile(rf'\b{field_name} "([^"]*)"')
            for game in _META_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
                body = game.group("body")
                value = value_re.search(body)
                if value:
                    for crc in _CRC_RE.findall(body):
                        table.setdefault(crc.upper(), value.group(1))
        _META_CACHE[(system, field_name)] = table
    return next((table[crc] for crc in crcs if crc in table), None)


# --- Composition --------------------------------------------------------------

def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    img = image.convert("RGB")
    scale = max(size[0] / img.width, size[1] / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                     Image.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def _backdrop(image: Image.Image, size: tuple[int, int], darken: float) -> Image.Image:
    blurred = _cover(image, size).filter(ImageFilter.GaussianBlur(max(size) / 24))
    return ImageEnhance.Brightness(blurred).enhance(darken).convert("RGBA")


def _contain(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    img = image.convert("RGBA")
    scale = min(box[0] / img.width, box[1] / img.height)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                      Image.LANCZOS)


def _paste_center(canvas: Image.Image, image: Image.Image, dy: int = 0) -> None:
    pos = ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2 + dy)
    canvas.alpha_composite(image, pos)


def _boxart_front(boxart: Image.Image) -> Image.Image:
    """Crop GBA box art to the cover illustration: drop the vertical
    "GAME BOY ADVANCE" stripe on the left and the rating bar at the bottom."""
    w, h = boxart.size
    if not 0.9 <= w / h <= 1.1:  # not the usual square GBA box, leave it alone
        return boxart
    return boxart.crop((round(w * 0.2), 0, w, round(h * 0.8)))


def compose_icon(source: Image.Image, size: int = 192) -> Image.Image:
    """Fit the whole image over a blurred copy of itself, so nothing is cropped."""
    canvas = _backdrop(source, (size, size), 0.8)
    _paste_center(canvas, _contain(source, (size, size)))
    return canvas.convert("RGB")


def _on_backdrop(background: Image.Image, foreground: Image.Image, size: tuple[int, int],
                 box: tuple[int, int], frame: bool = False) -> Image.Image:
    canvas = _backdrop(background, size, 0.55)
    fg = _contain(foreground, box)
    if frame:
        framed = Image.new("RGBA", (fg.width + 4, fg.height + 4), (255, 255, 255, 230))
        framed.alpha_composite(fg, (2, 2))
        fg = framed
    _paste_center(canvas, fg)
    return canvas


# Styles in order of preference; each needs the listed artwork kinds.
ICON_STYLES = {
    "boxart-full": ("Box art (whole box)", ("boxart",)),
    "boxart": ("Box art (cover only)", ("boxart",)),
    "logo": ("Logo", ("logo",)),
    "title": ("Title screen", ("title",)),
    "generated": ("Generated", ()),
}
BANNER_STYLES = {
    "logo": ("Logo", ("logo",)),
    "title": ("Title screen", ("title",)),
    "boxart": ("Box art", ("boxart",)),
    "generated": ("Generated", ()),
}


def available(styles: dict, artwork: Artwork) -> list[str]:
    return [key for key, (_label, needs) in styles.items()
            if all(kind in artwork.images for kind in needs)]


def render_icon(style: str, artwork: Artwork, title: str) -> Image.Image:
    imgs = artwork.images
    backdrop = imgs.get("boxart") or imgs.get("title")
    if style == "boxart" and "boxart" in imgs:
        return compose_icon(_boxart_front(imgs["boxart"]))
    if style == "boxart-full" and "boxart" in imgs:
        return compose_icon(imgs["boxart"])
    if style == "logo" and "logo" in imgs:
        return _on_backdrop(backdrop or imgs["logo"], imgs["logo"], (192, 192), (180, 150)).convert("RGB")
    if style == "title" and "title" in imgs:
        return compose_icon(imgs["title"])
    return art.default_icon(title)


def render_banner(style: str, artwork: Artwork, title: str,
                  system_name: str = "Game Boy Advance") -> Image.Image:
    imgs = artwork.images
    size = art.BANNER_SIZE
    backdrop = imgs.get("boxart") or imgs.get("title")
    if style == "logo" and "logo" in imgs:
        return _on_backdrop(backdrop or imgs["logo"], imgs["logo"], size, (236, 108))
    if style == "title" and "title" in imgs:
        return _on_backdrop(backdrop, imgs["title"], size, (180, 116), frame=True)
    if style == "boxart" and "boxart" in imgs:
        return _on_backdrop(imgs["boxart"], imgs["boxart"], size, (240, 118))
    return art.default_banner(title, system_name)


def default_art(artwork: Artwork, title: str) -> tuple[Image.Image, Image.Image]:
    """Best icon and banner from what was found, falling back to generated art."""
    return (render_icon(available(ICON_STYLES, artwork)[0], artwork, title),
            render_banner(available(BANNER_STYLES, artwork)[0], artwork, title))

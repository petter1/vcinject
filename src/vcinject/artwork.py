"""Online artwork lookup via the libretro thumbnail database.

ROMs are identified against the No-Intro DAT shipped in libretro-database:
by CRC32 first (exact dump), then by the cartridge serial (hacks, translations
and other revisions), then by file name. The matching release name is the key
for https://thumbnails.libretro.com, which hosts box art, title screens,
in-game screenshots and logos for most releases. Nothing needs an API key.
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
# Prefer the clean retail release when several share a serial.
_PENALTIES = ("(Beta", "(Proto", "(Demo", "(Sample", "(Kiosk", "(Virtual Console", "[b]", "(Rev")


@dataclass
class Match:
    name: str
    method: str  # "crc", "serial" or "filename"


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


_DB_CACHE: dict[str, "ReleaseDatabase"] = {}


class ReleaseDatabase:
    def __init__(self, text: str):
        self.by_crc: dict[str, str] = {}
        self.by_serial: dict[str, list[str]] = {}
        self.crcs: dict[str, list[str]] = {}
        self.serials: dict[str, set[str]] = {}
        self.names: list[str] = []
        for game in _GAME_RE.finditer(text):
            name, body = game.group("name"), game.group("body")
            self.names.append(name)
            self.crcs[name] = [crc.upper() for crc in _CRC_RE.findall(body)]
            for crc in self.crcs[name]:
                self.by_crc.setdefault(crc, name)
            self.serials[name] = {s.upper() for s in _SERIAL_RE.findall(body)}
            for serial in self.serials[name]:
                self.by_serial.setdefault(serial, []).append(name)

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
        crc = f"{zlib.crc32(rom) & 0xFFFFFFFF:08X}"
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


def thumbnail_url(name: str, kind: str, system: str = SYSTEM) -> str:
    return _THUMB_URL.format(system=urllib.parse.quote(system),
                             kind=KINDS[kind],
                             name=urllib.parse.quote(_THUMB_UNSAFE.sub("_", name)))


def fetch_image(name: str, kind: str, system: str = SYSTEM) -> Image.Image | None:
    folder = _cache_dir() if system == SYSTEM else _cache_dir() / system
    cache = folder / KINDS[kind] / f"{_THUMB_UNSAFE.sub('_', name)}.png"
    missing = cache.with_suffix(".missing")
    if cache.is_file():
        data = cache.read_bytes()
    elif missing.is_file() and time.time() - missing.stat().st_mtime < _DAT_MAX_AGE:
        return None
    else:
        data = _download(thumbnail_url(name, kind, system))
        cache.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            missing.touch()
            return None
        cache.write_bytes(data)
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
    if match is None:
        return Artwork()
    images = {}
    for kind in kinds:
        image = fetch_image(match.name, kind, system)
        if image is not None:
            images[kind] = image
    crcs = db.sibling_crcs(match.name)
    return Artwork(match, images, year=_metadata("releaseyear", crcs, system),
                   publisher=_metadata("publisher", crcs, system))


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
    "boxart": ("Box art", ("boxart",)),
    "boxart-full": ("Box art (whole box)", ("boxart",)),
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

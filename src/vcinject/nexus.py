"""Fallback artwork from RetroGamesNexus (https://retrogamesnexus.com).

Used when the libretro databases don't know a ROM or have no box art for it,
which is mostly the case for ROM hacks (Pokémon Clover, Ghost Grey ...): the
site lists those with a box-art style cover. Games are found by title in the
site's sitemap; the game page's schema.org data gives name, console, release
date and cover. Only these public pages are read (robots.txt allows them), and
everything is cached like the libretro data.
"""
from __future__ import annotations

import difflib
import io
import math
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


BASE = "https://retrogamesnexus.com"
_MAX_AGE = 30 * 24 * 3600
_MIN_SCORE = 0.88

# vcinject system key -> the site's gamePlatform
PLATFORMS = {"gba": "gba", "snes": "snes", "nes": "nes", "gb": "gb", "gbc": "gbc", "gen": "genesis"}
# words that only tell the console or edition apart, ignored when comparing titles
_NOISE = {"the", "version", "edition", "game", "rom", "hack", "selfdumped", "nes", "snes", "gba",
          "gb", "gbc", "genesis", "ps1", "nds", "n64", "sega", "mega", "drive"}
_LD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_DEVELOPER_RE = re.compile(r'\\"developer\\":\{\\"id\\":\\"[^"\\]*\\",\\"name\\":\\"([^"\\]+)\\"')


@dataclass
class Game:
    name: str
    slug: str
    platform: str
    year: str | None
    developer: str | None
    image_url: str | None


def _cache_dir() -> Path:
    from .artwork import _cache_dir as artwork_cache
    return artwork_cache() / "retrogamesnexus"


def _get(url: str, etag: str | None = None):
    """(data | None | NOT_MODIFIED, etag); tests replace this."""
    from .artwork import conditional_get
    return conditional_get(url, etag)


def _cached(path: Path, url: str, refresh: bool = False,
            max_age: float | None = None) -> bytes | None:
    """``url`` through the shared file cache (see artwork.cached_download)."""
    from .artwork import cached_download
    return cached_download(path, url, refresh, max_age, get=lambda u, etag: _get(u, etag))


def words(title: str) -> list[str]:
    """Title as comparable words: no tags, versions, accents or punctuation."""
    title = re.sub(r"\s*[\(\[].*?[\)\]]", " ", title)          # (Europe) [b] ...
    title = re.sub(r"\s+-\s+selfdumped.*$", "", title, flags=re.I)
    title = re.sub(r"\b[vV]\d+(?:\.\d+)*[a-z]?\b", " ", title)  # hack versions: v1.3
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = title.lower().replace("&", " and ").replace("'", "")
    tokens = re.findall(r"[a-z0-9]+", title)
    return [t for t in tokens if t not in _NOISE]


_SLUGS: dict[Path, list[str]] = {}  # per cache folder; parsing the sitemap takes a moment


def slugs() -> list[str]:
    """Every game page on the site (from its sitemap)."""
    path = _cache_dir() / "sitemap.xml"
    if path in _SLUGS:
        return _SLUGS[path]
    # the list of games is refreshed like the other game lists, monthly
    data = _cached(path, f"{BASE}/sitemap.xml", max_age=_MAX_AGE) or b""
    found = re.findall(rb"<loc>https://retrogamesnexus\.com/games/([a-z0-9-]+)</loc>", data)
    result = sorted({s.decode() for s in found})
    if result:  # an empty list (offline) is tried again next time
        _SLUGS[path] = result
    return result


def known_slugs() -> list[str] | None:
    """The game list if it is already loaded or on disk (never downloads)."""
    path = _cache_dir() / "sitemap.xml"
    if path in _SLUGS:
        return _SLUGS[path]
    return slugs() if path.is_file() else None


def candidates(title: str, all_slugs: list[str],
               context: frozenset = frozenset()) -> list[tuple[float, str]]:
    """Slugs whose title is close to ``title``, best first. Words of ``context``
    (e.g. the base game of a ROM hack) may be missing from ``title``: a hack saved
    as "Celia's Stupid" is listed as "Pokemon Celia's Stupid"."""
    wanted_words = words(title)
    wanted = " ".join(wanted_words)
    if not wanted:
        return []
    numbers = {w for w in wanted_words if w.isdigit()}
    scored = []
    for slug in all_slugs:
        have_words = [w for w in slug.split("-") if w not in _NOISE]
        if {w for w in have_words if w.isdigit()} != numbers:
            continue  # Sonic 2 is not Sonic 3
        have = " ".join(have_words)
        score = 1.0 if have == wanted else difflib.SequenceMatcher(None, wanted, have).ratio()
        extra = [w for w in have_words if w not in wanted_words]
        if score < _MIN_SCORE and context and extra and set(extra) <= context \
                and all(w in have_words for w in wanted_words):
            score = 0.95  # the title plus words of the base game
        if score >= _MIN_SCORE:
            scored.append((score, slug))
    return sorted(scored, key=lambda s: (-s[0], len(s[1])))


_FILL = r"(?:\s+(?:gba|the|and|of|a|version|edition))?"


def find_in_text(text: str, system_key: str, prefix: str = "pokemon",
                 refresh: bool = False) -> Game | None:
    """A game whose title appears in ``text`` (a ROM's own text, see romtext).

    Only titles starting with ``prefix`` and of at least two more words count;
    they must appear as a phrase (small words like "GBA" may sit in between).
    Longer and more frequent titles win: a hack names itself again and again."""
    platform = PLATFORMS.get(system_key)
    if not platform or not text:
        return None
    scored = []
    for slug in slugs():
        parts = slug.split("-")
        if parts[0] != prefix:
            continue
        words = parts[1:]
        if len([w for w in words if w not in ("and", "the", "of", "a")]) < 2:
            continue
        pattern = r"\b" + (_FILL + r"\s+").join(re.escape(w) for w in words) + r"\b"
        count = len(re.findall(pattern, text))
        if count:
            scored.append((len(words) * math.log(1 + count), slug))
    for _score, slug in sorted(scored, reverse=True)[:5]:
        found = game(slug, refresh)
        if found and found.platform == platform:
            return found
    return None


def game(slug: str, refresh: bool = False) -> Game | None:
    page = _cached(_cache_dir() / "pages" / f"{slug}.html", f"{BASE}/games/{slug}", refresh)
    if not page:
        return None
    html = page.decode("utf-8", "replace")
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data.get("@graph", [data]):
            if item.get("@type") != "VideoGame":
                continue
            image = item.get("image")
            image_url = image.get("url") if isinstance(image, dict) else image
            date = str(item.get("datePublished") or "")
            developer = _DEVELOPER_RE.search(html)
            developer = developer.group(1) if developer else None
            if developer and "community" in developer.lower():
                developer = None  # ROM hacks: "Community developer"
            # the cover folder names the console more precisely ("genesis", not "sega")
            folder = re.search(r"/games/cover/([a-z0-9-]+)/", image_url or "")
            platform = folder.group(1) if folder else str(item.get("gamePlatform", ""))
            return Game(item.get("name") or slug, slug, platform,
                        date[:4] if re.match(r"\d{4}", date) else None, developer,
                        image_url if image_url and "/games/cover/" in image_url else None)
    return None


def find(titles: list[str], system_key: str, refresh: bool = False,
         context: str = "") -> Game | None:
    """The site's page for the first of ``titles`` it knows on that console.
    ``context``: the base game's title, for ROM hacks (see candidates())."""
    platform = PLATFORMS.get(system_key)
    if not platform:
        return None
    all_slugs = slugs()
    extra_words = frozenset(words(context))
    seen = set()
    for title in titles:
        for _score, slug in candidates(title, all_slugs, extra_words)[:4]:
            if slug in seen:
                continue
            seen.add(slug)
            found = game(slug, refresh)
            if found and found.platform == platform:
                return found
    return None


# languages the site has pages in (its default is English, without a prefix)
LANGUAGES = {"de", "fr", "es", "it", "nl", "pt", "ru", "ja", "ko", "zh", "zh-TW", "hi", "fil", "vi",
             "th"}


def page_url(slug: str, language: str = "") -> str:
    prefix = f"/{language}" if language in LANGUAGES else ""
    return f"{BASE}{prefix}/games/{slug}"


def cover(found: Game, refresh: bool = False) -> Image.Image | None:
    if not found.image_url:
        return None
    suffix = Path(found.image_url).suffix or ".img"
    data = _cached(_cache_dir() / "covers" / found.platform / f"{found.slug}{suffix}",
                   found.image_url, refresh)
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return img.convert("RGB")
    except OSError:
        return None

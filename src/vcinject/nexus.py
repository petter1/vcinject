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
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .errors import InjectError

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


def _get(url: str) -> bytes | None:
    from .artwork import _download
    return _download(url)


def _cached(path: Path, url: str, refresh: bool = False,
            max_age: float | None = None) -> bytes | None:
    """``url`` through a file cache; None when the page does not exist.
    ``refresh`` downloads it again; ``max_age`` lets old copies expire."""
    missing = path.with_name(path.name + ".missing")
    fresh = lambda p: max_age is None or time.time() - p.stat().st_mtime < max_age
    if not refresh and path.is_file() and fresh(path):
        return path.read_bytes()
    if not refresh and missing.is_file() and fresh(missing):
        return None
    try:
        data = _get(url)
    except InjectError:
        if path.is_file():
            return path.read_bytes()  # offline: a stale copy is fine
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        missing.touch()
        return None
    path.write_bytes(data)
    missing.unlink(missing_ok=True)
    return data


def words(title: str) -> list[str]:
    """Title as comparable words: no tags, versions, accents or punctuation."""
    title = re.sub(r"\s*[\(\[].*?[\)\]]", " ", title)          # (Europe) [b] ...
    title = re.sub(r"\s+-\s+selfdumped.*$", "", title, flags=re.I)
    title = re.sub(r"\b[vV]\d+(?:\.\d+)*[a-z]?\b", " ", title)  # hack versions: v1.3
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = title.lower().replace("&", " and ").replace("'", "")
    tokens = re.findall(r"[a-z0-9]+", title)
    return [t for t in tokens if t not in _NOISE]


def slugs() -> list[str]:
    """Every game page on the site (from its sitemap)."""
    # the list of games is refreshed like the other game lists, monthly
    data = _cached(_cache_dir() / "sitemap.xml", f"{BASE}/sitemap.xml", max_age=_MAX_AGE) or b""
    found = re.findall(rb"<loc>https://retrogamesnexus\.com/games/([a-z0-9-]+)</loc>", data)
    return sorted({s.decode() for s in found})


def candidates(title: str, all_slugs: list[str]) -> list[tuple[float, str]]:
    """Slugs whose title is close to ``title``, best first."""
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
        if score >= _MIN_SCORE:
            scored.append((score, slug))
    return sorted(scored, key=lambda s: (-s[0], len(s[1])))


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


def find(titles: list[str], system_key: str, refresh: bool = False) -> Game | None:
    """The site's page for the first of ``titles`` it knows on that console."""
    platform = PLATFORMS.get(system_key)
    if not platform:
        return None
    all_slugs = slugs()
    seen = set()
    for title in titles:
        for _score, slug in candidates(title, all_slugs)[:4]:
            if slug in seen:
                continue
            seen.add(slug)
            found = game(slug, refresh)
            if found and found.platform == platform:
                return found
    return None


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

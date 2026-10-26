"""A short description of a game.

First from RetroGamesNexus, where vcinject already finds games per console
(ROM hacks included): its game pages carry a short description and genres,
translated into many languages. Else the summary of the game's Wikipedia
article, searched in the user's language, then in English; a hit counts only
when its title holds the words of the game's title (search engines like to
answer "Drome Racers" with "Lego Racers"). Found or not, the answer is kept,
so each game is looked up once.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass

from .template import data_dir

_SKIP = ("list of", "liste ", "liste der", "liste von")
_SMALL = {"the", "a", "an", "of", "and", "der", "die", "das", "und", "edition", "version"}


@dataclass(frozen=True)
class About:
    text: str
    url: str        # where it is from, linked under the text (Wikipedia: CC BY-SA)
    title: str
    language: str
    source: str = "Wikipedia"
    genres: tuple = ()


def _words(text: str) -> set[str]:
    text = re.sub(r"['’`]", "", text)  # Link's / Link’s
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {w for w in re.split(r"[^a-z0-9]+", plain) if w and w not in _SMALL}


def matches(game: str, article: str) -> bool:
    """Whether a search hit is about the game, not a list, a console or a similar game."""
    if article.lower().startswith(_SKIP):
        return False
    wanted = _words(game)
    return bool(wanted) and wanted <= _words(article)


def _get(url: str) -> dict | None:
    from .artwork import _download
    data = _download(url)
    return json.loads(data) if data else None


def _lookup(title: str, system: str, language: str) -> About | None:
    query = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": f"{title} {system}",
                                    "srlimit": 5, "format": "json"})
    found = _get(f"https://{language}.wikipedia.org/w/api.php?{query}") or {}
    for hit in found.get("query", {}).get("search", []):
        if not matches(title, hit["title"]):
            continue
        page = urllib.parse.quote(hit["title"].replace(" ", "_"), safe="")
        summary = _get(f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{page}") or {}
        if summary.get("type") == "standard" and summary.get("extract"):
            return About(summary["extract"], summary.get("content_urls", {}).get("desktop", {}).get("page", ""),
                         summary.get("title", hit["title"]), language)
    return None


def _from_nexus(title: str, system_key: str, language: str) -> About | None:
    from . import nexus
    game = nexus.find([title], system_key)
    found = nexus.describe(game.slug, language) if game else None
    if not found:
        return None
    text, genres = found
    lang = language if language in nexus.LANGUAGES else "en"
    return About(text, nexus.page_url(game.slug, lang if lang != "en" else ""), game.name, lang,
                 "RetroGamesNexus", tuple(genres))


def describe(title: str, system: str, language: str = "en", system_key: str = "") -> About | None:
    """The game's description (cached); None when nothing fits. Raises InjectError
    when offline and nothing is cached."""
    key = re.sub(r"[^\w.-]+", "_", f"{system}-{title}")[:120]
    cache = data_dir() / "about" / language / f"{key}.json"
    if cache.is_file():
        known = json.loads(cache.read_text(encoding="utf-8"))
        if known:
            known["genres"] = tuple(known.get("genres", ()))
        return About(**known) if known else None
    found = _from_nexus(title, system_key, language) if system_key else None
    for lang in dict.fromkeys((language, "en")):
        if found:
            break
        found = _lookup(title, system, lang)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(found.__dict__ if found else {}, ensure_ascii=False), encoding="utf-8")
    return found


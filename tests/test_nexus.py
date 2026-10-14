import io
import json

from PIL import Image

from vcinject import artwork, nexus
from vcinject.artwork import Match


def _page(name, platform, date, cover_folder, slug):
    ld = {"@graph": [{"@type": "VideoGame", "name": name, "gamePlatform": platform,
                      "datePublished": date,
                      "image": {"url": f"{nexus.BASE}/images/games/cover/{cover_folder}/{slug}.webp"}}]}
    return (f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            '\\"developer\\":{\\"id\\":\\"102\\",\\"name\\":\\"Community developer\\"}').encode()


def _site(monkeypatch, tmp_path):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    image = io.BytesIO()
    Image.new("RGB", (40, 40), "green").save(image, "WEBP")
    pages = {
        "sitemap.xml": b"".join(f"<loc>{nexus.BASE}/games/{s}</loc>".encode() for s in
                                ("pokemon-clover", "sonic-the-hedgehog-2", "sonic-the-hedgehog-3",
                                 "pokemon-clover-nds")),
        "games/pokemon-clover": _page("Pokemon Clover", "gba", "2023-10-11", "gba", "pokemon-clover"),
        "games/pokemon-clover-nds": _page("Pokemon Clover", "nds", "2023", "nds", "pokemon-clover-nds"),
        "games/sonic-the-hedgehog-3": _page("Sonic 3", "sega", "1994-02-02", "genesis",
                                            "sonic-the-hedgehog-3"),
        "games/sonic-the-hedgehog-2": _page("Sonic 2", "sega", "1992", "genesis", "sonic-the-hedgehog-2"),
    }
    requested = []

    def fake_get(url):
        requested.append(url)
        if "/images/games/cover/" in url:
            return image.getvalue()
        return pages.get(url[len(nexus.BASE) + 1:])

    monkeypatch.setattr(nexus, "_get", fake_get)
    return requested


def test_finds_hack_by_title_and_console(monkeypatch, tmp_path):
    _site(monkeypatch, tmp_path)
    game = nexus.find(["Pokemon Clover v1.3 (Hack)"], "gba")
    assert (game.name, game.platform, game.year, game.developer) == ("Pokemon Clover", "gba", "2023", None)
    assert nexus.cover(game).size == (40, 40)
    assert nexus.find(["Pokemon Clover"], "snes") is None


def test_numbers_must_match_and_console_from_cover_folder(monkeypatch, tmp_path):
    _site(monkeypatch, tmp_path)
    assert nexus.find(["Sonic The Hedgehog 3 (USA)"], "gen").name == "Sonic 3"
    assert nexus.find(["Sonic The Hedgehog 4"], "gen") is None


def test_pages_are_cached(monkeypatch, tmp_path):
    requested = _site(monkeypatch, tmp_path)
    nexus.find(["Pokemon Clover"], "gba")
    count = len(requested)
    nexus.find(["Pokemon Clover"], "gba")
    assert len(requested) == count


def test_hack_detection():
    firered = Match("Pokemon - FireRed Version (USA, Europe) (Rev 1)", "serial")
    assert artwork.is_probably_hack(firered, "Pokemon Clover.gba")
    assert not artwork.is_probably_hack(firered, "Pokemon - FireRed Version (Germany) [T-De].gba")
    assert not artwork.is_probably_hack(Match(firered.name, "crc"), "Pokemon Clover.gba")

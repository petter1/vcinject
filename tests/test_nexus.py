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

    def fake_get(url, etag=None):
        requested.append(url)
        if "/images/games/cover/" in url:
            return image.getvalue(), None
        return pages.get(url[len(nexus.BASE) + 1:]), None

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


def test_menu_offers_the_site_only_when_it_has_the_game(monkeypatch, tmp_path):
    from vcinject import library, systems

    _site(monkeypatch, tmp_path)
    gba = systems.SYSTEMS["gba"]
    nexus.slugs()  # the game list is loaded (as after the first lookup)
    assert library.nexus_page_exists("Pokemon Clover", gba)
    assert not library.nexus_page_exists("Bionicle Heroes", gba)  # no similar title at all
    assert library.nexus_url("Pokemon Clover", gba, "de") == f"{nexus.BASE}/de/games/pokemon-clover"
    assert library.nexus_url("Pokemon Clover", systems.SYSTEMS["snes"]) is None
    # remembered: a game the site does not have on that console is no longer offered
    assert not library.nexus_page_exists("Pokemon Clover", systems.SYSTEMS["snes"])


def test_hack_found_with_words_of_its_base_game(monkeypatch, tmp_path):
    _site(monkeypatch, tmp_path)
    slugs = ["pokemon-celias-stupid", "pokemon-clover"]
    assert nexus.candidates("Celia's Stupid (1.0.91)", slugs) == []
    context = frozenset(nexus.words("Pokemon - FireRed Version (USA, Europe)"))
    assert nexus.candidates("Celia's Stupid (1.0.91)", slugs, context)[0][1] == "pokemon-celias-stupid"
    # words that are not from the base game still have to match
    assert nexus.candidates("Stupid", ["pokemon-celias-stupid"], context) == []


def test_unlisted_hack_is_never_its_base_game(monkeypatch, tmp_path):
    from pathlib import Path

    from vcinject import artwork, library, systems

    _site(monkeypatch, tmp_path)

    class Db:
        serials = {"Pokemon - FireRed Version (USA, Europe)": {"BPRE"}}
        by_sha1, by_crc = {}, {}

        def identify_crc(self, *args):
            return artwork.Match("Pokemon - FireRed Version (USA, Europe)", "serial")

        def sibling_crcs(self, name):
            return []

        def siblings(self, name):
            return []

    monkeypatch.setattr(artwork.ReleaseDatabase, "load", classmethod(lambda cls, **kw: Db()))
    path = tmp_path / "My Own Hack v2.gba"
    path.write_bytes(b"\0" * 0x200)
    rom = library.analyze(library.LibraryRom(Path(path), systems.SYSTEMS["gba"], 0x200))
    assert rom.release is None and rom.method == "hack" and rom.family == ""
    assert rom.title == "My Own Hack v2" and library.verified_label(rom) == "ROM hack"
    assert not library.dump_is_bad(rom)


def _gen3(text: str) -> bytes:
    out = bytearray()
    for char in text:
        if char == " ":
            out.append(0x00)
        elif char.isdigit():
            out.append(0xA1 + int(char))
        elif char.isupper():
            out.append(0xBB + ord(char) - ord("A"))
        elif char.islower():
            out.append(0xD5 + ord(char) - ord("a"))
        else:
            out.append({"!": 0xAB, "'": 0xB4}[char])
    return bytes(out) + b"\xff"


def test_pokemon_hack_named_in_its_own_text(monkeypatch, tmp_path):
    from vcinject import romtext

    _site(monkeypatch, tmp_path)
    monkeypatch.setattr(nexus, "slugs", lambda: ["pokemon-sword-shield", "pokemon-clover",
                                                 "pokemon-sword-and-shield-ultimate"])
    found = {"pokemon-sword-and-shield-ultimate": nexus.Game("Pokemon Sword and Shield Ultimate",
                                                             "pokemon-sword-and-shield-ultimate",
                                                             "gba", "2023", None, None)}
    monkeypatch.setattr(nexus, "game", lambda slug, refresh=False: found.get(slug))
    rom = bytearray(0x200)
    rom[0xA0:0xB0] = b"POKEMON FIREBPRE"
    rom += _gen3("Welcome to Sword and Shield GBA ULTIMATE!") * 3 + _gen3("Swords Dance")
    assert romtext.is_gen3_pokemon(bytes(rom))
    text = romtext.gen3_text(bytes(rom))
    assert "welcome to sword and shield gba ultimate!" in text
    assert nexus.find_in_text(text, "gba").slug == "pokemon-sword-and-shield-ultimate"
    assert nexus.find_in_text(romtext.gen3_text(_gen3("Swords Dance and a shield")), "gba") is None

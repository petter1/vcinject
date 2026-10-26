import json

from vcinject import about, artwork, nexus


def test_only_articles_about_the_game_count():
    assert about.matches("Drome Racers", "Drome Racers")
    assert not about.matches("Drome Racers", "Lego Racers")
    assert about.matches("Pokémon – Saphir-Edition", "Pokémon Rubin- und Saphir-Edition")
    assert about.matches("The Legend of Zelda - Link's Awakening", "The Legend of Zelda: Link’s Awakening")
    assert not about.matches("Pokémon Saphir", "Liste der Pokémon-Spiele")


def test_retrogamesnexus_first_then_wikipedia_and_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    asked = []

    def download(url):
        asked.append(url)
        if "list=search" in url:
            return json.dumps({"query": {"search": [{"title": "Lego Racers"},
                                                    {"title": "Bionicle (video game)"}]}}).encode()
        if "page/summary/Bionicle" in url:
            return json.dumps({"type": "standard", "extract": "Bionicle is a 2003 platform game.",
                               "title": "Bionicle (video game)",
                               "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Bionicle_(video_game)"}}}).encode()
        return None

    monkeypatch.setattr(artwork, "_download", download)
    monkeypatch.setattr(nexus, "find", lambda titles, key, *a, **k:
                        nexus.Game("Drome Racers", "drome-racers", "gba", "2002", None, None)
                        if titles == ["Drome Racers"] else None)
    monkeypatch.setattr(nexus, "describe", lambda slug, lang: ("Spieler treten an.", ["racing"]))

    drome = about.describe("Drome Racers", "Game Boy Advance", "de", "gba")
    assert (drome.source, drome.text, drome.genres, drome.language) == \
        ("RetroGamesNexus", "Spieler treten an.", ("racing",), "de")
    assert drome.url.endswith("/de/games/drome-racers")

    bionicle = about.describe("Bionicle", "Game Boy Advance", "de", "gba")
    assert bionicle.source == "Wikipedia" and bionicle.title == "Bionicle (video game)"
    count = len(asked)
    assert about.describe("Bionicle", "Game Boy Advance", "de", "gba") == bionicle   # from the cache
    assert len(asked) == count

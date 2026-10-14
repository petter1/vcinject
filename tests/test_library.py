from pathlib import Path

from vcinject import library, registry


def test_scan_finds_roms_in_sub_folders(tmp_path):
    (tmp_path / "GBA" / "RPG").mkdir(parents=True)
    (tmp_path / "SNES").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "GBA" / "RPG" / "a.gba").write_bytes(b"\0" * 0xC0)
    (tmp_path / "SNES" / "b.sfc").write_bytes(b"x")
    (tmp_path / "c.md").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("no rom")
    (tmp_path / ".hidden" / "d.gba").write_bytes(b"x")

    roms = library.scan(tmp_path)
    assert [(r.folder, r.path.name, r.system.key) for r in roms] == [
        ("", "c.md", "gen"), ("GBA/RPG", "a.gba", "gba"), ("SNES", "b.sfc", "snes")]


def test_analyze_offline_reports_built_roms(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    rom_path = tmp_path / "Game (Europe).gba"
    rom_path.write_bytes(b"\1" * 0xC0)
    rom = library.analyze(library.scan(tmp_path)[0], online=False)
    assert rom.built == [] and rom.title == "Game"
    registry.record(registry.Entry(title_id=0x000400000F000100, title="Game", rom_sha1=rom.sha1,
                                   rom_name=rom_path.name, product_code="", save_type="",
                                   output="", built_at=1.0))
    assert library.analyze(rom, online=False).built[0].title_id == 0x000400000F000100


def test_catalog_groups_regions_and_marks_owned(monkeypatch):
    from vcinject import artwork, systems
    dat = '''
game (
	name "Pokemon - Emerald Version (USA, Europe)"
	rom ( name "a.gba" size 1 crc 11111111 serial "BPEE" )
)

game (
	name "Pokemon - Smaragd-Edition (Germany)"
	rom ( name "b.gba" size 1 crc 22222222 serial "BPED" )
)

game (
	name "Pokemon - Emerald Version (USA, Europe) (Beta)"
	rom ( name "c.gba" size 1 crc 33333333 serial "BPEE" )
)

game (
	name "Some Pirate Game (Pirate)"
	rom ( name "d.gba" size 1 crc 44444444 )
)

game (
	name "Golden Sun (USA, Europe)"
	rom ( name "e.gba" size 1 crc 55555555 serial "AGSE" )
)
'''
    monkeypatch.setattr(artwork.ReleaseDatabase, "load", classmethod(lambda cls, **k: cls(dat)))
    games = library.catalog(systems.SYSTEMS["gba"], prefer=("Germany",))
    assert [g.title for g in games] == ["Golden Sun", "Pokemon - Smaragd-Edition"]
    rom = library.LibraryRom(path=None, system=systems.SYSTEMS["gba"], size=1,
                             release="Pokemon - Emerald Version (USA, Europe)")
    assert library.mark_owned(games, [rom]) == 1
    assert games[1].owned and not games[0].owned


def test_revisions_count_as_owning_the_game(monkeypatch):
    from vcinject import artwork, systems

    dat = "\n".join(f'game (\n\tname "{name}"\n\tserial "{serial}"\n\trom ( crc {crc} )\n)'
                    for name, serial, crc in (
                        ("Pokemon - Rubin-Edition (Germany)", "AXVD", "00000001"),
                        ("Pokemon - Rubin-Edition (Germany) (Rev 1)", "AXVD", "00000002"),
                        ("Pokemon - Rubin-Edition (Germany) (Beta)", "AXVD", "00000005"),
                        ("Pokemon - Ruby Version (USA, Europe)", "AXVE", "00000003"),
                        ("Unreleased Game (Europe) (Proto)", "BUGE", "00000004")))
    monkeypatch.setattr(artwork.ReleaseDatabase, "load",
                        classmethod(lambda cls, **kw: artwork.ReleaseDatabase(dat)))
    monkeypatch.setattr(artwork, "_metadata", lambda *a: None)
    gba = systems.SYSTEMS["gba"]
    games = library.catalog(gba, ("Germany", "Europe"))
    assert [g.title for g in games] == ["Pokemon - Rubin-Edition"]  # the prototype is no game
    assert games[0].releases[0] == "Pokemon - Rubin-Edition (Germany)"
    assert games[0].releases[-1].endswith("(Beta)")
    rev1 = library.LibraryRom(Path("rubin.gba"), gba, 1,
                              release="Pokemon - Rubin-Edition (Germany) (Rev 1)")
    library.mark_owned(games, [rev1])
    assert games[0].owned


def test_other_regions_versions_count_as_owned():
    from vcinject import systems

    gba = systems.SYSTEMS["gba"]
    kart = library.CatalogGame("Mario Kart - Super Circuit",
                               ["Mario Kart - Super Circuit (Europe)", "Mario Kart - Super Circuit (USA)"],
                               gba)
    usa_only = library.CatalogGame("Some Game", ["Some Game (USA)"], gba)
    owned_usa = library.CatalogGame("Mario Party Advance", ["Mario Party Advance (USA)"], gba)
    roms = [library.LibraryRom(Path(f"{n}.gba"), gba, 1, release=n)
            for n in ("Mario Kart - Super Circuit (USA)", "Mario Party Advance (USA)")]
    kept = library.for_region([kart, usa_only, owned_usa], roms, ("Europe", "Germany"))
    assert [g.title for g in kept] == ["Mario Kart - Super Circuit", "Mario Party Advance"]
    assert all(g.owned for g in kept)  # neither shows up as missing

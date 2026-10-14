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

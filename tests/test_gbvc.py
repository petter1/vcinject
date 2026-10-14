import struct

from vcinject import gbvc, romfs


def _template(kind="gbc"):
    exheader = bytearray(0x800)
    struct.pack_into("<Q", exheader, 0x1C0, 0x20000)
    files = {"/config.ini": b"[CGB]\r\n", "/layout.arc": b"menu"}
    return gbvc.GbVcTemplate(kind, bytes(exheader), b"code", b"", files, "/rom/DMGAAUD0.K00")


def _rom(color=False, ram_code=3):
    rom = bytearray(0x8000)
    rom[0x143] = 0x80 if color else 0x00
    rom[0x149] = ram_code
    return bytes(rom)


def test_game_replaces_the_original_with_an_empty_patch():
    exheader, image = _template().build(_rom(color=True), 0x000400000F123400, "1234")
    files = romfs.read(image)
    assert files["/rom/DMGAAUD0.K00"] == _rom(color=True)
    assert files["/DMGAAUD0.K00.patch"].startswith(b";")  # no Pokémon patches
    assert files["/config.ini"] == b"[CGB]\r\n"
    assert struct.unpack_from("<Q", exheader, 0x200)[0] == 0x000400000F123400
    assert struct.unpack_from("<Q", exheader, 0x1C0)[0] == 0x20000


def test_save_data_grows_with_cartridge_ram():
    exheader = _template().exheader
    assert gbvc.save_size(exheader, _rom(ram_code=3)) == 0x20000   # 32 KB: the donor's size
    assert gbvc.save_size(exheader, _rom(ram_code=4)) == 0x80000   # 128 KB RAM


def test_emulator_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    _template("gb").save()
    assert gbvc.template_for(_rom()).kind == "gb"
    assert gbvc.template_for(_rom(color=True)).kind == "gb"  # last resort for Color games
    _template("gbc").save()
    assert gbvc.template_for(_rom(color=True)).kind == "gbc"
    assert gbvc.template_for(_rom()).kind == "gb"

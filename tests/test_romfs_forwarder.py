import struct

import pytest

from vcinject import forwarder, ncch, romfs
from vcinject.errors import InjectError

FILES = {
    "/rom_path.txt": b"romfs:/rom.sfc",
    "/internal_name.txt": b"snes_3bdb9_super_metroid",
    "/rom.sfc": b"OLD ROM" * 100,
    "/retroarch.cfg": b'savefile_directory = "sdmc:/nsui_formwarders_data/snes_3bdb9_super_metroid/s"',
    "/shader/a.shbin": b"x" * 5000,
    "/shader/deep/b.bin": b"",
    "/été.txt": b"unicode name",
}


def test_romfs_roundtrip_and_header():
    image = romfs.build(FILES)
    assert image[:4] == b"IVFC"
    assert romfs.read(image) == FILES
    assert romfs.hash_region_size(image) == 0x200


def test_romfs_many_files_use_prime_bucket_counts():
    files = {f"/d{i % 7}/f{i}.bin": bytes([i % 256]) * i for i in range(60)}
    assert romfs.read(romfs.build(files)) == files


def test_internal_name_matches_nsui_scheme():
    assert forwarder.internal_name("gen", 0x00040000054FE400, "Sonic & Knuckles") == "gen_54fe4_sonic__knuckles"
    assert forwarder.internal_name("nes", 0x0004000007907E00, "Super Mario Bros. 3Mix") \
        == "nes_7907e_super_mario_bros._3mix"


def fake_inject(tmp_path, ad_has_title_id=True):
    title_id = 0x0004000003BDB900
    exh = bytearray(0x800)
    struct.pack_into("<Q", exh, 0x1C8, title_id)
    struct.pack_into("<Q", exh, 0x200, title_id)
    struct.pack_into("<Q", exh, 0x600, title_id if ad_has_title_id else 0x00040000FFFFFF00)
    exefs = ncch.build_exefs([(".code", b"EMULATOR"), ("logo", b"L" * 0x2000)])
    cxi = ncch.build_ncch(title_id=title_id, product_code="SNES-P-TEST", exheader=bytes(exh),
                          exefs=exefs, romfs=romfs.build(FILES))
    path = tmp_path / "donor.cxi"
    path.write_bytes(cxi)
    return path


def test_template_extract_and_build(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    template = forwarder.install_from(fake_inject(tmp_path))
    assert template.kind == "snes" and template.rom_name == "/rom.sfc"
    assert "/rom.sfc" not in template.files
    assert forwarder.installed_kinds() == ["snes"]

    loaded = forwarder.ForwarderTemplate.load("snes")
    new_id = 0x0004000004B5A200
    exh, image, internal = loaded.build(b"NEW ROM", ".smc", new_id, "Mega Man X")
    files = romfs.read(image)
    assert internal == "snes_4b5a2_mega_man_x"
    assert files["/rom.smc"] == b"NEW ROM" and "/rom.sfc" not in files
    assert files["/rom_path.txt"] == b"romfs:/rom.smc"
    assert files["/internal_name.txt"] == internal.encode()
    assert b"snes_4b5a2_mega_man_x/s" in files["/retroarch.cfg"]
    assert files["/shader/a.shbin"] == FILES["/shader/a.shbin"]
    for offset in (0x1C8, 0x200, 0x600):
        assert struct.unpack_from("<Q", exh, offset)[0] == new_id


def test_signed_access_descriptor_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    template = forwarder.ForwarderTemplate.from_inject(fake_inject(tmp_path, ad_has_title_id=False))
    exh, _image, _name = template.build(b"R", ".sfc", 0x000400000F000100, "X")
    assert struct.unpack_from("<Q", exh, 0x600)[0] == 0x00040000FFFFFF00


def test_missing_template_message(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(InjectError, match="--forwarder-donor"):
        forwarder.ForwarderTemplate.load("gen")


def test_detect_system_from_titles(tmp_path):
    from vcinject import vcbanner

    def parts(files=None, code=b"X"):
        exefs = ncch.build_exefs([(".code", code)])
        image = romfs.build(files) if files else b""
        return ncch.parse_ncch(ncch.build_ncch(title_id=1, product_code="T", exheader=bytes(0x800),
                                               exefs=exefs, romfs=image))

    assert vcbanner.detect_system(parts(code=b"\0" * 0x300 + b".CAA" + b"\0" * 12)) == "gba"
    assert vcbanner.detect_system(parts({"/rom/NESYM0A.070": b"TNES" + b"\0" * 64})) == "nes"
    gb = bytearray(0x200)
    gb[0x104:0x108] = b"\xce\xed\x66\x66"
    assert vcbanner.detect_system(parts({"/rom/rom.bin": bytes(gb)})) == "gb"
    gb[0x143] = 0x80
    assert vcbanner.detect_system(parts({"/rom/rom.bin": bytes(gb)})) == "gbc"
    assert vcbanner.detect_system(parts({"/internal_name.txt": b"gen_1_x", "/rom.md": b"r"})) == "gen"
    assert vcbanner.detect_system(parts({"/other.bin": b"?"})) is None

import hashlib
import struct

import pytest
from PIL import Image

from vcinject import ncch, smdh, template
from vcinject.errors import InjectError

TITLE_ID = 0x000400000F012300


def fake_exheader():
    exh = bytearray(0x800)
    exh[0x400:0x500] = b"\xAA" * 0x100  # stands in for the signed access descriptor
    return bytes(exh)


def test_exefs_layout():
    exefs = ncch.build_exefs([(".code", b"A" * 0x201), ("icon", b"B" * 3)])
    assert struct.unpack_from("<8sII", exefs, 0) == (b".code\0\0\0", 0, 0x201)
    assert struct.unpack_from("<8sII", exefs, 0x10) == (b"icon\0\0\0\0", 0x400, 3)
    assert exefs[0xC0 + 9 * 0x20:0xC0 + 10 * 0x20] == hashlib.sha256(b"A" * 0x201).digest()
    assert exefs[0xC0 + 8 * 0x20:0xC0 + 9 * 0x20] == hashlib.sha256(b"B" * 3).digest()
    assert len(exefs) == 0x200 + 0x400 + 0x200


def test_patch_exheader_keeps_access_descriptor():
    exh = fake_exheader()
    out = ncch.patch_exheader(exh, TITLE_ID, "ABCD")
    assert out[:8] == b"ABCD\0\0\0\0"
    assert struct.unpack_from("<Q", out, 0x1C8)[0] == TITLE_ID
    assert struct.unpack_from("<Q", out, 0x200)[0] == TITLE_ID
    assert out[0x400:] == exh[0x400:]


def test_ncch_roundtrip_and_hashes():
    exh = ncch.patch_exheader(fake_exheader(), TITLE_ID, "ABCD")
    code = b"\x11" * 0x300 + b".CAA" + b"\0" * 12
    exefs = ncch.build_exefs([(".code", code), ("logo", b"L" * 0x2000)])
    romfs = b"IVFC" + b"\0" * 0x3FFC
    cxi = ncch.build_ncch(title_id=TITLE_ID, product_code="CTR-N-ABCD", exheader=exh,
                          exefs=exefs, romfs=romfs)

    assert cxi[0x160:0x180] == hashlib.sha256(exh[:0x400]).digest()
    exefs_off = struct.unpack_from("<I", cxi, 0x1A0)[0] * 0x200
    assert cxi[0x1C0:0x1E0] == hashlib.sha256(cxi[exefs_off:exefs_off + 0x200]).digest()
    romfs_off = struct.unpack_from("<I", cxi, 0x1B0)[0] * 0x200
    assert romfs_off % 0x1000 == 0
    assert cxi[0x1E0:0x200] == hashlib.sha256(cxi[romfs_off:romfs_off + 0x200]).digest()

    parts = ncch.parse_ncch(cxi)
    assert parts.title_id == TITLE_ID
    assert parts.product_code == "CTR-N-ABCD"
    assert parts.exheader == exh
    assert parts.exefs_files[".code"] == code
    assert parts.romfs == romfs


def test_donor_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    exh = fake_exheader()
    code = b"\x11" * 0x300 + b".CAA" + b"\0" * 12
    exefs = ncch.build_exefs([(".code", code), ("logo", b"L" * 0x2000)])
    cxi = ncch.build_ncch(title_id=TITLE_ID, product_code="CTR-N-ABCD", exheader=exh,
                          exefs=exefs, romfs=b"IVFC" + b"\0" * 0x1FC)
    donor = tmp_path / "donor.cxi"
    donor.write_bytes(cxi)

    extracted = template.from_donor(donor)
    assert extracted.exheader == exh
    assert extracted.logo == b"L" * 0x2000
    assert template.is_installed()
    assert template.GbaTemplate.load() == extracted


def test_donor_must_be_gba_vc(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    exefs = ncch.build_exefs([(".code", b"\0" * 0x100), ("logo", b"L")])
    cxi = ncch.build_ncch(title_id=TITLE_ID, product_code="X", exheader=fake_exheader(),
                          exefs=exefs, romfs=b"")
    donor = tmp_path / "donor.cxi"
    donor.write_bytes(cxi)
    with pytest.raises(InjectError, match="not a GBA Virtual Console"):
        template.from_donor(donor)


def test_smdh_layout():
    data = smdh.build_smdh("Short", "Long", "Pub", Image.new("RGB", (100, 60), (255, 0, 0)))
    assert len(data) == 0x36C0 and data[:4] == b"SMDH"
    for lang in range(16):
        base = 0x8 + lang * 0x200
        assert data[base:base + 10] == "Short".encode("utf-16-le")
        assert data[base + 0x80:base + 0x88] == "Long".encode("utf-16-le")
    # pure red in RGB565
    assert data[0x24C0:0x24C2] == struct.pack("<H", 0xF800)
    with pytest.raises(InjectError):
        smdh.build_smdh("x" * 64, "", "", Image.new("RGB", (48, 48)))


def test_morton_order_covers_tile():
    coords = {smdh._morton_xy(i) for i in range(64)}
    assert coords == {(x, y) for x in range(8) for y in range(8)}
    assert [smdh._morton_xy(i) for i in (0, 1, 2, 4, 16)] == [(0, 0), (1, 0), (0, 1), (2, 0), (4, 0)]


def test_patch_exheader_sets_save_size():
    out = ncch.patch_exheader(fake_exheader(), TITLE_ID, "ABCD", save_data_size=0x40400)
    assert struct.unpack_from("<Q", out, 0x1C0)[0] == 0x40400

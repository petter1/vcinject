import struct

import pytest

from conftest import make_rom
from vcinject import builder, ncch, tools
from vcinject.errors import InjectError
from vcinject.gba import GbaRom, SaveType
from vcinject.template import GbaTemplate


def test_title_id_helpers(rom_bytes):
    rom = GbaRom.parse(rom_bytes)
    tid = builder.default_title_id(rom)
    assert tid >> 32 == 0x00040000
    assert 0xF0000 <= (tid >> 8) & 0xFFFFF < 0xF8000
    assert builder.default_title_id(rom) == tid  # stable
    assert builder.parse_title_id("0x00040000F0123400") == 0x00040000F0123400
    with pytest.raises(InjectError):
        builder.parse_title_id("0004000E00012300")
    with pytest.raises(InjectError):
        builder.parse_title_id("1234")
    assert builder.default_product_code(rom) == "CTR-N-BTSE"


@pytest.mark.skipif(not all(tools.find_tool(t) for t in tools.TOOLS),
                    reason="makerom/bannertool not built")
def test_full_build(tmp_path):
    rom_path = tmp_path / "My Game.gba"
    rom_path.write_bytes(make_rom(save_string=b"FLASH1M_V103"))
    exheader = bytearray(0x800)
    tmpl = GbaTemplate(exheader=bytes(exheader), romfs=b"IVFC" + b"\0" * 0x3FFC, logo=b"L" * 0x2000)

    lines = []
    options = builder.GbaInjectOptions(rom_path=rom_path, keep_cxi=True, online_artwork=False)
    result = builder.build_gba(options, template=tmpl, log=lines.append)

    assert result.output_path == tmp_path / "My Game.cia"
    assert result.output_path.stat().st_size > 0
    assert result.save_type == SaveType.FLASH_1M_MACRONIX
    parts = ncch.parse_ncch((tmp_path / "My Game.cxi").read_bytes())
    assert set(parts.exefs_files) == {".code", "banner", "icon", "logo"}
    assert struct.unpack_from("<I", parts.exefs_files[".code"], 0x40008)[0] == 0xB
    assert any("Done" in line for line in lines)

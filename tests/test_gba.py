import hashlib
import struct

import pytest

from conftest import make_rom
from vcinject.errors import InjectError
from vcinject.gba import FOOTER_SIZE, GbaRom, SaveType, build_code_bin, build_footer, build_video_lut

# SHA-256 of footers taken from official GBA VC titles (4 MiB ROM), which
# build_footer reproduces byte for byte.
OFFICIAL_FOOTERS = [
    (SaveType.SRAM_256K, 0x90, "c900e2cb0f713dc5"),
    (SaveType.FLASH_512K_PANASONIC, 0x90, "6398430a0107b3d3"),
    (SaveType.EEPROM_64K, 0x80, "7dcfafea074d5e6c"),
]


@pytest.mark.parametrize("save, ghosting, digest", OFFICIAL_FOOTERS)
def test_footer_matches_official(save, ghosting, digest):
    footer = build_footer(0x400000, save, ghosting=ghosting, brightness=0.7)
    assert hashlib.sha256(footer).hexdigest().startswith(digest)


def test_footer_structure():
    footer = build_footer(0x1230, SaveType.FLASH_1M_MACRONIX_RTC)
    assert len(footer) == FOOTER_SIZE
    magic, version, desc_offset, count = struct.unpack_from("<4sIII", footer, 0x350)
    assert (magic, version, desc_offset, count) == (b".CAA", 1, 0x1230 + 0x330, 2 << 4)
    assert struct.unpack_from("<4I", footer, 0x330) == (0, 0, 0x1230, 0)
    assert struct.unpack_from("<4I", footer, 0x340) == (1, 0x1230, 0x324, 0)
    assert struct.unpack_from("<II", footer, 4) == (0x1230, 0xA)
    # 1 Mbit flash uses the "large" timing set
    assert struct.unpack_from("<I", footer, 0x10)[0] == 0x17D43E


def test_lut_brightness():
    assert build_video_lut(1.0)[-3:] == b"\xff\xff\xff"
    assert build_video_lut(0.7)[-1] == 178
    assert build_video_lut(0.6)[-1] == 153
    with pytest.raises(InjectError):
        build_video_lut(1.5)


def test_code_bin_is_padded_rom_plus_footer():
    rom = GbaRom.parse(make_rom(size=0x40001))
    code = build_code_bin(rom, SaveType.NONE)
    assert code[:0x40001] == rom.data
    assert len(code) == 0x40010 + FOOTER_SIZE
    assert code[-0x10:-0xC] == b".CAA"


def test_header_parsing(rom_bytes):
    rom = GbaRom.parse(rom_bytes)
    assert (rom.title, rom.game_code, rom.maker_code) == ("TESTGAME", "BTSE", "01")
    assert rom.header_valid
    bad = bytearray(rom_bytes)
    bad[0xBD] ^= 0xFF
    assert not GbaRom.parse(bytes(bad)).header_valid


def test_rejects_tiny_and_huge_files():
    with pytest.raises(InjectError):
        GbaRom.parse(b"\0" * 0x10)
    with pytest.raises(InjectError):
        GbaRom.parse(b"\0" * (32 * 1024 * 1024 + 1))


@pytest.mark.parametrize("string, extra, size, expected", [
    (b"SRAM_V113", b"", 0x40000, SaveType.SRAM_256K),
    (b"SRAM_F_V100", b"", 0x40000, SaveType.SRAM_256K),
    (b"EEPROM_V124", b"", 0x40000, SaveType.EEPROM_64K),
    (b"EEPROM_V122", b"", 0x40000, SaveType.EEPROM_8K),
    (b"EEPROM_V124", b"", 0x1000001, SaveType.EEPROM_64K_LARGE_ROM),
    (b"FLASH_V126", b"", 0x40000, SaveType.FLASH_512K_PANASONIC),
    (b"FLASH512_V131", b"SIIRTC_V001", 0x40000, SaveType.FLASH_512K_PANASONIC_RTC),
    (b"FLASH1M_V103", b"", 0x40000, SaveType.FLASH_1M_MACRONIX),
    (b"FLASH1M_V103", b"SIIRTC_V001", 0x40000, SaveType.FLASH_1M_MACRONIX_RTC),
    (b"", b"", 0x40000, SaveType.NONE),
])
def test_save_detection(string, extra, size, expected):
    rom = GbaRom.parse(make_rom(size=size, save_string=string, extra=extra))
    assert rom.detect_save_type() == expected


def test_homebrew_save_override():
    rom = GbaRom.parse(make_rom(game_code=b"2ABC", save_string=b"FLASH1M_V103"))
    assert rom.detect_save_type() == SaveType.SRAM_256K


def test_sd_save_size_fits_two_agbsave_slots():
    # official titles (<= 512 Kbit) use the AGBSAVE partition size
    assert SaveType.FLASH_512K_PANASONIC.sd_save_size == 0x30000
    assert SaveType.SRAM_256K.sd_save_size == 0x30000
    # 1 Mbit flash needs 2 * (0x200 header + 128 KiB); a smaller file loses the save
    assert SaveType.FLASH_1M_MACRONIX_RTC.sd_save_size == 0x40400

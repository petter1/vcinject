"""GBA ROM inspection and AGB_FIRM footer construction.

AGB_FIRM expects ExeFS:/.code to contain the GBA ROM followed by a 0x360 byte
footer (see https://www.3dbrew.org/wiki/3DS_Virtual_Console#GBA_VC):

    rom (padded)                      offset 0
    AGB ROM metadata   (0x324)        offset rom_size
    padding            (0x00C)
    config descriptors (2 * 0x10)     type 0 = ROM, type 1 = metadata
    ".CAA" header      (0x010)        points at the descriptor array
"""
from __future__ import annotations

import enum
import re
import struct
from dataclasses import dataclass

from .errors import InjectError

MAX_ROM_SIZE = 32 * 1024 * 1024
FOOTER_SIZE = 0x360
METADATA_SIZE = 0x324
_DESCRIPTORS_OFFSET = 0x330  # within the footer
_CAA_OFFSET = 0x350

DEFAULT_GHOSTING = 0x80
DEFAULT_BRIGHTNESS = 1.0  # full brightness; official GBA VC titles use 0.7


class SaveType(enum.IntEnum):
    EEPROM_8K = 0x0
    EEPROM_8K_LARGE_ROM = 0x1
    EEPROM_64K = 0x2
    EEPROM_64K_LARGE_ROM = 0x3
    FLASH_512K_ATMEL_RTC = 0x4
    FLASH_512K_ATMEL = 0x5
    FLASH_512K_SST_RTC = 0x6
    FLASH_512K_SST = 0x7
    FLASH_512K_PANASONIC_RTC = 0x8
    FLASH_512K_PANASONIC = 0x9
    FLASH_1M_MACRONIX_RTC = 0xA
    FLASH_1M_MACRONIX = 0xB
    FLASH_1M_SANYO_RTC = 0xC
    FLASH_1M_SANYO = 0xD
    SRAM_256K = 0xE
    NONE = 0xF

    @property
    def label(self) -> str:
        return _SAVE_LABELS[self]

    @property
    def has_rtc(self) -> bool:
        return self.name.endswith("_RTC")

    @property
    def save_bytes(self) -> int:
        """Size of the emulated save chip (matches libn3ds' sizeShiftLut)."""
        return _SAVE_BYTES[self]

    @property
    def sd_save_size(self) -> int:
        """Size of the title's SD save file (ExtHeader/TMD SaveDataSize).

        NATIVE_FIRM keeps two slots of AGBSAVE header (0x200) + save there and
        drops the save if the file has any other size, so 1 Mbit games need
        more than the 0x30000 official (<= 512 Kbit) titles use.
        """
        return max(AGBSAVE_PARTITION_SIZE, 2 * (0x200 + self.save_bytes))


AGBSAVE_PARTITION_SIZE = 0x30000

_SAVE_LABELS = {
    SaveType.EEPROM_8K: "EEPROM 4 Kbit",
    SaveType.EEPROM_8K_LARGE_ROM: "EEPROM 4 Kbit (ROM > 16 MiB)",
    SaveType.EEPROM_64K: "EEPROM 64 Kbit",
    SaveType.EEPROM_64K_LARGE_ROM: "EEPROM 64 Kbit (ROM > 16 MiB)",
    SaveType.FLASH_512K_ATMEL_RTC: "Flash 512 Kbit Atmel + RTC",
    SaveType.FLASH_512K_ATMEL: "Flash 512 Kbit Atmel",
    SaveType.FLASH_512K_SST_RTC: "Flash 512 Kbit SST + RTC",
    SaveType.FLASH_512K_SST: "Flash 512 Kbit SST",
    SaveType.FLASH_512K_PANASONIC_RTC: "Flash 512 Kbit Panasonic + RTC",
    SaveType.FLASH_512K_PANASONIC: "Flash 512 Kbit Panasonic",
    SaveType.FLASH_1M_MACRONIX_RTC: "Flash 1 Mbit Macronix + RTC",
    SaveType.FLASH_1M_MACRONIX: "Flash 1 Mbit Macronix",
    SaveType.FLASH_1M_SANYO_RTC: "Flash 1 Mbit Sanyo + RTC",
    SaveType.FLASH_1M_SANYO: "Flash 1 Mbit Sanyo",
    SaveType.SRAM_256K: "SRAM/FRAM 256 Kbit",
    SaveType.NONE: "No save",
}

_SAVE_BYTES = {
    **{t: 0x200 for t in (SaveType.EEPROM_8K, SaveType.EEPROM_8K_LARGE_ROM)},
    **{t: 0x2000 for t in (SaveType.EEPROM_64K, SaveType.EEPROM_64K_LARGE_ROM)},
    **{t: 0x10000 for t in (SaveType.FLASH_512K_ATMEL_RTC, SaveType.FLASH_512K_ATMEL,
                            SaveType.FLASH_512K_SST_RTC, SaveType.FLASH_512K_SST,
                            SaveType.FLASH_512K_PANASONIC_RTC, SaveType.FLASH_512K_PANASONIC)},
    **{t: 0x20000 for t in (SaveType.FLASH_1M_MACRONIX_RTC, SaveType.FLASH_1M_MACRONIX,
                            SaveType.FLASH_1M_SANYO_RTC, SaveType.FLASH_1M_SANYO)},
    SaveType.SRAM_256K: 0x8000,
    SaveType.NONE: 0,
}

# Short names accepted on the command line.
SAVE_ALIASES = {
    "auto": None,
    "none": SaveType.NONE,
    "sram": SaveType.SRAM_256K,
    "eeprom4k": SaveType.EEPROM_8K,
    "eeprom64k": SaveType.EEPROM_64K,
    "flash512": SaveType.FLASH_512K_PANASONIC,
    "flash512-rtc": SaveType.FLASH_512K_PANASONIC_RTC,
    "flash1m": SaveType.FLASH_1M_MACRONIX,
    "flash1m-rtc": SaveType.FLASH_1M_MACRONIX_RTC,
}

# Emulated save chip timings written to the footer at 0x10: flash chip erase,
# flash sector erase, flash program and EEPROM write cycles. Official titles
# use one set for 512 Kbit flash / 4 Kbit EEPROM and one for 1 Mbit flash /
# 64 Kbit EEPROM (values match libn3ds' lgy9.c).
_TIMING_512K_4K = (0x27C886, 0x8CE35, 0x184, 0x31170)
_TIMING_1M_64K = (0x17D43E, 0x26206, 0x86, 0x2DD13)
_LARGE_TIMING_TYPES = {
    SaveType.EEPROM_64K,
    SaveType.EEPROM_64K_LARGE_ROM,
    SaveType.FLASH_1M_MACRONIX_RTC,
    SaveType.FLASH_1M_MACRONIX,
    SaveType.FLASH_1M_SANYO_RTC,
    SaveType.FLASH_1M_SANYO,
}

# The Nintendo SDK links a library ID string such as "FLASH1M_V103" into every
# game that uses a save chip. The EEPROM size can't be told apart from the
# string alone, so use the size common for each library version.
_SDK_SAVE_RE = re.compile(rb"(EEPROM|SRAM_F|SRAM|FLASH1M|FLASH512|FLASH)_V(\d{3})")
_EEPROM_64K_VERSIONS = {b"121", b"124"}
_RTC_MARKER = b"SIIRTC_V"

# Homebrew save overrides keyed by the first game code character, following
# the conventions used by open_agb_firm and modern homebrew toolchains.
_HOMEBREW_OVERRIDES = {
    "1": SaveType.EEPROM_64K,
    "2": SaveType.SRAM_256K,
    "3": SaveType.FLASH_512K_PANASONIC_RTC,
    "4": SaveType.FLASH_1M_MACRONIX_RTC,
    "F": SaveType.EEPROM_8K,  # Classic NES Series
    "S": SaveType.SRAM_256K,  # Butano
}


@dataclass(frozen=True)
class GbaRom:
    data: bytes
    title: str
    game_code: str
    maker_code: str
    version: int
    header_valid: bool

    @classmethod
    def parse(cls, data: bytes) -> "GbaRom":
        if len(data) < 0xC0:
            raise InjectError("File is too small to be a GBA ROM.")
        if len(data) > MAX_ROM_SIZE:
            raise InjectError("GBA ROMs can be at most 32 MiB.")
        checksum = (-sum(data[0xA0:0xBD]) - 0x19) & 0xFF
        return cls(
            data=data,
            title=_ascii(data[0xA0:0xAC]),
            game_code=_ascii(data[0xAC:0xB0]),
            maker_code=_ascii(data[0xB0:0xB2]),
            version=data[0xBC],
            header_valid=data[0xB2] == 0x96 and data[0xBD] == checksum,
        )

    def detect_save_type(self) -> SaveType:
        override = _HOMEBREW_OVERRIDES.get(self.game_code[:1])
        if override is not None:
            return self._adjust_for_rom_size(override)

        match = _SDK_SAVE_RE.search(self.data)
        if match is None:
            return SaveType.NONE
        kind, version = match.groups()
        has_rtc = _RTC_MARKER in self.data
        if kind == b"EEPROM":
            save = SaveType.EEPROM_64K if version in _EEPROM_64K_VERSIONS else SaveType.EEPROM_8K
        elif kind in (b"SRAM", b"SRAM_F"):
            save = SaveType.SRAM_256K
        elif kind == b"FLASH1M":
            save = SaveType.FLASH_1M_MACRONIX_RTC if has_rtc else SaveType.FLASH_1M_MACRONIX
        else:
            save = SaveType.FLASH_512K_PANASONIC_RTC if has_rtc else SaveType.FLASH_512K_PANASONIC
        return self._adjust_for_rom_size(save)

    def _adjust_for_rom_size(self, save: SaveType) -> SaveType:
        # EEPROM is addressed differently on carts with more than 16 MiB of ROM.
        if len(self.data) > 0x1000000 and save in (SaveType.EEPROM_8K, SaveType.EEPROM_64K):
            return SaveType(save + 1)
        return save


def _ascii(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()


def build_video_lut(brightness: float = DEFAULT_BRIGHTNESS) -> bytes:
    """Per-channel output LUT (RGB interleaved, 256 entries).

    Official titles use floor(i * 0.7), which darkens the image to mimic the
    original GBA screen; 1.0 gives the full 3DS brightness.
    """
    if not 0.1 <= brightness <= 1.0:
        raise InjectError("Brightness must be between 0.1 and 1.0.")
    scale = round(brightness * 1000)
    out = bytearray()
    for i in range(256):
        value = i * scale // 1000
        out += bytes((value, value, value))
    return bytes(out)


def build_footer(rom_size: int, save_type: SaveType, *, ghosting: int = DEFAULT_GHOSTING,
                 brightness: float = DEFAULT_BRIGHTNESS) -> bytes:
    """Build the 0x360 byte footer for a ROM padded to ``rom_size`` bytes."""
    if not 1 <= ghosting <= 0xFF:
        raise InjectError("Ghosting must be between 1 and 255.")
    timing = _TIMING_1M_64K if save_type in _LARGE_TIMING_TYPES else _TIMING_512K_4K

    footer = bytearray(FOOTER_SIZE)
    struct.pack_into("<IIIHH", footer, 0x00, 0, rom_size, save_type, 0xFFFF, 0)
    struct.pack_into("<4I", footer, 0x10, *timing)
    struct.pack_into("<I", footer, 0x20, ghosting)
    footer[0x24:METADATA_SIZE] = build_video_lut(brightness)

    struct.pack_into("<4I", footer, _DESCRIPTORS_OFFSET, 0, 0, rom_size, 0)
    struct.pack_into("<4I", footer, _DESCRIPTORS_OFFSET + 0x10, 1, rom_size, METADATA_SIZE, 0)
    struct.pack_into("<4sIII", footer, _CAA_OFFSET, b".CAA", 1,
                     rom_size + _DESCRIPTORS_OFFSET, 2 << 4)
    return bytes(footer)


def build_code_bin(rom: GbaRom, save_type: SaveType, *, ghosting: int = DEFAULT_GHOSTING,
                   brightness: float = DEFAULT_BRIGHTNESS) -> bytes:
    """ExeFS:/.code for AGB_FIRM: the ROM padded to 16 bytes plus the footer."""
    padded = rom.data + b"\0" * (-len(rom.data) % 0x10)
    return padded + build_footer(len(padded), save_type, ghosting=ghosting, brightness=brightness)

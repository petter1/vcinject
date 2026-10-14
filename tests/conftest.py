import pytest


def make_rom(size=0x40000, game_code=b"BTSE", save_string=b"", extra=b""):
    data = bytearray(size)
    data[0xA0:0xAC] = b"TESTGAME".ljust(12, b"\0")
    data[0xAC:0xB0] = game_code
    data[0xB0:0xB2] = b"01"
    data[0xB2] = 0x96
    data[0xBD] = (-sum(data[0xA0:0xBD]) - 0x19) & 0xFF
    blob = save_string + b"\0\0\0\0" + extra
    data[0x1000:0x1000 + len(blob)] = blob
    return bytes(data)


@pytest.fixture
def rom_bytes():
    return make_rom(save_string=b"SRAM_V113")

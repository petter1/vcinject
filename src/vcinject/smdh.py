"""SMDH (ExeFS:/icon) generation: titles, settings and the two icons."""
from __future__ import annotations

import struct

from PIL import Image

from .errors import InjectError

SMDH_SIZE = 0x36C0
_LANGUAGES = 16
_SHORT_LEN, _LONG_LEN, _PUBLISHER_LEN = 0x40, 0x80, 0x40  # UTF-16 code units

# SMDH settings flags
_FLAG_VISIBLE = 0x0001
_FLAG_AUTO_SAVE_ON_EXIT = 0x0010
_FLAG_USES_SAVE_DATA = 0x0080
_FLAG_RECORD_USAGE = 0x0100
_REGION_FREE = 0x7FFFFFFF


def build_smdh(short_title: str, long_title: str, publisher: str, icon: Image.Image) -> bytes:
    out = bytearray(SMDH_SIZE)
    struct.pack_into("<4sHH", out, 0, b"SMDH", 0, 0)

    entry = (_utf16(short_title, _SHORT_LEN, "Title")
             + _utf16(long_title, _LONG_LEN, "Long title")
             + _utf16(publisher, _PUBLISHER_LEN, "Publisher"))
    for lang in range(_LANGUAGES):
        out[0x8 + lang * 0x200:0x8 + (lang + 1) * 0x200] = entry

    flags = _FLAG_VISIBLE | _FLAG_AUTO_SAVE_ON_EXIT | _FLAG_USES_SAVE_DATA | _FLAG_RECORD_USAGE
    struct.pack_into("<I", out, 0x2018, _REGION_FREE)
    struct.pack_into("<I", out, 0x2028, flags)

    out[0x2040:0x24C0] = encode_icon(icon, 24)
    out[0x24C0:0x36C0] = encode_icon(icon, 48)
    return bytes(out)


LANGUAGES = ("ja", "en", "fr", "de", "it", "es", "zh", "ko", "nl", "pt", "ru", "zh-tw")


def read_titles(smdh: bytes, language: str = "en") -> tuple[str, str, str]:
    """Short title, long title and publisher; English when ``language`` has none."""
    if len(smdh) < SMDH_SIZE or smdh[:4] != b"SMDH":
        raise InjectError("Not an SMDH.")

    def entry(index: int) -> tuple[str, str, str]:
        base = 0x8 + index * 0x200
        fields = ((base, _SHORT_LEN), (base + 0x80, _LONG_LEN), (base + 0x180, _PUBLISHER_LEN))
        return tuple(smdh[start:start + units * 2].decode("utf-16-le", "replace").split("\0", 1)[0]
                     .strip() for start, units in fields)

    index = LANGUAGES.index(language) if language in LANGUAGES else 1
    found = entry(index)
    return found if found[0] else entry(1)


def read_icon(smdh: bytes, size: int = 48) -> Image.Image:
    """The large (48) or small (24) icon of an SMDH."""
    start = 0x24C0 if size == 48 else 0x2040
    img = Image.new("RGB", (size, size))
    px = img.load()
    pos = start
    for tile_y in range(0, size, 8):
        for tile_x in range(0, size, 8):
            for i in range(64):
                x, y = _morton_xy(i)
                value = struct.unpack_from("<H", smdh, pos)[0]
                pos += 2
                r, g, b = value >> 11, (value >> 5) & 0x3F, value & 0x1F
                px[tile_x + x, tile_y + y] = ((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                                              (b << 3) | (b >> 2))
    return img


def _utf16(text: str, units: int, field: str) -> bytes:
    raw = text.encode("utf-16-le")
    if len(raw) > (units - 1) * 2:
        raise InjectError(f"{field} is too long (max {units - 1} characters).")
    return raw.ljust(units * 2, b"\0")


def encode_icon(image: Image.Image, size: int) -> bytes:
    """Encode an image as tiled, Morton-ordered RGB565 like the 3DS GPU expects."""
    img = fit_square(image, size)
    px = img.load()
    out = bytearray()
    for tile_y in range(0, size, 8):
        for tile_x in range(0, size, 8):
            for i in range(64):
                x, y = _morton_xy(i)
                r, g, b = px[tile_x + x, tile_y + y]
                out += struct.pack("<H", ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    return bytes(out)


def _morton_xy(i: int) -> tuple[int, int]:
    x = (i & 1) | ((i >> 1) & 2) | ((i >> 2) & 4)
    y = ((i >> 1) & 1) | ((i >> 2) & 2) | ((i >> 3) & 4)
    return x, y


def fit_square(image: Image.Image, size: int) -> Image.Image:
    """Center-crop to a square, resize, and flatten transparency onto white."""
    img = image.convert("RGBA")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, img).convert("RGB")

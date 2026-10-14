"""Just enough CBMD/CGFX handling to swap textures in an existing 3D banner.

A banner (ExeFS:/banner) is a CBMD container: one LZ11-compressed common CGFX,
up to 13 optional per-language CGFX and a BCWAV jingle at the end. We never
touch models or animations, we only replace the pixel data of named textures
in place (same size and format), so any 3D banner can serve as a template.

References: https://www.3dbrew.org/wiki/CBMD, /wiki/CGFX, /wiki/LZ11
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from PIL import Image

from .errors import InjectError

# CBMD language slots (index into the 13 optional offsets).
REGIONS = ("EUR-en", "EUR-fr", "EUR-de", "EUR-it", "EUR-es", "EUR-nl", "EUR-pt", "EUR-ru",
           "JPN-ja", "USA-en", "USA-fr", "USA-es", "USA-pt")

# PICA200 texture formats we can write.
FMT_RGBA8, FMT_RGB8, FMT_RGBA5551, FMT_RGB565, FMT_RGBA4, FMT_LA8 = 0, 1, 2, 3, 4, 5
_BYTES_PER_PIXEL = {FMT_RGBA8: 4, FMT_RGB8: 3, FMT_RGBA5551: 2, FMT_RGB565: 2, FMT_RGBA4: 2,
                    FMT_LA8: 2}


# --- LZ11 ----------------------------------------------------------------------

def lz11_decompress(data: bytes) -> bytes:
    if not data or data[0] != 0x11:
        raise InjectError("Not LZ11 data.")
    size = data[1] | data[2] << 8 | data[3] << 16
    pos = 4
    if size == 0:
        size = struct.unpack_from("<I", data, 4)[0]
        pos = 8
    out = bytearray()
    while len(out) < size:
        flags = data[pos]
        pos += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if not flags & (0x80 >> bit):
                out.append(data[pos])
                pos += 1
                continue
            b0 = data[pos]
            indicator = b0 >> 4
            if indicator == 0:
                length = ((b0 & 0xF) << 4 | data[pos + 1] >> 4) + 0x11
                disp = ((data[pos + 1] & 0xF) << 8 | data[pos + 2]) + 1
                pos += 3
            elif indicator == 1:
                length = ((b0 & 0xF) << 12 | data[pos + 1] << 4 | data[pos + 2] >> 4) + 0x111
                disp = ((data[pos + 2] & 0xF) << 8 | data[pos + 3]) + 1
                pos += 4
            else:
                length = indicator + 1
                disp = ((b0 & 0xF) << 8 | data[pos + 1]) + 1
                pos += 2
            start = len(out) - disp
            for i in range(length):  # byte-wise: matches may overlap themselves
                out.append(out[start + i])
    return bytes(out)


def lz11_compress(data: bytes) -> bytes:
    """Greedy LZ11 with a hash chain over 3-byte prefixes (window 4 KiB)."""
    size = len(data)
    out = bytearray([0x11, size & 0xFF, size >> 8 & 0xFF, size >> 16 & 0xFF]) if size < 1 << 24 \
        else bytearray([0x11, 0, 0, 0]) + struct.pack("<I", size)
    chains: dict[bytes, list[int]] = {}
    pos = 0
    while pos < size:
        flag_index = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if pos >= size:
                break
            best_len, best_disp = 0, 0
            key = data[pos:pos + 3]
            candidates = chains.get(key, ()) if len(key) == 3 else ()
            max_len = min(0x10110, size - pos)
            for cand in reversed(candidates[-48:]):
                disp = pos - cand
                if disp > 0x1000:
                    break
                length = 3
                while length < max_len and data[cand + length] == data[pos + length]:
                    length += 1
                if length > best_len:
                    best_len, best_disp = length, disp
                    if length == max_len:
                        break
            step = best_len if best_len >= 3 else 1
            for p in range(pos, min(pos + step, size - 2)):
                chains.setdefault(data[p:p + 3], []).append(p)
            if best_len >= 3:
                flags |= 0x80 >> bit
                d = best_disp - 1
                if best_len <= 0x10:
                    out += bytes([(best_len - 1) << 4 | d >> 8, d & 0xFF])
                elif best_len <= 0x110:
                    n = best_len - 0x11
                    out += bytes([n >> 4, (n & 0xF) << 4 | d >> 8, d & 0xFF])
                else:
                    n = best_len - 0x111
                    out += bytes([0x10 | n >> 12, n >> 4 & 0xFF, (n & 0xF) << 4 | d >> 8, d & 0xFF])
                pos += best_len
            else:
                out.append(data[pos])
                pos += 1
        out[flag_index] = flags
    return bytes(out + b"\0" * (-len(out) % 4))


# --- CBMD ------------------------------------------------------------------------

@dataclass
class Cbmd:
    common: bytes  # decompressed CGFX
    regions: dict[int, bytes] = field(default_factory=dict)  # index -> decompressed CGFX
    cwav: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "Cbmd":
        if data[:4] != b"CBMD":
            raise InjectError("Not a CBMD banner.")
        offsets = struct.unpack_from("<14I", data, 0x08)
        cwav_offset = struct.unpack_from("<I", data, 0x84)[0]
        bounds = sorted({o for o in offsets if o} | {cwav_offset or len(data)})

        def chunk(offset: int) -> bytes:
            end = next(b for b in bounds if b > offset)
            return lz11_decompress(data[offset:end])

        regions = {i: chunk(o) for i, o in enumerate(offsets[1:]) if o}
        return cls(chunk(offsets[0]), regions, data[cwav_offset:] if cwav_offset else b"")

    def build(self) -> bytes:
        header = bytearray(0x88)
        header[:4] = b"CBMD"
        body = bytearray()

        def add(cgfx: bytes) -> int:
            offset = 0x88 + len(body)
            body.extend(lz11_compress(cgfx))
            return offset

        struct.pack_into("<I", header, 0x08, add(self.common))
        for index, cgfx in sorted(self.regions.items()):
            struct.pack_into("<I", header, 0x0C + index * 4, add(cgfx))
        if self.cwav:
            body.extend(b"\0" * (-len(body) % 0x10))
            struct.pack_into("<I", header, 0x84, 0x88 + len(body))
            body.extend(self.cwav)
        return bytes(header + body)


# --- CGFX textures -----------------------------------------------------------------

@dataclass(frozen=True)
class TextureInfo:
    name: str
    width: int
    height: int
    format: int
    data_offset: int
    data_size: int


def _cstr(data: bytes, offset: int) -> str:
    return data[offset:data.index(b"\0", offset)].decode("ascii", errors="replace")


def textures(cgfx: bytes) -> dict[str, TextureInfo]:
    if cgfx[:4] != b"CGFX":
        raise InjectError("Not a CGFX file.")
    data_block = struct.unpack_from("<H", cgfx, 6)[0]
    if cgfx[data_block:data_block + 4] != b"DATA":
        raise InjectError("CGFX has no DATA block.")
    count, rel = struct.unpack_from("<Ii", cgfx, data_block + 0x10)  # dictionary #1: textures
    result: dict[str, TextureInfo] = {}
    if not count:
        return result
    dict_offset = data_block + 0x14 + rel
    if cgfx[dict_offset:dict_offset + 4] != b"DICT":
        raise InjectError("Malformed CGFX texture dictionary.")
    for i in range(struct.unpack_from("<I", cgfx, dict_offset + 8)[0]):
        entry = dict_offset + 0x1C + i * 0x10
        name_rel, data_rel = struct.unpack_from("<ii", cgfx, entry + 8)
        name = _cstr(cgfx, entry + 8 + name_rel)
        txob = entry + 12 + data_rel
        if cgfx[txob + 4:txob + 8] != b"TXOB":
            continue
        height, width = struct.unpack_from("<II", cgfx, txob + 0x18)
        fmt = struct.unpack_from("<I", cgfx, txob + 0x34)[0]
        size, pixels_rel = struct.unpack_from("<Ii", cgfx, txob + 0x44)
        result[name] = TextureInfo(name, width, height, fmt, txob + 0x48 + pixels_rel, size)
    return result


def _morton(i: int) -> tuple[int, int]:
    x = (i & 1) | ((i >> 1) & 2) | ((i >> 2) & 4)
    y = ((i >> 1) & 1) | ((i >> 2) & 2) | ((i >> 3) & 4)
    return x, y


_MORTON = [_morton(i) for i in range(64)]


def _encode_pixel(fmt: int, r: int, g: int, b: int, a: int) -> bytes:
    if fmt == FMT_RGB565:
        return struct.pack("<H", (r >> 3) << 11 | (g >> 2) << 5 | b >> 3)
    if fmt == FMT_LA8:
        return bytes((a, (r * 299 + g * 587 + b * 114) // 1000))
    if fmt == FMT_RGBA4:
        return struct.pack("<H", (r >> 4) << 12 | (g >> 4) << 8 | (b >> 4) << 4 | a >> 4)
    if fmt == FMT_RGBA5551:
        return struct.pack("<H", (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | (a >= 128))
    if fmt == FMT_RGB8:
        return bytes((b, g, r))
    return bytes((a, b, g, r))  # RGBA8


def _decode_pixel(fmt: int, raw: bytes) -> tuple[int, int, int, int]:
    if fmt == FMT_RGB565:
        v = struct.unpack("<H", raw)[0]
        return (v >> 11) << 3, (v >> 5 & 0x3F) << 2, (v & 0x1F) << 3, 255
    if fmt == FMT_LA8:
        return raw[1], raw[1], raw[1], raw[0]
    if fmt == FMT_RGBA4:
        v = struct.unpack("<H", raw)[0]
        return (v >> 12) * 17, (v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17
    if fmt == FMT_RGBA5551:
        v = struct.unpack("<H", raw)[0]
        return (v >> 11) << 3, (v >> 6 & 0x1F) << 3, (v >> 1 & 0x1F) << 3, 255 * (v & 1)
    if fmt == FMT_RGB8:
        return raw[2], raw[1], raw[0], 255
    return raw[3], raw[2], raw[1], raw[0]


# --- ETC1 (PICA200: 4x4 blocks in Morton order inside 8x8 tiles, little endian) ----

FMT_ETC1, FMT_ETC1A4 = 12, 13
_ETC1_TABLES = ((2, 8), (5, 17), (9, 29), (13, 42), (18, 60), (24, 80), (33, 106), (47, 183))
_BLOCK_ORDER = ((0, 0), (4, 0), (0, 4), (4, 4))


def _clamp(v: int) -> int:
    return 0 if v < 0 else 255 if v > 255 else v


def _etc1_block_colors(word: int) -> tuple[tuple, tuple, bool, int, int, int]:
    """Base colours (8 bit) of both sub-blocks, flip bit and both table indexes."""
    hi = word >> 32
    flip = bool(hi & 1)
    diff = bool(hi & 2)
    t1, t2 = (hi >> 5) & 7, (hi >> 2) & 7
    if diff:
        def five(shift):
            base = (hi >> (shift + 3)) & 0x1F
            delta = (hi >> shift) & 7
            delta = delta - 8 if delta & 4 else delta
            return base, base + delta
        r = five(24)
        g = five(16)
        b = five(8)
        c1 = tuple((v << 3) | (v >> 2) for v in (r[0], g[0], b[0]))
        c2 = tuple(((v & 0x1F) << 3) | ((v & 0x1F) >> 2) for v in (r[1], g[1], b[1]))
    else:
        c1 = tuple(((hi >> sh) & 0xF) * 17 for sh in (28, 20, 12))
        c2 = tuple(((hi >> sh) & 0xF) * 17 for sh in (24, 16, 8))
    return c1, c2, flip, t1, t2, diff


def _etc1_decode_block(word: int) -> list[tuple[int, int, int]]:
    c1, c2, flip, t1, t2, _diff = _etc1_block_colors(word)
    lo = word & 0xFFFFFFFF
    pixels = []
    for y in range(4):
        for x in range(4):
            i = x * 4 + y
            msb, lsb = (lo >> (16 + i)) & 1, (lo >> i) & 1
            second = (y >= 2) if flip else (x >= 2)
            base, table = (c2, t2) if second else (c1, t1)
            small, large = _ETC1_TABLES[table]
            mod = (small, large, -small, -large)[(msb << 1) | lsb]
            pixels.append(tuple(_clamp(c + mod) for c in base))
    return pixels


def _etc1_words(cgfx: bytes, tex: TextureInfo):
    alpha = tex.format == FMT_ETC1A4
    pos = tex.data_offset
    for ty in range(0, tex.height, 8):
        for tx in range(0, tex.width, 8):
            for bx, by in _BLOCK_ORDER:
                if alpha:
                    pos += 8
                yield tx + bx, ty + by, pos, struct.unpack_from("<Q", cgfx, pos)[0]
                pos += 8


def decode_etc1(cgfx: bytes, tex: TextureInfo) -> Image.Image:
    img = Image.new("RGB", (tex.width, tex.height))
    px = img.load()
    for x0, y0, _pos, word in _etc1_words(cgfx, tex):
        for i, color in enumerate(_etc1_decode_block(word)):
            px[x0 + i % 4, y0 + i // 4] = color
    return img


def recolor_etc1(cgfx: bytes, tex: TextureInfo, transform) -> bytes:
    """Apply ``transform(rgb) -> rgb`` to the base colours of every ETC1 block.

    The per-pixel luminance modifiers stay untouched, so shading and detail of
    the texture survive; blocks are re-encoded in individual (4 bit) mode.
    """
    out = bytearray(cgfx)
    for _x, _y, pos, word in _etc1_words(cgfx, tex):
        c1, c2, flip, t1, t2, _diff = _etc1_block_colors(word)
        n1 = [round(_clamp(v) / 17) for v in transform(c1)]
        n2 = [round(_clamp(v) / 17) for v in transform(c2)]
        hi = (n1[0] << 28 | n2[0] << 24 | n1[1] << 20 | n2[1] << 16 | n1[2] << 12 | n2[2] << 8
              | t1 << 5 | t2 << 2 | int(flip))
        struct.pack_into("<Q", out, pos, (hi << 32) | (word & 0xFFFFFFFF))
    return bytes(out)


def decode_texture(cgfx: bytes, tex: TextureInfo) -> Image.Image:
    if tex.format in (FMT_ETC1, FMT_ETC1A4):
        return decode_etc1(cgfx, tex).convert("RGBA")
    bpp = _BYTES_PER_PIXEL.get(tex.format)
    if bpp is None:
        raise InjectError(f"Unsupported texture format {tex.format} for {tex.name}.")
    img = Image.new("RGBA", (tex.width, tex.height))
    px = img.load()
    pos = tex.data_offset
    for ty in range(0, tex.height, 8):
        for tx in range(0, tex.width, 8):
            for x, y in _MORTON:
                px[tx + x, ty + y] = _decode_pixel(tex.format, cgfx[pos:pos + bpp])
                pos += bpp
    return img


def _encode_level(img: Image.Image, fmt: int) -> bytes:
    px = img.load()
    out = bytearray()
    for ty in range(0, img.height, 8):
        for tx in range(0, img.width, 8):
            for x, y in _MORTON:
                out += _encode_pixel(fmt, *px[tx + x, ty + y])
    return bytes(out)


def encode_texture(image: Image.Image, tex: TextureInfo) -> bytes:
    """Encode level 0 plus as many mipmap levels as the original texture had."""
    if tex.format not in _BYTES_PER_PIXEL:
        raise InjectError(f"Unsupported texture format {tex.format} for {tex.name}.")
    img = image.convert("RGBA")
    if img.size != (tex.width, tex.height):
        img = img.resize((tex.width, tex.height), Image.LANCZOS)
    out = bytearray()
    w, h = tex.width, tex.height
    while len(out) < tex.data_size and w >= 8 and h >= 8:
        level = img if (w, h) == img.size else img.resize((w, h), Image.LANCZOS)
        out += _encode_level(level, tex.format)
        w, h = w // 2, h // 2
    if len(out) != tex.data_size:
        raise InjectError(f"Texture {tex.name} has an unexpected size.")
    return bytes(out)


def replace_texture(cgfx: bytes, name: str, image: Image.Image) -> bytes:
    tex = textures(cgfx).get(name)
    if tex is None:
        raise InjectError(f"Banner template has no texture '{name}'.")
    pixels = encode_texture(image, tex)
    return cgfx[:tex.data_offset] + pixels + cgfx[tex.data_offset + len(pixels):]

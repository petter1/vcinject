"""Unencrypted NCCH (CXI) and ExeFS packing, plus the bits of parsing needed to
pull a template out of a decrypted donor title.

Format reference: https://www.3dbrew.org/wiki/NCCH and /wiki/ExeFS
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .errors import InjectError

MEDIA_UNIT = 0x200
EXHEADER_SIZE = 0x400  # hashed part; the access descriptor follows it
EXHEADER_FULL_SIZE = 0x800
_ROMFS_ALIGN = 0x1000

# NCCH header flags (offset 0x188)
_PLATFORM_CTR = 0x01
_CONTENT_EXECUTABLE_DATA = 0x03
_FLAG_NO_CRYPTO = 0x04


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _pad(data: bytes, alignment: int) -> bytes:
    return data + b"\0" * (-len(data) % alignment)


def build_exefs(files: list[tuple[str, bytes]]) -> bytes:
    """Pack ExeFS: a 0x200 byte header followed by 0x200 aligned files."""
    if len(files) > 10:
        raise InjectError("ExeFS can hold at most 10 files.")
    header = bytearray(0x200)
    body = bytearray()
    for i, (name, data) in enumerate(files):
        raw_name = name.encode("ascii")
        if len(raw_name) > 8:
            raise InjectError(f"ExeFS file name too long: {name}")
        struct.pack_into("<8sII", header, i * 0x10, raw_name, len(body), len(data))
        # Hashes are stored in reverse order, the first file's hash comes last.
        slot = 0xC0 + (9 - i) * 0x20
        header[slot:slot + 0x20] = hashlib.sha256(data).digest()
        body += _pad(data, MEDIA_UNIT)
    return bytes(header) + bytes(body)


def patch_exheader(exheader: bytes, title_id: int, app_title: str,
                   save_data_size: int | None = None) -> bytes:
    """Retarget a template exheader to a new title.

    Only the system control info and the ARM11 local caps are changed. The
    access descriptor (0x400..0x800) is signed and must stay untouched.
    """
    if len(exheader) != EXHEADER_FULL_SIZE:
        raise InjectError("Template exheader must be 0x800 bytes.")
    out = bytearray(exheader)
    out[0x000:0x008] = app_title.encode("ascii", errors="replace")[:8].ljust(8, b"\0")
    if save_data_size is not None:
        struct.pack_into("<Q", out, 0x1C0, save_data_size)  # also ends up in the TMD
    struct.pack_into("<Q", out, 0x1C8, title_id)  # jump ID
    struct.pack_into("<Q", out, 0x200, title_id)  # program ID
    return bytes(out)


def build_ncch(*, title_id: int, product_code: str, exheader: bytes, exefs: bytes,
               romfs: bytes) -> bytes:
    """Assemble an unencrypted CXI and fill in all header hashes."""
    if len(product_code.encode("ascii")) > 0x10:
        raise InjectError("Product code must be at most 16 characters.")

    exefs_offset = MEDIA_UNIT + EXHEADER_FULL_SIZE
    exefs = _pad(exefs, MEDIA_UNIT)
    romfs_offset = _align(exefs_offset + len(exefs), _ROMFS_ALIGN) if romfs else 0
    romfs = _pad(romfs, MEDIA_UNIT)
    total = (romfs_offset + len(romfs)) if romfs else exefs_offset + len(exefs)

    hdr = bytearray(MEDIA_UNIT)
    struct.pack_into("<4sIQ2sHI", hdr, 0x100, b"NCCH", total // MEDIA_UNIT, title_id,
                     b"01", 2, 0)
    struct.pack_into("<Q", hdr, 0x118, title_id)
    hdr[0x150:0x160] = product_code.encode("ascii").ljust(0x10, b"\0")
    hdr[0x160:0x180] = hashlib.sha256(exheader[:EXHEADER_SIZE]).digest()
    struct.pack_into("<I", hdr, 0x180, EXHEADER_SIZE)
    hdr[0x18C] = _PLATFORM_CTR
    hdr[0x18D] = _CONTENT_EXECUTABLE_DATA
    hdr[0x18F] = _FLAG_NO_CRYPTO
    struct.pack_into("<III", hdr, 0x1A0, exefs_offset // MEDIA_UNIT, len(exefs) // MEDIA_UNIT, 1)
    hdr[0x1C0:0x1E0] = hashlib.sha256(exefs[:MEDIA_UNIT]).digest()
    if romfs:
        # The header hash covers the IVFC header and master hash.
        master_size = struct.unpack_from("<I", romfs, 0x08)[0] if romfs[:4] == b"IVFC" else 0
        hash_region = _align(0x60 + master_size, MEDIA_UNIT)
        struct.pack_into("<III", hdr, 0x1B0, romfs_offset // MEDIA_UNIT, len(romfs) // MEDIA_UNIT,
                         hash_region // MEDIA_UNIT)
        hdr[0x1E0:0x200] = hashlib.sha256(romfs[:hash_region]).digest()

    out = bytearray(total)
    out[0:MEDIA_UNIT] = hdr
    out[MEDIA_UNIT:exefs_offset] = exheader
    out[exefs_offset:exefs_offset + len(exefs)] = exefs
    if romfs:
        out[romfs_offset:romfs_offset + len(romfs)] = romfs
    return bytes(out)


@dataclass
class NcchParts:
    title_id: int
    product_code: str
    exheader: bytes
    exefs_files: dict[str, bytes]
    logo_region: bytes
    romfs: bytes


def parse_ncch(data: bytes) -> NcchParts:
    if len(data) < MEDIA_UNIT or data[0x100:0x104] != b"NCCH":
        raise InjectError("Not an NCCH file (or it is still encrypted).")
    if not data[0x18F] & _FLAG_NO_CRYPTO:
        raise InjectError("This NCCH is encrypted. Decrypt it first (e.g. with GodMode9).")

    def region(offset_field: int) -> bytes:
        offset, size = struct.unpack_from("<II", data, offset_field)
        return data[offset * MEDIA_UNIT:(offset + size) * MEDIA_UNIT]

    exheader_size = struct.unpack_from("<I", data, 0x180)[0]
    exheader = data[MEDIA_UNIT:MEDIA_UNIT + EXHEADER_FULL_SIZE] if exheader_size else b""

    exefs = region(0x1A0)
    files = {}
    for i in range(10):
        name, offset, size = struct.unpack_from("<8sII", exefs, i * 0x10)
        name = name.rstrip(b"\0").decode("ascii", errors="replace")
        if name:
            files[name] = exefs[0x200 + offset:0x200 + offset + size]

    return NcchParts(
        title_id=struct.unpack_from("<Q", data, 0x118)[0],
        product_code=data[0x150:0x160].rstrip(b"\0").decode("ascii", errors="replace"),
        exheader=exheader,
        exefs_files=files,
        logo_region=region(0x198),
        romfs=region(0x1B0),
    )


def first_cia_content(data: bytes) -> bytes:
    """Return the first content (the CXI) of a CIA whose contents are not encrypted."""
    header_size, _type, _version, cert_size, ticket_size, tmd_size, _meta, content_size = \
        struct.unpack_from("<IHHIIIIQ", data, 0)
    if header_size != 0x2020:
        raise InjectError("Not a CIA file.")
    offset = sum(_align(size, 64) for size in (header_size, cert_size, ticket_size, tmd_size))
    content = data[offset:offset + content_size]
    if content[0x100:0x104] != b"NCCH":
        raise InjectError("The CIA content is encrypted. Use a decrypted CIA "
                          "(GodMode9: 'Build CIA (decrypted)' or 'Decrypt file').")
    ncch_size = struct.unpack_from("<I", content, 0x104)[0] * MEDIA_UNIT
    return content[:ncch_size]

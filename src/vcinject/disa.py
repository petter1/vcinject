"""Reading files out of a 3DS save data container (DISA), as stored on the NAND.

System save data such as the HOME Menu's can't be opened through FS while its
owner runs, but the container file on the NAND can be read. Inside it
(3dbrew "DISA and DIFF", "Savegames"): a partition whose DPFS levels keep two
copies of every block plus bitmaps choosing the current one; within that an
IVFC hash tree whose last level is the SAVE file system; and in that a
directory table, a file table and a FAT chaining data blocks.

Read only: writing needs the console's keys for the container's CMAC, so it
goes through FS on the 3DS.
"""
from __future__ import annotations

import hashlib
import struct

from .errors import InjectError


def _bit(bitmap: bytes, index: int) -> int:
    """DPFS bitmaps are u32 words, first block in the highest bit."""
    word = struct.unpack_from("<I", bitmap, index // 32 * 4)[0]
    return (word >> (31 - index % 32)) & 1


def _levels(descriptor: bytes, first: int, count: int) -> list[tuple[int, int, int]]:
    """(offset, size, block size) of each level in an IVFC or DPFS descriptor."""
    return [(off, size, 1 << log) for off, size, log in
            (struct.unpack_from("<QQI", descriptor, first + i * 0x18) for i in range(count))]


def partition(container: bytes) -> bytes:
    """The data of the container's (first) partition: IVFC level 4."""
    if container[0x100:0x104] != b"DISA":
        raise InjectError("Not a save data container (DISA).")
    head = container[0x100:0x200]
    secondary, primary, table_size = struct.unpack_from("<QQQ", head, 0x10)
    desc_offset, desc_size = struct.unpack_from("<QQ", head, 0x28)
    part_offset, part_size = struct.unpack_from("<QQ", head, 0x48)
    # the table whose hash the header carries is the current one
    table = next((t for t in (secondary, primary) if hashlib.sha256(
        container[t:t + table_size]).digest() == head[0x6C:0x8C]), None)
    if table is None:
        raise InjectError("The save data container is damaged (partition table).")
    difi = container[table + desc_offset:table + desc_offset + desc_size]
    if difi[:4] != b"DIFI":
        raise InjectError("The save data container is damaged (DIFI).")
    ivfc_at, ivfc_size, dpfs_at, dpfs_size = struct.unpack_from("<QQQQ", difi, 0x8)
    external_level4, selector = difi[0x38], difi[0x39]
    ivfc = difi[ivfc_at:ivfc_at + ivfc_size]
    dpfs = difi[dpfs_at:dpfs_at + dpfs_size]
    part = container[part_offset:part_offset + part_size]

    # DPFS: level 1 chooses the copies of level 2, level 2 those of level 3
    (l1_at, l1_size, _), (l2_at, l2_size, l2_block), (l3_at, l3_size, l3_block) = _levels(dpfs, 0x8, 3)
    level1 = part[l1_at + selector * l1_size:l1_at + (selector + 1) * l1_size]

    def current(at: int, size: int, block: int, bitmap: bytes) -> bytes:
        out = bytearray()
        for i in range(-(-size // block)):
            copy = at + _bit(bitmap, i) * size
            out += part[copy + i * block:copy + min((i + 1) * block, size)]
        return bytes(out)

    level2 = current(l2_at, l2_size, l2_block, level1)
    level3 = current(l3_at, l3_size, l3_block, level2)

    # IVFC: level 4 is the data, each of its blocks hashed in level 3
    levels = _levels(ivfc, 0x10, 4)
    (h_at, _h_size, _), (d_at, d_size, d_block) = levels[2], levels[3]
    if external_level4:
        data = part[struct.unpack_from("<Q", difi, 0x3C)[0]:][:d_size]
    else:
        data = level3[d_at:d_at + d_size]
    for i in range(-(-d_size // d_block)):
        block = data[i * d_block:(i + 1) * d_block].ljust(d_block, b"\0")
        if hashlib.sha256(block).digest() != level3[h_at + i * 32:h_at + (i + 1) * 32]:
            raise InjectError("The save data container is damaged (hash mismatch).")
    return data


def read_file(container: bytes, name: str) -> bytes:
    """A file in the root directory of the save data in ``container``."""
    fs = partition(container)
    if fs[:4] != b"SAVE":
        raise InjectError("No save file system in the container.")
    info = struct.unpack_from("<Q", fs, 0x8)[0]
    block_size = struct.unpack_from("<I", fs, info + 0x4)[0]
    fat_at, fat_count = struct.unpack_from("<QI", fs, info + 0x28)
    data_at = struct.unpack_from("<Q", fs, info + 0x38)[0]
    table_block, _table_blocks = struct.unpack_from("<II", fs, info + 0x58)
    max_files = struct.unpack_from("<I", fs, info + 0x60)[0]
    data = fs[data_at:]
    fat = [struct.unpack_from("<II", fs, fat_at + i * 8) for i in range(fat_count + 1)]

    files = data[table_block * block_size:]
    for index in range(1, max_files + 1):  # entry 0 holds the table's counts
        entry = files[index * 0x30:(index + 1) * 0x30]
        if len(entry) < 0x30 or entry[4:20].split(b"\0", 1)[0].decode("ascii", "replace") != name:
            continue
        first, size = struct.unpack_from("<IQ", entry, 0x1C)
        out = bytearray()
        node = first + 1  # FAT entry n stands for data block n - 1
        while node and len(out) < size:
            u, v = fat[node]
            end = node
            if v & 0x80000000:  # a node of several blocks: its second entry names the last
                end = fat[node + 1][1] & 0x7FFFFFFF
            for block in range(node - 1, end):
                out += data[block * block_size:(block + 1) * block_size]
            node = v & 0x7FFFFFFF
        if len(out) < size:
            raise InjectError(f"{name} is cut short in the save data container.")
        return bytes(out[:size])
    raise InjectError(f"{name} is not in the save data container.")

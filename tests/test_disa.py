import hashlib
import struct

import pytest

from vcinject import disa
from vcinject.errors import InjectError

BLOCK = 0x200


def save_fs(files: dict[str, tuple[bytes, list[list[int]]]]) -> bytes:
    """A SAVE file system; each file lists its data blocks as FAT nodes of consecutive blocks."""
    blocks = 8
    fs = bytearray(BLOCK + blocks * BLOCK)
    struct.pack_into("<4sIQQI", fs, 0, b"SAVE", 0x40000, 0x20, len(fs) // BLOCK, BLOCK)
    info = 0x20
    struct.pack_into("<I", fs, info + 0x4, BLOCK)
    struct.pack_into("<QI", fs, info + 0x28, 0x100, blocks)            # FAT
    struct.pack_into("<Q", fs, info + 0x38, BLOCK)                     # data region
    struct.pack_into("<II", fs, info + 0x58, 1, 1)                     # file table in block 1
    struct.pack_into("<I", fs, info + 0x60, len(files))
    fat = [[0, 0] for _ in range(blocks + 1)]
    for index, (name, (content, nodes)) in enumerate(files.items(), start=1):
        entry = BLOCK + BLOCK + index * 0x30
        fs[entry + 4:entry + 4 + len(name)] = name.encode()
        struct.pack_into("<IQ", fs, entry + 0x1C, nodes[0][0], len(content))
        for n, node in enumerate(nodes):
            first, last = node[0] + 1, node[-1] + 1                   # FAT entry = block + 1
            nxt = nodes[n + 1][0] + 1 if n + 1 < len(nodes) else 0
            prev = nodes[n - 1][0] + 1 if n else 0
            fat[first] = [prev | (0x80000000 if n == 0 else 0), nxt | (0x80000000 if last > first else 0)]
            if last > first:
                fat[first + 1] = [0x80000000 | first, last]
        for block, chunk in zip([b for node in nodes for b in node],
                                [content[i:i + BLOCK] for i in range(0, len(content), BLOCK)]):
            fs[BLOCK + block * BLOCK:BLOCK + block * BLOCK + len(chunk)] = chunk
    for i, (u, v) in enumerate(fat):
        struct.pack_into("<II", fs, 0x100 + i * 8, u, v)
    return bytes(fs)


def container(fs: bytes, broken_hash: bool = False) -> bytes:
    """A DISA around ``fs`` whose current blocks are spread over both DPFS copies."""
    hashes = b"".join(hashlib.sha256(fs[i:i + BLOCK]).digest() for i in range(0, len(fs), BLOCK))
    if broken_hash:
        hashes = b"\0" * 32 + hashes[32:]
    level3 = hashes.ljust(BLOCK, b"\0") + fs
    blocks = len(level3) // BLOCK
    choice = [i % 2 for i in range(blocks)]                            # alternate the copies
    l2_bitmap = struct.pack("<I", sum(c << (31 - i) for i, c in enumerate(choice)))
    l1_bitmap = struct.pack("<I", 1 << 31)                             # level 2: second copy
    part = bytearray(0x100 + 2 * len(level3))
    struct.pack_into("<4s", part, 0, l1_bitmap)                        # level 1, copy 0
    part[0x10 + 4:0x10 + 8] = l2_bitmap                                # level 2, copy 1 (size 4)
    for i in range(blocks):
        good = level3[i * BLOCK:(i + 1) * BLOCK]
        copy = choice[i]
        for which, content in ((copy, good), (1 - copy, b"\xEE" * BLOCK)):  # and a stale copy
            at = 0x100 + which * len(level3) + i * BLOCK
            part[at:at + BLOCK] = content

    ivfc = bytearray(0x78)
    struct.pack_into("<4sIQ", ivfc, 0, b"IVFC", 0x20000, 0x20)
    struct.pack_into("<QQI", ivfc, 0x10 + 2 * 0x18, 0, len(hashes), 9)      # level 3: hashes
    struct.pack_into("<QQI", ivfc, 0x10 + 3 * 0x18, BLOCK, len(fs), 9)      # level 4: data
    dpfs = bytearray(0x50)
    struct.pack_into("<4sI", dpfs, 0, b"DPFS", 0x10000)
    struct.pack_into("<QQI", dpfs, 0x8, 0, 4, 0)
    struct.pack_into("<QQI", dpfs, 0x20, 0x10, 4, 7)
    struct.pack_into("<QQI", dpfs, 0x38, 0x100, len(level3), 9)
    difi = bytearray(0x44) + ivfc + dpfs
    struct.pack_into("<4sIQQQQ", difi, 0, b"DIFI", 0x10000, 0x44, 0x78, 0xBC, 0x50)

    out = bytearray(0x1000) + part
    out[0x200:0x200 + len(difi)] = difi                                # the current table
    head = bytearray(0x100)
    struct.pack_into("<4sII", head, 0, b"DISA", 0x40000, 1)
    struct.pack_into("<QQQ", head, 0x10, 0x200, 0x400, len(difi))
    struct.pack_into("<QQ", head, 0x28, 0, len(difi))
    struct.pack_into("<QQ", head, 0x48, 0x1000, len(part))
    head[0x6C:0x8C] = hashlib.sha256(bytes(difi)).digest()
    out[0x100:0x200] = head
    return bytes(out)


LAUNCHER = bytes(range(256)) * 9 + b"end"          # 2307 bytes: 5 blocks


def test_reads_a_file_spread_over_nodes_and_copies():
    fs = save_fs({"Other.dat": (b"x" * 100, [[2]]),
                  "Launcher.dat": (LAUNCHER, [[3, 4], [6, 7], [5]])})
    data = container(fs)
    assert disa.read_file(data, "Launcher.dat") == LAUNCHER
    assert disa.read_file(data, "Other.dat") == b"x" * 100
    with pytest.raises(InjectError, match="not in"):
        disa.read_file(data, "Missing.dat")


def test_refuses_damaged_containers():
    fs = save_fs({"Launcher.dat": (LAUNCHER, [[2, 3, 4, 5, 6]])})
    with pytest.raises(InjectError, match="hash"):
        disa.read_file(container(fs, broken_hash=True), "Launcher.dat")
    with pytest.raises(InjectError, match="DISA"):
        disa.read_file(b"\0" * 0x1000, "Launcher.dat")

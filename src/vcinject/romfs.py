"""Read and write 3DS RomFS images (IVFC hash tree + level 3 file system).

Reference: https://www.3dbrew.org/wiki/RomFS
"""
from __future__ import annotations

import hashlib
import struct

from .errors import InjectError

_BLOCK_LOG = 12
_BLOCK = 1 << _BLOCK_LOG
_UNUSED = 0xFFFFFFFF
_IVFC_HEADER_SIZE = 0x5C


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


# --- reading ---------------------------------------------------------------------

def _level3(image: bytes) -> bytes:
    if image[:4] != b"IVFC":
        raise InjectError("Not a RomFS image.")
    master_size = struct.unpack_from("<I", image, 0x08)[0]
    block = 1 << struct.unpack_from("<I", image, 0x4C)[0]
    return image[_align(0x60 + master_size, block):]


def read(image: bytes) -> dict[str, bytes]:
    """All files of a RomFS image as {"/dir/name": data}."""
    l3 = _level3(image)
    dir_meta, file_meta, data_offset = (struct.unpack_from("<I", l3, o)[0] for o in (0x0C, 0x1C, 0x24))
    files: dict[str, bytes] = {}

    def visit(offset: int, path: str) -> None:
        _parent, _sibling, child, file, _hash, name_len = struct.unpack_from("<6I", l3, dir_meta + offset)
        name = l3[dir_meta + offset + 0x18:dir_meta + offset + 0x18 + name_len].decode("utf-16-le")
        here = f"{path}{name}/" if offset else "/"
        while file != _UNUSED:
            _p, sibling, off, size, _h, flen = struct.unpack_from("<IIQQII", l3, file_meta + file)
            fname = l3[file_meta + file + 0x20:file_meta + file + 0x20 + flen].decode("utf-16-le")
            files[here + fname] = l3[data_offset + off:data_offset + off + size]
            file = sibling
        while child != _UNUSED:
            visit(child, here)
            child = struct.unpack_from("<I", l3, dir_meta + child + 4)[0]

    visit(0, "")
    return files


# --- writing -----------------------------------------------------------------------

def _hash_name(parent: int, name: str) -> int:
    value = parent ^ 123456789
    for unit in struct.unpack(f"<{len(name.encode('utf-16-le')) // 2}H", name.encode("utf-16-le")):
        value = ((value >> 5) | (value << 27)) & 0xFFFFFFFF
        value ^= unit
    return value


def _bucket_count(entries: int) -> int:
    if entries < 3:
        return 3
    if entries < 19:
        return entries | 1
    count = entries
    while any(count % p == 0 for p in (2, 3, 5, 7, 11, 13, 17)):
        count += 1
    return count


class _Dir:
    def __init__(self, name: str, parent: "_Dir | None"):
        self.name, self.parent = name, parent
        self.dirs: dict[str, _Dir] = {}
        self.files: dict[str, bytes] = {}
        self.offset = 0


def _tree(files: dict[str, bytes]) -> _Dir:
    root = _Dir("", None)
    for path, data in files.items():
        parts = [p for p in path.split("/") if p]
        if not parts:
            raise InjectError(f"Invalid RomFS path: {path!r}")
        node = root
        for part in parts[:-1]:
            node = node.dirs.setdefault(part, _Dir(part, node))
        node.files[parts[-1]] = data
    return root


def _entry_size(name: str, fixed: int) -> int:
    return fixed + _align(len(name.encode("utf-16-le")), 4)


def _build_level3(files: dict[str, bytes]) -> bytes:
    root = _tree(files)
    # Breadth-first order for directories, like the official tools.
    dirs, queue = [], [root]
    while queue:
        node = queue.pop(0)
        dirs.append(node)
        queue.extend(node.dirs[k] for k in sorted(node.dirs))
    offset = 0
    for node in dirs:
        node.offset = offset
        offset += _entry_size(node.name, 0x18)
    dir_meta_size = offset

    file_list: list[tuple[_Dir, str, bytes, int]] = []
    offset = data_size = 0
    for node in dirs:
        for name in sorted(node.files):
            data = node.files[name]
            data_size = _align(data_size, 0x10)
            file_list.append((node, name, data, offset))
            offset += _entry_size(name, 0x20)
            data_size += len(data)
    file_meta_size = offset

    dir_buckets = _bucket_count(len(dirs))
    file_buckets = _bucket_count(len(file_list))
    dir_hash = [_UNUSED] * dir_buckets
    file_hash = [_UNUSED] * file_buckets

    dir_meta = bytearray(dir_meta_size)
    for node in dirs:
        parent = node.parent.offset if node.parent else 0
        sibling = _UNUSED
        if node.parent is not None:
            names = sorted(node.parent.dirs)
            index = names.index(node.name)
            if index + 1 < len(names):
                sibling = node.parent.dirs[names[index + 1]].offset
        child = node.dirs[sorted(node.dirs)[0]].offset if node.dirs else _UNUSED
        first_file = next((off for d, _n, _b, off in file_list if d is node), _UNUSED)
        bucket = _hash_name(parent, node.name) % dir_buckets
        raw_name = node.name.encode("utf-16-le")
        struct.pack_into("<6I", dir_meta, node.offset, parent, sibling, child, first_file,
                         dir_hash[bucket], len(raw_name))
        dir_hash[bucket] = node.offset
        dir_meta[node.offset + 0x18:node.offset + 0x18 + len(raw_name)] = raw_name

    file_meta = bytearray(file_meta_size)
    data_blob = bytearray()
    for i, (node, name, data, off) in enumerate(file_list):
        sibling = file_list[i + 1][3] if i + 1 < len(file_list) and file_list[i + 1][0] is node else _UNUSED
        data_blob.extend(b"\0" * (-len(data_blob) % 0x10))
        bucket = _hash_name(node.offset, name) % file_buckets
        raw_name = name.encode("utf-16-le")
        struct.pack_into("<IIQQII", file_meta, off, node.offset, sibling, len(data_blob), len(data),
                         file_hash[bucket], len(raw_name))
        file_hash[bucket] = off
        file_meta[off + 0x20:off + 0x20 + len(raw_name)] = raw_name
        data_blob.extend(data)

    header_size = 0x28
    dir_hash_off = header_size
    dir_meta_off = dir_hash_off + dir_buckets * 4
    file_hash_off = dir_meta_off + dir_meta_size
    file_meta_off = file_hash_off + file_buckets * 4
    data_off = _align(file_meta_off + file_meta_size, 0x10)
    header = struct.pack("<10I", header_size, dir_hash_off, dir_buckets * 4, dir_meta_off,
                         dir_meta_size, file_hash_off, file_buckets * 4, file_meta_off,
                         file_meta_size, data_off)
    out = bytearray(header)
    out += struct.pack(f"<{dir_buckets}I", *dir_hash) + dir_meta
    out += struct.pack(f"<{file_buckets}I", *file_hash) + file_meta
    out += b"\0" * (data_off - len(out))
    out += data_blob
    return bytes(out)


def _hash_level(data: bytes) -> bytes:
    out = bytearray()
    for pos in range(0, len(data), _BLOCK):
        out += hashlib.sha256(data[pos:pos + _BLOCK].ljust(_BLOCK, b"\0")).digest()
    return bytes(out)


def build(files: dict[str, bytes]) -> bytes:
    """Build an IVFC RomFS image from {"/dir/name": data}."""
    level3 = _build_level3(files)
    level2 = _hash_level(level3)
    level1 = _hash_level(level2)
    master = _hash_level(level1)

    l1_logical = 0
    l2_logical = _align(l1_logical + len(level1), _BLOCK)
    l3_logical = _align(l2_logical + len(level2), _BLOCK)
    header = bytearray(0x60)
    struct.pack_into("<4sII", header, 0, b"IVFC", 0x10000, len(master))
    for i, (logical, data) in enumerate(((l1_logical, level1), (l2_logical, level2),
                                         (l3_logical, level3))):
        struct.pack_into("<QQII", header, 0x0C + i * 0x18, logical, len(data), _BLOCK_LOG, 0)
    struct.pack_into("<I", header, 0x54, _IVFC_HEADER_SIZE)

    image = bytearray(header + master)
    image += b"\0" * (_align(len(image), _BLOCK) - len(image))
    image += level3
    image += b"\0" * (_align(len(image), _BLOCK) - len(image))
    image += level1
    image += b"\0" * (_align(len(image), _BLOCK) - len(image))
    image += level2
    image += b"\0" * (_align(len(image), _BLOCK) - len(image))
    return bytes(image)


def hash_region_size(image: bytes) -> int:
    """Bytes the NCCH header's RomFS hash covers (IVFC header + master hash)."""
    return _align(0x60 + struct.unpack_from("<I", image, 0x08)[0], 0x200)

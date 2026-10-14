"""Read title IDs from a 3DS ticket database (ticket.db) or .tik files.

Importing them into the register makes vcinject avoid every title ID that is
already in use on the console, including injects made with other tools.

A ticket's data starts with its issuer string; the title ID follows at a
fixed offset (https://www.3dbrew.org/wiki/Ticket). Instead of unpacking the
ticket.db container we scan for issuer strings, which works for ticket.db,
single .tik files and folders of them alike. ticket.db can still contain
tickets of deleted titles, so a few unused IDs may be reported as taken; that
only makes vcinject more careful.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .errors import InjectError

_ISSUERS = (b"Root-CA00000003-XS0000000c", b"Root-CA00000004-XS00000009")  # retail, dev
_TITLE_ID_OFFSET = 0x9C  # from the start of the issuer field
APPLICATION = 0x00040000


def title_ids_in(data: bytes) -> set[int]:
    found = set()
    for issuer in _ISSUERS:
        pos = data.find(issuer)
        while pos != -1:
            if pos + _TITLE_ID_OFFSET + 8 <= len(data):
                found.add(struct.unpack_from(">Q", data, pos + _TITLE_ID_OFFSET)[0])
            pos = data.find(issuer, pos + 1)
    return found


def read_title_ids(paths: list[Path], applications_only: bool = True) -> set[int]:
    files: list[Path] = []
    for path in map(Path, paths):
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.suffix.lower() in (".tik", ".db"))
        elif path.is_file():
            files.append(path)
        else:
            raise InjectError(f"Not found: {path}")
    ids: set[int] = set()
    for file in files:
        ids |= title_ids_in(file.read_bytes())
    if not ids:
        raise InjectError("No tickets found. Use the ticket.db copied from 1:/dbs/ with "
                          "GodMode9, or .tik files.")
    if applications_only:
        ids = {tid for tid in ids if tid >> 32 == APPLICATION}
    return ids

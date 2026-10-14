import struct

import pytest

from vcinject import registry, tickets
from vcinject.errors import InjectError


def fake_ticket(title_id: int) -> bytes:
    sig = struct.pack(">I", 0x00010004) + b"\xAA" * 0x13C  # RSA-2048 signature block
    data = bytearray(0x210)
    data[:0x40] = b"Root-CA00000003-XS0000000c".ljust(0x40, b"\0")
    struct.pack_into(">Q", data, 0x9C, title_id)
    return sig + bytes(data)


def test_scan_ticket_db_like_blob(tmp_path):
    blob = b"BDRI" + b"\0" * 100 + fake_ticket(0x000400000F6B2B00) + b"\xFF" * 33 \
        + fake_ticket(0x0004013800000002) + fake_ticket(0x00040000001B5000)
    db = tmp_path / "ticket.db"
    db.write_bytes(blob)
    assert tickets.read_title_ids([db]) == {0x000400000F6B2B00, 0x00040000001B5000}
    assert 0x0004013800000002 in tickets.read_title_ids([db], applications_only=False)


def test_folder_of_tik_files_and_empty_input(tmp_path):
    (tmp_path / "a.tik").write_bytes(fake_ticket(0x000400000F000100))
    assert tickets.read_title_ids([tmp_path]) == {0x000400000F000100}
    (tmp_path / "empty.db").write_bytes(b"\0" * 64)
    with pytest.raises(InjectError):
        tickets.read_title_ids([tmp_path / "empty.db"])


def test_imported_ids_are_never_suggested(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    sha = "c" * 40
    taken = registry.preferred_title_id(sha)
    assert registry.import_title_ids({taken}) == 1
    assert registry.import_title_ids({taken}) == 0  # idempotent
    assert registry.suggest_title_id(sha) != taken
    assert registry.conflict(taken, sha).imported

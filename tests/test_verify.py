import hashlib
import zlib
from pathlib import Path

from vcinject import artwork, dumpdb, library, systems

GBA = systems.SYSTEMS["gba"]


def _setup(monkeypatch, good: bytes, name="Game (Europe)"):
    sha1, crc = hashlib.sha1(good).hexdigest().upper(), f"{zlib.crc32(good) & 0xFFFFFFFF:08X}"
    dat = f'game (\n\tname "{name}"\n\tserial "BGME"\n\trom ( crc {crc} sha1 {sha1} )\n)'
    monkeypatch.setattr(artwork.ReleaseDatabase, "load",
                        classmethod(lambda cls, **kw: artwork.ReleaseDatabase(dat)))
    entry = {"gn": "Game", "ne": "(Europe)", "rc": zlib.crc32(good), "rs": len(good)}
    monkeypatch.setattr(dumpdb, "database",
                        lambda key: {dumpdb.header_key(key, good): entry} if key == "gba" else {})


def test_good_bad_and_unknown_dumps(monkeypatch):
    good = bytes(range(256)) * 64
    _setup(monkeypatch, good)
    rom = library.verify_data(Path("game.gba"), GBA, good)
    assert (rom.verified, rom.verified_by) == ("good", "No-Intro, FlashGBX")

    damaged = bytearray(good)
    damaged[0x2000] ^= 0xFF  # header unchanged, contents differ
    rom = library.verify_data(Path("damaged.gba"), GBA, bytes(damaged))
    assert rom.verified == "mismatch" and "bad dump" in library.verified_label(rom)

    other = bytes(reversed(good))
    assert library.verify_data(Path("homebrew.gba"), GBA, other).verified == "unknown"


def test_flashgbx_alone_is_enough(monkeypatch):
    good = bytes(range(256)) * 64
    _setup(monkeypatch, good)
    monkeypatch.setattr(artwork.ReleaseDatabase, "load",
                        classmethod(lambda cls, **kw: artwork.ReleaseDatabase("")))
    rom = library.verify_data(Path("game.gba"), GBA, good)
    assert (rom.verified, rom.verified_by) == ("good", "FlashGBX")


def test_offline_leaves_dumps_unchecked(monkeypatch):
    rom = library.verify_data(Path("game.gba"), GBA, b"\0" * 512)
    assert rom.verified == "" and library.verified_label(rom) == ""

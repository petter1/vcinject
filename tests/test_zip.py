import zipfile

import pytest

from vcinject import builder, library, systems
from vcinject.errors import InjectError


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_zipped_rom_is_recognised_and_read(tmp_path):
    rom = b"\x00" * 0x200 + b"NES"
    path = _zip(tmp_path / "Game (Europe).zip", {"readme.txt": b"hi", "Game (Europe).nes": rom,
                                                  "__MACOSX/._Game (Europe).nes": b"x"})
    assert systems.for_rom(path).key == "nes"
    assert systems.rom_name(path) == "Game (Europe).nes"
    assert systems.read_rom(path) == rom
    system, loaded = builder.load_rom(path)
    assert system.key == "nes" and loaded.data == rom


def test_zip_without_rom_is_rejected(tmp_path):
    path = _zip(tmp_path / "notes.zip", {"notes.txt": b"hi"})
    with pytest.raises(InjectError):
        systems.for_rom(path)
    (tmp_path / "broken.zip").write_bytes(b"not a zip")
    with pytest.raises(InjectError):
        systems.for_rom(tmp_path / "broken.zip")


def test_library_scans_and_zips_roms(tmp_path):
    (tmp_path / "sub").mkdir()
    loose = tmp_path / "sub" / "Loose.gb"
    loose.write_bytes(bytes(range(256)) * 64)
    _zip(tmp_path / "Other.zip", {"Other.sfc": b"\x01" * 1024})
    _zip(tmp_path / "junk.zip", {"junk.txt": b""})
    roms = library.scan(tmp_path)
    names = sorted(r.path.name for r in roms)
    assert "Other.zip" in names and "Loose.gb" in names and "junk.zip" not in names
    assert [r.path.name for r in library.unzipped(roms)] == ["Loose.gb"]

    zipped = library.zip_rom(loose)
    assert not loose.exists() and zipped.name == "Loose.zip"
    assert systems.read_rom(zipped) == bytes(range(256)) * 64
    assert library.crc32(zipped) == f"{__import__('zlib').crc32(bytes(range(256)) * 64):08X}"
    with pytest.raises(InjectError):  # never overwrite an existing zip
        loose.write_bytes(b"again")
        library.zip_rom(loose)
    assert loose.exists()

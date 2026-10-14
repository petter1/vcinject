import hashlib
import zipfile

import pytest

from vcinject import library, systems
from vcinject.errors import InjectError

GBA = systems.SYSTEMS["gba"]


def _rom(tmp_path, name, data=b"A", release=None, method="crc", family=""):
    path = tmp_path / name
    path.write_bytes(data)
    return library.LibraryRom(path, GBA, len(data), release=release, sha1=hashlib.sha1(data).hexdigest(),
                              method=method if release else "", family=family)


def test_self_dumped_mark():
    assert library.is_self_dumped("Pokemon - Smaragd-Edition (Germany) - selfdumped.gba")
    assert library.plain_stem("Pokemon - Smaragd-Edition (Germany) - selfdumped.gba") == \
        "Pokemon - Smaragd-Edition (Germany)"
    assert library.plain_stem("Game (Europe) [selfdumped].zip") == "Game (Europe)"
    assert not library.is_self_dumped("Game (Europe).gba")


def test_language_versions_and_copies_are_grouped(tmp_path):
    de = _rom(tmp_path, "smaragd.gba", b"de", "Pokemon - Smaragd-Edition (Germany)", family="BPE")
    us = _rom(tmp_path, "emerald.gba", b"us", "Pokemon - Emerald Version (USA, Europe)", family="BPE")
    copy = _rom(tmp_path, "copy of smaragd.gba", b"de", "Pokemon - Smaragd-Edition (Germany)",
                family="BPE")
    other = _rom(tmp_path, "Bionicle.gba", b"x")
    groups = sorted(library.group_versions([de, us, copy, other]), key=len)
    assert [len(g) for g in groups] == [1, 3]
    dupes = library.duplicates(groups[1])
    assert len(dupes) == 1 and dupes[0].identical
    assert sorted(r.path.name for r in dupes[0].copies) == ["copy of smaragd.gba", "smaragd.gba"]


def test_same_release_with_different_bytes_is_not_identical(tmp_path):
    a = _rom(tmp_path, "a.gba", b"good", "Game (Europe)")
    b = _rom(tmp_path, "b.gba", b"trimmed", "Game (Europe)")
    [dupe] = library.duplicates([a, b])
    assert not dupe.identical


def test_clean_names_keep_the_self_dumped_mark(tmp_path):
    rom = _rom(tmp_path, "pkmn emerald - selfdumped.gba", b"d", "Pokemon - Emerald Version (USA, Europe)")
    serial_only = _rom(tmp_path, "My Hack.gba", b"h", "Pokemon - FireRed Version (USA)", method="serial")
    assert library.clean_names([rom, serial_only]) == \
        [(rom, "Pokemon - Emerald Version (USA, Europe) - selfdumped")]
    library.rename(rom, "Pokemon - Emerald Version (USA, Europe) - selfdumped")
    assert rom.path.name == "Pokemon - Emerald Version (USA, Europe) - selfdumped.gba"
    assert rom.path.read_bytes() == b"d"
    library.set_self_dumped(rom, False)
    assert rom.path.name == "Pokemon - Emerald Version (USA, Europe).gba"
    library.set_self_dumped(serial_only, True)
    assert serial_only.path.name == "My Hack - selfdumped.gba"


def test_rename_zip_renames_the_rom_inside_and_never_overwrites(tmp_path):
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("x.gba", b"rom")
    rom = library.LibraryRom(path, GBA, 3, release="Game (Europe)", method="crc")
    library.rename(rom, "Game (Europe)")
    assert rom.path.name == "Game (Europe).zip"
    assert systems.rom_name(rom.path) == "Game (Europe).gba" and systems.read_rom(rom.path) == b"rom"
    (tmp_path / "Taken.gba").write_bytes(b"")
    other = _rom(tmp_path, "y.gba")
    with pytest.raises(InjectError):
        library.rename(other, "Taken")
    assert other.path.exists()

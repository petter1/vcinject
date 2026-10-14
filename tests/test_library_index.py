import hashlib
import shutil

from PIL import Image

from vcinject import artwork, library, systems
from vcinject.errors import InjectError


def _rom(path, data=b"rom data"):
    path.write_bytes(data)
    return library.LibraryRom(path, systems.SYSTEMS["gba"], len(data))


def _identified(path):
    rom = _rom(path)
    rom.sha1 = hashlib.sha1(b"rom data").hexdigest()
    rom.release, rom.method, rom.family, rom.year = "Game (Europe)", "crc", "BGA", "2003"
    rom.cover = Image.new("RGB", (60, 80), "red")
    library._remember(rom)
    library.save_index()
    return rom


def test_unchanged_files_come_from_the_index(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    _identified(tmp_path / "game.gba")
    library._index = None  # as after a restart
    again = library.LibraryRom(tmp_path / "game.gba", systems.SYSTEMS["gba"], 8)
    assert library.from_index(again)
    assert (again.release, again.year, again.family) == ("Game (Europe)", "2003", "BGA")
    assert again.cover.size == (60, 80)

    (tmp_path / "game.gba").write_bytes(b"changed!!")
    assert not library.from_index(again)


def test_renamed_files_are_recognised_without_going_online(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    rom = _identified(tmp_path / "game.gba")
    library.rename(rom, "Game (Europe)")
    assert library.from_index(library.LibraryRom(rom.path, rom.system, 8))

    shutil.copy(rom.path, tmp_path / "copy.gba")  # new name, same data
    monkeypatch.setattr(artwork.ReleaseDatabase, "load",
                        classmethod(lambda cls, **kw: (_ for _ in ()).throw(InjectError("offline"))))
    copy = library.analyze(library.LibraryRom(tmp_path / "copy.gba", rom.system, 8))
    assert copy.release == "Game (Europe)" and copy.cover is not None


def test_each_data_folder_has_its_own_index(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "one"))
    _identified(tmp_path / "game.gba")
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "two"))
    assert not library.from_index(library.LibraryRom(tmp_path / "game.gba", systems.SYSTEMS["gba"], 8))

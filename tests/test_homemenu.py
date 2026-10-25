import struct

import pytest

from vcinject import homemenu, remote
from vcinject.errors import InjectError
from vcinject.homemenu import MAIN, Folder, GameCard, Icon

MARIO, ZELDA, POKEMON = 0x0004000000030700, 0x0004000000033500, 0x000400000F783500
SETTINGS = 0x0004001000022000


def files():
    """A SaveData.dat with three SD games and a Launcher.dat with System Settings,
    the Game Card and a folder "Pokémon" holding one of the games."""
    savedata = bytearray(0x2DA0)
    savedata[0] = 4
    savedata[0x2000:0x2010] = b"theme settings.."   # unknown data must survive
    struct.pack_into("<360h", savedata, 0xCB0, *[-1] * 360)
    struct.pack_into("<360b", savedata, 0xF80, *[-1] * 360)
    for slot, (tid, pos, folder) in enumerate([(MARIO, 2, -1), (ZELDA, 3, -1), (POKEMON, 0, 0)]):
        struct.pack_into("<Q", savedata, 0x8 + slot * 8, tid)
        struct.pack_into("<h", savedata, 0xCB0 + slot * 2, pos)
        struct.pack_into("<b", savedata, 0xF80 + slot, folder)

    launcher = bytearray(0x2558)
    launcher[0x2500:0x2508] = b"stats..."
    struct.pack_into("<H", launcher, 0x2, 0)                     # Game Card at 0
    struct.pack_into("<360h", launcher, 0xD9A, *[-1] * 360)
    struct.pack_into("<360b", launcher, 0x106A, *[-1] * 360)
    struct.pack_into("<Q", launcher, 0x8, SETTINGS)
    struct.pack_into("<h", launcher, 0xD9A, 1)
    struct.pack_into("<60h", launcher, 0x11DC, *[-1] * 60)
    struct.pack_into("<h", launcher, 0x11DC, 4)                  # folder 0 at 4
    launcher[0x1434] = 2
    launcher[0x1560:0x1560 + 14] = "Pokémon".encode("utf-16-le")
    struct.pack_into("<I", launcher, 0xD80, 7)
    return bytes(savedata), bytes(launcher)


def test_reads_the_layout():
    layout = homemenu.Layout(*files())
    main = layout.items(MAIN)
    assert [type(item) for item in main] == [GameCard, Icon, Icon, Icon, Folder]
    assert [item.title_id for item in main if isinstance(item, Icon)] == [SETTINGS, MARIO, ZELDA]
    [folder] = layout.folders.values()
    assert (folder.name, folder.pos) == ("Pokémon", 4)
    assert [icon.title_id for icon in layout.members(folder)] == [POKEMON]
    assert main[1].nand and not main[2].nand
    assert not layout.changed
    assert layout.files() == files()


def test_moving_swaps_and_keeps_unknown_data():
    layout = homemenu.Layout(*files(), nand_locked=False)
    zelda = next(i for i in layout.icons if i.title_id == ZELDA)
    layout.move(zelda, MAIN, 1)                       # onto System Settings: they swap
    settings = next(i for i in layout.icons if i.title_id == SETTINGS)
    assert (zelda.pos, settings.pos) == (1, 3)
    folder = layout.folders[0]
    layout.move(next(i for i in layout.icons if i.title_id == MARIO), folder.id)
    savedata, launcher = layout.files()
    assert savedata[0x2000:0x2010] == b"theme settings.." and launcher[0x2500:0x2508] == b"stats..."

    again = homemenu.Layout(savedata, launcher)
    assert [i.title_id for i in again.members(again.folders[0])] == [POKEMON, MARIO]
    assert [getattr(i, "title_id", None) for i in again.items(MAIN)] == [None, ZELDA, SETTINGS, None]


def test_folders_create_rename_delete():
    layout = homemenu.Layout(*files(), nand_locked=False)
    new = layout.create_folder("Zelda")
    assert (new.id, new.pos) == (1, 5)                # first free place in the main grid
    zelda = next(i for i in layout.icons if i.title_id == ZELDA)
    layout.move(zelda, new.id)
    layout.rename_folder(layout.folders[0], "Pokémon GBA")
    savedata, launcher = layout.files()
    assert struct.unpack_from("<I", savedata, 0x12C8 + 4)[0] == 7      # next folder number
    assert struct.unpack_from("<I", launcher, 0xD80)[0] == 8

    again = homemenu.Layout(savedata, launcher, nand_locked=False)
    assert {f.name for f in again.folders.values()} == {"Pokémon GBA", "Zelda"}
    again.delete_folder(again.folders[0])
    pokemon = next(i for i in again.icons if i.title_id == POKEMON)
    assert (pokemon.folder, pokemon.pos) == (MAIN, 3)  # Zelda's old place, now free
    assert 0 not in homemenu.Layout(*again.files()).folders


def test_refuses_what_the_home_menu_cannot_show():
    layout = homemenu.Layout(*files(), nand_locked=False)
    with pytest.raises(InjectError, match="main grid"):
        layout.move(layout.folders[0], 0, 1)
    with pytest.raises(InjectError, match="16"):
        layout.create_folder("A very long folder name")
    with pytest.raises(InjectError, match="outside"):
        layout.move(layout.icons[0], 0, 60)
    layout.icons[0].pos = layout.icons[1].pos
    with pytest.raises(InjectError, match="share"):
        layout.files()
    with pytest.raises(InjectError, match="format"):
        homemenu.Layout(b"\x03" + files()[0][1:], files()[1])


def test_by_default_only_sd_titles_move():
    """Launcher.dat can't be written while the HOME Menu runs (see homemenu)."""
    layout = homemenu.Layout(*files())
    icons = {i.title_id: i for i in layout.icons}
    for change in (lambda: layout.move(icons[SETTINGS], MAIN, 6),          # a system app
                   lambda: layout.move(layout.folders[0], MAIN, 6),        # a folder
                   lambda: layout.move(layout.game_card, MAIN, 6),
                   lambda: layout.create_folder("Zelda"),
                   lambda: layout.rename_folder(layout.folders[0], "x"),
                   lambda: layout.delete_folder(layout.folders[0])):
        with pytest.raises(InjectError, match="locked"):
            change()
    with pytest.raises(InjectError, match="belongs to"):                   # onto System Settings
        layout.move(icons[ZELDA], MAIN, 1)
    layout.move(icons[ZELDA], MAIN, 2)                                     # swaps with Mario
    layout.move(icons[ZELDA], 0)                                           # into the folder
    savedata, launcher = layout.files()
    assert launcher == files()[1] and savedata != files()[0]
    again = homemenu.Layout(savedata, launcher)
    assert [i.title_id for i in again.members(again.folders[0])] == [POKEMON, ZELDA]


class FakeLink:
    def __init__(self, savedata, launcher):
        self.store = {"/SaveData.dat": savedata, "/Launcher.dat": launcher}
        self.calls = []

    def read_file(self, archive, archive_path, path):
        return self.store[path]

    def write_file(self, archive, archive_path, path, data, commit=False):
        self.calls.append((path, archive, archive_path[1]))
        self.store[path] = data

    def note(self, text):
        pass

    def reboot(self):
        self.calls.append("reboot")


def test_write_backs_up_writes_savedata_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    link = FakeLink(*files())
    console = remote.Console("192.168.1.150", info={"region": "2", "id": "0a1b2c3d", "name": "Yanik"})
    layout = homemenu.read(link, region=2)
    zelda = next(i for i in layout.icons if i.title_id == ZELDA)
    layout.move(zelda, 0)
    saved = homemenu.write(link, console, layout)
    assert (saved / "SaveData.dat").read_bytes() == files()[0]
    assert saved.parent == tmp_path / "3ds" / "0A1B2C3D" / "homemenu-backups"
    assert link.calls == [("/SaveData.dat", 0x6, struct.pack("<3I", 1, 0x98, 0)), "reboot"]
    assert link.store["/Launcher.dat"] == files()[1]
    again = homemenu.Layout(link.store["/SaveData.dat"], link.store["/Launcher.dat"])
    assert [i.title_id for i in again.members(again.folders[0])] == [POKEMON, ZELDA]


def test_restore_refuses_a_backup_that_does_not_fit(tmp_path, monkeypatch):
    savedata = bytearray(files()[0])
    struct.pack_into("<h", savedata, 0xCB0, 1)       # Mario onto System Settings' place
    link = FakeLink(*files())
    with pytest.raises(InjectError, match="share"):
        homemenu.restore(link, 2, bytes(savedata))
    assert link.calls == []


def test_write_stops_when_the_3ds_changed_meanwhile(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    link = FakeLink(*files())
    layout = homemenu.read(link, region=2)
    link.store["/Launcher.dat"] = link.store["/Launcher.dat"][:-1] + b"!"
    with pytest.raises(InjectError, match="changed"):
        homemenu.write(link, remote.Console("x", info={"region": "2"}), layout)
    assert link.calls == []

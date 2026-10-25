"""The HOME Menu layout: which icon sits where, and the folders.

The HOME Menu keeps it in two files (format: 3dbrew "Home Menu"):
SaveData.dat in its extdata on the SD card holds the positions of titles
installed on the SD card; Launcher.dat in its system save data on the NAND
holds those of NAND titles (system apps), the Game Card slot and the folders
(name, tile position). Positions are linear and count separately in the main
grid and in each folder; both files share them.

Both files are kept byte for byte and only the known arrays are rewritten,
so everything else (themes, cursor, statistics) stays as it is.

Over vcinject Link only SaveData.dat can be written: the running HOME Menu
keeps its system save open (Launcher.dat is read from its container on the
NAND, see disa.py), and stopping it makes ns fail. So by default a Layout
only moves titles on the SD card – within the grid and into existing folders –
and the 3DS restarts after writing, as the HOME Menu reads the file only when
it starts (tested on a real console).
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from . import disa, remote
from .errors import InjectError

SLOTS = 360
FOLDERS = 60
MAIN = -1                      # the main grid (folder ID of icons outside folders)
FOLDER_CAPACITY = 60
_EMPTY = (0, 0xFFFFFFFFFFFFFFFF)

# by CFGU_SecureInfoGetRegion: JPN USA EUR AUS CHN KOR TWN
_HOME_MENU_IDS = {0: 0x82, 1: 0x8F, 2: 0x98, 3: 0x98, 4: 0xA1, 5: 0xA9, 6: 0xB1}

# SaveData.dat (version 4)
_SD_SIZES = (0x2DA0, 0x2CB0)
_SD_TID, _SD_POS, _SD_FOLDER, _SD_FOLDER_NUMBER = 0x8, 0xCB0, 0xF80, 0x12C8
# Launcher.dat
_LN_SIZE = 0x2490
_LN_CART, _LN_TID, _LN_POS, _LN_FOLDER = 0x2, 0x8, 0xD9A, 0x106A
_LN_NEXT_FOLDER, _LN_NEXT_FOLDER_MIRROR = 0xD80, 0xD85
_LN_FOLDER_POS, _LN_FOLDER_ROWS, _LN_FOLDER_NAME = 0x11DC, 0x1434, 0x1560
_NAME_BYTES, NAME_MAX = 0x22, 16


@dataclass
class Icon:
    nand: bool        # in Launcher.dat (NAND title) or SaveData.dat (SD title)
    slot: int
    title_id: int
    folder: int       # MAIN or a folder ID
    pos: int


@dataclass
class Folder:
    id: int
    name: str
    pos: int          # on the main grid
    rows: int

    @property
    def folder(self) -> int:
        return MAIN


class GameCard:
    """The Game Card slot's tile on the main grid."""

    def __init__(self, pos: int):
        self.pos = pos
        self.folder = MAIN


Item = Union[Icon, Folder, GameCard]


LOCKED = ("Folders, system apps and the Game Card can't be changed yet: the 3DS keeps their "
          "file locked while it runs. Games and apps on the SD card can be arranged and moved into "
          "the existing folders.")


class Layout:
    def __init__(self, savedata: bytes, launcher: bytes, nand_locked: bool = True):
        if len(savedata) not in _SD_SIZES or savedata[0] != 4:
            raise InjectError(f"SaveData.dat has an unknown format (version {savedata[:1].hex()}, "
                              f"0x{len(savedata):X} bytes).")
        if len(launcher) < _LN_SIZE:
            raise InjectError(f"Launcher.dat has an unknown format (0x{len(launcher):X} bytes).")
        self.savedata, self.launcher = bytes(savedata), bytes(launcher)
        self.icons = (self._icons(savedata, False, _SD_TID, _SD_POS, _SD_FOLDER)
                      + self._icons(launcher, True, _LN_TID, _LN_POS, _LN_FOLDER))
        self.folders: dict[int, Folder] = {}
        positions = struct.unpack_from(f"<{FOLDERS}h", launcher, _LN_FOLDER_POS)
        rows = launcher[_LN_FOLDER_ROWS:_LN_FOLDER_ROWS + FOLDERS]
        for fid in range(FOLDERS):
            if positions[fid] >= 0:
                start = _LN_FOLDER_NAME + fid * _NAME_BYTES
                name = launcher[start:start + _NAME_BYTES].decode("utf-16-le", "replace")
                self.folders[fid] = Folder(fid, name.split("\0", 1)[0], positions[fid], rows[fid])
        cart = struct.unpack_from("<H", launcher, _LN_CART)[0]
        self.game_card = GameCard(cart) if cart < SLOTS else None
        self._new_folders: list[int] = []
        self.nand_locked = nand_locked  # Launcher.dat can't be written (see above)

    @staticmethod
    def _icons(data: bytes, nand: bool, tid_at: int, pos_at: int, folder_at: int) -> list[Icon]:
        tids = struct.unpack_from(f"<{SLOTS}Q", data, tid_at)
        positions = struct.unpack_from(f"<{SLOTS}h", data, pos_at)
        folders = struct.unpack_from(f"<{SLOTS}b", data, folder_at)
        return [Icon(nand, slot, tids[slot], folders[slot], positions[slot]) for slot in range(SLOTS)
                if tids[slot] not in _EMPTY and positions[slot] >= 0]

    # --- reading --------------------------------------------------------------

    def items(self, folder: int = MAIN) -> list[Item]:
        """What is shown in the main grid or a folder, by position."""
        found: list[Item] = [icon for icon in self.icons if icon.folder == folder]
        if folder == MAIN:
            found += list(self.folders.values())
            if self.game_card:
                found.append(self.game_card)
        return sorted(found, key=lambda item: item.pos)

    def at(self, folder: int, pos: int) -> Item | None:
        return next((item for item in self.items(folder) if item.pos == pos), None)

    def members(self, folder: Folder) -> list[Icon]:
        return sorted((icon for icon in self.icons if icon.folder == folder.id), key=lambda i: i.pos)

    @staticmethod
    def capacity(folder: int) -> int:
        return SLOTS if folder == MAIN else FOLDER_CAPACITY

    def first_free(self, folder: int) -> int:
        used = {item.pos for item in self.items(folder)}
        free = next((p for p in range(self.capacity(folder)) if p not in used), None)
        if free is None:
            raise InjectError("There is no free place left there.")
        return free

    # --- editing --------------------------------------------------------------

    def move(self, item: Item, folder: int, pos: int | None = None) -> None:
        """Puts ``item`` at ``pos`` in ``folder`` (the first free place when None);
        an item already there swaps places with it."""
        if self.nand_locked and not (isinstance(item, Icon) and not item.nand):
            raise InjectError(LOCKED)
        if not isinstance(item, Icon) and folder != MAIN:
            raise InjectError("Folders and the Game Card can only be in the main grid.")
        if folder != MAIN and folder not in self.folders:
            raise InjectError("That folder does not exist.")
        if pos is None:
            pos = item.pos if item.folder == folder else self.first_free(folder)
        if not 0 <= pos < self.capacity(folder):
            raise InjectError("That place is outside the grid.")
        other = self.at(folder, pos)
        if other is not None and self.nand_locked and not (isinstance(other, Icon) and not other.nand):
            raise InjectError("That place belongs to a folder, a system app or the Game Card, which "
                              "can't be moved yet. Choose a free place or a game or app on the SD card.")
        if other is not None and other is not item:
            if not isinstance(other, Icon) and item.folder != MAIN:
                raise InjectError("A folder can't swap places with an icon inside a folder.")
            _place(other, item.folder, item.pos)
        _place(item, folder, pos)

    def create_folder(self, name: str) -> Folder:
        self._unlocked()
        free = [fid for fid in range(FOLDERS) if fid not in self.folders
                and not any(icon.folder == fid for icon in self.icons)]
        if not free:
            raise InjectError(f"The HOME Menu has no room for more than {FOLDERS} folders.")
        folder = Folder(free[0], _folder_name(name), self.first_free(MAIN), 2)
        self.folders[folder.id] = folder
        self._new_folders.append(folder.id)
        return folder

    def rename_folder(self, folder: Folder, name: str) -> None:
        self._unlocked()
        folder.name = _folder_name(name)

    def delete_folder(self, folder: Folder) -> None:
        """Deletes a folder; its icons go to free places in the main grid."""
        self._unlocked()
        members = self.members(folder)
        del self.folders[folder.id]
        for icon in members:
            _place(icon, MAIN, self.first_free(MAIN))
        if folder.id in self._new_folders:
            self._new_folders.remove(folder.id)

    def _unlocked(self) -> None:
        if self.nand_locked:
            raise InjectError(LOCKED)

    # --- writing --------------------------------------------------------------

    def validate(self) -> None:
        """Raises unless every item has its own place in an existing container."""
        seen: dict[tuple[int, int], Item] = {}
        everything: list[Item] = list(self.icons) + list(self.folders.values())
        if self.game_card:
            everything.append(self.game_card)
        for item in everything:
            if item.folder != MAIN and item.folder not in self.folders:
                raise InjectError(f"An icon is in a folder that does not exist ({item.folder}).")
            if not 0 <= item.pos < self.capacity(item.folder):
                raise InjectError(f"An icon is outside the grid (place {item.pos}).")
            if (item.folder, item.pos) in seen:
                raise InjectError(f"Two icons share place {item.pos}.")
            seen[item.folder, item.pos] = item

    def files(self) -> tuple[bytes, bytes]:
        """SaveData.dat and Launcher.dat with the changes."""
        self.validate()
        savedata, launcher = bytearray(self.savedata), bytearray(self.launcher)
        for icon in self.icons:
            data, pos_at, folder_at = ((launcher, _LN_POS, _LN_FOLDER) if icon.nand
                                       else (savedata, _SD_POS, _SD_FOLDER))
            struct.pack_into("<h", data, pos_at + icon.slot * 2, icon.pos)
            struct.pack_into("<b", data, folder_at + icon.slot, icon.folder)
        for fid in range(FOLDERS):
            folder = self.folders.get(fid)
            struct.pack_into("<h", launcher, _LN_FOLDER_POS + fid * 2, folder.pos if folder else -1)
            if folder is None:
                continue
            launcher[_LN_FOLDER_ROWS + fid] = folder.rows or 2
            name = folder.name.encode("utf-16-le").ljust(_NAME_BYTES, b"\0")
            launcher[_LN_FOLDER_NAME + fid * _NAME_BYTES:_LN_FOLDER_NAME + (fid + 1) * _NAME_BYTES] = name
        if self.game_card:
            struct.pack_into("<H", launcher, _LN_CART, self.game_card.pos)
        if self._new_folders:
            # new folders take the HOME Menu's next folder numbers, like folders made on the 3DS
            number = struct.unpack_from("<I", launcher, _LN_NEXT_FOLDER)[0]
            for fid in self._new_folders:
                struct.pack_into("<I", savedata, _SD_FOLDER_NUMBER + fid * 4, number)
                number += 1
            struct.pack_into("<I", launcher, _LN_NEXT_FOLDER, number)
            launcher[_LN_NEXT_FOLDER_MIRROR] = number & 0xFF
        return bytes(savedata), bytes(launcher)

    @property
    def changed(self) -> bool:
        return self.files() != (self.savedata, self.launcher)


def _place(item: Item, folder: int, pos: int) -> None:
    if isinstance(item, Icon):
        item.folder = folder
    item.pos = pos


def _folder_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise InjectError("A folder needs a name.")
    if len(name.encode("utf-16-le")) > NAME_MAX * 2:
        raise InjectError(f"Folder names have at most {NAME_MAX} characters.")
    return name


# --- on the 3DS --------------------------------------------------------------------

def _locations(region: int):
    home_id = _HOME_MENU_IDS.get(region)
    if home_id is None:
        raise InjectError(f"Unknown 3DS region ({region}).")
    return ((remote.ARCHIVE_EXTDATA, remote.binary_path(remote.MEDIA_SD, home_id, 0), "/SaveData.dat"),
            (remote.ARCHIVE_SYSTEM_SAVEDATA, remote.binary_path(remote.MEDIA_NAND, 0x00020000 | home_id),
             "/Launcher.dat"))


def backups_dir(console: remote.Console) -> Path:
    return console.folder / "homemenu-backups"


def read(link: remote.Link, region: int) -> Layout:
    sd_where, nand_where = _locations(region)
    savedata = link.read_file(*sd_where)
    try:
        launcher = link.read_file(*nand_where)
    except remote.RemoteError:
        # the running HOME Menu keeps its system save open: read its container on the NAND
        launcher = disa.read_file(_launcher_container(link, region), "Launcher.dat")
    return Layout(savedata, launcher)


def _launcher_container(link: remote.Link, region: int) -> bytes:
    home_id = _HOME_MENU_IDS[region]
    for id0, is_dir, _size in link.list_dir(remote.ARCHIVE_NAND_CTR_FS, None, "/data"):
        path = f"/data/{id0}/sysdata/{0x00020000 | home_id:08x}/00000000"
        if is_dir:
            try:
                return link.read_file(remote.ARCHIVE_NAND_CTR_FS, None, path)
            except remote.RemoteError:
                continue
    raise InjectError("The HOME Menu's save data was not found on the NAND.")


def backup(console: remote.Console, savedata: bytes, launcher: bytes) -> Path:
    folder = backups_dir(console) / time.strftime("%Y-%m-%d %H-%M-%S")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SaveData.dat").write_bytes(savedata)
    (folder / "Launcher.dat").write_bytes(launcher)
    return folder


def write(link: remote.Link, console: remote.Console, layout: Layout) -> Path:
    """Writes the changed layout (SaveData.dat) and restarts the 3DS. Backs up what is
    on the 3DS first and stops if it changed since ``layout`` was read. Returns the
    backup."""
    savedata, launcher = layout.files()
    if launcher != layout.launcher:
        raise InjectError(LOCKED)
    current = read(link, console.region)
    if (current.savedata, current.launcher) != (layout.savedata, layout.launcher):
        raise InjectError("The HOME Menu changed on the 3DS since it was read. Reload it and "
                          "make the changes again.")
    saved = backup(console, current.savedata, current.launcher)
    link.note("Saving the HOME Menu, then restarting")
    restore(link, console.region, savedata)
    return saved


def restore(link: remote.Link, region: int, savedata: bytes) -> None:
    """Writes SaveData.dat (e.g. from a backup) and restarts the 3DS, whose HOME Menu
    reads it when it starts. It has to fit the Launcher.dat that is on the 3DS."""
    current = read(link, region)
    Layout(savedata, current.launcher).validate()  # e.g. no place taken twice
    link.write_file(*_locations(region)[0], savedata)
    if link.read_file(*_locations(region)[0]) != savedata:
        link.write_file(*_locations(region)[0], current.savedata)
        raise InjectError("SaveData.dat did not arrive intact; the old one was put back.")
    link.reboot()

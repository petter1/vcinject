import zipfile

import pytest

from vcinject import remote, saves
from vcinject.errors import InjectError

GAME = remote.Title(0x00040000001B5100, 1, 0x1020, remote.MEDIA_GAME_CARD, "CTR-P-A2BA")


class FakeLink:
    """Save data of one title, with a sub folder, as vcinject Link would list it."""

    def __init__(self, files=None):
        self.files = dict(files if files is not None else {"/main": b"\x01" * 600, "/extra/box.bin": b"box"})
        self.commits = []

    def _check(self, archive, archive_path):
        assert archive == remote.ARCHIVE_USER_SAVEDATA
        assert archive_path == remote.binary_path(GAME.media, GAME.id & 0xFFFFFFFF, GAME.id >> 32)

    def list_dir(self, archive, archive_path, folder):
        self._check(archive, archive_path)
        if not self.files:
            raise remote.RemoteError(0xC8A04555, "Listing /")
        prefix = folder.rstrip("/") + "/"
        names = {path[len(prefix):].split("/")[0] for path in self.files if path.startswith(prefix)}
        return [(n, not (prefix + n) in self.files, 0) for n in sorted(names)]

    def read_file(self, archive, archive_path, path):
        self._check(archive, archive_path)
        return self.files[path]

    def write_file(self, archive, archive_path, path, data, commit=False):
        self._check(archive, archive_path)
        self.files[path] = data
        self.commits.append(commit)


def test_backup_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    console = remote.Console("192.168.1.150", info={"id": "022BBA8F"})
    link = FakeLink()
    backup = saves.backup(link, console, GAME)
    assert backup.parent == tmp_path / "3ds" / "022BBA8F" / "saves" / "00040000001B5100"
    with zipfile.ZipFile(backup) as archive:
        assert sorted(archive.namelist()) == ["extra/box.bin", "main"]
    assert saves.backups(console, GAME) == [backup]

    link.files["/main"] = b"later"                       # the game saved again meanwhile
    assert saves.restore(link, GAME, backup) == 2
    assert link.files["/main"] == b"\x01" * 600 and all(link.commits)


def test_no_save_and_bad_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    console = remote.Console("x", info={"id": "1"})
    with pytest.raises(InjectError, match="no save data"):
        saves.backup(FakeLink(files={}), console, GAME)
    evil = tmp_path / "evil.zip"
    evil.write_bytes(saves.zip_bytes({"/../../boot.firm": b"x"}))
    with pytest.raises(InjectError, match="strange path"):
        saves.restore(FakeLink(), GAME, evil)

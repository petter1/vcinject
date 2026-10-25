"""Save data of the titles on a 3DS: backed up on this computer and restored, over
vcinject Link (FS opens a title's save data as ARCHIVE_USER_SAVEDATA).

Backups are zip files per console and title:
<data>/3ds/<console>/saves/<title ID>/<date time>.zip
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from . import remote
from .errors import InjectError


def _archive(title: remote.Title):
    return remote.binary_path(title.media, title.id & 0xFFFFFFFF, title.id >> 32)


def read(link: remote.Link, title: remote.Title) -> dict[str, bytes]:
    """Every file of the title's save data, by path."""
    found: dict[str, bytes] = {}

    def walk(folder: str):
        for name, is_dir, _size in link.list_dir(remote.ARCHIVE_USER_SAVEDATA, _archive(title), folder):
            path = f"{folder.rstrip('/')}/{name}"
            if is_dir:
                walk(path)
            else:
                found[path] = link.read_file(remote.ARCHIVE_USER_SAVEDATA, _archive(title), path)

    try:
        walk("/")
    except remote.RemoteError as exc:
        raise InjectError(f"This title has no save data to back up ({exc}).") from exc
    return found


def folder(console: remote.Console, title: remote.Title) -> Path:
    return console.folder / "saves" / f"{title.id:016X}"


def backup(link: remote.Link, console: remote.Console, title: remote.Title) -> Path:
    files = read(link, title)
    if not files:
        raise InjectError("The save data is empty.")
    target = folder(console, title) / f"{time.strftime('%Y-%m-%d %H-%M-%S')}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path.lstrip("/"), data)
    return target


def backups(console: remote.Console, title: remote.Title) -> list[Path]:
    """The backups of this title on this console, newest first."""
    where = folder(console, title)
    return sorted(where.glob("*.zip"), reverse=True) if where.is_dir() else []


def restore(link: remote.Link, title: remote.Title, backup_file: Path) -> int:
    """Writes the files of a backup into the title's save data; returns how many."""
    with zipfile.ZipFile(backup_file) as archive:
        files = {"/" + name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    if not files:
        raise InjectError("The backup is empty.")
    for path, data in files.items():
        if ".." in path.split("/"):
            raise InjectError(f"The backup holds a strange path: {path}")
        # save data is transactional: each write is committed, or it is lost on closing
        link.write_file(remote.ARCHIVE_USER_SAVEDATA, _archive(title), path, data, commit=True)
    return len(files)


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """A zip of save files (for tests and exports)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(path.lstrip("/"), data)
    return buffer.getvalue()

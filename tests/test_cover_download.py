import os
import threading

import pytest

from vcinject import library, systems

GBA = systems.SYSTEMS["gba"]


def _roms(tmp_path, count):
    roms = []
    for i in range(count):
        path = tmp_path / f"game{i}.gba"
        path.write_bytes(bytes([i]) * 64)
        roms.append(library.LibraryRom(path, GBA, 64))
    return roms


def test_download_covers_skips_cached_and_survives_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    roms = _roms(tmp_path, 6)
    seen = []

    def analyze(rom, **kwargs):
        seen.append(rom.path.name)
        if rom.path.name == "game3.gba":
            raise RuntimeError("broken file")
        rom.sha1, rom.crc = "x" * 40, "00000000"
        library._remember(rom)
        return rom

    monkeypatch.setattr(library, "analyze", analyze)
    progress = []
    done, failed = library.download_covers(roms, [], threading.Event(),
                                           lambda d, t, n: progress.append((d, t)))
    assert (done, failed) == (6, 1)
    assert progress[-1] == (6, 6)
    seen.clear()
    library.download_covers(roms, [], threading.Event())  # second run: only what failed
    assert seen == ["game3.gba"]


def test_download_covers_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    roms = _roms(tmp_path, 40)
    stop = threading.Event()
    monkeypatch.setattr(library, "analyze", lambda rom, **kw: stop.set())
    done, _failed = library.download_covers(roms, [], stop, workers=1)
    assert done < 40


def test_cover_dialog_postpones_and_resumes(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import coversync
    from vcinject.gui.qtutil import gui_settings

    folder = tmp_path / "roms"
    folder.mkdir()
    _roms(folder, 3)
    gui_settings().setValue("library/folder", str(folder))
    monkeypatch.setattr(library, "missing_games", lambda roms, prefer=(): [])
    assert coversync.needed()
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = coversync.CoverDownloadDialog()
    dialog._count()
    qt.processEvents()
    assert dialog.mine_button.isEnabled()
    dialog.pages.setCurrentIndex(1)
    dialog.postpone()  # "Continue on next startup"
    assert gui_settings().value(coversync.STATE) == "pending" and coversync.needed()

    monkeypatch.setattr(library, "download_covers", lambda roms, games, stop, progress: (3, 0))
    dialog = coversync.CoverDownloadDialog()
    dialog._count()
    qt.processEvents()
    dialog.start("mine")
    for _ in range(50):
        qt.processEvents()
        if not coversync.needed():
            break
        threading.Event().wait(0.02)
    assert gui_settings().value(coversync.STATE) == "done" and not coversync.needed()

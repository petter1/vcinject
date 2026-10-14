import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_library_pop_out_button_opens_window(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject.gui import app as gui

    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = gui.MainWindow()
    window.show()
    button = next(b for b in window.library.findChildren(QtWidgets.QToolButton) if b.text() == "⧉")
    button.click()
    qt.processEvents()
    assert any(isinstance(w, gui.LibraryWindow) and w.isVisible() for w in qt.topLevelWidgets())
    for w in qt.topLevelWidgets():
        w.close()


def test_library_sorts_and_groups_series(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import app as gui

    roms = tmp_path / "roms"
    roms.mkdir()
    for name in ("Pokemon - Emerald Version.gba", "Bionicle.gba", "Pokemon Pinball.gba",
                 "Apple Game.gba"):
        (roms / name).write_bytes(b"\0" * 0x200)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = gui.LibraryPanel()
    panel.load(roms)
    order = lambda: [panel.list.item(i).text().split("\n")[0] for i in range(panel.list.count())]
    assert order() == ["Apple Game", "Bionicle", "Pokemon - Emerald Version", "Pokemon Pinball"]
    panel.sort.setCurrentIndex(panel.sort.findData("series"))
    assert order()[:2] == ["Pokemon - Emerald Version", "Pokemon Pinball"]
    series = [panel.series_filter.itemText(i) for i in range(panel.series_filter.count())]
    assert series == ["All series", "Pokemon (2)"]
    panel.series_filter.setCurrentIndex(1)
    visible = [panel.list.item(i).text().split("\n")[0] for i in range(panel.list.count())
               if not panel.list.item(i).isHidden()]
    assert visible == ["Pokemon - Emerald Version", "Pokemon Pinball"]
    panel.stop()

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402


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


def _texts(view):
    return [view.item(i).text().split("\n")[0].strip() for i in range(view.count())
            if not view.item(i).isHidden()]


def test_library_series_stack_opens_and_closes(tmp_path, monkeypatch):
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
    assert _texts(panel.list) == ["Apple Game", "Bionicle", "Pokemon - Emerald Version",
                                  "Pokemon Pinball"]
    series = [panel.series_filter.itemText(i) for i in range(panel.series_filter.count())]
    assert series == ["All series", "Pokemon (2)"]

    panel.stacks.setChecked(True)
    assert _texts(panel.list) == ["Apple Game", "Bionicle", "▸ Pokemon"]
    stack = panel.list.item(2)
    panel.list.itemClicked.emit(stack)  # click opens the stack in place
    assert _texts(panel.list) == ["Apple Game", "Bionicle", "▾ Pokemon",
                                  "Pokemon - Emerald Version", "Pokemon Pinball"]
    panel.list.itemClicked.emit(panel.list.item(2))
    assert _texts(panel.list) == ["Apple Game", "Bionicle", "▸ Pokemon"]

    panel.sort.setCurrentIndex(panel.sort.findData("folder"))
    panel.series_filter.setCurrentIndex(1)
    assert _texts(panel.list) == ["▸ Pokemon"]
    panel.stop()


def test_library_window_views(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    for name in ("Pokemon - Emerald Version.gba", "Pokemon Pinball.gba", "Bionicle.gba"):
        (roms / name).write_bytes(b"\0" * 0x200)
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(grid=True)
    panel.resize(900, 600)
    panel.show()
    panel.load(roms)
    panel.stacks.setChecked(True)
    for key in ("grid", "details", "coverflow"):
        panel.view_choice.setCurrentIndex(panel.view_choice.findData(key))
        qt.processEvents()
        assert panel.view is panel._views[key]
    details = panel._views["details"]
    assert details.topLevelItemCount() == 2 and details.topLevelItem(1).childCount() == 2
    details.topLevelItem(1).setExpanded(True)  # expanding the tree node opens the stack
    assert "stack:Pokemon" in panel._expanded
    flow = panel._views["coverflow"]
    flow.set_entries(panel._entries)
    assert [e.title for e in flow.flat] == ["Bionicle", "Pokemon", "Pokemon - Emerald Version",
                                            "Pokemon Pinball"]
    flow.go(2)
    flow.repaint()
    assert flow.current().title == "Pokemon - Emerald Version"
    panel.stop()


def test_missing_games_keep_their_own_covers(tmp_path, monkeypatch):
    """Styled copies are short-lived; their reused ids must not mix up covers."""
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from PIL import Image
    from vcinject import library, systems
    from vcinject.gui import libraryview

    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(autoload=False)
    gba = systems.SYSTEMS["gba"]
    games = [library.CatalogGame(f"Game {i}", [f"Game {i} (Europe)"], gba) for i in range(40)]
    panel._missing = panel._catalog = games
    for i, game in enumerate(games):  # a different brightness per game
        panel._missing_covers[libraryview._game_key(game)] = Image.new("RGB", (20, 20), (i * 6,) * 3)
    panel.missing_style.setCurrentIndex(panel.missing_style.findData("bw"))
    panel._refresh()
    qt.processEvents()
    for entry in panel._entries:
        shade = panel.icon(entry, QtCore.QSize(20, 20)).toImage().pixelColor(10, 10).red()
        assert shade == int(entry.title.split()[1]) * 6, entry.title
    panel.stop()

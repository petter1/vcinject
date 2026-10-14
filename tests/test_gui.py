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

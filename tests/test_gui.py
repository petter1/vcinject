import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402


def test_side_bar_switches_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject import remote
    from vcinject.gui import app as gui
    from vcinject.gui.homebrew_view import HomebrewPage
    from vcinject.gui.setup_view import SetupPage

    monkeypatch.setattr(remote, "discover", lambda *a, **k: [])
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = gui.MainWindow()
    window.show()
    assert window.pages.currentIndex() == 0 and window.games.currentWidget() is window.library
    assert window.install_button.text() == "Install via QR code"     # no 3DS online
    # the game page like the Homebrew pages: one install button, the rest in its menu
    menu = window.install_button.menu()
    menu.aboutToShow.emit()
    assert [a.text() for a in menu.actions() if a.text()] == ["QR code (FBI)", "Only build the CIA"]
    window._fill_on_3ds()
    assert "Set up a 3DS" in window.on_3ds_label.text()                # no 3DS known yet
    window.nav["setup"].click()
    assert isinstance(window.pages.currentWidget(), SetupPage)
    assert window.nav["setup"].isChecked() and not window.nav["games"].isChecked()
    window.nav["homebrew"].click()
    assert isinstance(window.pages.currentWidget(), HomebrewPage)
    window.nav["console"].click()
    assert window.pages.currentWidget() is window.console_window
    window.open_library_window()                                        # old entry point
    assert window.pages.currentIndex() == 0 and window.games.currentWidget() is window.library
    for _ in range(100):
        qt.processEvents()
        if not window.console_window._busy:
            break
        QtCore.QThread.msleep(10)
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
    for key in ("cards", "details", "coverflow"):
        panel.view_controls.set_mode(key)
        qt.processEvents()
        assert panel.view is panel._views[key]
    panel.view_controls.set_mode("rows")                 # rows are the card view's other look
    assert panel.view is panel._views["cards"] and panel.view.mode == "rows"
    cards = panel._views["cards"]
    assert [c.title for c in cards.cards()] == ["Bionicle", "▸ Pokemon"]
    cards.card_clicked.emit("stack:Pokemon")             # a click opens the stack
    assert [c.title for c in cards.cards()] == ["Bionicle", "▾ Pokemon", "Pokemon - Emerald Version",
                                                "Pokemon Pinball"]
    cards.card_clicked.emit("stack:Pokemon")
    assert not panel.options.isVisible()                 # the options open beside the covers
    panel.options_button.click()
    assert panel.options.isVisible() and panel.options.parent() is panel
    assert panel.options.geometry().left() >= panel.views.geometry().right()
    panel.options_button.click()
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


def test_missing_games_only_of_owned_series(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject import library, systems
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Pokemon - Ruby Version.gba").write_bytes(b"\0" * 0x200)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(autoload=False)
    panel.load(roms)
    gba = systems.SYSTEMS["gba"]
    panel._catalog = panel._missing = [
        library.CatalogGame(t, [f"{t} (Europe)"], gba)
        for t in ("Pokemon - Sapphire Version", "Pokemon Pinball", "Tetris World", "Tetris Worlds 2")]
    panel.show_missing.setChecked(True)
    panel._catalog_timer.stop()
    panel._refresh()
    titles = lambda: sorted(e.title for e in panel._entries if e.kind == "missing")
    assert len(titles()) == 4
    panel.collected_only.setChecked(True)  # Tetris is a series too, but none of it is owned
    assert titles() == ["Pokemon - Sapphire Version", "Pokemon Pinball"]
    panel.stop()


def test_owned_games_lead_their_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject import library, systems
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Pokemon - Sapphire Version.gba").write_bytes(b"\0" * 0x200)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(autoload=False)
    panel.owned_first.setChecked(False)  # even when owned and missing games are mixed
    panel.stacks.setChecked(True)
    panel.load(roms)
    gba = systems.SYSTEMS["gba"]
    panel._catalog = panel._missing = [library.CatalogGame(t, [f"{t} (Europe)"], gba)
                                       for t in ("Pokemon - Emerald Version", "Pokemon Pinball")]
    panel.show_missing.setChecked(True)
    panel._catalog_timer.stop()
    panel._refresh()
    [stack] = [e for e in panel._entries if e.kind == "stack"]
    assert [c.title for c in stack.children] == ["Pokemon - Sapphire Version",
                                                 "Pokemon - Emerald Version", "Pokemon Pinball"]
    panel.stop()


def test_opening_a_stack_closes_the_others(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    for name in ("Pokemon - Ruby.gba", "Pokemon Pinball.gba", "Sonic Advance.gba",
                 "Sonic Advance 2.gba"):
        (roms / name).write_bytes(b"\0" * 0x200)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(autoload=False)
    panel.stacks.setChecked(True)
    panel.load(roms)
    panel._toggle_stack("stack:Pokemon", True)
    panel._toggle_stack("stack:Sonic Advance", True)
    assert panel._expanded == {"stack:Sonic Advance"}
    assert [e.expanded for e in panel._entries if e.kind == "stack"] == [False, True]
    panel.stop()


def test_red_only_without_any_verified_version(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    for name in ("Game (USA).gba", "Game (Germany).gba", "Other (USA).gba"):
        (roms / name).write_bytes(name.encode() * 64)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(autoload=False)
    panel.load(roms)
    panel.stop()
    for rom in panel.roms:
        rom.release = rom.path.stem
        rom.verified = "good" if rom.path.stem == "Game (Germany)" else "unknown"
    panel._picked = {roms / "Game (USA).gba"}  # the unverified version is the one shown
    panel._refresh()
    states = {e.title: e.dump_state for e in panel._entries}
    assert states == {"Game": "", "Other": "bad"}  # Game has a verified German version


def test_rescan_keeps_covers_on_screen(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from PIL import Image
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    (roms / "Game (Europe).gba").write_bytes(b"\0" * 0x200)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(autoload=False)
    panel.load(roms)
    panel.stop()
    panel.roms[0].cover = Image.new("RGB", (8, 8), "red")
    panel.roms[0].release = "Game (Europe)"
    panel.load(roms)  # Rescan: the new scan starts with what was known
    assert panel.roms[0].cover is not None and panel.roms[0].release == "Game (Europe)"
    panel.stop()


def test_qr_panel_button_turns_into_done(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import app as gui

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cia = tmp_path / "game.cia"
    cia.write_bytes(b"\0" * 1024)
    panel = gui.QrPanel()
    panel.start(cia)
    assert panel.stop_button.text() == "Stop"
    panel._show_progress(512, 1024)
    assert panel.stop_button.text() == "Stop"
    panel._show_progress(1024, 1024)
    assert panel.stop_button.text() == "Done"
    panel.stop_button.click()  # Done stops serving and closes the panel
    assert panel.server is None


def test_home_grid_drag_swaps_and_drops_into_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from PySide6.QtTest import QTest

    from test_homemenu import MARIO, SETTINGS, ZELDA, files
    from vcinject import homemenu
    from vcinject.gui.console_view import HomeGrid

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    layout = homemenu.Layout(*files(), nand_locked=False)
    grid = HomeGrid()
    grid.show_layout(layout, {})
    grid.set_rows(2)
    changes = []
    grid.changed.connect(lambda: changes.append(1))

    def centre(pos):
        return grid._rect(pos).center()

    def drag(start, end):
        QTest.mousePress(grid, QtCore.Qt.LeftButton, pos=centre(start))
        QTest.mouseMove(grid, centre(end))
        QTest.mouseRelease(grid, QtCore.Qt.LeftButton, pos=centre(end))

    drag(3, 1)                       # Zelda onto System Settings: they swap
    icons = {i.title_id: i for i in layout.icons}
    assert (icons[ZELDA].pos, icons[SETTINGS].pos) == (1, 3)
    drag(2, 4)                       # Mario onto the folder: into it
    assert icons[MARIO].folder == 0
    assert len(changes) == 2 and layout.changed


def test_my_3ds_shows_a_card_per_3ds(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject import remote
    from vcinject.gui import app as gui
    from vcinject.gui.wifi_install import ConsoleWatcher

    monkeypatch.setattr(remote, "discover", lambda *a, **k: [])
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = gui.MainWindow()
    window.console_button.click()
    console = window.console_window
    assert console.pages.currentWidget() is console.devices
    assert "No 3DS found yet" in console.devices.hint.text()
    texts = [w.text() for w in console.devices.findChildren(QtWidgets.QPushButton)]
    assert sorted(texts) == ["Refresh", "Show QR code"]     # only the Add a 3DS card

    petter = remote.Console("192.168.1.150", info={"name": "Petter1", "id": "022BBA8F", "model": "1",
                                                   "version": "13", "system": "11.17.0-50E"})
    opened = []
    monkeypatch.setattr(console, "connect_to", opened.append)
    ConsoleWatcher.shared()._update([petter])
    open_button = next(b for b in console.devices.findChildren(QtWidgets.QPushButton) if b.text() == "Open")
    open_button.click()
    assert opened == [petter] and console.pages.currentIndex() == 1
    ConsoleWatcher.shared()._update([])                     # leave the shared watcher as found
    ConsoleWatcher.shared()._seen.clear()
    for w in qt.topLevelWidgets():
        w.close()


def test_update_buttons_and_link_start_the_update(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject import remote, status
    from vcinject.gui.console_view import ConsoleWindow, MirrorPanel

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = MirrorPanel()
    pressed = []
    panel.act.connect(pressed.append)
    link = status.Finding(status.UPDATE, "vcinject Link", "version 5 -> 6", "link")
    panel.set_report([link, status.Finding(status.OK, "System", "11.17.0-50E, the latest")])
    [button] = [b for b in panel.findChildren(QtWidgets.QPushButton) if b.text() == "Update"]
    assert button.isEnabled() and panel.update_all.isHidden()   # one update: no "Update all"
    button.click()
    assert pressed == [link]

    fbi = status.Finding(status.UPDATE, "FBI Reloaded", "2.6.1 -> 2.6.3", "app:fbi")
    panel.set_report([link, fbi])
    everything = []
    panel.act_all.connect(everything.append)
    assert not panel.update_all.isHidden() and panel.update_all.text() == "Update all (2)"
    panel.update_all.click()
    assert everything == [[link, fbi]]

    monkeypatch.setattr(remote, "discover", lambda *a, **k: [])
    qt = QtWidgets.QApplication.instance()
    window = ConsoleWindow(QtWidgets.QWidget())
    for _ in range(100):  # the search it starts with
        qt.processEvents()
        if not window._busy:
            break
        QtCore.QThread.msleep(10)
    window.console = remote.Console("192.168.1.150", info={"version": "5", "name": "Petter1"})
    window._show_status()
    assert "Update now" in window.status.text()
    started = []
    monkeypatch.setattr(window, "act_on", started.append)
    window.status.linkActivated.emit("update")
    assert [f.action for f in started] == ["link"]


def test_after_a_restart_the_outcome_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from test_homemenu import ZELDA, files
    from vcinject import homemenu, remote, status
    from vcinject.gui import console_view
    from vcinject.gui.console_view import ConsoleWindow, _arranged, _updated

    luma = status.Finding(status.UPDATE, "Luma3DS", "13.0.2 -> 13.4", "luma")
    fbi = status.Finding(status.UPDATE, "FBI Reloaded", "2.6.1 -> 2.6.3", "app:fbi")
    check = _updated([luma, fbi])
    ok, message = check([status.Finding(status.OK, "Luma3DS", "13.4, up to date"),
                         status.Finding(status.OK, "FBI Reloaded", "2.6.3, up to date")], None)
    assert ok and message == "Everything is up to date: Luma3DS 13.4, FBI Reloaded 2.6.3."
    ok, message = check([status.Finding(status.OK, "Luma3DS", "13.4, up to date"), fbi], None)
    assert not ok and "FBI Reloaded" in message

    layout = homemenu.Layout(*files())
    zelda = next(i for i in layout.icons if i.title_id == ZELDA)
    layout.move(zelda, 0)
    wanted = {(i.nand, i.title_id): (i.folder, i.pos) for i in layout.icons}
    assert _arranged(wanted)([], homemenu.Layout(*layout.files()))[0]
    assert not _arranged(wanted)([], homemenu.Layout(*files()))[0]       # still the old places
    assert not _arranged(wanted)([], None)[0]

    monkeypatch.setattr(remote, "discover", lambda *a, **k: [])
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    shown = []
    monkeypatch.setattr(console_view.QMessageBox, "information", lambda *a: shown.append(("ok", a[2])))
    monkeypatch.setattr(console_view.QMessageBox, "warning", lambda *a: shown.append(("warn", a[2])))
    window = ConsoleWindow(QtWidgets.QWidget())
    for _ in range(100):
        qt.processEvents()
        if not window._busy:
            break
        QtCore.QThread.msleep(10)
    window._verify = check
    window._report_outcome([status.Finding(status.OK, "Luma3DS", "13.4, up to date"),
                            status.Finding(status.OK, "FBI Reloaded", "2.6.3, up to date")])
    assert shown == [("ok", "Everything is up to date: Luma3DS 13.4, FBI Reloaded 2.6.3.")]
    assert window._verify is None
    window._report_outcome([])                       # nothing pending: nothing shown
    assert len(shown) == 1
    for _ in range(50):
        qt.processEvents()
        if not window._busy:
            break
        QtCore.QThread.msleep(10)


def test_wifi_install_button_follows_the_3dss(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject import remote
    from vcinject.gui.wifi_install import ConsoleWatcher, WifiInstallButton

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    watcher = ConsoleWatcher()
    button = WifiInstallButton(watcher)
    chosen = []
    button.install.connect(chosen.append)
    assert button.isHidden()                                   # no 3DS: no button

    petter = remote.Console("192.168.1.150", info={"name": "Petter1", "id": "022BBA8F", "model": "1"})
    watcher._update([petter])
    assert not button.isHidden() and button.text() == "Install on Petter1 (3DS XL)"
    button.click()
    assert chosen == [[petter]]

    other = remote.Console("192.168.1.151", info={"name": "Annas 3DS", "id": "11111111"})
    watcher._update([petter, other])
    assert button.text() == "Install on 3DS (2)"
    actions = {a.text(): a for a in button.menu().actions()}
    actions["All 2"].trigger()
    assert chosen[-1] == [other, petter]                       # sorted by name


def test_wifi_install_reports_each_3ds(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from test_remote import Fake3DS
    from vcinject import remote
    from vcinject.gui import wifi_install

    fake = Fake3DS()
    cia = tmp_path / "Golden Sun.cia"
    cia.write_bytes(b"\x01" * 4096)
    here = remote.Console("127.0.0.1", fake.port, {"name": "Petter1"})
    with __import__("socket").socket() as unused:
        unused.bind(("127.0.0.1", 0))
        gone = remote.Console("127.0.0.1", unused.getsockname()[1], {"name": "Gone"})
    seen = []
    results = wifi_install.install([here, gone], [cia], lambda text, sent, total: seen.append(text))
    assert results[0] == "Petter1: installed" and results[1].startswith("Gone: Could not connect")
    assert fake.installed == [(remote.MEDIA_SD, cia.read_bytes())]
    assert seen and seen[-1] == "Golden Sun → Petter1"


def test_missing_games_and_device_summaries(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject import library, remote, status, systems
    from vcinject.gui import app as gui
    from vcinject.gui.console_view import DevicesPage
    from vcinject.gui.libraryview import Entry

    monkeypatch.setattr(remote, "discover", lambda *a, **k: [])
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = gui.MainWindow()
    game = library.CatalogGame("Golden Sun", ["Golden Sun (Europe) (En,Fr,De)"], systems.SYSTEMS["gba"], "2001")
    window.library.missing_chosen.emit(Entry("missing:gba:gs", "missing", "Golden Sun", game=game))
    assert window.games.currentWidget() is window.missing_page
    assert window.missing_page.name.text() == "Golden Sun"
    assert "Not in your collection" in window.missing_page.state.text()
    assert "Golden Sun (Europe)" in window.missing_page.releases.text()
    page = window.missing_page
    assert not page.nexus.isVisibleTo(page)              # not before the site is known to have it
    page._show_nexus("missing:gba:other", "https://retrogamesnexus.com/games/x")
    assert not page.nexus.isVisibleTo(page)              # an answer for another game
    page._show_nexus("missing:gba:gs", None)
    assert not page.nexus.isVisibleTo(page)              # the site has no page
    page._show_nexus("missing:gba:gs", "https://retrogamesnexus.com/games/golden-sun")
    assert page.nexus.isVisibleTo(page)

    page = DevicesPage()
    page.reports["A"] = (0, [status.Finding(status.UPDATE, "Checkpoint", "3.7.4 -> 5.3.0"),
                             status.Finding(status.OK, "System", "11.17")], "")
    page.reports["B"] = (0, [status.Finding(status.OK, "System", "11.17")], "")
    assert "1 update" in page._summary("A") and "Checkpoint" in page._summary("A")
    assert "Everything up to date" in page._summary("B")
    assert "Not checked yet" in page._summary("C")


def test_gallery_console_chips_switch_on_and_off(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))
    from vcinject.gui import libraryview

    roms = tmp_path / "roms"
    roms.mkdir()
    for name in ("Golden Sun.gba", "Bionicle.gba", "Tetris.gb"):
        (roms / name).write_bytes(b"\0" * 0x200)
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = libraryview.LibraryPanel(grid=True)
    panel.load(roms)
    chips = panel._chips
    assert list(chips) == ["all", "gba", "gb"]
    assert chips["gba"].text() == "Game Boy Advance  2" and chips["gba"].isChecked()

    def shown():
        return sorted(e.title for e in panel._all_entries() if e.kind == "rom")

    assert shown() == ["Bionicle", "Golden Sun", "Tetris"]
    chips["gba"].click()                                   # off
    assert shown() == ["Tetris"] and not chips["gba"].isChecked() and not chips["all"].isChecked()
    chips["gb"].click()                                    # the last one stays on
    assert shown() == ["Tetris"] and chips["gb"].isChecked()
    chips["all"].click()
    assert shown() == ["Bionicle", "Golden Sun", "Tetris"] and chips["all"].isChecked()
    chips["gb"].click()
    again = libraryview.LibraryPanel(grid=True)            # the choice is kept
    again.load(roms)
    assert not again._chips["gb"].isChecked()
    panel.stop()
    again.stop()


def test_card_view_modes_zoom_and_clicks(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from vcinject.gui.cardview import Card, CardView, ViewControls

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    view = CardView(with_text=True)
    controls = ViewControls("test", 200, [view])
    view.resize(860, 600)
    view.show()
    view.set_cards([Card(i, f"Game {i}", "GBA · 2003", "About it", "✓ Petter1") for i in range(12)])
    assert view.mode == "cards" and view.cell_size().width() == (view.viewport().width() - 1) // 4
    controls.zoom.setValue(400)                                   # stepless: fewer, wider cards
    assert view.cell_size().width() == (view.viewport().width() - 1) // 2
    clicked = []
    view.card_clicked.connect(clicked.append)
    view.clicked.emit(view.model().index(3))
    assert clicked == [3]
    view.set_current(5)
    view.set_cards([Card(i, f"Game {i}") for i in range(12)])    # new texts keep the selection
    assert view.current_key() == 5
    controls.set_mode("list")
    assert view.mode == "list" and controls.zoom.isHidden()
    assert ViewControls("test").mode == "list"                    # remembered


def test_thumbnails_are_made_once(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage
    from vcinject.gui.qtutil import thumbnail

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    big = QImage(1000, 500, QImage.Format_RGB32)
    big.fill(0x336699)
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    big.save(buffer, "PNG")
    loads = []
    load = lambda: loads.append(1) or bytes(buffer.data())  # noqa: E731
    assert thumbnail("https://x/a.png", 300, load).size().toTuple() == (300, 150)
    assert thumbnail("https://x/a.png", 300, load).width() == 300
    assert loads == [1]                                           # the second came from the disk
    assert thumbnail("https://x/none.png", 300, lambda: None).isNull()

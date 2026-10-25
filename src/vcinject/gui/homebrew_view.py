"""The "3DS Homebrew" window: what the console needs, and apps to install by QR code."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import forwarder, homebrew, systems
from ..errors import InjectError
from . import wifi_install

REQUIREMENTS_HTML = """
<p><b>Every inject</b> needs a 3DS with <a href="https://3ds.hacks.guide">Luma3DS</a>
(custom firmware; its signature patches are on by default) and <b>FBI</b> to install the
<code>.cia</code> – also over the QR codes shown here.</p>
<p><b>What else, per system:</b></p>
<ul>
<li><b>Game Boy Advance</b> and <b>Game Boy / Color with Nintendo's emulator</b>: nothing –
they run like eShop Virtual Console games. Saves are kept in the title's own save data.</li>
<li><b>SNES</b> (snes9x) and <b>NES</b> (VirtuaNES) forwarders: nothing. The emulator is inside
each <code>.cia</code>; saves go to <code>sdmc:/nsui_forwarders_data/&lt;game&gt;</code>.</li>
<li><b>RetroArch forwarders</b> ({retroarch}): the 3DS's <b>sound firmware</b> must be on the SD
card (<code>sdmc:/3ds/dspfirm.cdc</code>), otherwise they have no sound or don't start.
Install <b>DSP1</b> below, start it once from the HOME Menu, done. RetroArch itself does
<i>not</i> need to be installed – it is inside each forwarder.</li>
</ul>
"""


class HomebrewDialog(QDialog):
    """Lists the console's requirements and homebrew apps; installing goes through the
    main window's QR panel."""
    _looked_up = Signal(str, object, str)    # app key, Release or None, error
    _downloaded = Signal(str, object, str)   # app key, Path or None, error
    _installed = Signal(str, str)            # app key, what happened
    _installing = Signal(str, str)           # app key, progress text

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("3DS Homebrew")
        self.setMinimumSize(640, 620)
        layout = QVBoxLayout(self)
        retroarch = ", ".join(sorted({s.name for s in systems.SYSTEMS.values()
                                      if s.forwarder in retroarch_kinds()})) or "Game Boy, Mega Drive"
        intro = QLabel(REQUIREMENTS_HTML.format(retroarch=retroarch), wordWrap=True,
                       openExternalLinks=True)
        intro.setTextFormat(Qt.RichText)
        layout.addWidget(intro)

        apps = QWidget()
        grid = QGridLayout(apps)
        grid.setColumnStretch(0, 1)
        self.info: dict[str, QLabel] = {}
        self.buttons: dict[str, QPushButton] = {}
        self.wifi_buttons: dict[str, wifi_install.WifiInstallButton] = {}
        for row, app in enumerate(homebrew.CATALOG):
            text = QLabel(f"<b>{app.name}</b> <span style='color:gray'>· "
                          f"<a href='https://github.com/{app.repo}'>{app.repo}</a></span><br>"
                          f"{app.purpose}"
                          + (f"<br><i>Needed for {app.needed_for}.</i>" if app.needed_for else ""),
                          wordWrap=True, openExternalLinks=True)
            info = QLabel("Looking up the newest version…", objectName="muted")
            button = QPushButton("Install via QR", enabled=False)
            button.clicked.connect(lambda _=False, key=app.key: self.install(key))
            wifi = wifi_install.WifiInstallButton(object_name="")
            wifi.setEnabled(False)  # once the release and its licence are known
            wifi.install.connect(lambda consoles, key=app.key: self.install_over_wifi(key, consoles))
            self.info[app.key], self.buttons[app.key] = info, button
            self.wifi_buttons[app.key] = wifi
            box = QVBoxLayout()
            box.addWidget(text)
            box.addWidget(info)
            grid.addLayout(box, row * 2, 0)
            buttons = QVBoxLayout()
            buttons.addWidget(button)
            buttons.addWidget(wifi)
            buttons.addStretch(1)
            grid.addLayout(buttons, row * 2, 1)
            line = QFrame(frameShape=QFrame.HLine)
            line.setStyleSheet("color: palette(midlight)")
            grid.addWidget(line, row * 2 + 1, 0, 1, 2)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(apps)
        layout.addWidget(scroll, 1)
        note = QLabel("Downloaded from each author's latest GitHub release; offered only for "
                      "projects with an open-source licence. Install also via Universal-Updater "
                      "on the 3DS for everything else.", wordWrap=True, objectName="muted")
        layout.addWidget(note)


        self._looked_up.connect(self._show_release)
        self._downloaded.connect(self._serve)
        self._installed.connect(lambda key, text: (self.info[key].setText(text),
                                                   self.wifi_buttons[key].setEnabled(True)))
        self._installing.connect(lambda key, text: self.info[key].setText(text))
        for app in homebrew.CATALOG:
            threading.Thread(target=self._look_up, args=(app,), daemon=True).start()

    def _look_up(self, app: homebrew.App):
        try:
            self._looked_up.emit(app.key, homebrew.release(app), "")
        except (InjectError, OSError, ValueError) as exc:
            self._looked_up.emit(app.key, None, str(exc))

    def _show_release(self, key: str, found, error: str):
        info, button = self.info[key], self.buttons[key]
        if found is None:
            info.setText(f"Not available right now ({error})")
            return
        info.setText(f"{found.tag} · {found.size / 1024 / 1024:.1f} MiB · licence {found.licence}")
        button.setEnabled(found.allowed)
        self.wifi_buttons[key].setEnabled(found.allowed)
        if not found.allowed:
            button.setToolTip("No open-source licence: get it from the author's page")

    def install(self, key: str):
        button = self.buttons[key]
        button.setEnabled(False)
        button.setText("Downloading…")

        def work():
            try:
                self._downloaded.emit(key, homebrew.download(homebrew.APPS[key]), "")
            except (InjectError, OSError) as exc:
                self._downloaded.emit(key, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def install_over_wifi(self, key: str, consoles: list):
        """Downloads the app and installs it on ``consoles`` over Wi-Fi."""
        app = homebrew.APPS[key]
        self.wifi_buttons[key].setEnabled(False)
        self.info[key].setText(f"Downloading {app.name}…")

        def progress(text, sent, total):
            self._installing.emit(key, f"Installing on {text.split('→')[-1].strip()}: "
                                       f"{sent * 100 // max(total, 1)} %")

        def work():
            try:
                path = homebrew.download(app)
                results = wifi_install.install(consoles, [path], progress)
                self._installed.emit(key, " · ".join(results))
            except (InjectError, OSError) as exc:
                self._installed.emit(key, f"Failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _serve(self, key: str, path, error: str):
        button = self.buttons[key]
        button.setText("Install via QR")
        button.setEnabled(True)
        if path is None:
            self.info[key].setText(f"Download failed: {error}")
            return
        self.main.show_section("games")  # the QR code appears on the game page
        self.main.games.setCurrentWidget(self.main.game_page)
        self.main.show_qr(path)


def retroarch_kinds() -> set[str]:
    """Forwarder templates that are RetroArch builds (they need the DSP firmware)."""
    kinds = set()
    for kind in forwarder.installed_kinds():
        try:
            if forwarder.ForwarderTemplate.load(kind).needs_dsp_firmware:
                kinds.add(kind)
        except InjectError:
            continue
    return kinds


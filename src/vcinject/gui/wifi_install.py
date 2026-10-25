"""Installing over Wi-Fi on every 3DS that runs vcinject Link – next to the QR code."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QMenu, QToolButton

from .. import remote
from ..errors import InjectError


class ConsoleWatcher(QObject):
    """Knows which 3DSs run vcinject Link right now (asks every few seconds, quietly)."""
    changed = Signal(list)   # [remote.Console]
    installed_changed = Signal()  # a title list was read again (installed.py)
    _found = Signal(list)

    TITLES_EVERY = 300  # seconds between reading a console's titles again

    INTERVAL = 10_000
    GRACE = 35  # seconds a 3DS counts as online after its last answer (busy apps don't answer)
    _shared: "ConsoleWatcher | None" = None

    @classmethod
    def shared(cls) -> "ConsoleWatcher":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def __init__(self):
        super().__init__()
        self.consoles: list[remote.Console] = []
        self._looking = False
        self._titles_read: dict[str, float] = {}  # console key -> when
        self._seen: dict[str, tuple[float, remote.Console]] = {}  # console key -> last answer
        self._found.connect(self._update)
        self._timer = QTimer(self, interval=self.INTERVAL, timeout=self.refresh)
        self._timer.start()
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        if self._looking:
            return
        self._looking = True

        def look():
            try:
                found = remote.discover(timeout=1.0, scan=False)
            except OSError:
                found = []
            self._found.emit(found)

        threading.Thread(target=look, daemon=True).start()

    def read_titles(self, console: remote.Console, force: bool = False):
        """Reads which titles ``console`` has (in the background), for installed.py."""
        import time
        if not force and time.time() - self._titles_read.get(console.key, 0) < self.TITLES_EVERY:
            return
        self._titles_read[console.key] = time.time()

        def read():
            from .. import installed
            try:
                with remote.Link(console.host, console.port) as link:
                    installed.remember(console, link.all_titles(product_codes=False))
            except (InjectError, OSError):
                self._titles_read.pop(console.key, None)
                return
            self.installed_changed.emit()

        threading.Thread(target=read, daemon=True).start()

    def _update(self, found: list):
        import time
        self._looking = False
        now = time.time()
        for console in found:
            self._seen[console.key] = (now, console)
            self.read_titles(console)
        found = sorted((c for seen, c in self._seen.values() if now - seen < self.GRACE),
                       key=lambda c: (c.name.lower(), c.key))
        if [(c.key, c.host) for c in found] != [(c.key, c.host) for c in self.consoles]:
            self.consoles = found
            self.changed.emit(found)


class WifiInstallButton(QToolButton):
    """'Install on <3DS>', or a menu to choose one or all when several run vcinject Link;
    hidden while none does."""
    install = Signal(list)   # the chosen [remote.Console]

    def __init__(self, watcher: ConsoleWatcher | None = None, object_name: str = "secondary"):
        super().__init__(objectName=object_name)
        self.setToolTip("Installs over Wi-Fi on the 3DS that runs vcinject Link – no QR code, "
                        "no FBI.")
        self.watcher = watcher or ConsoleWatcher.shared()
        self.watcher.changed.connect(self._show)
        self.clicked.connect(self._clicked)
        self._show(self.watcher.consoles)

    def _show(self, consoles: list):
        self.consoles = consoles
        self.setVisible(bool(consoles))
        if len(consoles) == 1:
            self.setText(f"Install on {consoles[0].label}")
            self.setPopupMode(QToolButton.DelayedPopup)
            self.setMenu(None)
            return
        self.setText(f"Install on 3DS ({len(consoles)})")
        menu = QMenu(self)
        for console in consoles:
            menu.addAction(f"{console.label}  · {console.host}", lambda c=console: self.install.emit([c]))
        menu.addSeparator()
        menu.addAction(f"All {len(consoles)}", lambda: self.install.emit(list(consoles)))
        self.setMenu(menu)
        self.setPopupMode(QToolButton.InstantPopup)

    def _clicked(self):
        if len(self.consoles) == 1:
            self.install.emit(list(self.consoles))


class InstallTarget(QComboBox):
    """Where installs go: a 3DS that runs vcinject Link, all of them, or a QR code for FBI.
    The choice is kept (by console) across starts."""
    QR = "qr"

    def __init__(self, watcher: ConsoleWatcher | None = None):
        super().__init__()
        from .qtutil import gui_settings
        self.settings = gui_settings()
        self.setMinimumWidth(220)
        self.setToolTip("Where Install puts a game: over Wi-Fi on a 3DS that runs vcinject Link, "
                        "or a QR code for FBI.")
        self.watcher = watcher or ConsoleWatcher.shared()
        self.watcher.changed.connect(self._fill)
        self.activated.connect(lambda: self.settings.setValue("install_target", self._key()))
        self._fill(self.watcher.consoles)

    def _key(self) -> str:
        data = self.currentData()
        if data == self.QR:
            return self.QR
        return "all" if data and len(data) > 1 else (data[0].key if data else "")

    def _fill(self, consoles: list):
        wanted = str(self.settings.value("install_target", ""))
        self.blockSignals(True)
        self.clear()
        for console in consoles:
            self.addItem(console.label, [console])
        if len(consoles) > 1:
            self.addItem(f"All {len(consoles)} 3DS", list(consoles))
        self.addItem("QR code (FBI)", self.QR)
        keys = [self._key_at(i) for i in range(self.count())]
        self.setCurrentIndex(keys.index(wanted) if wanted in keys else 0)
        self.blockSignals(False)
        self.currentIndexChanged.emit(self.currentIndex())

    def _key_at(self, index: int) -> str:
        data = self.itemData(index)
        if data == self.QR:
            return self.QR
        return "all" if len(data) > 1 else data[0].key

    def consoles(self) -> list | None:
        """The chosen 3DSs, or None for a QR code."""
        data = self.currentData()
        return None if data == self.QR or not data else list(data)

    def action_text(self) -> str:
        consoles = self.consoles()
        if consoles is None:
            return "Install via QR code"
        return f"Install on {consoles[0].label}" if len(consoles) == 1 else f"Install on all {len(consoles)}"


def install(consoles: list[remote.Console], paths: list[Path],
            progress: Callable[[str, int, int], None] = lambda text, sent, total: None) -> list[str]:
    """Installs ``paths`` on each console in turn (runs in the background); returns a line
    per console about how it went."""
    results = []
    for console in consoles:
        try:
            with remote.Link(console.host, console.port) as link:
                for path in paths:
                    link.note(f"Installing {path.stem[:40]}")
                    link.install(path, progress=lambda sent, total, path=path, console=console:
                                 progress(f"{path.stem} → {console.name}", sent, total))
                link.note("Installed: press HOME to see it")
            results.append(f"{console.name}: installed")
            ConsoleWatcher.shared().read_titles(console, force=True)
        except InjectError as exc:
            results.append(f"{console.name}: {exc}")
    return results

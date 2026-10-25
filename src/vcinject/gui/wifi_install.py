"""Installing over Wi-Fi on every 3DS that runs vcinject Link – next to the QR code."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMenu, QToolButton

from .. import remote
from ..errors import InjectError


class ConsoleWatcher(QObject):
    """Knows which 3DSs run vcinject Link right now (asks every few seconds, quietly)."""
    changed = Signal(list)   # [remote.Console]
    _found = Signal(list)

    INTERVAL = 10_000
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

    def _update(self, found: list):
        self._looking = False
        found = sorted(found, key=lambda c: c.name.lower())
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
        except InjectError as exc:
            results.append(f"{console.name}: {exc}")
    return results

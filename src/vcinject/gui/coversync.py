"""First start: download the library's covers before the app is used.

The user chooses between every cover (own games and every missing game of the
same systems) and only the covers of their own games; a progress bar follows.
"Continue on next startup" stops and resumes there – everything downloaded so
far is cached, so nothing is fetched twice.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import library
from .qtutil import gui_settings

STATE = "covers/state"   # "" (never asked), "pending" (continue next start), "done"
MODE = "covers/mode"     # "all" or "mine"


def needed() -> bool:
    """True when a ROM folder is set and its covers were not downloaded yet."""
    settings = gui_settings()
    folder = settings.value("library/folder", "")
    return bool(folder) and Path(folder).is_dir() and settings.value(STATE, "") != "done"


class CoverDownloadDialog(QDialog):
    _counted = Signal(object, object)       # roms, missing games
    _progress = Signal(int, int, str)       # done, total, current name
    _finished = Signal(int, int)            # done, failed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = gui_settings()
        self.setWindowTitle("Download game covers")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(230)
        self._stop = threading.Event()
        self.roms: list = []
        self.games: list = []
        self.pages = QStackedWidget()

        # page 1: what to download
        choose = QWidget()
        layout = QVBoxLayout(choose)
        heading = QLabel("<b>Download game covers</b>")
        self.counts = QLabel("Looking at your ROM folder…", wordWrap=True)
        explain = QLabel(
            "Covers are downloaded once and kept on this computer, so the library opens "
            "instantly afterwards. They come from the libretro thumbnail database "
            "(and RetroGamesNexus for ROM hacks).", wordWrap=True, objectName="muted")
        self.all_button = QPushButton("Download all covers", objectName="primary")
        self.all_button.setToolTip("Your games and every missing game of the same consoles, "
                                   "for “Show missing games”")
        self.mine_button = QPushButton("Only cache covers of my games")
        self.mine_button.setToolTip("Missing games load their covers later, when you scroll to them")
        for button in (self.all_button, self.mine_button):
            button.setEnabled(False)
        self.all_button.clicked.connect(lambda: self.start("all"))
        self.mine_button.clicked.connect(lambda: self.start("mine"))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.mine_button)
        buttons.addWidget(self.all_button)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addWidget(self.counts)
        layout.addWidget(explain)
        layout.addStretch(1)
        layout.addLayout(buttons)

        # page 2: progress
        running = QWidget()
        layout = QVBoxLayout(running)
        self.status = QLabel("Downloading covers…", wordWrap=True)
        self.bar = QProgressBar()
        self.current = QLabel(objectName="muted")
        self.current.setTextFormat(Qt.PlainText)
        later = QPushButton("Continue on next startup", clicked=self.postpone)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(later)
        layout.addWidget(self.status)
        layout.addWidget(self.bar)
        layout.addWidget(self.current)
        layout.addStretch(1)
        layout.addLayout(row)

        self.pages.addWidget(choose)
        self.pages.addWidget(running)
        outer = QVBoxLayout(self)
        outer.addWidget(self.pages)

        self._counted.connect(self._show_counts)
        self._progress.connect(self._show_progress)
        self._finished.connect(self._done)
        threading.Thread(target=self._count, daemon=True).start()

    # --- counting ---

    def _count(self):
        folder = Path(self.settings.value("library/folder", ""))
        try:
            roms = library.scan(folder)
        except Exception:
            roms = []
        language = QLocale.system().name().split("_")[0]
        prefer = tuple(t for t in (library.LANGUAGE_REGION.get(language), "Europe") if t)
        games = library.missing_games(roms, prefer) if roms else []
        self._counted.emit(roms, games)

    def _show_counts(self, roms, games):
        self.roms, self.games = roms, games
        consoles = len({rom.system.key for rom in roms})
        self.counts.setText(
            f"Your ROM folder has <b>{len(roms):,}</b> games for {consoles} console"
            f"{'s' if consoles != 1 else ''}. The database knows <b>{len(games):,}</b> more games "
            "of these consoles that are not in your folder.")
        self.all_button.setText(f"Download all covers ({len(roms) + len(games):,})")
        self.mine_button.setText(f"Only cache covers of my games ({len(roms):,})")
        for button in (self.all_button, self.mine_button):
            button.setEnabled(True)
        self.adjustSize()  # the counts made the text longer
        mode = self.settings.value(MODE, "")
        if self.settings.value(STATE, "") == "pending" and mode in ("all", "mine"):
            self.start(mode)  # resume what was postponed last time

    # --- downloading ---

    def start(self, mode: str):
        self.settings.setValue(MODE, mode)
        self.settings.setValue(STATE, "pending")  # until finished
        self.pages.setCurrentIndex(1)
        games = self.games if mode == "all" else []
        self.bar.setRange(0, max(1, len(self.roms) + len(games)))
        roms = self.roms

        def work():
            done, failed = library.download_covers(roms, games, self._stop, self._progress.emit)
            self._finished.emit(done, failed)

        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, done: int, total: int, name: str):
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        self.status.setText(f"Downloading covers… {done:,} of {total:,}")
        if name:
            self.current.setText(name)

    def _done(self, done: int, failed: int):
        if self._stop.is_set():
            return
        self.settings.setValue(STATE, "done")
        self.accept()

    def postpone(self):
        """Stop now and carry on at the next start."""
        self._stop.set()
        self.settings.setValue(STATE, "pending")
        self.reject()

    def closeEvent(self, event):
        if self.pages.currentIndex() == 1 and not self._stop.is_set():
            self.postpone()  # closing the window while downloading = continue later
        else:
            self.settings.setValue(STATE, self.settings.value(STATE, "") or "pending")
        super().closeEvent(event)

    def reject(self):
        if self.pages.currentIndex() == 1 and not self._stop.is_set():
            self._stop.set()
            self.settings.setValue(STATE, "pending")
        super().reject()

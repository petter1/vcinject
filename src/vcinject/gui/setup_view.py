"""The Setup page: everything vcinject needs, as one checklist."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import health
from .wifi_install import ConsoleWatcher

# groups of health checks, by resource prefix
GROUPS = (("Needed for every inject", ("tool:", "gba-template")),
          ("VC Console Look – the 3D banner of Virtual Console titles", ("vclook:",)),
          ("Emulator templates – for SNES, Mega Drive, Game Boy and NES", ("forwarder:",)),
          ("Nintendo's own emulator (Game Boy / Color, experimental)", ("gbvc:",)))


def _detail(check: health.Check) -> str:
    """What a check means, in plain words."""
    if check.ok:
        return "Ready"
    if check.auto:
        return "vcinject sets this up by itself – click to do it now"
    if check.resource.startswith("vclook:"):
        return "Not set up: choose a Virtual Console title or inject you own with this console's 3D banner"
    if check.resource.startswith(("forwarder:", "gbvc:")):
        return "Not set up: choose an inject of this system you own (.cia)"
    return "Missing"


class SetupPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.summary = QLabel()
        self.summary.setStyleSheet("font-size: 14px;")
        self.bar = QProgressBar(textVisible=False, maximumHeight=8, maximumWidth=220)
        head = QHBoxLayout()
        title = QLabel("Setup")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(self.summary, 1)
        head.addWidget(self.bar)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        inner = QWidget()
        inner.setLayout(self.body)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(inner)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(16)
        layout.addLayout(head)
        layout.addWidget(scroll, 1)
        ConsoleWatcher.shared().changed.connect(lambda _consoles: self.refresh())
        self.refresh()

    def refresh(self):
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        checks = health.check()
        consoles = ConsoleWatcher.shared().consoles
        rows = [("A 3DS in your Wi-Fi",
                 ", ".join(c.label for c in consoles) + " run vcinject Link." if consoles else
                 "Install vcinject Link on your 3DS: it then installs games over Wi-Fi.",
                 bool(consoles), "Add a 3DS…" if consoles else "Install vcinject Link…",
                 self.main.install_link)]
        ready = sum(c.ok for c in checks) + bool(consoles)
        total = len(checks) + 1
        self.summary.setText(f"{ready} of {total} ready" + (
            " – vcinject can build GBA injects" if all(c.ok for c in checks if c.required) else ""))
        self.bar.setRange(0, total)
        self.bar.setValue(ready)
        self.body.addWidget(self._group("Your 3DS", rows))
        for title, prefixes in GROUPS:
            group = [c for c in checks if c.resource.startswith(prefixes)]
            rows = [(c.label, _detail(c),
                     c.ok, "Set up…" if not c.ok else "Replace…",
                     lambda resource=c.resource: self._set_up(resource)) for c in group]
            self.body.addWidget(self._group(title, rows))
        tickets = [("Title IDs used on your 3DS",
                    "Taken from every 3DS that runs vcinject Link. Without it, import ticket.db "
                    "(GodMode9).", bool(consoles), "Import ticket.db…", self.main.import_tickets)]
        self.body.addWidget(self._group("Title IDs", tickets))
        self.body.addStretch(1)

    def _set_up(self, resource: str):
        self.main.open_setup(resource)
        self.refresh()

    def _group(self, title: str, rows: list) -> QFrame:
        frame = QFrame(objectName="card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(12)
        grid.setColumnStretch(1, 1)
        heading = QLabel(title, objectName="cardTitle")
        grid.addWidget(heading, 0, 0, 1, 3)
        for row, (label, detail, ok, action, slot) in enumerate(rows, start=1):
            mark = QLabel("✓" if ok else "○", alignment=Qt.AlignCenter)
            mark.setFixedSize(26, 26)
            mark.setStyleSheet(f"border-radius: 13px; font-weight: 700; "
                               f"background: {'#DCF3E6' if ok else '#ECE9F6'}; "
                               f"color: {'#135C36' if ok else '#3D2A99'};")
            text = QLabel(f"<b>{label}</b><br><span style='color:gray'>{detail}</span>", wordWrap=True)
            button = QPushButton(action, objectName="primary" if not ok else "secondary")
            button.clicked.connect(slot)
            grid.addWidget(mark, row, 0)
            grid.addWidget(text, row, 1)
            grid.addWidget(button, row, 2, alignment=Qt.AlignRight)
        return frame

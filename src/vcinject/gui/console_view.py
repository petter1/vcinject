"""The "3DS" window: a console running vcinject Link – its titles and its HOME Menu."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QLocale, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import homemenu, remote, smdh, status
from ..errors import InjectError
from ..homemenu import MAIN, Folder, GameCard, Icon
from .qtutil import gui_settings, to_pixmap

SETUP_HTML = """
<p>vcinject talks to your 3DS through <b>vcinject Link</b>, a small app for the 3DS.</p>
<ol>
<li>Install it once: <i>Install vcinject Link</i> shows a QR code for FBI.</li>
<li>Start <b>vcinject Link</b> from the HOME Menu. The 3DS needs to be in the same Wi-Fi.</li>
<li><i>Search again</i> here.</li>
</ol>
"""
_MEDIA = {remote.MEDIA_SD: "SD card", remote.MEDIA_NAND: "System", remote.MEDIA_GAME_CARD: "Game Card"}


def _updated(findings: list[status.Finding]):
    """A check for _await_console: are these updates in place now?"""
    titles = {f.title for f in findings}

    def verify(report: list[status.Finding], _layout):
        missing = [f.title for f in report if f.title in titles and f.level == status.UPDATE]
        if missing:
            return False, f"Not updated yet: {', '.join(missing)}."
        now = [f"{f.title} {f.detail.split(',')[0]}" for f in report if f.title in titles]
        return True, "Everything is up to date: " + ", ".join(now or sorted(titles)) + "."
    return verify


def _link_updated(report: list[status.Finding], _layout):
    return _updated([status.Finding(status.UPDATE, "vcinject Link", "")])(report, _layout)


def _arranged(wanted: dict):
    """A check for _await_console: is every icon where it was put?"""
    def verify(_report, layout):
        if layout is None:
            return False, "The HOME Menu could not be read after the restart."
        now = {(icon.nand, icon.title_id): (icon.folder, icon.pos) for icon in layout.icons}
        moved = [key for key, place in wanted.items() if key in now and now[key] != place]
        if moved:
            return False, f"The HOME Menu restarted, but {len(moved)} icon(s) are not where they were put."
        return True, "The HOME Menu is arranged as you saved it."
    return verify


def _has_icon(title: remote.Title) -> bool:
    """Games, apps and system applications (Settings, …) have icons; modules, updates
    and DLC don't."""
    return title.is_app or title.category == 0x00040010


def _language() -> str:
    code = QLocale.system().name().split("_")[0]
    return code if code in smdh.LANGUAGES else "en"


class TitleInfo:
    """What is shown of a title: its names and icon (from its SMDH)."""

    def __init__(self, title: remote.Title, data: bytes | None):
        self.title = title
        self.name, self.long_name, self.publisher = f"{title.id:016X}", "", ""
        self.icon: Image.Image | None = None
        if data:
            try:
                self.name, self.long_name, self.publisher = smdh.read_titles(data, _language())
                self.icon = smdh.read_icon(data)
            except InjectError:
                pass


# --- the HOME Menu grid -----------------------------------------------------------

class HomeGrid(QWidget):
    """The main grid or a folder like the HOME Menu shows it: column by column."""
    changed = Signal()
    open_folder = Signal(object)        # Folder
    context = Signal(object, QPoint)    # item, global position

    CELL = 60

    def __init__(self):
        super().__init__()
        self.layout_: homemenu.Layout | None = None
        self._busy = 0  # background jobs running
        self._verify = None  # after a restart: checks that it worked, see _await_console
        self.folder = MAIN
        self.rows = 3
        self.infos: dict[int, TitleInfo] = {}
        self.selected = None
        self._drag = None
        self._drag_at = QPoint()
        self.setMouseTracking(True)

    def show_layout(self, layout: homemenu.Layout | None, infos: dict[int, TitleInfo]):
        self.layout_, self.infos, self.selected = layout, infos, None
        self.folder = MAIN
        self._resize()

    def set_folder(self, folder: int):
        self.folder, self.selected = folder, None
        self._resize()

    def set_rows(self, rows: int):
        self.rows = rows
        self._resize()

    def _columns(self) -> int:
        if self.layout_ is None:
            return 0
        last = max((item.pos for item in self.layout_.items(self.folder)), default=-1)
        return last // self.rows + 2  # one empty column to drop onto

    def _resize(self):
        self.setFixedSize(max(1, self._columns()) * self.CELL + 8, self.rows * self.CELL + 8)
        self.update()

    def _rect(self, pos: int) -> QRect:
        return QRect(4 + pos // self.rows * self.CELL, 4 + pos % self.rows * self.CELL,
                     self.CELL, self.CELL)

    def _pos_at(self, point: QPoint) -> int | None:
        col, row = (point.x() - 4) // self.CELL, (point.y() - 4) // self.CELL
        if point.x() < 4 or point.y() < 4 or not 0 <= row < self.rows:
            return None
        pos = col * self.rows + row
        return pos if self.layout_ and pos < self.layout_.capacity(self.folder) else None

    def item_at(self, point: QPoint):
        pos = self._pos_at(point)
        return None if pos is None or self.layout_ is None else self.layout_.at(self.folder, pos)

    def label(self, item) -> str:
        if isinstance(item, Icon):
            info = self.infos.get(item.title_id)
            return info.long_name.replace("\n", " ") if info and info.long_name else (
                info.name if info else f"{item.title_id:016X}")
        if isinstance(item, Folder):
            return f"Folder: {item.name} ({len(self.layout_.members(item))})"
        return "Game Card"

    # --- drawing ----------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        if self.layout_ is None:
            return
        for col in range(self._columns()):
            for row in range(self.rows):
                rect = self._rect(col * self.rows + row).adjusted(3, 3, -3, -3)
                p.setPen(Qt.NoPen)
                p.setBrush(palette.alternateBase())
                p.drawRoundedRect(rect, 8, 8)
        for item in self.layout_.items(self.folder):
            rect = self._rect(item.pos).adjusted(6, 6, -6, -6)
            if item is self._drag:
                p.setOpacity(0.3)
            self._draw_item(p, item, rect)
            p.setOpacity(1)
            if item is self.selected:
                p.setPen(palette.highlight().color())
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 8, 8)
        if self._drag is not None:
            rect = QRect(self._drag_at - QPoint(24, 24), QSize(48, 48))
            self._draw_item(p, self._drag, rect)

    def _draw_item(self, p: QPainter, item, rect: QRect):
        if isinstance(item, Icon):
            info = self.infos.get(item.title_id)
            if info and info.icon is not None:
                p.drawPixmap(rect, to_pixmap(info.icon, rect.width(), rect.height()))
                return
            p.setBrush(QColor(150, 150, 160))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 6, 6)
            p.setPen(Qt.white)
            p.setFont(QFont(p.font().family(), 7))
            p.drawText(rect, Qt.AlignCenter | Qt.TextWrapAnywhere, f"{item.title_id:016X}")
            return
        p.setPen(Qt.NoPen)
        if isinstance(item, Folder):
            p.setBrush(QColor(236, 196, 84))
            p.drawRoundedRect(rect.adjusted(0, 8, 0, 0), 6, 6)
            p.drawRoundedRect(QRect(rect.left(), rect.top() + 3, rect.width() // 2, 10), 4, 4)
            # the first icons inside, small
            for i, icon in enumerate(self.layout_.members(item)[:4]):
                info = self.infos.get(icon.title_id)
                if info and info.icon is not None:
                    small = QRect(rect.left() + 5 + (i % 2) * 19, rect.top() + 13 + (i // 2) * 15, 16, 16)
                    p.drawPixmap(small, to_pixmap(info.icon, 16, 16))
            return
        p.setBrush(QColor(90, 90, 100))
        p.drawRoundedRect(rect.adjusted(4, 8, -4, -8), 4, 4)
        p.setPen(Qt.white)
        p.setFont(QFont(p.font().family(), 7))
        p.drawText(rect, Qt.AlignCenter, "Game\nCard")

    # --- mouse ------------------------------------------------------------------

    def mousePressEvent(self, event):
        item = self.item_at(event.position().toPoint())
        self.selected = item
        if event.button() == Qt.LeftButton and item is not None:
            self._drag, self._drag_at = item, event.position().toPoint()
        self.update()
        if event.button() == Qt.RightButton and item is not None:
            self.context.emit(item, event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        if self._drag is not None:
            self._drag_at = point
            self.update()
        else:
            item = self.item_at(point)
            self.setToolTip(self.label(item) if item is not None else "")

    def mouseReleaseEvent(self, event):
        item, self._drag = self._drag, None
        if item is None:
            return
        pos = self._pos_at(event.position().toPoint())
        target = None if pos is None else self.layout_.at(self.folder, pos)
        try:
            if pos is None or target is item:
                pass
            elif isinstance(target, Folder) and isinstance(item, Icon):
                self.layout_.move(item, target.id)         # dropped onto a folder: into it
                self.changed.emit()
            else:
                self.layout_.move(item, self.folder, pos)  # an item there swaps places
                self.changed.emit()
        except InjectError as exc:
            QMessageBox.warning(self, "Can't move it there", str(exc))
        self._resize()

    def mouseDoubleClickEvent(self, event):
        item = self.item_at(event.position().toPoint())
        if isinstance(item, Folder):
            self.open_folder.emit(item)


# --- the overview: the same as vcinject Link shows on the 3DS ----------------------

MIRROR_STYLE = """
#mirror { background: #18152b; border-radius: 12px; }
#mirror QLabel { color: #f0eefa; background: transparent; }
#mirror QLabel#muted { color: #9690b8; }
#mirror QLabel#heading { color: #ab98f0; font-weight: 600; }
#mirror QFrame#mirrorCard { background: #262142; border: 1px solid #38315e; border-radius: 10px; }
#mirror QPushButton { background: #765cce; color: white; border: none; border-radius: 6px;
                      padding: 4px 12px; font-weight: 600; }
#mirror QPushButton:hover { background: #8b74dd; }
#mirror QPushButton:disabled { background: #38315e; color: #9690b8; }
"""
_BADGES = {status.OK: ("✓", "#4cd08a"), status.UPDATE: ("↑", "#5aa9f5"),
           status.WARN: ("!", "#f5a623"), status.INFO: ("i", "#9690b8")}


def _badge(level: str) -> QLabel:
    glyph, color = _BADGES.get(level, _BADGES[status.INFO])
    badge = QLabel(glyph, alignment=Qt.AlignCenter)
    badge.setFixedSize(20, 20)
    badge.setStyleSheet(f"background: {color}; color: white; border-radius: 10px; font-weight: 700;")
    return badge


class MirrorPanel(QFrame):
    """Status report and activity, looking like vcinject Link on the 3DS."""
    act = Signal(object)    # Finding whose button was pressed
    act_all = Signal(list)  # all findings with an update

    def __init__(self):
        super().__init__(objectName="mirror")
        self.setStyleSheet(MIRROR_STYLE)
        self.headline = QLabel("Not connected")
        self.headline.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.subline = QLabel(objectName="muted", wordWrap=True)
        self.dot = QLabel()
        self.dot.setFixedSize(14, 14)
        state = QFrame(objectName="mirrorCard")
        row = QHBoxLayout(state)
        row.setContentsMargins(16, 12, 16, 12)
        row.addWidget(self.dot)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.headline)
        text.addWidget(self.subline)
        self.bar = QProgressBar(visible=False, textVisible=False, maximumHeight=8)
        self.bar.setStyleSheet("QProgressBar { background: #38315e; border: none; border-radius: 4px; }"
                               "QProgressBar::chunk { background: #ab98f0; border-radius: 4px; }")
        text.addWidget(self.bar)
        row.addLayout(text, 1)
        self.busy = QLabel(objectName="muted")
        row.addWidget(self.busy, alignment=Qt.AlignRight | Qt.AlignVCenter)

        report = QFrame(objectName="mirrorCard")
        box = QVBoxLayout(report)
        box.setContentsMargins(16, 12, 16, 12)
        head = QHBoxLayout()
        head.addWidget(QLabel("Status report", objectName="heading"), 1)
        self.update_all = QPushButton("Update all")
        self.update_all.setProperty("needs3ds", True)
        self.update_all.setVisible(False)
        self.update_all.clicked.connect(lambda: self.act_all.emit(self._updates))
        head.addWidget(self.update_all)
        box.addLayout(head)
        self._updates: list[status.Finding] = []
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(10)
        self.rows.setVerticalSpacing(8)
        self.rows.setColumnStretch(2, 1)
        box.addLayout(self.rows)
        box.addStretch(1)
        self.report_hint = QLabel("vcinject checks the 3DS when it connects.", objectName="muted")
        box.addWidget(self.report_hint)

        activity = QFrame(objectName="mirrorCard")
        log_box = QVBoxLayout(activity)
        log_box.setContentsMargins(16, 12, 16, 12)
        log_box.addWidget(QLabel("Activity", objectName="heading"))
        self.log = QLabel(objectName="muted", wordWrap=True, alignment=Qt.AlignTop | Qt.AlignLeft)
        self.log.setTextFormat(Qt.RichText)
        log_box.addWidget(self.log, 1)
        self._lines: list[str] = []

        columns = QHBoxLayout()
        columns.setSpacing(10)
        columns.addWidget(report, 3)
        columns.addWidget(activity, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(state)
        layout.addLayout(columns, 1)
        self.set_state("Not connected", "", "#e5534b")

    def set_state(self, headline: str, subline: str, color: str = "#4cd08a"):
        self.headline.setText(headline)
        self.subline.setText(subline)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 7px;")

    def set_busy(self, text: str, done: int = 0, total: int = 0):
        """What is going on (empty: nothing); a bar when ``total`` is known, else a moving one."""
        self.busy.setText(text)
        self.bar.setVisible(bool(text))
        self.bar.setRange(0, total)  # 0, 0: busy without a known end
        self.bar.setValue(done)

    def set_report(self, findings: list[status.Finding] | None):
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.report_hint.setVisible(not findings)
        self._updates = [f for f in findings or [] if f.level == status.UPDATE and f.action]
        self.update_all.setText(f"Update all ({len(self._updates)})")
        self.update_all.setVisible(len(self._updates) > 1)
        for row, finding in enumerate(findings or []):
            self.rows.addWidget(_badge(finding.level), row, 0)
            title = QLabel(f"<b>{finding.title}</b>")
            title.setToolTip(finding.hint)
            self.rows.addWidget(title, row, 1)
            detail = QLabel(finding.detail.replace("->", "→"), objectName="muted")
            detail.setToolTip(finding.hint)
            self.rows.addWidget(detail, row, 2)
            if finding.action:
                button = QPushButton("Update" if finding.level == status.UPDATE else "Install")
                button.setToolTip(finding.hint)
                button.setProperty("needs3ds", True)
                button.clicked.connect(lambda _=False, f=finding: self.act.emit(f))
                self.rows.addWidget(button, row, 3)

    def add_activity(self, text: str):
        from time import strftime
        self._lines = (self._lines + [f"<span style='color:#9690b8'>{strftime('%H:%M')}</span>&nbsp; "
                                      f"<span style='color:#f0eefa'>{text}</span>"])[-12:]
        self.log.setText("<br>".join(self._lines))


# --- the window -----------------------------------------------------------------

class ConsoleWindow(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("3DS")
        self.resize(900, 640)
        self.console: remote.Console | None = None
        self.titles: list[remote.Title] = []
        self.infos: dict[int, TitleInfo] = {}
        self.layout_: homemenu.Layout | None = None
        self._busy = 0  # background jobs running
        self._verify = None  # after a restart: checks that it worked, see _await_console

        self.status = QLabel("Searching for a 3DS running vcinject Link…", wordWrap=True)
        self.status.linkActivated.connect(lambda _link: self.update_link())
        self.consoles = QComboBox()
        self.consoles.setVisible(False)
        self.consoles.activated.connect(lambda: self.connect_to(self.consoles.currentData()))
        search = QPushButton("Search again", clicked=self.search)
        by_ip = QPushButton("Connect by IP…", clicked=self.connect_by_ip)
        setup = QPushButton("Install vcinject Link…", clicked=self.install_link)
        setup.setToolTip("Shows a QR code: FBI → Remote Install → Scan QR Code")
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.consoles)
        for button in (search, by_ip, setup):
            top.addWidget(button)

        self.setup_hint = QLabel(SETUP_HTML, wordWrap=True)
        self.setup_hint.setTextFormat(Qt.RichText)
        self.tabs = QTabWidget()
        self.mirror = MirrorPanel()
        self.mirror.act.connect(self.act_on)
        self.mirror.act_all.connect(self.update_everything)
        self.tabs.addTab(self.mirror, "Overview")
        self.tabs.addTab(self._titles_tab(), "Titles")
        self.tabs.addTab(self._home_tab(), "HOME Menu")
        self.tabs.setVisible(False)
        self.progress = QProgressBar(visible=False)
        self.busy_label = QLabel(objectName="muted")
        self._progress.connect(self._show_progress)
        self._icons_arrived.connect(self._add_icons)
        self._needs_3ds = [search, by_ip]

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.setup_hint)
        layout.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.busy_label)
        bottom.addWidget(self.progress, 1)
        layout.addLayout(bottom)
        self.search()

    # --- tabs -------------------------------------------------------------------

    def _titles_tab(self) -> QWidget:
        self.title_list = QTreeWidget()
        self.title_list.setHeaderLabels(["Title", "Publisher", "Title ID", "Where", "Version", "Size"])
        self.title_list.setIconSize(QSize(32, 32))
        self.title_list.setRootIsDecorated(False)
        self.title_list.setSortingEnabled(True)
        self.title_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.title_list.setColumnWidth(0, 300)
        self.show_system = QCheckBox("Show system titles, updates and DLC")
        self.show_system.toggled.connect(self._fill_titles)
        install = QPushButton("Install CIA…", clicked=self.install_cia)
        self.delete_button = QPushButton("Delete…", clicked=self.delete_titles)
        self._extra_needs_3ds = [install, self.delete_button]
        row = QHBoxLayout()
        row.addWidget(self.show_system, 1)
        row.addWidget(self.delete_button)
        row.addWidget(install)
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.addWidget(self.title_list, 1)
        box.addLayout(row)
        return tab

    def _home_tab(self) -> QWidget:
        self.grid = HomeGrid()
        self.grid.changed.connect(self._layout_changed)
        self.grid.open_folder.connect(lambda folder: self._show_folder(folder.id))
        self.grid.context.connect(self._item_menu)
        scroll = QScrollArea()
        scroll.setWidget(self.grid)
        scroll.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.place = QLabel()
        self.back = QPushButton("◀ HOME Menu", clicked=lambda: self._show_folder(MAIN), visible=False)
        rows = QSpinBox(minimum=1, maximum=6, prefix="Rows: ")
        rows.setValue(int(gui_settings().value("home_rows", 3)))
        rows.valueChanged.connect(self._set_rows)
        self.grid.rows = rows.value()
        head = QHBoxLayout()
        head.addWidget(self.back)
        head.addWidget(self.place, 1)
        head.addWidget(rows)

        new_folder = QPushButton("New folder…", clicked=self.new_folder, enabled=False)
        new_folder.setToolTip(homemenu.LOCKED)
        backups = QPushButton("Restore backup…", clicked=self.restore_backup)
        self._extra_needs_3ds.append(backups)
        self.discard_button = QPushButton("Discard changes", clicked=self.load_home_menu)
        self.save_button = QPushButton("Save to 3DS", objectName="primary", clicked=self.save_home_menu)
        self.save_button.setToolTip("Writes the layout and restarts the 3DS (the HOME Menu reads it "
                                    "only when it starts). The current one is backed up first.")
        row = QHBoxLayout()
        row.addWidget(new_folder)
        row.addWidget(backups)
        row.addStretch(1)
        row.addWidget(self.discard_button)
        row.addWidget(self.save_button)
        hint = QLabel("Drag games and apps to arrange them; drop one onto a folder to put it inside. "
                      "Double-click a folder to open it; right-click for more. Folders, system apps and "
                      "the Game Card stay where they are for now (the 3DS locks them while it runs).",
                      objectName="muted", wordWrap=True)
        tab = QWidget()
        box = QVBoxLayout(tab)
        box.addLayout(head)
        box.addWidget(scroll, 1)
        box.addWidget(hint)
        box.addLayout(row)
        self._layout_changed()
        return tab

    # --- background work ------------------------------------------------------------

    def _run(self, func, done, busy: str):
        """Runs ``func(link)`` (or ``func(None)`` with no console) in the background. The
        window stays usable; what needs the 3DS waits until it is done."""
        from .app import Task
        self._set_busy(busy)

        def work(log):
            if self.console is None:
                return func(None)
            with remote.Link(self.console.host, self.console.port) as link:
                link.on_note = log  # what the 3DS shows in its log appears here too
                return func(link)

        def finished(result, error):
            self._set_busy(None)
            done(result, error)

        Task(self, work, finished, on_log=self.mirror.add_activity)

    def _set_busy(self, text: str | None, done: int = 0, total: int = 0):
        """Shows what is going on (None: finished one job) and keeps the buttons that
        need the 3DS off meanwhile, so jobs never overlap."""
        if text is None:
            self._busy = max(0, self._busy - 1)
            text = ""
        elif not done and not total:
            self._busy += 1
        busy = self._busy > 0
        for button in self._needs_3ds + self._extra_needs_3ds + self.mirror.findChildren(QPushButton):
            if button.property("needs3ds") or button in self._needs_3ds + self._extra_needs_3ds:
                button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self._has_changes())
        self.busy_label.setText(text)
        self.progress.setVisible(bool(text))
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.mirror.set_busy(text, done, total)

    def _show_status(self, error: str = ""):
        if error:
            self.status.setText(error)
            self.mirror.set_state("Not connected", error, "#e5534b")
            return
        if self.console is None:
            self.status.setText("No 3DS found.")
            self.mirror.set_state("Not connected", "No 3DS found.", "#e5534b")
            return
        self.mirror.set_state(f"Connected to {self.console.name}",
                              f"{self.console.host} · vcinject Link {self.console.info.get('version', '?')}")
        info = self.console.info
        free = int(info.get("sd_free", 0)) / 2**30
        self.status.setText(f"<b>{self.console.name}</b> · {self.console.host} · "
                            f"system {info.get('system', '?')} · {free:.1f} GiB free on the SD card"
                            + (f"<br><span style='color:#c0392b'>vcinject Link on this 3DS is outdated "
                               f"(version {info.get('version', '?')}, now {remote.LINK_VERSION}):</span> "
                               "<a href='update'>Update now</a>" if self.console.outdated else ""))

    # --- connecting ------------------------------------------------------------------

    def search(self):
        self.console = None
        self._run(lambda _link: remote.discover(), self._found, "Searching for a 3DS running vcinject Link…")

    def connect_by_ip(self):
        host, ok = QInputDialog.getText(self, "Connect by IP", "The 3DS's IP address (shown in "
                                        "vcinject Link):", text=str(gui_settings().value("3ds_host", "")))
        if ok and host.strip():
            self.console = None
            self._run(lambda _link: remote.discover(timeout=2, hosts=[host.strip()]), self._found,
                      f"Connecting to {host.strip()}…")

    def _found(self, consoles, error: str):
        consoles = consoles or []
        self.consoles.clear()
        for console in consoles:
            self.consoles.addItem(f"{console.name} ({console.host})", console)
        self.consoles.setVisible(len(consoles) > 1)
        if not consoles:
            self.tabs.setVisible(False)
            self.setup_hint.setVisible(True)
            self._show_status(error or "No 3DS found. Is vcinject Link open on it, in the same Wi-Fi?")
            return
        self.connect_to(consoles[0])

    def connect_to(self, console: remote.Console):
        self.console = console
        gui_settings().setValue("3ds_host", console.host)
        self.setup_hint.setVisible(False)
        self.tabs.setVisible(True)
        self._show_status()
        self.load_titles()

    def install_link(self):
        self.main.show_qr(remote.CIA)
        self.main.raise_()

    # --- titles ----------------------------------------------------------------------

    def load_titles(self, then=None):
        """Titles first (fast), then the HOME Menu, the icons that are not cached yet
        and the status report, each in the background."""
        def work(link):
            info, titles = link.info(), link.all_titles(product_codes=False)
            link.note(f"Read {sum(t.is_app for t in titles)} games and apps")
            remote.reserve_title_ids(self.console, titles)
            return info, titles

        def done(result, error):
            if result is None:
                self._show_status(f"Reading the titles failed: {error}")
                return
            self.console.info.update(result[0])
            self.titles = result[1]
            self.infos = {t.id: TitleInfo(t, None) for t in self.titles if _has_icon(t)}
            self._show_status()
            self._fill_titles()
            if then:
                then()
            else:
                # the report matters most and is quick; the icons may take a while the first time
                self.load_home_menu(then=lambda: self.check_status(then=self.load_icons))

        self._run(work, done, "Reading what is installed…")

    _icons_arrived = Signal(object)  # {title ID: SMDH or None}

    def load_icons(self, then=None):
        titles = [t for t in self.titles if _has_icon(t)]

        def work(link):
            def batch(found, done, total):
                self._icons_arrived.emit(found)
                if total:
                    self._progress.emit(done, total)
            link.cached_smdhs(titles, self.console, on_batch=batch)
            return True

        def done(_result, error):
            if error:
                self.mirror.add_activity(f"Loading icons failed: {error}")
            if then:
                then()

        self._run(work, done, "Loading icons…")

    def _add_icons(self, found: dict):
        by_id = {t.id: t for t in self.titles}
        for title_id, data in found.items():
            if title_id in by_id:
                self.infos[title_id] = TitleInfo(by_id[title_id], data)
        self._fill_titles()
        self.grid.update()

    def check_status(self, then=None):
        """Builds the status report and sends it to the 3DS, which shows the same."""
        titles = self.titles

        def work(link):
            findings = status.check(link, self.console, titles)
            link.report(status.report_text(findings))
            updates = sum(f.level == status.UPDATE for f in findings)
            link.note(f"Checked for updates: {updates or 'none'} available" if updates
                      else "Checked for updates: all up to date")
            return findings

        def done(findings, error):
            self.mirror.set_report(findings)
            if findings is None:
                self.mirror.add_activity(f"Checking for updates failed: {error}")
            self._show_status()
            self._report_outcome(findings or [])
            if then:
                then()

        self._run(work, done, "Checking for updates…")

    def act_on(self, finding: status.Finding):
        if QMessageBox.question(self, finding.title, f"{finding.hint or finding.title}\n\nGo ahead?") \
                != QMessageBox.Yes:
            return

        def done(result, error):
            if result is None:
                QMessageBox.critical(self, finding.title, error)
                return
            message, restart = result
            if restart == status.RESTART_APP:
                self._offer_restart_app(message, _updated([finding]))
            elif restart == status.REBOOT:
                self._offer_reboot(message, _updated([finding]))
            else:
                QMessageBox.information(self, finding.title, message)
                self.load_titles(then=lambda: self.check_status())

        self._run(lambda link: status.apply(link, self.console, finding,
                                            progress=lambda sent, total: self._progress.emit(sent, total)),
                  done, f"{finding.title}…")

    def update_everything(self, findings: list[status.Finding]):
        """Every update in one go: the apps, then Luma3DS, vcinject Link last (it does the
        work); one restart at the end."""
        order = {"luma": 1, "link": 2}
        findings = sorted(findings, key=lambda f: order.get(f.action, 0))
        names = "\n".join(f"• {f.title}  {f.detail.replace('->', '→')}" for f in findings)
        if QMessageBox.question(self, "Update all", f"Install these updates over Wi-Fi?\n\n{names}") \
                != QMessageBox.Yes:
            return

        def work(link):
            done, failed, restarts = [], [], set()
            for finding in findings:
                try:
                    _message, restart = status.apply(
                        link, self.console, finding,
                        progress=lambda sent, total: self._progress.emit(sent, total))
                    done.append(finding.title)
                    restarts.add(restart)
                except InjectError as exc:
                    failed.append(f"{finding.title}: {exc}")
            link.note(f"Updated {len(done)} of {len(findings)}")
            return done, failed, restarts

        def finished(result, error):
            if result is None:
                QMessageBox.critical(self, "Update all", error)
                return
            done, failed, restarts = result
            message = f"Updated: {', '.join(done) or 'nothing'}." + (
                "\n\nFailed:\n" + "\n".join(failed) if failed else "")
            updated = [f for f in findings if f.title in done]
            if status.REBOOT in restarts:  # the restart also starts the new vcinject Link
                self._offer_reboot(message, _updated(updated))
            elif status.RESTART_APP in restarts:
                self._offer_restart_app(message, _updated(updated))
            else:
                QMessageBox.information(self, "Update all", message)
                self.load_titles(then=lambda: self.check_status())

        self._run(work, finished, "Updating everything…")

    def update_link(self):
        """Installs the vcinject Link that comes with vcinject over Wi-Fi."""
        self.act_on(status.Finding(status.UPDATE, "vcinject Link", "", "link",
                                   "Installs the new vcinject Link over Wi-Fi; then it restarts."))

    def _offer_restart_app(self, message: str, verify=None):
        if QMessageBox.question(self, "vcinject Link", f"{message}\n\nRestart vcinject Link on the "
                                "3DS now?") != QMessageBox.Yes:
            self.status.setText("Restart vcinject Link on the 3DS when it suits you (START, then "
                                "open it again).")
            return

        def done(_result, error):
            if error:  # vcinject Link before version 5 can't restart itself
                QMessageBox.information(self, "vcinject Link", "This version of vcinject Link can't "
                                        "restart itself yet: press START on the 3DS and open it again "
                                        "from the HOME Menu. vcinject reconnects by itself.")
            self.mirror.add_activity("vcinject Link is restarting")
            self._await_console("vcinject Link is restarting", verify or _link_updated)

        self._run(lambda link: link.restart_app(), done, "Restarting vcinject Link…")

    def _offer_reboot(self, message: str, verify=None):
        if QMessageBox.question(self, "Restart the 3DS", f"{message}\n\nRestart the 3DS now?") \
                != QMessageBox.Yes:
            self.status.setText("The update counts from the next restart of the 3DS.")
            return

        def done(_result, error):
            if error:
                self.status.setText(error)
                return
            self.mirror.add_activity("The 3DS is restarting")
            self._await_console("The 3DS is restarting", verify)

        self._run(lambda link: link.reboot(), done, "Restarting the 3DS…")

    def _await_console(self, waiting: str, verify, patience: float = 600):
        """After a restart: looks for the 3DS until it is back (vcinject Link may first have to
        be opened on it), loads everything as when connecting, and then shows what
        ``verify(findings, layout)`` -> (ok, message) says about the outcome."""
        import time
        host = self.console.host if self.console else None
        self.console = None
        self._verify = verify
        self.mirror.set_state(waiting, "vcinject reconnects by itself as soon as vcinject Link runs "
                              "on the 3DS again.", "#f5a623")
        self.status.setText(f"{waiting}. vcinject reconnects by itself.")
        given_up = time.monotonic() + patience

        def found(consoles, _error):
            if consoles:
                self.mirror.add_activity("The 3DS is back")
                self._found(consoles, "")  # loads it all; check_status then runs the check
            elif time.monotonic() > given_up:
                self._verify = None
                QMessageBox.information(self, "3DS", "The 3DS did not come back. Open vcinject Link "
                                        "on it, then Search again.")
            else:
                QTimer.singleShot(4000, look)

        def look():
            if self.isVisible() and self.console is None:
                self._run(lambda _link: remote.discover(timeout=1.5, scan=False), found,
                          f"{waiting}: waiting for vcinject Link…")

        QTimer.singleShot(5000, look)

    def _report_outcome(self, findings: list[status.Finding]):
        verify, self._verify = self._verify, None
        if verify is None:
            return
        ok, message = verify(findings, self.layout_)
        self.mirror.add_activity(message)
        self.status.setText(message)
        self._run(lambda link: link.note(message[:60]), lambda _r, _e: None, "Done")
        if ok:
            QMessageBox.information(self, "Done", message)
        else:
            QMessageBox.warning(self, "Not quite", message)

    def _fill_titles(self):
        self.title_list.clear()
        for title in self.titles:
            if not title.is_app and not self.show_system.isChecked():
                continue
            info = self.infos.get(title.id) or TitleInfo(title, None)
            item = QTreeWidgetItem([info.name, info.publisher, f"{title.id:016X}",
                                    _MEDIA.get(title.media, "?"), str(title.version),
                                    f"{title.size / 2**20:.1f} MiB"])
            item.setData(0, Qt.UserRole, title)
            item.setToolTip(0, info.long_name)
            if info.icon is not None:
                item.setIcon(0, to_pixmap(info.icon, 32, 32))
            self.title_list.addTopLevelItem(item)
        self.title_list.sortItems(0, Qt.AscendingOrder)

    def install_cia(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Install on the 3DS", "", "CIA (*.cia)")
        if not paths:
            return
        def work(link):
            for path in paths:
                link.note(f"Installing {Path(path).stem}")
                link.install(Path(path), progress=lambda sent, total: self._progress.emit(sent, total))
            return True

        def done(result, error):
            if result is None:
                QMessageBox.critical(self, "Install failed", error)
            self.load_titles()

        self._run(work, done, f"Installing {len(paths)} CIA(s)…")

    _progress = Signal(int, int)

    def _show_progress(self, done: int, total: int):
        text = self.busy_label.text().split(" (")[0]
        if total > 1 << 20:  # bytes of an install
            self._set_busy(f"{text} ({done / 1e6:.1f} / {total / 1e6:.1f} MB)", done // 1024, total // 1024)
        else:
            self._set_busy(f"{text} ({done} / {total})", done, total)

    def delete_titles(self):
        chosen = [item.data(0, Qt.UserRole) for item in self.title_list.selectedItems()]
        chosen = [title for title in chosen if not title.is_system]
        if not chosen:
            QMessageBox.information(self, "Delete", "Select the games or apps to delete. System "
                                    "titles can't be deleted here.")
            return
        names = "\n".join(self.infos[t.id].name if t.id in self.infos else f"{t.id:016X}" for t in chosen)
        if QMessageBox.question(self, "Delete from the 3DS", f"Delete from the 3DS?\n\n{names}\n\n"
                                "Their save data is deleted too.") != QMessageBox.Yes:
            return

        def work(link):
            for title in chosen:
                link.note(f"Deleting {self.infos[title.id].name if title.id in self.infos else hex(title.id)}")
                link.delete(title)
            return True

        def done(_result, error):
            if error:
                QMessageBox.critical(self, "Delete failed", error)
            self.load_titles()

        self._run(work, done, "Deleting…")

    # --- HOME Menu ----------------------------------------------------------------

    def load_home_menu(self, then=None):
        def done(layout, error):
            self.layout_ = layout
            self.grid.show_layout(layout, self.infos)
            self._show_folder(MAIN)
            self._show_status()
            if layout is None:
                self.place.setText(f"The HOME Menu could not be read: {error}")
            if then:
                then()

        def work(link):
            link.note("Reading the HOME Menu")
            return homemenu.read(link, self.console.region)

        self._run(work, done, "Reading the HOME Menu…")

    def _set_rows(self, rows: int):
        gui_settings().setValue("home_rows", rows)
        self.grid.set_rows(rows)

    def _show_folder(self, folder: int):
        self.grid.set_folder(folder)
        self.back.setVisible(folder != MAIN)
        if self.layout_ is not None:
            self.place.setText("HOME Menu" if folder == MAIN else f"Folder: <b>{self.layout_.folders[folder].name}</b>")

    def _has_changes(self) -> bool:
        try:
            return self.layout_ is not None and self.layout_.changed
        except InjectError:
            return True

    def _layout_changed(self):
        try:
            changed = self.layout_ is not None and self.layout_.changed
        except InjectError:
            changed = True
        self.save_button.setEnabled(changed and not self._busy)
        self.discard_button.setEnabled(changed)
        self.grid.update()

    def _item_menu(self, item, at: QPoint):
        menu = QMenu(self)
        layout = self.layout_
        if isinstance(item, Icon) and not item.nand:
            into = menu.addMenu("Move to folder")
            for folder in sorted(layout.folders.values(), key=lambda f: f.name.lower()):
                if folder.id != item.folder:
                    into.addAction(folder.name, lambda f=folder: self._move(item, f.id))
            if not layout.nand_locked:
                into.addSeparator()
                into.addAction("New folder…", lambda: self.new_folder(item))
            if item.folder != MAIN:
                menu.addAction("Move to the HOME Menu", lambda: self._move(item, MAIN))
        elif isinstance(item, Folder):
            menu.addAction("Open", lambda: self._show_folder(item.id))
            if not layout.nand_locked:
                menu.addAction("Rename…", lambda: self.rename_folder(item))
                menu.addAction("Delete folder (keeps its icons)", lambda: self._edit(layout.delete_folder, item))
        if not menu.isEmpty():
            menu.exec(at)

    def _edit(self, change, *args):
        try:
            change(*args)
        except InjectError as exc:
            QMessageBox.warning(self, "HOME Menu", str(exc))
        self._show_folder(self.grid.folder if self.grid.folder in self.layout_.folders else MAIN)
        self._layout_changed()

    def _move(self, item: Icon, folder: int):
        self._edit(self.layout_.move, item, folder)

    def new_folder(self, icon: Icon | None = None):
        if self.layout_ is None:
            return
        name, ok = QInputDialog.getText(self, "New folder", f"Name (at most {homemenu.NAME_MAX} characters):")
        if not ok:
            return

        def create():
            folder = self.layout_.create_folder(name)
            if icon is not None:
                self.layout_.move(icon, folder.id)

        self._edit(create)

    def rename_folder(self, folder: Folder):
        name, ok = QInputDialog.getText(self, "Rename folder", "Name:", text=folder.name)
        if ok:
            self._edit(self.layout_.rename_folder, folder, name)

    def save_home_menu(self):
        if QMessageBox.question(self, "Save to 3DS", "Write the new HOME Menu to the 3DS?\n\nThe 3DS "
                                "restarts right afterwards. The current layout is backed up on "
                                "this computer first.") != QMessageBox.Yes:
            return

        wanted = {(icon.nand, icon.title_id): (icon.folder, icon.pos) for icon in self.layout_.icons}

        def done(saved, error):
            if saved is None:
                QMessageBox.critical(self, "Saving failed", error)
                return
            self.mirror.add_activity(f"HOME Menu saved; backup: {saved.name}")
            self.layout_ = None
            self.grid.show_layout(None, self.infos)
            self._layout_changed()
            self._await_console("The 3DS restarts with the new HOME Menu", _arranged(wanted))

        def work(link):
            link.note("Saving the HOME Menu")
            return homemenu.write(link, self.console, self.layout_)

        self._run(work,
                  done, "Writing the HOME Menu…")

    def restore_backup(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a HOME Menu backup",
                                                  str(homemenu.backups_dir(self.console)))
        if not folder or self.console is None:
            return
        try:
            savedata = (Path(folder) / "SaveData.dat").read_bytes()
        except OSError:
            QMessageBox.warning(self, "Restore", "That folder has no SaveData.dat.")
            return
        if QMessageBox.question(self, "Restore", "Write this backup to the 3DS? It restarts "
                                "afterwards.") != QMessageBox.Yes:
            return
        def done(_result, error):
            self.status.setText(error or "Restored. The 3DS is restarting. Start vcinject Link "
                                "again, then Search again.")

        self._run(lambda link: homemenu.restore(link, self.console.region, savedata) or True,
                  done, "Restoring…")

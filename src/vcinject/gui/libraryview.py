"""The ROM library: data, filters and its views (list, grid, details, Cover Flow).

LibraryPanel owns everything about the library – scanning, identification,
the missing-games catalog, versions, series – and turns it into a list of
``Entry`` objects in display order. A view only shows entries and reports what
the user does with them, so all views behave the same and switching is cheap.
With "Group series" on, the games of a series become one stack entry that
expands in place.
"""
from __future__ import annotations

import queue
import threading
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (QEasingCurve, QFile, QLocale, QPointF, QRectF, QSize, Qt, QTimer, QUrl,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPen,
                           QPixmap, QTransform)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QProgressDialog, QPushButton, QScrollBar, QStackedWidget, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import artwork, library, systems
from ..errors import InjectError
from .qtutil import gui_settings, to_qimage

KEY_ROLE = Qt.UserRole


@dataclass
class Entry:
    """One thing a view shows: a ROM, a missing game or a stack (series)."""
    key: str
    kind: str                      # "rom", "missing" or "stack"
    title: str
    details: list[str] = field(default_factory=list)
    tooltip: str = ""
    path: Path | None = None       # ROM file (kind "rom")
    rom: library.LibraryRom | None = None
    game: library.CatalogGame | None = None
    children: list["Entry"] = field(default_factory=list)
    expanded: bool = False
    in_stack: str = ""             # series of the open stack this entry belongs to
    # details columns
    system: str = ""
    year: str = ""
    series: str = ""
    release: str = ""
    folder: str = ""
    size: int = 0
    status: str = ""

    @property
    def subtitle(self) -> str:
        return " · ".join(d for d in self.details if d)


def flatten(entries: list[Entry]) -> list[Entry]:
    """Entries as a flat sequence: open stacks are followed by their games."""
    flat = []
    for entry in entries:
        flat.append(entry)
        if entry.kind == "stack" and entry.expanded:
            flat += entry.children
    return flat


# --- views ------------------------------------------------------------------------

class ListView(QListWidget):
    """Compact list (side panel) or cover grid (library window)."""
    current_changed = Signal(object)
    activated = Signal(object)
    context = Signal(object, object)
    toggled = Signal(str, bool)
    scrolled = Signal()

    ICON = QSize(40, 48)
    GRID_ICON = QSize(110, 140)
    GRID_CELL = QSize(160, 200)

    def __init__(self, panel: "LibraryPanel", grid: bool):
        super().__init__()
        self.panel, self.grid = panel, grid
        self._entries: dict[str, Entry] = {}
        self._items: dict[str, QListWidgetItem] = {}
        self._order: list[str] = []
        if grid:
            self.setViewMode(QListView.IconMode)
            self.setIconSize(self.GRID_ICON)
            self.setGridSize(self.GRID_CELL)
            self.setResizeMode(QListView.Adjust)
            self.setMovement(QListView.Static)
            self.setWordWrap(True)
            self.setTextElideMode(Qt.ElideRight)
            self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        else:
            self.setIconSize(self.ICON)
            self.setSpacing(2)
            self.setUniformItemSizes(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.itemAt(pos) and self.context.emit(
                self._entry(self.itemAt(pos)), self.viewport().mapToGlobal(pos)))
        self.currentItemChanged.connect(lambda item, _old: self.current_changed.emit(self._entry(item)))
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(lambda item: self.activated.emit(self._entry(item)))
        self.verticalScrollBar().valueChanged.connect(lambda: self.scrolled.emit())

    def _entry(self, item) -> Entry | None:
        return self._entries.get(item.data(KEY_ROLE)) if item is not None else None

    def _clicked(self, item):
        entry = self._entry(item)
        if entry is not None and entry.kind == "stack":
            self.toggled.emit(entry.key, not entry.expanded)
        else:
            self.current_changed.emit(entry)

    def set_entries(self, entries: list[Entry]):
        flat = flatten(entries)
        keys = [e.key for e in flat]
        self._entries = {e.key: e for e in flat}
        if keys == self._order:  # same rows: only texts and icons may have changed
            for entry in flat:
                self._fill(self._items[entry.key], entry)
            return
        current = self.currentItem().data(KEY_ROLE) if self.currentItem() else None
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        self._items = {}
        for entry in flat:
            item = QListWidgetItem()
            item.setData(KEY_ROLE, entry.key)
            self._fill(item, entry)
            self.addItem(item)
            self._items[entry.key] = item
        self._order = keys
        if current in self._items:
            self.setCurrentItem(self._items[current])
        self.blockSignals(False)
        self.verticalScrollBar().setValue(scroll)
        self.scrolled.emit()

    def _fill(self, item: QListWidgetItem, entry: Entry):
        if self.grid:
            marker = {"stack": "▾ " if entry.expanded else "▸ "}.get(entry.kind, "")
            note = [d for d in entry.details if d.startswith(("✓", "⚠")) or "versions" in d]
            text = marker + entry.title + (f"\n{entry.details[0]}" if entry.kind == "stack" else "")
            if note and entry.kind != "stack":
                text += "\n" + note[0]
            size = self.GRID_CELL
        else:
            marker = {"stack": "▾ " if entry.expanded else "▸ "}.get(entry.kind, "")
            indent = "    " if entry.in_stack else ""
            text = f"{indent}{marker}{entry.title}\n{indent}{entry.subtitle}"
            size = QSize(0, 54 if entry.kind != "missing" else 44)
        item.setText(text)
        item.setToolTip(entry.tooltip)
        item.setSizeHint(size)
        item.setIcon(self.panel.icon(entry, self.iconSize()))
        grey = self.palette().placeholderText().color()
        item.setForeground(grey if entry.kind == "missing" else self.palette().text().color())
        if entry.in_stack:
            tint = QColor(self.palette().highlight().color())
            tint.setAlpha(28)
            item.setBackground(tint)
        else:
            item.setBackground(QBrush())
        flags = Qt.ItemIsEnabled | (Qt.ItemIsSelectable if entry.kind != "missing" else Qt.NoItemFlags)
        item.setFlags(flags)

    def set_current(self, key: str):
        item = self._items.get(key)
        if item is not None and item is not self.currentItem():
            self.blockSignals(True)
            self.setCurrentItem(item)
            self.blockSignals(False)
            self.scrollToItem(item)

    def visible_entries(self) -> list[Entry]:
        viewport = self.viewport().rect()
        return [self._entries[key] for key, item in self._items.items()
                if self.visualItemRect(item).intersects(viewport)]


class DetailsView(QTreeWidget):
    """Table with columns; stacks are tree nodes."""
    current_changed = Signal(object)
    activated = Signal(object)
    context = Signal(object, object)
    toggled = Signal(str, bool)
    scrolled = Signal()
    sort_requested = Signal(str)

    COLUMNS = ["Title", "System", "Year", "Series", "Release", "Folder", "Size", "Status"]
    SORTABLE = {0: "name", 1: "system", 2: "year", 5: "folder"}
    ICON = QSize(26, 32)

    def __init__(self, panel: "LibraryPanel"):
        super().__init__()
        self.panel = panel
        self._entries: dict[str, Entry] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._shape: list = []
        self.setHeaderLabels(self.COLUMNS)
        self.setIconSize(self.ICON)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(
            lambda column: column in self.SORTABLE and self.sort_requested.emit(self.SORTABLE[column]))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.itemAt(pos) and self.context.emit(
                self._entry(self.itemAt(pos)), self.viewport().mapToGlobal(pos)))
        self.currentItemChanged.connect(lambda item, _old: self.current_changed.emit(self._entry(item)))
        self.itemDoubleClicked.connect(lambda item, _col: self.activated.emit(self._entry(item)))
        self.itemExpanded.connect(lambda item: self._expand(item, True))
        self.itemCollapsed.connect(lambda item: self._expand(item, False))
        self.verticalScrollBar().valueChanged.connect(lambda: self.scrolled.emit())

    def _entry(self, item) -> Entry | None:
        return self._entries.get(item.data(0, KEY_ROLE)) if item is not None else None

    def _expand(self, item, expanded: bool):
        entry = self._entry(item)
        if entry is not None and entry.kind == "stack" and entry.expanded != expanded:
            self.toggled.emit(entry.key, expanded)

    def set_entries(self, entries: list[Entry]):
        shape = [(e.key, tuple(c.key for c in e.children)) for e in entries]
        self._entries = {e.key: e for e in flatten(entries)}
        for entry in entries:  # children of closed stacks are rows too
            self._entries.update({c.key: c for c in entry.children})
        if shape == self._shape:
            for key, item in self._items.items():
                self._fill(item, self._entries[key])
                if self._entries[key].kind == "stack":
                    self.blockSignals(True)
                    item.setExpanded(self._entries[key].expanded)
                    self.blockSignals(False)
            return
        current = self.currentItem().data(0, KEY_ROLE) if self.currentItem() else None
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        self._items = {}
        for entry in entries:
            item = self._make(entry)
            self.addTopLevelItem(item)
            for child in entry.children:
                item.addChild(self._make(child))
            item.setExpanded(entry.expanded)
        self._shape = shape
        if current in self._items:
            self.setCurrentItem(self._items[current])
        self.blockSignals(False)
        self.verticalScrollBar().setValue(scroll)
        self.scrolled.emit()

    def _make(self, entry: Entry) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, KEY_ROLE, entry.key)
        self._items[entry.key] = item
        self._fill(item, entry)
        return item

    def _fill(self, item: QTreeWidgetItem, entry: Entry):
        size = f"{entry.size / 1024 / 1024:.1f} MiB" if entry.size else ""
        values = [entry.title, entry.system, entry.year, entry.series, entry.release, entry.folder,
                  size, entry.status]
        grey = self.palette().placeholderText().color()
        for column, value in enumerate(values):
            item.setText(column, value)
            if entry.kind == "missing":
                item.setForeground(column, grey)
        if entry.kind == "stack":
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        item.setToolTip(0, entry.tooltip)
        item.setIcon(0, self.panel.icon(entry, self.ICON))
        item.setTextAlignment(6, Qt.AlignRight | Qt.AlignVCenter)

    def set_current(self, key: str):
        item = self._items.get(key)
        if item is not None and item is not self.currentItem():
            self.blockSignals(True)
            if item.parent() is not None:
                item.parent().setExpanded(True)
            self.setCurrentItem(item)
            self.blockSignals(False)
            self.scrollToItem(item)

    def visible_entries(self) -> list[Entry]:
        viewport = self.viewport().rect()
        found = []
        for key, item in self._items.items():
            if (item.parent() is None or item.parent().isExpanded()) \
                    and self.visualItemRect(item).intersects(viewport):
                found.append(self._entries[key])
        return found


class CoverFlowView(QWidget):
    """Covers in a row, the current one facing the viewer, the others turned away."""
    current_changed = Signal(object)
    activated = Signal(object)
    context = Signal(object, object)
    toggled = Signal(str, bool)
    scrolled = Signal()

    COVER = QSize(220, 220)
    SIDE = 7  # covers shown on each side

    def __init__(self, panel: "LibraryPanel"):
        super().__init__()
        self.panel = panel
        self.flat: list[Entry] = []
        self.index = 0
        self.position = 0.0  # animated towards index
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(360)
        self._animation = QVariantAnimation(self, duration=260,
                                            easingCurve=QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._moved)
        self.scrollbar = QScrollBar(Qt.Horizontal, self)
        self.scrollbar.valueChanged.connect(lambda value: value != self.index and self.go(value))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.current() and self.context.emit(self.current(), self.mapToGlobal(pos)))

    def current(self) -> Entry | None:
        return self.flat[self.index] if 0 <= self.index < len(self.flat) else None

    def set_entries(self, entries: list[Entry]):
        key = self.current().key if self.current() else None
        self.flat = flatten(entries)
        keys = [e.key for e in self.flat]
        self.index = keys.index(key) if key in keys else min(self.index, max(0, len(keys) - 1))
        self.position = float(self.index) if key not in keys else self.position
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, max(0, len(self.flat) - 1))
        self.scrollbar.setValue(self.index)
        self.scrollbar.blockSignals(False)
        self.update()
        self.scrolled.emit()

    def set_current(self, key: str):
        keys = [e.key for e in self.flat]
        if key in keys and keys.index(key) != self.index:
            self.go(keys.index(key), notify=False)

    def go(self, index: int, notify: bool = True):
        if not self.flat:
            return
        index = max(0, min(len(self.flat) - 1, index))
        if index == self.index and notify:
            return
        self.index = index
        self._animation.stop()
        self._animation.setStartValue(float(self.position))
        self._animation.setEndValue(float(index))
        self._animation.start()
        self.scrollbar.blockSignals(True)
        self.scrollbar.setValue(index)
        self.scrollbar.blockSignals(False)
        if notify:
            self.current_changed.emit(self.current())
        self.scrolled.emit()

    def _moved(self, value):
        self.position = float(value)
        self.update()

    def visible_entries(self) -> list[Entry]:
        return self.flat[max(0, self.index - self.SIDE):self.index + self.SIDE + 1]

    # --- input ---

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Up):
            self.go(self.index - 1)
        elif key in (Qt.Key_Right, Qt.Key_Down):
            self.go(self.index + 1)
        elif key == Qt.Key_PageUp:
            self.go(self.index - 10)
        elif key == Qt.Key_PageDown:
            self.go(self.index + 10)
        elif key == Qt.Key_Home:
            self.go(0)
        elif key == Qt.Key_End:
            self.go(len(self.flat) - 1)
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self.current():
            self._activate(self.current())
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta()
        step = delta.x() or delta.y()
        if step:
            self.go(self.index + (-1 if step > 0 else 1))

    def _hit(self, pos) -> int | None:
        for index, rect in reversed(self._rects):
            if rect.contains(QPointF(pos)):
                return index
        return None

    def mousePressEvent(self, event):
        self.setFocus()
        index = self._hit(event.position())
        if index is None:
            return
        if index != self.index:
            self.go(index)
        elif self.current() and self.current().kind == "stack":
            self.toggled.emit(self.current().key, not self.current().expanded)

    def mouseDoubleClickEvent(self, event):
        if self._hit(event.position()) == self.index and self.current():
            self._activate(self.current())

    def _activate(self, entry: Entry):
        if entry.kind == "stack":
            self.toggled.emit(entry.key, not entry.expanded)
        else:
            self.activated.emit(entry)

    # --- drawing ---

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scrollbar.setGeometry(24, self.height() - 22, self.width() - 48, 14)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        background = QLinearGradient(0, 0, 0, self.height())
        background.setColorAt(0, QColor(28, 30, 36))
        background.setColorAt(1, QColor(6, 7, 9))
        p.fillRect(self.rect(), background)
        self._rects: list[tuple[int, QRectF]] = []
        if not self.flat:
            p.setPen(QColor(160, 160, 170))
            p.drawText(self.rect(), Qt.AlignCenter, "No games to show")
            return
        size = min(self.COVER.width(), int(self.height() * 0.5))
        center_x = self.width() / 2
        base_y = self.height() * 0.12
        spacing = size * 0.42
        first = max(0, int(self.position) - self.SIDE)
        last = min(len(self.flat) - 1, int(self.position) + self.SIDE + 1)
        # far covers first so the current one ends up on top
        order = sorted(range(first, last + 1), key=lambda i: -abs(i - self.position))
        for i in order:
            offset = i - self.position
            distance = min(1.0, abs(offset))
            angle = -60 * max(-1.0, min(1.0, offset))
            scale = 1.0 - 0.18 * distance
            x = center_x + offset * spacing + (size * 0.34 if offset > 0 else -size * 0.34) * distance
            pix = self.panel.icon(self.flat[i], QSize(size, size))
            w, h = pix.width() * scale, pix.height() * scale
            top = base_y + (size - h)
            transform = QTransform()
            transform.translate(x, top + h / 2)
            transform.rotate(angle, Qt.YAxis)
            transform.translate(-w / 2, -h / 2)
            p.save()
            p.setTransform(transform)
            target = QRectF(0, 0, w, h)
            if self.panel._cover_of(self.flat[i]) is None and self.flat[i].kind != "stack":
                p.fillRect(target, QColor(44, 46, 54))  # no cover yet: a dark card
            p.drawPixmap(target, pix, QRectF(pix.rect()))
            # reflection
            p.save()
            p.translate(0, h * 2 + 4)
            p.scale(1, -1)
            p.setOpacity(0.22)
            p.drawPixmap(target, pix, QRectF(pix.rect()))
            p.restore()
            fade = QLinearGradient(0, h + 4, 0, h + 4 + h * 0.6)
            fade.setColorAt(0, QColor(10, 11, 14, 90))
            fade.setColorAt(1, QColor(8, 9, 11, 255))
            p.fillRect(QRectF(-2, h + 4, w + 4, h * 0.6 + 2), fade)
            if i == self.index and self.hasFocus():
                p.setPen(QPen(self.palette().highlight().color(), 2))
                p.drawRect(target.adjusted(-2, -2, 2, 2))
            p.restore()
            self._rects.append((i, transform.mapRect(target)))
        self._rects.sort(key=lambda item: -abs(item[0] - self.position))
        entry = self.current()
        if entry is not None:
            p.setPen(QColor(240, 240, 245))
            font = QFont(self.font())
            font.setPointSize(font.pointSize() + 4)
            font.setBold(True)
            p.setFont(font)
            title_y = base_y + size + size * 0.32
            marker = ("▾ " if entry.expanded else "▸ ") if entry.kind == "stack" else ""
            p.drawText(QRectF(20, title_y, self.width() - 40, 28), Qt.AlignCenter,
                       marker + entry.title)
            p.setFont(self.font())
            p.setPen(QColor(150, 152, 160))
            p.drawText(QRectF(20, title_y + 28, self.width() - 40, 22), Qt.AlignCenter,
                       entry.subtitle)
            p.drawText(QRectF(20, title_y + 50, self.width() - 40, 22), Qt.AlignCenter,
                       f"{self.index + 1} of {len(self.flat)}")


# --- the panel ------------------------------------------------------------------------

class LibraryPanel(QFrame):
    """ROM folder (incl. sub folders) with filters and a switchable view."""
    rom_chosen = Signal(object)          # Path
    rom_install = Signal(object)         # Path, double-click: build and show the QR code
    open_window = Signal()               # "show the library in its own window"
    rom_renamed = Signal(object, object)  # old Path, new Path
    _analysis_done = Signal(int)          # scan generation
    _analyzed = Signal(int, int, object)  # scan generation, index in roms, LibraryRom
    _catalog_ready = Signal(int, object)  # catalog generation, list[CatalogGame]
    _cover_ready = Signal(int, object, object)  # catalog generation, game key, PIL image

    REGIONS = {
        "Europe": ("Europe", "Germany", "France", "Spain", "Italy", "UK", "World", "Netherlands",
                   "Sweden", "Scandinavia", "Australia"),
        "USA": ("USA", "World"),
        "Japan": ("Japan",),
        "All regions": (),
    }
    MISSING_STYLES = {"greyed": "Missing: greyed out", "bw": "Missing: black & white"}
    SORTS = {"name": "Sort: Name", "year": "Sort: Release year", "system": "Sort: System",
             "folder": "Sort: Folder"}
    VIEWS = {"grid": "Grid", "details": "Details", "coverflow": "Cover Flow"}
    COMPILATIONS = "\x00compilations"  # series filter entry for multi-game cartridges

    _panels: "weakref.WeakSet[LibraryPanel]" = weakref.WeakSet()  # main window + library window

    def __init__(self, grid: bool = False):
        super().__init__(objectName="card")
        LibraryPanel._panels.add(self)
        self.grid = grid  # True: the big library window with view choice
        if not grid:
            self.setFixedWidth(300)
        self.settings = gui_settings()
        self._missing_covers: dict[str, Image.Image] = {}   # game key -> cover
        self._cover_queue: queue.Queue = queue.Queue()
        self._cover_requested: set[str] = set()
        threading.Thread(target=self._cover_worker, daemon=True).start()
        self.roms: list[library.LibraryRom] = []
        self._rows: dict[Path, int] = {}                 # path -> index in roms
        self._catalog: list[library.CatalogGame] = []
        self._missing: list[library.CatalogGame] = []
        self._catalog_generation = 0
        self._generation = 0
        self._series: dict[str, str] = {}                # title -> series
        self._group_of: dict[int, list] = {}             # index in roms -> its versions
        self._hidden_versions: set[int] = set()          # versions folded into another entry
        self._group_notes: dict[int, str] = {}           # "3 versions · ⚠ duplicate"
        self._picked: set[Path] = set()                  # versions chosen in the version menu
        self._expanded: set[str] = set()                 # open stacks
        self._entries: list[Entry] = []
        self._icons: dict[tuple, QPixmap] = {}
        language = QLocale.system().name().split("_")[0]
        self._prefer = tuple(t for t in (library.LANGUAGE_REGION.get(language), "Europe") if t)

        # --- controls ---
        title = QLabel("Library", objectName="cardTitle")
        choose = QPushButton("Folder…", clicked=self.choose_folder)
        refresh = QToolButton(text="↻", clicked=self.rescan, toolTip="Scan again")
        zip_roms = QToolButton(text="Zip", clicked=self.zip_roms,
                               toolTip="Pack every unzipped ROM into its own .zip "
                                       "(saves space, vcinject reads zips)")
        names = QToolButton(text="Names", clicked=self.clean_names,
                            toolTip="Rename identified ROMs to their official release name "
                                    "(keeps the selfdumped mark)")
        top = QHBoxLayout()
        top.addWidget(title, 1)
        self.view_choice = QComboBox()
        if grid:
            for key, label in self.VIEWS.items():
                self.view_choice.addItem(label, key)
            self.view_choice.setCurrentIndex(max(0, self.view_choice.findData(
                self.settings.value("library/view", "grid"))))
            top.addWidget(self.view_choice)
        else:
            pop_out = QToolButton(text="⧉", clicked=lambda: self.open_window.emit(),
                                  toolTip="Open the library in its own window "
                                          "(grid, details, Cover Flow)")
            top.addWidget(pop_out)
        top.addWidget(names)
        top.addWidget(zip_roms)
        top.addWidget(refresh)
        top.addWidget(choose)
        self.folder_label = QLabel("No folder chosen", objectName="muted")
        self.folder_label.setWordWrap(True)
        self.search = QLineEdit(placeholderText="Search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._schedule)
        self.system_filter = QComboBox()
        self.system_filter.addItem("All systems", None)
        for key, system in systems.SYSTEMS.items():
            self.system_filter.addItem(system.name, key)
        self.system_filter.currentIndexChanged.connect(lambda: self._refresh())
        self.system_filter.currentIndexChanged.connect(lambda: self._catalog_timer.start())
        self.sort = QComboBox()
        for key, label in self.SORTS.items():
            self.sort.addItem(label, key)
        self.sort.setCurrentIndex(max(0, self.sort.findData(self.settings.value("library/sort", "name"))))
        self.sort.currentIndexChanged.connect(self._options_changed)
        self.series_filter = QComboBox()
        self.series_filter.setToolTip("Series are recognised from the game titles "
                                      "(e.g. all Pokémon games)")
        self.series_filter.addItem("All series", None)
        self.series_filter.currentIndexChanged.connect(lambda: self._refresh())
        self.stacks = QCheckBox("Group series")
        self.stacks.setToolTip("Show the games of a series as one stack; click it to open it")
        self.stacks.setChecked(self.settings.value("library/stacks", False, type=bool))
        self.stacks.toggled.connect(self._options_changed)
        self.show_missing = QCheckBox("Show missing games")
        self.show_missing.setToolTip("List every game of the system from the No-Intro database "
                                     "and grey out the ones not in your folder")
        self.owned_first = QCheckBox("Owned first")
        self.owned_first.setToolTip("Off: missing games are sorted in between your own")
        self.owned_first.setChecked(self.settings.value("library/owned_first", True, type=bool))
        self.owned_first.toggled.connect(self._options_changed)
        self.owned_first.setVisible(False)
        self.show_missing.toggled.connect(self.owned_first.setVisible)
        self.region = QComboBox()
        for name in self.REGIONS:
            self.region.addItem(name)
        self.region.setVisible(False)
        self.show_missing.toggled.connect(self.region.setVisible)
        self.show_missing.toggled.connect(lambda: self._catalog_timer.start())
        self.region.currentIndexChanged.connect(lambda: self._catalog_timer.start())
        self.missing_style = QComboBox()
        for key, label in self.MISSING_STYLES.items():
            self.missing_style.addItem(label, key)
        self.missing_style.setCurrentIndex(max(0, self.missing_style.findData(
            self.settings.value("library/missing_style", "greyed"))))
        self.missing_style.setVisible(False)
        self.show_missing.toggled.connect(self.missing_style.setVisible)
        self.missing_style.currentIndexChanged.connect(self._missing_style_changed)
        self._catalog_timer = QTimer(self, singleShot=True, interval=600,
                                     timeout=self._refresh_catalog)
        # entries are worked out again shortly after changes (e.g. ROMs identified)
        self._refresh_timer = QTimer(self, singleShot=True, interval=250, timeout=self._refresh)
        self._visible_timer = QTimer(self, singleShot=True, interval=200,
                                     timeout=self._request_visible_covers)
        # Any change of the current entry (mouse or arrow keys) opens the ROM; a short
        # delay keeps fast scrolling smooth.
        self._open_timer = QTimer(self, singleShot=True, interval=150, timeout=self._open_current)
        self._current: Entry | None = None
        self.count = QLabel(objectName="muted")

        # --- views ---
        self.views = QStackedWidget()
        self._views: dict[str, QWidget] = {}
        if grid:
            self._add_view("grid", ListView(self, grid=True))
            self._add_view("details", DetailsView(self))
            self._add_view("coverflow", CoverFlowView(self))
            self._views["details"].sort_requested.connect(
                lambda key: self.sort.setCurrentIndex(self.sort.findData(key)))
            self.view_choice.currentIndexChanged.connect(self._view_changed)
        else:
            self._add_view("list", ListView(self, grid=False))
        self.view = self._views[self.view_choice.currentData() or "list"]
        self.views.setCurrentWidget(self.view)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(top)
        layout.addWidget(self.folder_label)
        if grid:  # filters in two rows above the view
            row1 = QHBoxLayout()
            for widget in (self.search, self.system_filter, self.series_filter, self.sort):
                row1.addWidget(widget)
            layout.addLayout(row1)
            row2 = QHBoxLayout()
            for widget in (self.stacks, self.show_missing, self.owned_first, self.region,
                           self.missing_style):
                row2.addWidget(widget)
            row2.addStretch(1)
            layout.addLayout(row2)
        else:
            layout.addWidget(self.search)
            row = QHBoxLayout()
            row.addWidget(self.system_filter, 1)
            row.addWidget(self.series_filter, 1)
            layout.addLayout(row)
            row = QHBoxLayout()
            row.addWidget(self.sort, 1)
            row.addWidget(self.stacks)
            layout.addLayout(row)
            row = QHBoxLayout()
            row.addWidget(self.show_missing)
            row.addWidget(self.owned_first)
            layout.addLayout(row)
            layout.addWidget(self.region)
            layout.addWidget(self.missing_style)
        layout.addWidget(self.views, 1)
        layout.addWidget(self.count)
        self._analyzed.connect(self._update_row)
        self._catalog_ready.connect(self._show_catalog)
        self._cover_ready.connect(self._set_missing_cover)
        self._analysis_done.connect(self._after_analysis)

        folder = self.settings.value("library/folder", "")
        if folder and Path(folder).is_dir():
            self.load(Path(folder))

    def _add_view(self, key: str, view: QWidget):
        self._views[key] = view
        self.views.addWidget(view)
        view.current_changed.connect(self._current_changed)
        view.activated.connect(self._activated)
        view.context.connect(self._context_menu)
        view.toggled.connect(self._toggle_stack)
        view.scrolled.connect(lambda: self._visible_timer.start())

    @property
    def list(self) -> QWidget:  # the current view (kept for callers that knew the old list)
        return self.view

    def _view_changed(self):
        key = self.view_choice.currentData()
        self.settings.setValue("library/view", key)
        self.view = self._views[key]
        self.views.setCurrentWidget(self.view)
        self.view.set_entries(self._entries)
        if self._current is not None:
            self.view.set_current(self._current.key)
        self.view.setFocus()

    # --- scanning ------------------------------------------------------------

    def choose_folder(self):
        start = self.settings.value("library/folder", "")
        folder = QFileDialog.getExistingDirectory(self, "ROM folder", start)
        if folder:
            self.settings.setValue("library/folder", folder)
            self.load(Path(folder))

    def rescan(self):
        folder = self.settings.value("library/folder", "")
        if folder:
            self.load(Path(folder))

    def load(self, folder: Path):
        self._generation += 1
        generation = self._generation
        try:
            self.roms = library.scan(folder)
        except InjectError as exc:
            QMessageBox.warning(self, "Library", str(exc))
            return
        self.folder_label.setText(str(folder))
        self._rows = {rom.path: i for i, rom in enumerate(self.roms)}
        self._catalog, self._missing = [], []
        self._group_notes = {}
        self._icons = {}
        self._refresh()
        if self.show_missing.isChecked():
            self._catalog_timer.start()
        # Identify ROMs and fetch covers in the background, newest scan wins.
        roms = list(self.roms)

        def work():
            for index, rom in enumerate(roms):
                if generation != self._generation:
                    return
                try:
                    library.analyze(rom)
                except Exception:  # unreadable file etc.: keep it listed as is
                    continue
                self._analyzed.emit(generation, index, rom)
            self._analysis_done.emit(generation)

        threading.Thread(target=work, daemon=True).start()

    def _update_row(self, generation: int, _index: int, rom: library.LibraryRom):
        index = self._rows.get(rom.path)  # files may have been removed meanwhile
        if generation == self._generation and index is not None and self.roms[index] is rom:
            self._schedule()
            if self.show_missing.isChecked():  # newly identified ROMs change what is owned
                self._catalog_timer.start()

    def refresh_rom(self, path: Path):
        """Re-check one ROM (e.g. after building it) without network access."""
        index = self._rows.get(Path(path))
        if index is None:
            return
        library.analyze(self.roms[index], online=False)
        self._refresh()

    def stop(self):
        self._generation += 1  # ends a running analysis

    # --- entries ---------------------------------------------------------------

    def _schedule(self):
        self._refresh_timer.start()

    def _options_changed(self):
        self.settings.setValue("library/sort", self.sort.currentData())
        self.settings.setValue("library/owned_first", self.owned_first.isChecked())
        self.settings.setValue("library/stacks", self.stacks.isChecked())
        self._refresh()

    def _refresh(self):
        """Work out series, versions and filters and show the result."""
        self._refresh_timer.stop()
        titles = [rom.title for rom in self.roms] + [g.title for g in self._catalog]
        self._series = library.series(titles)
        self._group_versions()
        self._fill_series_filter()
        self._entries = self._build_entries()
        self.view.set_entries(self._entries)
        self._update_count()

    def _group_versions(self):
        """One entry per game: other versions and copies are folded into it."""
        self._group_of, self._hidden_versions, notes = {}, set(), {}
        for group in library.group_versions(self.roms):
            indices = [self._rows[rom.path] for rom in group]
            for index in indices:
                self._group_of[index] = group
            if len(group) < 2:
                continue
            primary = self._primary(group)
            self._hidden_versions |= {i for i in indices if self.roms[i] is not primary}
            dupes = library.duplicates(group)
            note = f"{len(group)} versions"
            if dupes:
                note += " · ⚠ duplicate" + (" (identical)" if all(d.identical for d in dupes) else "")
            notes[self._rows[primary.path]] = note
        self._group_notes = notes

    def _primary(self, group: list) -> library.LibraryRom:
        """The version an entry shows: the one picked last, else the preferred language."""
        picked = [rom for rom in group if rom.path in self._picked]
        if picked:
            return picked[0]
        return min(group, key=lambda r: (library.preferred_order(r.release or r.path.stem,
                                                                 self._prefer),
                                         not r.self_dumped, r.path.name.lower()))

    def versions(self, path: Path) -> tuple[list, list]:
        """All versions of the game ``path`` belongs to, and duplicates among them."""
        index = self._rows.get(Path(path))
        group = self._group_of.get(index, []) if index is not None else []
        return group, (library.duplicates(group) if len(group) > 1 else [])

    def _in_series(self, title: str) -> bool:
        wanted = self.series_filter.currentData()
        if wanted is None:
            return True
        if wanted == self.COMPILATIONS:
            return library.is_compilation(title)
        return self._series.get(title) == wanted

    def _rom_entry(self, index: int, rom: library.LibraryRom) -> Entry:
        details = [rom.system.key.upper()]
        if rom.folder and rom.folder.lower() not in (rom.system.key, rom.system.name.lower()):
            details.append(rom.folder)
        if rom.year:
            details.append(rom.year)
        if library.is_compilation(rom.title):
            details.append("compilation")
        if rom.self_dumped:
            details.append("self-dumped")
        note = self._group_notes.get(index, "")
        if note:
            details.append(note)
        status = []
        if rom.built:
            details.append(f"✓ built ({rom.built[-1].title_id:016X})")
            status.append("✓ built")
        if rom.self_dumped:
            status.append("self-dumped")
        if note:
            status.append(note)
        series = self._series.get(rom.title, "")
        tip = [str(rom.path), " · ".join(details)] + ([f"Series: {series}"] if series else [])
        region = rom.release[len(rom.title):].strip() if rom.release else ""
        return Entry(f"rom:{rom.path}", "rom", rom.title, details, "\n".join(tip), rom.path, rom,
                     system=rom.system.key.upper(), year=rom.year or "", series=series,
                     release=region, folder=rom.folder, size=rom.size, status=" · ".join(status))

    def _missing_entry(self, game: library.CatalogGame) -> Entry:
        details = [game.system.key.upper()] + ([game.year] if game.year else []) + ["missing"]
        return Entry(_game_key(game), "missing", game.title, details,
                     "Not in your ROM folder:\n" + "\n".join(game.releases[:6]), game=game,
                     system=game.system.key.upper(), year=game.year or "",
                     series=self._series.get(game.title, ""),
                     release=game.releases[0][len(game.title):].strip() if game.releases else "",
                     status="missing")

    def _sort_key(self, entry: Entry, owned: bool) -> tuple:
        by = {"name": (),
              "year": (entry.year or "9999",),
              "system": (entry.system,),
              "folder": (entry.folder.lower(),)}[self.sort.currentData()]
        first = (0 if owned else 1,) if self.owned_first.isChecked() else ()
        return first + by + (entry.title.lower(),)

    def _build_entries(self) -> list[Entry]:
        text = self.search.text().strip().lower()
        system = self.system_filter.currentData()
        rows = []
        for index, rom in enumerate(self.roms):
            if index in self._hidden_versions or system not in (None, rom.system.key) \
                    or not self._in_series(rom.title):
                continue
            if text and text not in rom.title.lower() and text not in rom.path.name.lower() \
                    and text not in rom.folder.lower():
                continue
            entry = self._rom_entry(index, rom)
            rows.append((self._sort_key(entry, True), entry))
        for game in self._missing:
            if (text and text not in game.title.lower()) or not self._in_series(game.title):
                continue
            entry = self._missing_entry(game)
            rows.append((self._sort_key(entry, False), entry))
        rows.sort(key=lambda row: row[0])
        entries = [entry for _key, entry in rows]
        if not self.stacks.isChecked():
            return entries
        members: dict[str, list[Entry]] = {}
        for entry in entries:
            if entry.series:
                members.setdefault(entry.series, []).append(entry)
        result, placed = [], set()
        for entry in entries:
            series = entry.series
            if not series or len(members[series]) < 2:
                result.append(entry)
                continue
            if series in placed:
                continue
            placed.add(series)
            games = members[series]
            owned = sum(g.kind == "rom" for g in games)
            key = f"stack:{series}"
            expanded = key in self._expanded
            for game in games:
                game.in_stack = series if expanded else ""
            detail = f"{len(games)} games" + (f" · {owned} owned" if owned != len(games) else "")
            result.append(Entry(key, "stack", series, [detail],
                                f"{series}: {detail}\n" + "\n".join(g.title for g in games[:12]),
                                children=games, expanded=expanded, series=series,
                                system=", ".join(sorted({g.system for g in games})),
                                status=detail))
        return result

    def _toggle_stack(self, key: str, expanded: bool):
        if expanded:
            self._expanded.add(key)
        else:
            self._expanded.discard(key)
        self._entries = self._build_entries()
        self.view.set_entries(self._entries)
        if expanded:  # show the opened stack where it is
            self.view.set_current(key)

    def _fill_series_filter(self):
        chosen = self.series_filter.currentData()
        system = self.system_filter.currentData()
        owned: dict[str, int] = {}
        total: dict[str, int] = {}
        for title in {rom.title for rom in self.roms if system in (None, rom.system.key)}:
            if title in self._series:
                owned[self._series[title]] = owned.get(self._series[title], 0) + 1
        for game in self._catalog:
            if game.title in self._series:
                total[self._series[game.title]] = total.get(self._series[game.title], 0) + 1
        names = sorted(set(owned) | set(total), key=str.lower)
        compilations = sum(library.is_compilation(rom.title) for rom in self.roms
                           if system in (None, rom.system.key))
        self.series_filter.blockSignals(True)
        self.series_filter.clear()
        self.series_filter.addItem("All series", None)
        for name in names:
            count = f"{owned.get(name, 0)}/{total[name]}" if name in total else str(owned[name])
            self.series_filter.addItem(f"{name} ({count})", name)
        if compilations:
            self.series_filter.addItem(f"Compilations ({compilations})", self.COMPILATIONS)
        self.series_filter.setCurrentIndex(max(0, self.series_filter.findData(chosen)))
        self.series_filter.blockSignals(False)

    def _update_count(self):
        system = self.system_filter.currentData()
        shown = sum(1 for e in self._all_entries() if e.kind == "rom")
        built = sum(1 for rom in self.roms if rom.built)
        summary = f"{shown} of {len(self.roms)} ROMs · {built} built"
        if self.show_missing.isChecked() and self._catalog:
            games = [g for g in self._catalog if self._in_series(g.title)
                     and system in (None, g.system.key)]
            summary += f"\nCollection: {sum(g.owned for g in games)} of {len(games)} games"
        self.count.setText(summary)

    def _all_entries(self) -> list[Entry]:
        found = []
        for entry in self._entries:
            found += entry.children if entry.kind == "stack" else [entry]
        return found

    # --- icons -----------------------------------------------------------------

    def icon(self, entry: Entry, size: QSize) -> QPixmap:
        """Cover of an entry at ``size`` (cached); stacks show their first covers."""
        style = self.missing_style.currentData()
        if entry.kind == "stack":
            covers = tuple(self._cover_of(child) for child in entry.children[:3])
            key = ("stack", tuple(id(c) for c in covers), style, size.width(), size.height(),
                   entry.expanded)
            if key not in self._icons:
                self._icons[key] = self._stack_pixmap(entry, size)
            return self._icons[key]
        cover = self._cover_of(entry)
        key = (entry.kind, id(cover) if cover is not None else entry.system, style,
               size.width(), size.height())
        if key not in self._icons:
            self._icons[key] = self._pixmap(cover, entry, size)
        return self._icons[key]

    def _cover_of(self, entry: Entry) -> Image.Image | None:
        if entry.kind == "rom":
            return entry.rom.cover if entry.rom else None
        if entry.kind == "missing":
            cover = self._missing_covers.get(entry.key)
            return self._style_missing(cover) if cover is not None else None
        return None

    def _pixmap(self, cover, entry: Entry, size: QSize) -> QPixmap:
        if cover is not None:
            return QPixmap.fromImage(to_qimage(cover)).scaled(size, Qt.KeepAspectRatio,
                                                              Qt.SmoothTransformation)
        pix = QPixmap(size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self.palette().mid().color(), 1.5, Qt.DashLine))
        p.drawRoundedRect(QRectF(1, 1, pix.width() - 2, pix.height() - 2), 6, 6)
        p.setFont(QFont(p.font().family(), max(7, size.height() // 7), QFont.Bold))
        p.drawText(pix.rect(), Qt.AlignCenter, entry.system or "ROM")
        p.end()
        return pix

    def _stack_pixmap(self, entry: Entry, size: QSize) -> QPixmap:
        """Up to three covers fanned out like a pile of boxes."""
        pix = QPixmap(size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        children = entry.children[:3]
        card = QSize(int(size.width() * 0.78), int(size.height() * 0.78))
        for depth, child in reversed(list(enumerate(children))):
            cover = self.icon(child, card)
            dx = depth * size.width() * 0.09
            dy = depth * size.height() * 0.07
            x = (size.width() - card.width()) / 2 + dx - size.width() * 0.06
            y = (size.height() - card.height()) / 2 + dy - size.height() * 0.05
            p.save()
            p.translate(x + card.width() / 2, y + card.height() / 2)
            p.rotate((depth - 1) * 4)
            p.translate(-cover.width() / 2, -cover.height() / 2)
            p.setPen(QPen(QColor(0, 0, 0, 70), 1))
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRect(QRectF(-1, -1, cover.width() + 2, cover.height() + 2))
            p.drawPixmap(0, 0, cover)
            p.restore()
        # count badge
        badge = str(len(entry.children))
        font = QFont(p.font().family(), max(7, size.height() // 9), QFont.Bold)
        p.setFont(font)
        radius = max(9, size.height() // 7)
        center = QPointF(size.width() - radius - 1, radius + 1)
        p.setPen(Qt.NoPen)
        p.setBrush(self.palette().highlight().color())
        p.drawEllipse(center, radius, radius)
        p.setPen(self.palette().highlightedText().color())
        p.drawText(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2),
                   Qt.AlignCenter, badge)
        p.end()
        return pix

    # --- user actions -----------------------------------------------------------------

    def _current_changed(self, entry: Entry | None):
        self._current = entry
        if entry is not None and entry.kind == "rom":
            self._open_timer.start()

    def _activated(self, entry: Entry | None):
        if entry is None:
            return
        if entry.kind == "stack":
            self._toggle_stack(entry.key, not entry.expanded)
        elif entry.kind == "rom":
            self.rom_install.emit(entry.path)

    def _open_current(self):
        self._open_timer.stop()
        entry = self._current
        if entry is not None and entry.kind == "rom":  # "missing" entries have no file
            self.rom_chosen.emit(entry.path)

    def select(self, path: Path):
        """Highlight a ROM opened elsewhere (e.g. dropped) without reopening it."""
        index = self._rows.get(Path(path))
        if index is None:
            return
        if index in self._hidden_versions:  # a folded version becomes the entry's version
            self._picked -= {rom.path for rom in self._group_of.get(index, [])}
            self._picked.add(Path(path))
            self._refresh()
        series = self._series.get(self.roms[index].title)
        if self.stacks.isChecked() and series and f"stack:{series}" not in self._expanded \
                and any(e.key == f"stack:{series}" for e in self._entries):
            self._toggle_stack(f"stack:{series}", True)
        self.view.set_current(f"rom:{Path(path)}")

    def _context_menu(self, entry: Entry | None, global_pos):
        if entry is None:
            return
        menu = QMenu(self)
        if entry.kind == "stack":
            menu.addAction("Close stack" if entry.expanded else "Open stack",
                           lambda: self._toggle_stack(entry.key, not entry.expanded))
            menu.exec(global_pos)
            return
        if entry.kind != "rom":
            return
        rom = entry.rom
        menu.addAction("Open", lambda: self.rom_chosen.emit(rom.path))
        menu.addAction("Build and install via QR", lambda: self.rom_install.emit(rom.path))
        menu.addSeparator()
        mark = menu.addAction("Self-dumped (from my own cartridge)",
                              lambda: self.toggle_self_dumped(rom.path))
        mark.setCheckable(True)
        mark.setChecked(rom.self_dumped)
        stem = library.clean_stem(rom)
        clean = menu.addAction("Rename to release name", lambda: self._rename_all([(rom, stem)]))
        clean.setEnabled(bool(stem) and stem != rom.path.stem)
        if stem and stem != rom.path.stem:
            clean.setText(f"Rename to “{stem}”")
        group, _dupes = self.versions(rom.path)
        if len(group) > 1:
            menu.addAction(f"Versions and duplicates ({len(group)})…",
                           lambda: VersionsDialog(self, rom.path).exec())
        menu.addSeparator()
        menu.addAction("Show in folder",
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(rom.path.parent))))
        menu.exec(global_pos)

    # --- files: zip, rename, self-dumped, remove -------------------------------------

    def zip_roms(self):
        todo = library.unzipped(self.roms)
        if not todo:
            QMessageBox.information(self, "Zip ROMs", "All ROMs in the library are zipped already.")
            return
        size = sum(r.size for r in todo) / 1024 / 1024
        answer = QMessageBox.question(
            self, "Zip ROMs",
            f"Pack each of the {len(todo)} unzipped ROMs ({size:.1f} MiB) into its own .zip?\n\n"
            "Each original is deleted only after its zip was read back and verified. "
            "Injects built before keep working, vcinject recognises zipped ROMs.")
        if answer != QMessageBox.Yes:
            return
        progress = QProgressDialog("Zipping ROMs…", "Stop", 0, len(todo), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(300)
        failed = []
        for done, rom in enumerate(todo):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Zipping {rom.path.name}…")
            progress.setValue(done)
            QApplication.processEvents()
            try:
                library.zip_rom(rom.path)
            except InjectError as exc:
                failed.append(str(exc))
        progress.setValue(len(todo))
        self.rescan()
        if failed:
            QMessageBox.warning(self, "Zip ROMs", "Some ROMs were not zipped:\n\n"
                                + "\n".join(failed[:12]))

    def _renamed(self, rom: library.LibraryRom, old: Path):
        index = self._rows.pop(old)
        self._rows[rom.path] = index
        if old in self._picked:
            self._picked.discard(old)
            self._picked.add(rom.path)
        self.rom_renamed.emit(old, rom.path)
        for other in list(LibraryPanel._panels):  # the same folder in the other window
            if other is not self:
                other._follow_rename(old, rom.path)

    def _follow_rename(self, old: Path, new: Path):
        index = self._rows.pop(old, None)
        if index is None:
            return
        self.roms[index].path = new
        self._rows[new] = index
        if old in self._picked:
            self._picked.discard(old)
            self._picked.add(new)
        self._schedule()

    def _rename_all(self, pairs) -> list[str]:
        failed = []
        for rom, stem in pairs:
            old = rom.path
            try:
                library.rename(rom, stem)
            except InjectError as exc:
                failed.append(str(exc))
                continue
            self._renamed(rom, old)
        self._refresh()
        return failed

    def clean_names(self):
        pairs = library.clean_names(self.roms)
        if not pairs:
            QMessageBox.information(self, "Clean up file names",
                                    "All identified ROMs already carry their release name.")
            return
        box = QMessageBox(QMessageBox.Question, "Clean up file names",
                          f"Rename {len(pairs)} ROMs to their official release name?\n\n"
                          "Only ROMs identified by checksum (or known ROM hacks) are renamed; "
                          "the \"selfdumped\" mark is kept.",
                          QMessageBox.Yes | QMessageBox.Cancel, self)
        box.setDetailedText("\n".join(f"{rom.path.name}\n  → {stem}{rom.path.suffix}"
                                      for rom, stem in pairs))
        auto = QCheckBox("Do this automatically after every scan")
        auto.setChecked(self.settings.value("library/auto_names", False, type=bool))
        box.setCheckBox(auto)
        if box.exec() != QMessageBox.Yes:
            return
        self.settings.setValue("library/auto_names", auto.isChecked())
        failed = self._rename_all(pairs)
        if failed:
            QMessageBox.warning(self, "Clean up file names", "\n".join(failed[:12]))

    def _after_analysis(self, generation: int):
        if generation == self._generation and self.settings.value("library/auto_names", False,
                                                                    type=bool):
            self._rename_all(library.clean_names(self.roms))

    def toggle_self_dumped(self, path: Path):
        index = self._rows.get(Path(path))
        if index is None:
            return
        rom = self.roms[index]
        old = rom.path
        try:
            library.set_self_dumped(rom, not rom.self_dumped)
        except InjectError as exc:
            QMessageBox.warning(self, "Self-dumped", str(exc))
            return
        self._renamed(rom, old)
        self._refresh()

    @classmethod
    def forget_everywhere(cls, path: Path):
        """A file was deleted: drop it from every library view."""
        for panel in list(cls._panels):
            panel.forget(path)

    def forget(self, path: Path):
        index = self._rows.get(Path(path))
        if index is None:
            return
        self.roms.pop(index)
        self._rows = {rom.path: i for i, rom in enumerate(self.roms)}
        self._picked.discard(Path(path))
        self._refresh()

    # --- collection (all games from the database) ----------------------------

    def _refresh_catalog(self):
        self._catalog_generation += 1
        generation = self._catalog_generation
        self._catalog, self._missing = [], []
        self._missing_covers = {}
        self._cover_requested = set()
        if not self.show_missing.isChecked():
            self._refresh()
            return
        wanted = self.system_filter.currentData()
        keys = [wanted] if wanted else sorted({rom.system.key for rom in self.roms})
        tags = self.REGIONS[self.region.currentText()]
        roms = list(self.roms)
        prefer = self._prefer
        self.count.setText("Loading the game lists…")

        def work():
            games = []
            for key in keys:
                try:
                    games += library.catalog(systems.SYSTEMS[key], prefer)
                except InjectError:
                    continue  # offline and never downloaded: skip that system
            if tags:
                for game in games:
                    game.releases = [n for n in game.releases if any(f"{t}" in n for t in tags)]
                games = [g for g in games if g.releases]
                for game in games:
                    game.title = game.releases[0].split(" (", 1)[0]
            library.mark_owned(games, roms)
            self._catalog_ready.emit(generation, games)

        threading.Thread(target=work, daemon=True).start()

    def _show_catalog(self, generation: int, games: list):
        if generation != self._catalog_generation:
            return
        self._catalog = games
        self._missing = [g for g in games if not g.owned]
        self._refresh()

    # --- covers of missing games -----------------------------------------------

    def _request_visible_covers(self):
        """Queue covers for the missing games currently on screen."""
        if not self._missing:
            return
        for entry in self.view.visible_entries():
            for item in (entry.children[:3] if entry.kind == "stack" else [entry]):
                if item.kind == "missing" and item.key not in self._cover_requested:
                    self._cover_requested.add(item.key)
                    self._cover_queue.put((self._catalog_generation, item.key, item.game))

    def _cover_worker(self):
        while True:
            generation, key, game = self._cover_queue.get()
            if generation != self._catalog_generation:
                continue  # list changed meanwhile
            cover = None
            for name in game.releases[:4]:
                try:
                    cover = artwork.fetch_image(name, "boxart", game.system.database)
                except InjectError:
                    break  # offline
                if cover is not None:
                    break
            if cover is not None:
                cover.thumbnail((256, 256))
                self._cover_ready.emit(generation, key, cover)

    def _set_missing_cover(self, generation: int, key: str, cover):
        if generation != self._catalog_generation:
            return
        self._missing_covers[key] = cover
        self._schedule()

    def _style_missing(self, cover: Image.Image) -> Image.Image:
        grey = cover.convert("L").convert("RGBA")
        if self.missing_style.currentData() == "bw":
            return grey
        # greyed out: faded towards the background so owned games stand out
        background = self.palette().base().color()
        flat = Image.new("RGBA", grey.size, (background.red(), background.green(),
                                             background.blue(), 255))
        return Image.blend(grey, flat, 0.55)

    def _missing_style_changed(self):
        self.settings.setValue("library/missing_style", self.missing_style.currentData())
        self._icons = {}
        self._refresh()


def _game_key(game: library.CatalogGame) -> str:
    return f"missing:{game.system.key}:{game.releases[0] if game.releases else game.title}"


class VersionsDialog(QDialog):
    """All versions of one game in the library; duplicates can be moved to the trash."""

    def __init__(self, panel: LibraryPanel, path: Path):
        super().__init__(panel)
        self.panel = panel
        self.setWindowTitle("Versions and duplicates")
        self.setMinimumWidth(620)
        self.layout_ = QVBoxLayout(self)
        self.path = Path(path)
        self.removed: list[Path] = []
        self._build()

    def _build(self):
        while self.layout_.count():
            child = self.layout_.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                _clear_layout(child.layout())
        group, dupes = self.panel.versions(self.path)
        if not group:
            self.layout_.addWidget(QLabel("No other versions left."))
            self.layout_.addWidget(QPushButton("Close", clicked=self.accept), alignment=Qt.AlignRight)
            return
        intro = QLabel(f"<b>{group[0].title}</b>: {len(group)} versions in your library.")
        intro.setWordWrap(True)
        self.layout_.addWidget(intro)
        copy_of = {id(rom) for dupe in dupes for rom in dupe.copies}
        for dupe in dupes:
            text = (f"⚠ <b>{dupe.name}</b> is there {len(dupe.copies)} times – "
                    + ("bit-identical, one copy is enough." if dupe.identical
                       else "<b>not</b> bit-identical (different dumps or a damaged copy)."))
            warning = QLabel(text)
            warning.setWordWrap(True)
            self.layout_.addWidget(warning)
        grid = QGridLayout()
        for row, rom in enumerate(sorted(group, key=lambda r: r.path.name.lower())):
            name = QLabel(f"{rom.path.name}<br><span style='color:gray'>{rom.path.parent} · "
                          f"{rom.size / 1024 / 1024:.1f} MiB · SHA-1 {rom.sha1[:10] or '?'}"
                          + (" · self-dumped" if rom.self_dumped else "")
                          + (" · duplicate" if id(rom) in copy_of else "") + "</span>")
            name.setWordWrap(True)
            name.setTextInteractionFlags(Qt.TextSelectableByMouse)
            trash = QPushButton("Move to Trash")
            trash.clicked.connect(lambda _=False, r=rom: self._trash(r))
            grid.addWidget(name, row, 0)
            grid.addWidget(trash, row, 1)
        self.layout_.addLayout(grid)
        self.layout_.addWidget(QPushButton("Close", clicked=self.accept), alignment=Qt.AlignRight)

    def _trash(self, rom):
        answer = QMessageBox.question(self, "Move to Trash", f"Move {rom.path.name} to the trash?")
        if answer != QMessageBox.Yes:
            return
        group = self.panel.versions(self.path)[0]
        if not QFile.moveToTrash(str(rom.path)):
            QMessageBox.warning(self, "Move to Trash", f"Could not move {rom.path.name} to the trash.")
            return
        LibraryPanel.forget_everywhere(rom.path)
        self.removed.append(rom.path)
        if rom.path == self.path:  # the opened file itself: follow another version
            others = [r for r in group if r.path != rom.path]
            if others:
                self.path = others[0].path
        self._build()


def _clear_layout(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()
        elif child.layout():
            _clear_layout(child.layout())


class LibraryWindow(QMainWindow):
    """The library in its own resizable window: grid, details or Cover Flow."""

    def __init__(self, main):
        super().__init__(main)
        self.setWindowTitle("vcinject — Library")
        self.panel = LibraryPanel(grid=True)
        self.panel.rom_chosen.connect(main.load_rom)
        self.panel.rom_install.connect(main.install_from_library)
        self.panel.rom_renamed.connect(main._rom_renamed)
        self.setCentralWidget(self.panel)
        self.resize(1100, 780)

    def closeEvent(self, event):
        self.panel.stop()
        super().closeEvent(event)

"""One card design for every collection: the games, all homebrew, the titles on a 3DS.

Cards are painted by a delegate, so thousands of them stay fast. Two looks:
cards whose width follows a stepless zoom and that fill the width (as many
columns as fit), or rows, with or without a cover. ``ViewControls`` switches
between them and remembers the choice per collection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QItemSelectionModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QListView, QSizePolicy,
                               QSlider, QStyle, QStyledItemDelegate, QToolButton, QWidget)

from .qtutil import gui_settings

CARDS, ROWS, LIST = "cards", "rows", "list"
GREEN, BLUE, ORANGE = "#1E9E5A", "#1D4E99", "#b45309"
CARD_ROLE = Qt.UserRole + 40


@dataclass
class Card:
    """What one card shows. ``key`` is handed back by the view's signals."""
    key: object
    title: str
    subtitle: str = ""
    text: str = ""               # a short description: two lines on cards, hidden in rows
    status: str = ""             # e.g. "✓ Petter1 · not on petter1"
    status_color: str = ""       # "" is grey
    badge: str = ""              # label in the cover's corner
    dim: bool = False            # greyed out (games one does not have)
    tint: QColor | None = None   # background behind the cover
    tooltip: str = ""
    sharp: bool = False          # pixel art (3DS icons): scaled without smoothing
    data: object = None


class CardModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.cards: list[Card] = []
        self._rows: dict[object, int] = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cards)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        card = self.cards[index.row()]
        if role == CARD_ROLE:
            return card
        if role == Qt.DisplayRole:
            return card.title
        if role == Qt.ToolTipRole:
            return card.tooltip or None
        return None

    def set_cards(self, cards: list[Card]):
        if [c.key for c in cards] == [c.key for c in self.cards]:  # same cards: only repaint
            self.cards = list(cards)
            if cards:
                self.dataChanged.emit(self.index(0), self.index(len(cards) - 1))
            return
        self.beginResetModel()
        self.cards = list(cards)
        self._rows = {c.key: row for row, c in enumerate(cards)}
        self.endResetModel()

    def row_of(self, key) -> int | None:
        return self._rows.get(key)

    def replace(self, card: Card):
        row = self._rows.get(card.key)
        if row is not None:
            self.cards[row] = card
            self.dataChanged.emit(self.index(row), self.index(row))


class CardDelegate(QStyledItemDelegate):
    GAP = 8      # space around a card
    PAD = 10     # inside a card, below the cover

    def __init__(self, view: "CardView"):
        super().__init__(view)
        self.view = view

    # sizes ---------------------------------------------------------------------
    def _fonts(self):
        base = self.view.font()
        title = QFont(base)
        title.setBold(True)
        title.setPixelSize(13 if self.view.mode == CARDS else 13)
        small = QFont(base)
        small.setPixelSize(11)
        return title, small

    def card_height(self, width: int) -> int:
        title, small = self._fonts()
        lines = QFontMetrics(title).height() + 2 * QFontMetrics(small).height() + 6
        if self.view.with_text:
            lines += 2 * QFontMetrics(small).height() + 4
        return int((width - 2 * self.GAP) * self.view.cover_ratio) + 2 * self.PAD + lines + 2 * self.GAP

    def row_height(self) -> int:
        return 64 if self.view.mode == ROWS else 34

    def sizeHint(self, option, index):
        if self.view.mode == CARDS:
            return self.view.cell_size()
        return QSize(max(200, self.view.viewport().width()), self.row_height())

    # painting ------------------------------------------------------------------
    def paint(self, p: QPainter, option, index):
        card: Card = index.data(CARD_ROLE)
        if card is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform, not card.sharp)
        if self.view.mode == CARDS:
            self._paint_card(p, option, card)
        else:
            self._paint_row(p, option, card)
        p.restore()

    def _colors(self, option, card: Card):
        palette = option.palette
        text = palette.text().color()
        muted = palette.placeholderText().color()
        if card.dim:
            text = muted
        return text, muted

    def _status_color(self, card: Card, muted: QColor) -> QColor:
        return QColor(card.status_color) if card.status_color else muted

    def _draw_picture(self, p: QPainter, card: Card, area: QRect):
        pix = self.view.picture(card, area.size())
        if pix is None or pix.isNull():
            return
        size = pix.size().scaled(area.size(), Qt.KeepAspectRatio)
        if card.sharp and pix.width() and size.width() > pix.width():
            factor = max(1, size.width() // pix.width())  # whole steps: pixel art stays crisp
            size = QSize(pix.width() * factor, pix.height() * factor)
        target = QRect(QPoint(0, 0), size)
        target.moveCenter(area.center())
        if card.dim:
            p.setOpacity(0.45)
        p.drawPixmap(target, pix)
        p.setOpacity(1.0)

    def _paint_card(self, p: QPainter, option, card: Card):
        palette = option.palette
        text, muted = self._colors(option, card)
        title_font, small = self._fonts()
        rect = option.rect.adjusted(self.GAP, self.GAP, -self.GAP, -self.GAP)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        frame = QPainterPath()
        frame.addRoundedRect(rect.adjusted(0, 0, -1, -1), 10, 10)
        p.fillPath(frame, palette.base())
        border = palette.highlight().color() if selected else palette.mid().color() if hover \
            else palette.midlight().color()
        p.setPen(QPen(border, 2 if selected else 1))
        p.drawPath(frame)
        cover = QRect(rect.left() + 1, rect.top() + 1, rect.width() - 2, int(rect.width() * self.view.cover_ratio))
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(cover, 9, 9)
        p.setClipPath(clip)
        p.fillRect(cover, card.tint or palette.alternateBase().color())
        self._draw_picture(p, card, cover.adjusted(8, 8, -8, -8))
        p.restore()
        if card.badge:
            self._badge(p, card.badge, cover.topLeft() + QPoint(8, 8), small)
        x, w = rect.left() + self.PAD, rect.width() - 2 * self.PAD
        y = cover.bottom() + self.PAD
        y = self._line(p, card.title, title_font, text, x, y, w)
        y = self._line(p, card.subtitle, small, muted, x, y + 1, w)
        if self.view.with_text:
            height = 2 * QFontMetrics(small).height()
            p.setFont(small)
            p.setPen(text)
            body = QRect(x, y + 4, w, height)
            p.drawText(body, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                       self._two_lines(card.text, QFontMetrics(small), w))
            y += height + 4
        self._line(p, card.status, small, self._status_color(card, muted), x, y + 3, w)

    def _paint_row(self, p: QPainter, option, card: Card):
        palette = option.palette
        text, muted = self._colors(option, card)
        title_font, small = self._fonts()
        rect = option.rect
        if option.state & QStyle.State_Selected:
            tint = QColor(palette.highlight().color())
            tint.setAlpha(60)
            p.fillRect(rect, tint)
        elif option.state & QStyle.State_MouseOver:
            p.fillRect(rect, palette.alternateBase())
        p.setPen(palette.midlight().color())
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        x = rect.left() + 8
        if self.view.mode == ROWS:
            thumb = QRect(x, rect.top() + 6, 52, rect.height() - 12)
            if card.tint is not None:
                path = QPainterPath()
                path.addRoundedRect(thumb, 6, 6)
                p.fillPath(path, card.tint)
            self._draw_picture(p, card, thumb.adjusted(2, 2, -2, -2))
            x = thumb.right() + 12
        status_w = min(320, rect.width() // 3)
        status_x = rect.right() - status_w - 8
        text_w = status_x - x - 12
        if self.view.mode == ROWS:
            top = rect.top() + (rect.height() - QFontMetrics(title_font).height()
                                - QFontMetrics(small).height() - 2) // 2
            self._line(p, card.title, title_font, text, x, top, text_w)
            self._line(p, " · ".join(s for s in (card.badge, card.subtitle) if s), small, muted, x,
                       top + QFontMetrics(title_font).height() + 2, text_w)
        else:  # one line: title, then the facts in a column of their own
            middle = rect.top() + (rect.height() - QFontMetrics(title_font).height()) // 2
            title_w = text_w * 11 // 20
            self._line(p, card.title, title_font, text, x, middle, title_w - 8)
            self._line(p, " · ".join(s for s in (card.badge, card.subtitle) if s), small, muted,
                       x + title_w, middle + 1, text_w - title_w)
        self._line(p, card.status, small, self._status_color(card, muted), status_x,
                   rect.top() + (rect.height() - QFontMetrics(small).height()) // 2, status_w,
                   Qt.AlignRight)

    @staticmethod
    def _line(p: QPainter, value: str, font: QFont, color: QColor, x: int, y: int, width: int,
              align=Qt.AlignLeft) -> int:
        metrics = QFontMetrics(font)
        if value:
            p.setFont(font)
            p.setPen(color)
            p.drawText(QRect(x, y, width, metrics.height()), align | Qt.AlignVCenter,
                       metrics.elidedText(value, Qt.ElideRight, width))
        return y + metrics.height()

    @staticmethod
    def _two_lines(value: str, metrics: QFontMetrics, width: int) -> str:
        """The text cut after two lines, with an ellipsis."""
        words, lines, line = value.split(), [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if metrics.horizontalAdvance(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
                if len(lines) == 2:
                    break
        if len(lines) < 2 and line:
            lines.append(line)
            line = ""
        if len(lines) == 2 and (line or len(" ".join(lines).split()) < len(words)):
            lines[1] = metrics.elidedText(lines[1] + " …", Qt.ElideRight, width)
        return "\n".join(lines[:2])

    @staticmethod
    def _badge(p: QPainter, label: str, at: QPoint, font: QFont):
        bold = QFont(font)
        bold.setBold(True)
        p.setFont(bold)
        width = QFontMetrics(bold).horizontalAdvance(label) + 12
        box = QRect(at, QSize(width, 18))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 130))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(Qt.white)
        p.drawText(box, Qt.AlignCenter, label)


class CardView(QListView):
    """Cards or rows of ``Card``s. ``picture(card, size)`` hands out a card's picture
    when it is painted (only visible cards ask)."""
    card_clicked = Signal(object)     # key
    card_activated = Signal(object)   # key (double click, Enter)
    card_current = Signal(object)     # key or None
    card_context = Signal(object, object)  # key, global position

    def __init__(self, picture: Callable[[Card, QSize], QPixmap | None] = lambda card, size: None,
                 cover_ratio: float = 0.75, with_text: bool = False):
        super().__init__()
        self.picture = picture
        self.cover_ratio = cover_ratio
        self.with_text = with_text
        self.mode = CARDS
        self.zoom = 220      # width a card would like to have; the columns fill the rest
        self.model_ = CardModel()
        self.setModel(self.model_)
        self.setItemDelegate(CardDelegate(self))
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.NoFrame)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.clicked.connect(lambda index: self.card_clicked.emit(self._key(index)))
        self.activated.connect(lambda index: self.card_activated.emit(self._key(index)))
        self.selectionModel().currentChanged.connect(
            lambda index, _old: self.card_current.emit(self._key(index)))
        self._apply_mode()

    # cards ---------------------------------------------------------------------
    def set_cards(self, cards: list[Card]):
        current = self.current_key()
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.selectionModel().blockSignals(True)
        self.model_.set_cards(cards)
        if current is not None:
            self.set_current(current, scroll_to=False)
        self.selectionModel().blockSignals(False)
        self.blockSignals(False)
        self.verticalScrollBar().setValue(scroll)

    def update_card(self, card: Card):
        self.model_.replace(card)

    def repaint_cards(self):
        """Pictures changed (e.g. loaded): paint the visible cards again."""
        self.viewport().update()

    def cards(self) -> list[Card]:
        return self.model_.cards

    def current_key(self):
        return self._key(self.currentIndex())

    def set_current(self, key, scroll_to: bool = True):
        row = self.model_.row_of(key)
        if row is None:
            return
        index = self.model_.index(row)
        self.selectionModel().setCurrentIndex(index, QItemSelectionModel.ClearAndSelect)
        if scroll_to:
            self.scrollTo(index)

    def visible_cards(self) -> list[Card]:
        found, area = [], self.viewport().rect()
        for row, card in enumerate(self.model_.cards):
            if self.visualRect(self.model_.index(row)).intersects(area):
                found.append(card)
        return found

    def _key(self, index):
        card = index.data(CARD_ROLE) if index.isValid() else None
        return card.key if card else None

    def _context(self, pos: QPoint):
        index = self.indexAt(pos)
        if index.isValid():
            self.card_context.emit(self._key(index), self.viewport().mapToGlobal(pos))

    # look ----------------------------------------------------------------------
    def set_mode(self, mode: str):
        self.mode = mode
        self._apply_mode()

    def set_zoom(self, width: int):
        self.zoom = width
        self._apply_mode()

    def cell_size(self) -> QSize:
        available = max(1, self.viewport().width() - 1)
        columns = max(1, available // max(1, self.zoom))
        width = available // columns
        return QSize(width, self.itemDelegate().card_height(width))

    def _apply_mode(self):
        # cards lie on the window's background, rows on a white table
        self.setObjectName("cardGrid" if self.mode == CARDS else "cardRows")
        self.style().unpolish(self)
        self.style().polish(self)
        if self.mode == CARDS:
            self.setViewMode(QListView.IconMode)
            self.setResizeMode(QListView.Adjust)
            self.setMovement(QListView.Static)
            self.setSpacing(0)
            self.setGridSize(self.cell_size())
        else:
            self.setViewMode(QListView.ListMode)
            self.setGridSize(QSize())
            self.setSpacing(0)
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.mode == CARDS and self.gridSize() != self.cell_size():
            self.setGridSize(self.cell_size())
        elif self.mode != CARDS:
            self.scheduleDelayedItemsLayout()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and self.mode == CARDS:  # Ctrl + wheel zooms
            self.zoom_requested(event.angleDelta().y())
            return
        super().wheelEvent(event)

    def zoom_requested(self, delta: int):
        """Overwritten by ViewControls, which knows the slider."""
        self.set_zoom(max(ViewControls.ZOOM[0], min(ViewControls.ZOOM[1], self.zoom + delta // 6)))


class ViewControls(QWidget):
    """Cards / rows / rows without cover, and the zoom slider for the cards.
    Remembers the choice under ``<name>/view`` and ``<name>/zoom``."""
    changed = Signal()
    ZOOM = (140, 460)
    MODES = ((CARDS, "▦", "Cards"), (ROWS, "☰", "Rows with covers"), (LIST, "≡", "Rows without covers"))

    def __init__(self, name: str, default_zoom: int = 220, views: list[CardView] = ()):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.name = name
        self.settings = gui_settings()
        self.views: list[CardView] = list(views)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.zoom = QSlider(Qt.Horizontal, minimum=self.ZOOM[0], maximum=self.ZOOM[1])
        self.zoom.setFixedWidth(110)
        self.zoom.setToolTip("Card size (Ctrl + mouse wheel)")
        self.zoom.setValue(int(self.settings.value(f"{name}/zoom", default_zoom)))
        self.zoom.valueChanged.connect(self._zoomed)
        layout.addWidget(self.zoom)
        layout.addSpacing(6)
        self.group = QButtonGroup(self)
        wanted = str(self.settings.value(f"{name}/view", CARDS))
        for mode, symbol, tip in self.MODES:
            button = QToolButton(text=symbol, toolTip=tip, checkable=True, objectName="viewMode")
            button.setChecked(mode == wanted)
            button.setProperty("mode", mode)
            self.group.addButton(button)
            layout.addWidget(button)
        if self.group.checkedButton() is None:
            self.group.buttons()[0].setChecked(True)
        self.group.buttonClicked.connect(lambda _b: self._moded())
        for view in self.views:
            self.attach(view)

    @property
    def mode(self) -> str:
        return self.group.checkedButton().property("mode")

    def attach(self, view: CardView):
        if view not in self.views:
            self.views.append(view)
        view.zoom_requested = lambda delta: self.zoom.setValue(self.zoom.value() + delta // 6)
        view.zoom = self.zoom.value()
        view.set_mode(self.mode)
        self.zoom.setVisible(self.mode == CARDS)

    def set_mode(self, mode: str):
        for button in self.group.buttons():
            if button.property("mode") == mode:
                button.setChecked(True)
        self._moded()

    def _moded(self):
        self.settings.setValue(f"{self.name}/view", self.mode)
        self.zoom.setVisible(self.mode == CARDS)
        for view in self.views:
            view.set_mode(self.mode)
        self.changed.emit()

    def _zoomed(self, value: int):
        self.settings.setValue(f"{self.name}/zoom", value)
        for view in self.views:
            view.set_zoom(value)
        self.changed.emit()

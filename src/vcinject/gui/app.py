"""Qt GUI for vcinject, modelled on NSUI's GBA VC injector page."""
from __future__ import annotations

import os
import sys
import zlib
from pathlib import Path

try:
    from PySide6.QtCore import (QLocale, QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer,
                                QUrl, Signal)
    from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QFontDatabase, QImage, QLinearGradient, QPainter,
                               QPainterPath, QPen, QPixmap, QPolygonF, QTransform)
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
        QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
        QProgressBar, QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget,
    )
except ImportError:  # pragma: no cover - reported to the user at startup
    print("The GUI needs PySide6: pip install 'vcinject[gui]'", file=sys.stderr)
    raise

from PIL import Image

from .. import (__version__, art, artwork, gba, registry, serve, smdh, template, tickets, tools,
                vcbanner)
from .. import builder, forwarder, gbvc, health, library, systems
from ..builder import (GBA_3D, GbaInjectOptions, default_product_code,
                       default_title_id, parse_title_id)
from ..errors import InjectError, MissingResource
from . import coversync
from .libraryview import LibraryPanel, LibraryWindow, VersionsDialog, open_game_page
from .wifi_install import WifiInstallButton
from .qtutil import gui_settings, to_pixmap, to_qimage  # noqa: F401 (gui_settings re-exported)

ROM_FILTER = systems.rom_filter()
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All files (*)"
CUSTOM = "custom"
QR_SIZE = 360
BANNER_STYLES = {GBA_3D: ("VC Console Look", ()), **artwork.BANNER_STYLES}
INJECT_FILTER = "3DS titles (*.cia *.cxi *.bin);;All files (*)"

TICKET_HELP = """<b>Import the ticket database of your 3DS</b><br><br>
vcinject then never gives a new inject a title ID that is already used on your
console, including injects made with NSUI or other tools.<br><br>
<b>1.</b> Hold <b>START</b> while powering on the 3DS to boot GodMode9.<br>
<b>2.</b> Open <code>[1:] SYSNAND CTRNAND</code> → <code>dbs</code>.<br>
<b>3.</b> Select <code>ticket.db</code>, press <b>Y</b> (copy), go back to the main screen,
open <code>[0:] SDCARD</code> → <code>gm9</code> → <code>out</code> and press <b>Y</b>
again to paste.<br>
<b>4.</b> Copy <code>gm9/out/ticket.db</code> from the SD card to this computer
(card reader or FTP) and choose it in the next step.<br><br>
GodMode9 only reads here, so no write permission prompt appears."""

STYLESHEET = """
QFrame#card { background: palette(base); border: 1px solid palette(midlight); border-radius: 10px; }
QLabel#cardTitle { font-weight: 600; font-size: 13px; }
QLabel#muted { color: palette(placeholder-text); }
QLabel#dropHint { color: palette(placeholder-text); font-size: 13px; }
QPushButton#primary {
    background: palette(highlight); color: palette(highlighted-text); border: none;
    border-radius: 8px; padding: 9px 18px; font-weight: 600; font-size: 13px; }
QPushButton#primary:disabled { background: palette(mid); }
QPushButton#secondary {
    background: palette(button); border: 1px solid palette(mid); border-radius: 8px;
    padding: 8px 14px; font-size: 13px; }
QPushButton#secondary:hover { border-color: palette(highlight); }
QToolButton#disclosure { border: none; font-weight: 600; }
"""


# --- helpers -------------------------------------------------------------------

def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(objectName="card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(8)
    if title:
        layout.addWidget(QLabel(title, objectName="cardTitle"))
    return frame, layout


class Worker(QObject):
    """Runs a callable on a background thread, streaming log lines."""
    log = Signal(str)
    finished = Signal(object, str)  # result, error message

    def __init__(self, func):
        super().__init__()
        self._func = func

    def run(self):
        try:
            self.finished.emit(self._func(self.log.emit), "")
        except MissingResource as exc:  # handed over so the GUI can offer the fix
            self.finished.emit(exc, str(exc))
        except InjectError as exc:
            self.finished.emit(None, str(exc))
        except Exception as exc:  # surface unexpected failures instead of crashing
            self.finished.emit(None, f"Unexpected error: {exc!r}")


class Task(QObject):
    """A background job whose callbacks run on the GUI thread."""
    done = Signal(object, str)

    def __init__(self, parent: QObject, func, on_done, on_log=None):
        super().__init__(parent)
        self.thread = QThread(self)
        self.worker = Worker(func)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        if on_log is not None:
            self.worker.log.connect(on_log)
        # Connecting to a bound method of this (GUI-thread) object makes Qt
        # queue the call onto the GUI thread.
        self.worker.finished.connect(self._finished)
        self._on_done = on_done
        self.thread.start()

    def _finished(self, result, error):
        self.thread.quit()
        self.thread.wait()
        self._on_done(result, error)
        self.deleteLater()


# --- widgets -------------------------------------------------------------------

class RomDropZone(QFrame):
    """Card that accepts a dropped ROM and shows what was recognised."""
    clicked = Signal()

    def __init__(self):
        super().__init__(objectName="card")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(118)
        self.cover = QLabel(alignment=Qt.AlignCenter)
        self.cover.setFixedSize(92, 92)
        self.headline = QLabel("Drop a ROM here (GBA, SNES, NES, GB/GBC, Mega Drive)",
                               objectName="dropHint")
        self.headline.setWordWrap(True)
        self.details = QLabel("or click to browse", objectName="muted")
        self.details.setWordWrap(True)
        self.release = QLabel(objectName="muted")
        self.release.setWordWrap(True)
        # CRC32 / serial and links to libretro and RetroGamesNexus
        self.ids = QLabel(objectName="muted")
        self.ids.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.ids.setVisible(False)
        # several versions of the game in the library (languages, copies): pick one
        self.version_row = QWidget()  # hidden as a whole: hidden children alone upset the layout
        self.version_row.setVisible(False)
        self.versions = QComboBox()
        self.versions.setToolTip("Other versions of this game in your library")
        self.versions.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.versions.setMinimumContentsLength(12)
        self.duplicates = QPushButton()
        versions = QHBoxLayout(self.version_row)
        versions.setContentsMargins(0, 0, 0, 0)
        versions.addWidget(self.versions, 1)
        versions.addWidget(self.duplicates)
        text = QVBoxLayout()
        text.addStretch(1)
        text.addWidget(self.headline)
        text.addWidget(self.details)
        text.addWidget(self.release)
        text.addWidget(self.ids)
        text.addWidget(self.version_row)
        text.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.cover)
        layout.addLayout(text, 1)
        self.set_cover(None)

    def set_cover(self, image: Image.Image | None, label: str = "ROM"):
        if image is None:
            pix = QPixmap(92, 92)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(self.palette().mid().color(), 2, Qt.DashLine))
            p.drawRoundedRect(QRectF(2, 2, 88, 88), 10, 10)
            p.setPen(self.palette().mid().color())
            p.setFont(QFont(p.font().family(), 11, QFont.Bold))
            p.drawText(pix.rect(), Qt.AlignCenter, label)
            p.end()
            self.cover.setPixmap(pix)
        else:
            self.cover.setPixmap(to_pixmap(image, 92, 92))

    def mousePressEvent(self, event):
        self.clicked.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()


class ThreeDsPreview(QWidget):
    """Mock-up of the 3DS screens: banner on top, HOME Menu icon below."""

    TOP = QSize(400, 240)
    BOTTOM = QSize(320, 240)
    GAP = 14

    def __init__(self):
        super().__init__()
        self.banner: QImage | None = None
        self.icon: QImage | None = None
        self.top_screen: QImage | None = None
        self.block: tuple[QImage, QImage, QColor] | None = None  # face, plate, side colour
        self.depth = 0.28  # block thickness relative to its width
        self.angle = 0.0
        self._spin = QTimer(self, interval=33, timeout=self._tick)
        self.title = ""
        self.setFixedSize(self.TOP.width() + 8, self.TOP.height() + self.BOTTOM.height() + self.GAP + 8)

    def update_art(self, icon: Image.Image, banner: Image.Image | None, title: str,
                   top_screen: Image.Image | None = None, block=None, depth: float = 0.28):
        # Show the icon exactly as it will be encoded (48x48 RGB565).
        self.icon = to_qimage(smdh.fit_square(icon, 48))
        self.banner = to_qimage(art.letterbox(banner).convert("RGBA")) if banner else None
        self.top_screen = to_qimage(top_screen) if top_screen is not None else None
        self.block = None
        self.depth = depth
        if block is not None:
            face, plate, side = block
            self.block = (to_qimage(face), to_qimage(plate), QColor(*side))
            self._spin.start()
        else:
            self._spin.stop()
        self.title = title
        self.update()

    def _tick(self):
        self.angle = (self.angle + 2.4) % 360
        self.update()

    def _draw_block(self, p: QPainter, top: QRectF):
        """Rotating block like the HOME Menu renders newer NSUI GBA banners.

        The block is drawn as a stack of rounded slices. Front and back are the
        size of the picture; towards the middle the slices grow along a quarter
        circle, so the rounding starts where the picture ends.
        """
        import math

        face, plate, side = self.block
        fw, fh = face.width() * 1.15, face.height() * 1.15
        thickness = fw * self.depth
        r = thickness * 0.45  # radius of the rounded edge profile
        focal = 420.0
        center = QPointF(top.center().x(), top.top() + 34 + fh / 2)
        theta = math.radians(self.angle)
        cos, sin = math.cos(theta), math.sin(theta)

        def quad(z: float, w: float, h: float) -> tuple[QPolygonF, float]:
            points, depth = [], 0.0
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
                xr, zr = x * cos + z * sin, -x * sin + z * cos
                scale = focal / (focal + zr)
                points.append(QPointF(center.x() + xr * scale, center.y() + y * scale))
                depth += zr / 4
            return QPolygonF(points), depth

        half = thickness / 2
        steps = 64
        layers = []
        for i in range(steps + 1):
            z = -half + thickness * i / steps
            d = max(0.0, abs(z) - (half - r))  # distance into the rounded part
            inset = r - math.sqrt(max(0.0, r * r - d * d))
            # front/back are exactly the picture; the edge bulges out from there
            w, h = fw + 2 * (r - inset), fh + 2 * (r - inset)
            poly, depth = quad(z, w, h)
            kind = "front" if i == 0 else "back" if i == steps else "side"
            # slices facing more sideways (larger d) are a bit darker
            shade = 100 + int(15 * d / r + 18 * abs(sin))
            layers.append((depth, kind, poly, w, h, shade))

        for depth, kind, poly, w, h, shade in sorted(layers, key=lambda layer: -layer[0]):
            source = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            transform = QTransform()
            if not QTransform.quadToQuad(source, poly, transform):
                continue
            p.save()
            p.setTransform(transform, True)
            target = QRectF(0, 0, w, h)
            path = QPainterPath()
            path.addRoundedRect(target, 12, 12)
            color = side.darker(shade)
            p.setPen(QPen(color, 1.5))  # closes hairline gaps between slices
            p.setBrush(color)
            p.drawPath(path)
            p.restore()
            if kind in ("front", "back") and abs(cos) > 0.08 and (cos > 0) == (kind == "front"):
                # the picture covers the whole face, like the model's texture
                full, _ = quad(-half if kind == "front" else half, fw, fh)
                source_full = QPolygonF([QPointF(0, 0), QPointF(fw, 0), QPointF(fw, fh), QPointF(0, fh)])
                t = QTransform()
                if QTransform.quadToQuad(source_full, full, t):
                    p.save()
                    p.setTransform(t, True)
                    image = face if kind == "front" else face.mirrored(True, False)
                    p.drawImage(QRectF(0, 0, fw, fh), image)
                    p.restore()
        p.drawImage(QRectF(top.center().x() - 128, top.top() + 168, 256, 64), plate)

    def _screen(self, p: QPainter, rect: QRectF, top: str, bottom: str):
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(-4, -4, 4, 4), 12, 12)
        p.fillPath(path, QColor(28, 28, 32))
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, QColor(top))
        grad.setColorAt(1, QColor(bottom))
        p.fillRect(rect, grad)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top = QRectF(4, 4, self.TOP.width(), self.TOP.height())
        self._screen(p, top, "#f4f4f6", "#d9dbe2")
        if self.block is not None:
            self._draw_block(p, top)
        elif self.top_screen is not None:
            p.drawImage(top, self.top_screen)
        elif self.banner is not None:
            target = QRectF(top.center().x() - 128, top.top() + 40, 256, 128)
            p.drawImage(target, self.banner)
            p.setPen(QColor(90, 90, 100))
            p.setFont(QFont(p.font().family(), 10))
            p.drawText(QRectF(top.left(), target.bottom() + 14, top.width(), 20),
                       Qt.AlignCenter, self.title)

        bottom = QRectF(4 + (self.TOP.width() - self.BOTTOM.width()) / 2,
                        top.bottom() + self.GAP, self.BOTTOM.width(), self.BOTTOM.height())
        self._screen(p, bottom, "#eef0f4", "#cfd3dc")
        # HOME Menu grid with our icon selected in the middle.
        tile, gap = 60, 12
        cols, rows = 4, 3
        x0 = bottom.center().x() - (cols * tile + (cols - 1) * gap) / 2
        y0 = bottom.top() + 24
        for r in range(rows):
            for c in range(cols):
                cell = QRectF(x0 + c * (tile + gap), y0 + r * (tile + gap), tile, tile)
                path = QPainterPath()
                path.addRoundedRect(cell, 9, 9)
                if (r, c) == (1, 1) and self.icon is not None:
                    p.fillPath(path, QColor(255, 255, 255))
                    p.setPen(QPen(QColor(64, 160, 255), 3))
                    p.drawPath(path)
                    p.drawImage(cell.adjusted(6, 6, -6, -6), self.icon)
                else:
                    p.fillPath(path, QColor(255, 255, 255, 110))
        p.end()


class ArtPicker(QWidget):
    """Combo box of artwork styles plus a custom image option."""
    changed = Signal()

    def __init__(self, label: str, styles: dict):
        super().__init__()
        self._styles = styles
        self.custom_path: Path | None = None
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_index)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label))
        layout.addWidget(self.combo, 1)
        self.set_available(["generated"])

    def set_available(self, keys: list[str]):
        self.combo.blockSignals(True)
        self.combo.clear()
        for key in keys:
            self.combo.addItem(self._styles[key][0], key)
        self.combo.addItem("Custom image…", CUSTOM)
        self.custom_path = None
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        self.changed.emit()

    @property
    def style(self) -> str:
        return self.combo.currentData()

    def _on_index(self, _index):
        if self.style == GBA_3D and not vcbanner.is_installed(self.window().system.key):
            if not self.window().install_banner_template():
                self.combo.setCurrentIndex(0 if self.combo.currentIndex() else 1)
                return
        if self.style == CUSTOM:
            path, _ = QFileDialog.getOpenFileName(self, "Choose image", "", IMAGE_FILTER)
            if not path:
                self.combo.setCurrentIndex(0)
                return
            self.custom_path = Path(path)
        self.changed.emit()


class QrZoomDialog(QDialog):
    """The install QR code on its own, as large as the screen allows."""

    def __init__(self, png: bytes, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet("background: white;")
        screen = (parent.screen() if parent else QApplication.primaryScreen()).availableGeometry()
        side = int(min(screen.width(), screen.height()) * 0.8)
        label = QLabel(alignment=Qt.AlignCenter)
        # FastTransformation keeps the modules crisp, which scanners like best.
        label.setPixmap(QPixmap.fromImage(QImage.fromData(png)).scaled(
            side, side, Qt.KeepAspectRatio, Qt.FastTransformation))
        hint = QLabel("Click or press Esc to close", alignment=Qt.AlignCenter)
        hint.setStyleSheet("color: #666;")
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(hint)

    def mousePressEvent(self, event):
        self.accept()


class QrPanel(QFrame):
    """Inline QR code for FBI's remote install; serves the CIA while visible."""
    status = Signal(str)  # emitted from server threads, delivered on the GUI thread

    def __init__(self):
        super().__init__(objectName="card")
        self.server: serve.CiaServer | None = None
        self.qr = QLabel(alignment=Qt.AlignCenter)
        self.qr.setFixedSize(QR_SIZE, QR_SIZE)
        self.qr.setCursor(Qt.PointingHandCursor)
        self.qr.setToolTip("Click to enlarge")
        self.qr.mousePressEvent = lambda _event: self.zoom()
        self.title = QLabel(objectName="cardTitle")
        steps = QLabel("On the 3DS open <b>FBI</b> → <b>Remote Install</b> → <b>Scan QR Code</b>. "
                       "The 3DS must be on the same Wi-Fi. Click the code to enlarge it.",
                       wordWrap=True)
        self.url = QLabel(objectName="muted", textInteractionFlags=Qt.TextSelectableByMouse)
        self.state = QLabel("Waiting for the 3DS…", wordWrap=True)
        # transfer = install: FBI installs the CIA while it downloads it
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.transfer = QLabel(objectName="muted")
        self.transfer.setVisible(False)
        self.stop_button = QPushButton("Stop", objectName="secondary", clicked=self.stop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.addWidget(self.title)
        layout.addWidget(steps)
        layout.addWidget(self.qr, alignment=Qt.AlignHCenter)
        layout.addWidget(self.url, alignment=Qt.AlignHCenter)
        layout.addWidget(self.state)
        layout.addWidget(self.bar)
        layout.addWidget(self.transfer)
        layout.addWidget(self.stop_button, alignment=Qt.AlignLeft)
        self.status.connect(self.state.setText)
        self.progress.connect(self._show_progress)
        self.hide()

    shown = Signal(bool)
    progress = Signal(object, object)  # bytes sent, total (from the server thread)

    def setVisible(self, visible: bool):
        # Hide the preview before showing the panel (and show it after hiding the
        # panel) so both are never visible at once; otherwise Qt raises the
        # window's minimum height for good.
        if visible:
            self.shown.emit(True)
        super().setVisible(visible)
        if not visible:
            self.shown.emit(False)

    def start(self, path: Path):
        self.stop()
        self.server = serve.CiaServer([path], log=self.status.emit,
                                      progress=lambda _p, sent, total: self.progress.emit(sent, total)
                                      ).start()
        self.bar.setVisible(False)
        self.transfer.setVisible(False)
        self._started = None
        self._set_done(False)
        image = QImage.fromData(self.server.qr_png(scale=10))
        self.qr.setPixmap(QPixmap.fromImage(image).scaled(QR_SIZE, QR_SIZE, Qt.KeepAspectRatio,
                                                          Qt.SmoothTransformation))
        self.title.setText(f"Install {path.name}")
        self.url.setText("<br>".join(self.server.urls))
        self.state.setText("Waiting for the 3DS…")
        self.show()

    def _show_progress(self, sent: int, total: int):
        import time
        now = time.monotonic()
        if sent == 0 or self._started is None:
            self._started = (now, sent)  # (re)started: the 3DS asked again
        self.bar.setVisible(True)
        self.transfer.setVisible(True)
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(sent * 1000 / max(1, total)))
        mib = 1024 * 1024
        elapsed = now - self._started[0]
        speed = (sent - self._started[1]) / elapsed if elapsed > 0.5 else 0
        text = f"{sent / mib:.1f} of {total / mib:.1f} MiB"
        if speed:
            text += f" · {speed / mib:.1f} MiB/s"
            if sent < total:
                text += f" · {int((total - sent) / speed) + 1} s left"
        if sent >= total:
            text = f"✓ {total / mib:.1f} MiB sent – FBI finishes the installation on the 3DS"
        self.transfer.setText(text)
        self._set_done(sent >= total)  # the 3DS may ask again (e.g. a retry): back to Stop

    def _set_done(self, done: bool):
        """Stop while the CIA is offered or sent; Done once the 3DS has all of it."""
        self.stop_button.setText("Done" if done else "Stop")
        self.stop_button.setObjectName("primary" if done else "secondary")
        self.stop_button.style().unpolish(self.stop_button)
        self.stop_button.style().polish(self.stop_button)

    def zoom(self):
        if self.server is None:
            return
        QrZoomDialog(self.server.qr_png(scale=20), self.title.text(), self.window()).exec()

    def stop(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.hide()


# --- main window ---------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("vcinject — Virtual Console Injector")
        self.setAcceptDrops(True)
        self.rom = None
        self.system = systems.SYSTEMS["gba"]
        self.rom_path: Path | None = None
        self.found = artwork.Artwork()
        self.icon_image: Image.Image | None = None
        self.banner_image: Image.Image | None = None
        self.banner_screen: Image.Image | None = None
        self.last_output: Path | None = None
        self._busy = False
        self._qr_after_build = False
        self._wifi_after_build: list | None = None  # the 3DSs to install on once built
        self._qr_when_ready = None  # what to do once the ROM's artwork is in (install it)
        self._announce = False  # show the success message of the running task in a dialog
        self._lookup_id = 0
        self._art_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh_art)

        # ROM card
        self.drop = RomDropZone()
        self.drop.clicked.connect(self.browse_rom)

        # Title card
        title_card, title_layout = card("Title")
        self.title_edit = QLineEdit(maxLength=63, placeholderText="Shown below the icon")
        self.title_edit.textChanged.connect(lambda: self._art_timer.start())
        self.long_title_edit = QLineEdit(maxLength=127, placeholderText="Same as title")
        self.publisher_edit = QLineEdit("Virtual Console", maxLength=63)
        self.released_edit = QLineEdit(maxLength=12, placeholderText="e.g. 2004 (shown on the VC plate)")
        self.released_edit.textChanged.connect(lambda: self._art_timer.start())
        self.publisher_edit.textChanged.connect(lambda: self._art_timer.start())
        form = QFormLayout()
        form.addRow("Title", self.title_edit)
        form.addRow("Long title", self.long_title_edit)
        form.addRow("Publisher", self.publisher_edit)
        form.addRow("Released", self.released_edit)
        title_layout.addLayout(form)

        # Artwork card
        art_card, art_layout = card("Artwork")
        self.icon_picker = ArtPicker("Icon", artwork.ICON_STYLES)
        self.banner_picker = ArtPicker("Banner", BANNER_STYLES)
        self.frame_check = QCheckBox("Virtual Console icon frame", checked=True)
        self.frame_check.toggled.connect(self.refresh_art)
        self.icon_picker.changed.connect(self.refresh_art)
        self.banner_picker.changed.connect(self.refresh_art)
        self.art_status = QLabel("Artwork is looked up online in the libretro database.",
                                 objectName="muted", wordWrap=True)
        art_layout.addWidget(self.icon_picker)
        art_layout.addWidget(self.banner_picker)
        art_layout.addWidget(self.frame_check)
        # Offers whatever template the current ROM still needs.
        self.setup_button = QPushButton(objectName="secondary", clicked=self._setup_needed)
        art_layout.addWidget(self.setup_button)
        art_layout.addWidget(self.art_status)

        # Advanced card (collapsible)
        adv_card, adv_layout = card()
        self.adv_toggle = QToolButton(text="Advanced", objectName="disclosure", checkable=True)
        self.adv_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.adv_toggle.setArrowType(Qt.RightArrow)
        adv_layout.addWidget(self.adv_toggle)
        self.adv_body = QWidget()
        adv_layout.addWidget(self.adv_body)
        self.adv_body.setVisible(False)
        self.adv_toggle.toggled.connect(self._toggle_advanced)

        self.title_id_edit = QLineEdit(maxLength=16, placeholderText="derived from the ROM")
        self.product_code_edit = QLineEdit(maxLength=16, placeholderText="CTR-N-XXXX")
        self.plate_lang = QComboBox()
        for code, name in (("de", "Deutsch"), ("en", "English"), ("fr", "Français"),
                           ("it", "Italiano"), ("es", "Español"), ("nl", "Nederlands"),
                           ("pt", "Português"), ("ru", "Русский"), ("ja", "日本語")):
            self.plate_lang.addItem(name, code)
        system = QLocale.system().name().split("_")[0]
        self.plate_lang.setCurrentIndex(max(0, self.plate_lang.findData(system)))
        self.plate_lang.setToolTip("Language of the 'Released' line on the Virtual Console "
                                   "plate (banners with per-language plates use all languages)")
        self.plate_lang.currentIndexChanged.connect(lambda: self._art_timer.start())
        self.save_combo = QComboBox()
        self.save_combo.addItem("Auto-detect", None)
        for save in gba.SaveType:
            self.save_combo.addItem(f"{save.label} (0x{save:X})", save)
        self.ghosting = QSlider(Qt.Horizontal, minimum=1, maximum=255, value=gba.DEFAULT_GHOSTING)
        self.ghosting_label = QLabel(minimumWidth=60)
        self.ghosting.valueChanged.connect(
            lambda v: self.ghosting_label.setText(f"0x{v:02X}" + (" off" if v == 255 else "")))
        self.brightness = QSlider(Qt.Horizontal, minimum=10, maximum=100,
                                  value=round(gba.DEFAULT_BRIGHTNESS * 100))
        self.brightness_label = QLabel(minimumWidth=60)
        self.brightness.valueChanged.connect(lambda v: self.brightness_label.setText(f"{v}%"))
        self.ghosting.valueChanged.emit(self.ghosting.value())
        self.brightness.valueChanged.emit(self.brightness.value())
        adv = QGridLayout(self.adv_body)
        adv.setContentsMargins(0, 4, 0, 0)
        rows = [("Title ID", self.title_id_edit), ("Product code", self.product_code_edit),
                ("Save type", self.save_combo), ("Plate language", self.plate_lang)]
        self._gba_only: list[QWidget] = []
        for i, (label, widget) in enumerate(rows):
            caption = QLabel(label)
            adv.addWidget(caption, i, 0)
            adv.addWidget(widget, i, 1, 1, 2)
            if i >= 2:
                self._gba_only += [caption, widget]
        ghost_caption, bright_caption = QLabel("LCD ghosting"), QLabel("Brightness")
        adv.addWidget(ghost_caption, 4, 0)
        adv.addWidget(self.ghosting, 4, 1)
        adv.addWidget(self.ghosting_label, 4, 2)
        adv.addWidget(bright_caption, 5, 0)
        adv.addWidget(self.brightness, 5, 1)
        adv.addWidget(self.brightness_label, 5, 2)
        self._gba_only += [ghost_caption, self.ghosting, self.ghosting_label, bright_caption,
                           self.brightness, self.brightness_label]
        # Game Boy / Color: Nintendo's VC emulator or a RetroArch forwarder
        self.gb_emulator = QComboBox()
        for key, label in (("auto", "Automatic (RetroArch when set up)"),
                           ("nintendo", "Nintendo Virtual Console (experimental)"),
                           ("retroarch", "RetroArch forwarder")):
            self.gb_emulator.addItem(label, key)
        self.gb_emulator.setCurrentIndex(max(0, self.gb_emulator.findData(
            gui_settings().value("build/gb_emulator", "auto"))))
        self.gb_emulator.currentIndexChanged.connect(
            lambda: (gui_settings().setValue("build/gb_emulator", self.gb_emulator.currentData()),
                     self.rom is not None and self._show_rom_details()))
        emulator_caption = QLabel("Emulator")
        adv.addWidget(emulator_caption, 6, 0)
        adv.addWidget(self.gb_emulator, 6, 1, 1, 2)
        self._gb_only = [emulator_caption, self.gb_emulator]

        # Preview + actions
        preview_card, preview_layout = card("Preview")
        self.preview = ThreeDsPreview()
        preview_layout.addWidget(self.preview, alignment=Qt.AlignHCenter)

        self.output_edit = QLineEdit(placeholderText="Next to the ROM")
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Save to"))
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(QPushButton("…", clicked=self.browse_output, maximumWidth=36))
        self.build_button = QPushButton("Build CIA", objectName="primary", clicked=self.build)
        self.qr_button = QPushButton("Install via QR", objectName="secondary",
                                     clicked=self.install_via_qr)
        self.qr_button.setToolTip("Build the CIA with the current settings and show a QR "
                                  "code for FBI's remote install")
        self.wifi_button = WifiInstallButton()
        self.wifi_button.install.connect(self.install_over_wifi)
        self._wifi_progress.connect(self._show_wifi_progress)
        actions = QHBoxLayout()
        actions.addWidget(self.build_button, 2)
        actions.addWidget(self.qr_button, 1)
        actions.addWidget(self.wifi_button, 1)
        self.build_progress = QProgressBar()  # steps of a build, shown while building
        self.build_progress.setRange(0, 100)
        self.build_progress.setVisible(False)

        self.log_view = QPlainTextEdit(readOnly=True, placeholderText="Build log")
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log_view.setMinimumHeight(110)

        self.status_label = QLabel(objectName="muted")
        self.template_button = QPushButton("Setup…", clicked=self.open_setup)
        self.tickets_button = QPushButton("Import tickets…", clicked=self.import_tickets)
        self.homebrew_button = QPushButton("3DS Homebrew…", clicked=self.open_homebrew)
        self.console_button = QPushButton("My 3DS…", clicked=self.open_console)
        self.console_button.setToolTip("Over Wi-Fi with vcinject Link: what is installed, install "
                                       "and delete, arrange the HOME Menu")
        self.homebrew_button.setToolTip("What your 3DS needs for injects, and homebrew apps "
                                        "(DSP1, FBI, Checkpoint …) to install by QR code")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.homebrew_button)
        status_row.addWidget(self.console_button)
        status_row.addWidget(self.tickets_button)
        status_row.addWidget(self.template_button)

        left = QVBoxLayout()
        left.setSpacing(12)
        for widget in (self.drop, title_card, art_card, adv_card):
            left.addWidget(widget)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(preview_card)
        self.preview_card = preview_card
        right.addLayout(out_row)
        right.addLayout(actions)
        right.addWidget(self.build_progress)
        self.qr_panel = QrPanel()
        right.insertWidget(1, self.qr_panel)  # takes the preview's place while serving
        right.addWidget(self.log_view, 1)
        self.qr_panel.shown.connect(lambda visible: self.preview_card.setVisible(not visible))
        # on the first start the covers are downloaded before the library is shown
        self.library = LibraryPanel(autoload=not coversync.needed())
        self.library.rom_chosen.connect(self.load_rom)
        self.library.rom_install.connect(self.install_from_library)
        self.library.open_window.connect(self.open_library_window)
        self.library.rom_renamed.connect(self._rom_renamed)
        self.library.rom_refreshed.connect(self._rom_refreshed)
        self.drop.ids.linkActivated.connect(self._open_game_page)
        self.drop.versions.currentIndexChanged.connect(
            lambda: self.drop.versions.currentData() and self.load_rom(self.drop.versions.currentData()))
        self.drop.duplicates.clicked.connect(self.show_duplicates)
        self.library_window: LibraryWindow | None = None
        self.console_window = None
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(self.library)
        columns.addLayout(left, 5)
        columns.addLayout(right, 6)
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 10)
        root.addLayout(columns, 1)
        root.addLayout(status_row)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.resize(1320, 800)

        self.refresh_status()
        self._apply_artwork()  # preselects the VC Console Look when it is set up

    # --- ROM ---------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.load_rom(Path(urls[0].toLocalFile()))

    def browse_rom(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select ROM", "", ROM_FILTER)
        if path:
            self.load_rom(Path(path))

    def _show_rom_details(self):
        """System, size and how the game will run (save type / emulator)."""
        rom, system = self.rom, self.system
        size = f"{len(rom.data) / 1024 / 1024:.1f} MiB"
        if system.key == "gba":
            self.drop.details.setText(
                f"{rom.game_code or '----'} · {size} · {rom.detect_save_type().label}"
                + ("" if rom.header_valid else " · ⚠ bad header checksum"))
            return
        if system.key in ("gb", "gbc"):
            options = builder.GbaInjectOptions(rom_path=self.rom_path,
                                               gb_emulator=self.gb_emulator.currentData())
            if builder.gb_emulator(options) == "nintendo":
                how = ("Nintendo VC emulator (experimental)" if gbvc.installed_kinds()
                       else "⚠ Nintendo VC emulator not set up yet")
                self.drop.details.setText(f"{system.name} · {size} · {how}")
                return
        ready = system.forwarder in forwarder.installed_kinds()
        self.drop.details.setText(f"{system.name} · {size} · "
                                  + ("emulator forwarder" if ready else "⚠ no emulator template yet"))

    def _show_versions(self, path: Path):
        group, dupes = self.library.versions(path)
        combo = self.drop.versions
        combo.blockSignals(True)
        combo.clear()
        copies = {id(rom) for dupe in dupes for rom in dupe.copies}
        for rom in sorted(group, key=lambda r: (r.release or r.path.name).lower()):
            label = rom.release or library.plain_stem(rom.path)
            if rom.self_dumped:
                label += " · self-dumped"
            if id(rom) in copies:
                label += " · ⚠ duplicate"
            combo.addItem(label, rom.path)
        combo.setCurrentIndex(max(0, combo.findData(path)))
        combo.setVisible(len(group) > 1)
        combo.blockSignals(False)
        if dupes:
            identical = all(d.identical for d in dupes)
            self.drop.duplicates.setText("⚠ Duplicate" + (" (identical)" if identical else "") + "…")
        self.drop.duplicates.setVisible(bool(dupes))
        self.drop.version_row.setVisible(len(group) > 1 or bool(dupes))

    def open_homebrew(self):
        from .homebrew_view import HomebrewDialog
        dialog = HomebrewDialog(self)
        dialog.setModal(False)
        dialog.show()

    def open_console(self):
        from .console_view import ConsoleWindow
        if self.console_window is None:
            self.console_window = ConsoleWindow(self)
        self.console_window.show()
        self.console_window.raise_()

    def show_duplicates(self):
        if self.rom_path is None:
            return
        dialog = VersionsDialog(self.library, self.rom_path)
        dialog.exec()
        if self.rom_path in dialog.removed:  # the open ROM went to the trash
            self.rom_path = None
            self.load_rom(dialog.path)
        elif self.rom_path is not None:
            self._show_versions(self.rom_path)

    def _show_ids(self):
        """CRC32 and serial of the open ROM, with links once the release is known."""
        if self.rom is None or self.system is None:
            self.drop.ids.setVisible(False)
            return
        crc = f"{zlib.crc32(self.system.strip_header(self.rom.data)) & 0xFFFFFFFF:08X}"
        match = self.found.match if self.found else None
        serial = getattr(self.rom, "game_code", "") or (
            artwork.serial_of(match.name, self.system.database) if match else "")
        parts = [f"CRC32 {crc}"] + ([f"Serial {serial}"] if serial else [])
        if getattr(self, "_dump", ""):
            parts.append(self._dump)
        if match and match.method != "retrogamesnexus":
            parts.append('<a href="libretro">libretro ↗</a>')
        if getattr(self, "_nexus_url", None):  # only when the site has the game
            parts.append('<a href="nexus">RetroGamesNexus ↗</a>')
        self.drop.ids.setText(" · ".join(parts))
        self.drop.ids.setVisible(True)

    def _dump_checked(self, check_id: int, rom, error: str):
        """The dump of the open ROM was compared with No-Intro and FlashGBX."""
        if check_id != self._dump_id or error or rom is None:
            return
        if self.found and self.found.match and self.found.match.method == "retrogamesnexus":
            rom.method = "retrogamesnexus"
        index = self.library._rows.get(self.rom_path)
        if index is not None and self.library.roms[index].method in library.HACK_METHODS:
            rom.method = self.library.roms[index].method  # the library knows it is a hack
        label = library.verified_label(rom)
        colour = "#d64034" if library.dump_is_bad(rom) else "#2e9d4f" if rom.verified == "good" else None
        self._dump = f'<span style="color:{colour}">{label}</span>' if colour else label
        self._show_ids()

    def _find_nexus_page(self):
        """Look for the game on RetroGamesNexus in the background; the link shows up
        only when it is there."""
        match = self.found.match if self.found else None
        title = (match.name if match else library.plain_stem(self.rom_path)).split(" (", 1)[0]
        release = match.name if match and match.method != "retrogamesnexus" else None
        language = QLocale.system().name().split("_")[0]
        path, system = self.rom_path, self.system
        Task(self, lambda _log: library.nexus_url(title, system, language, release),
             lambda url, error: self._nexus_found(path, url))

    def _nexus_found(self, path: Path, url: str | None):
        if path == self.rom_path:
            self._nexus_url = url
            self._show_ids()

    def _open_game_page(self, site: str):
        if site == "nexus" and getattr(self, "_nexus_url", None):
            QDesktopServices.openUrl(QUrl(self._nexus_url))
            return
        match = self.found.match if self.found else None
        title = (match.name if match else library.plain_stem(self.rom_path)).split(" (", 1)[0]
        open_game_page(self, site, self.system, match.name if match else None, title)

    def _rom_refreshed(self, path: Path):
        """The library checked the open ROM online again: show what it found."""
        if path == self.rom_path and not self._busy:
            self.lookup_artwork()

    def _rom_renamed(self, old: Path, new: Path):
        if self.rom_path == old:
            self.rom_path = new
            self.drop.headline.setText(new.name)
            self.output_edit.setText(str(new.with_suffix(".cia")))
            self._show_versions(new)

    def open_library_window(self):
        if self.library_window is None:
            self.library_window = LibraryWindow(self)
        self.library_window.show()
        self.library_window.raise_()
        self.library_window.activateWindow()

    def install_from_library(self, path: Path):
        """Double-click in the library: open the ROM, then build and install it as soon as
        its artwork has been looked up – over Wi-Fi when a 3DS runs vcinject Link (asking
        which one when several do), else by QR code."""
        consoles = self.wifi_button.consoles
        if len(consoles) > 1:
            menu = QMenu(self)
            for console in consoles:
                menu.addAction(f"Install on {console.label}", lambda c=console: self._install_rom(path, [c]))
            menu.addAction(f"Install on all {len(consoles)}", lambda: self._install_rom(path, consoles))
            menu.addAction("Install via QR", lambda: self._install_rom(path, None))
            menu.exec(QCursor.pos())
        else:
            self._install_rom(path, consoles or None)

    def _install_rom(self, path: Path, consoles: list | None):
        install = (lambda: self.install_over_wifi(consoles)) if consoles else self.install_via_qr
        if self.rom_path != Path(path):
            self.load_rom(Path(path))
            self._qr_when_ready = install
        else:
            install()

    def load_rom(self, path: Path):
        if self.rom_path is not None and Path(path) == self.rom_path and not self._busy:
            return  # already open: keep the user's edits
        self._qr_when_ready = None
        try:
            system, rom = builder.load_rom(path)
        except (OSError, InjectError) as exc:
            QMessageBox.warning(self, "Invalid ROM", str(exc))
            return
        if self.qr_panel.server is not None:  # the served CIA belongs to the previous ROM
            self.qr_panel.stop()
            self.log_view.appendPlainText("Stopped serving: a new ROM was loaded.")
        self.rom, self.rom_path, self.system = rom, path, system
        self.library.select(path)
        for widget in self._gba_only:
            widget.setVisible(system.key == "gba")
        for widget in self._gb_only:
            widget.setVisible(system.key in ("gb", "gbc"))
        self.drop.headline.setText(path.name)
        self.drop.headline.setObjectName("cardTitle")
        self.drop.headline.style().unpolish(self.drop.headline)
        self.drop.headline.style().polish(self.drop.headline)
        self._show_rom_details()
        self.drop.release.setText("Looking up release…")
        self.drop.set_cover(None, system.key.upper())
        self.title_edit.setText(library.plain_stem(path))
        self._show_versions(path)
        self.found = artwork.Artwork()
        self._dump = ""
        self._nexus_url = None
        self._show_ids()
        self._dump_id = getattr(self, "_dump_id", 0) + 1
        check_id, data = self._dump_id, rom.data
        Task(self, lambda _log: library.verify_data(path, system, data),
             lambda result, error: self._dump_checked(check_id, result, error))
        # values of the previous ROM must not leak into this one
        self.released_edit.clear()
        self.long_title_edit.clear()
        self.publisher_edit.setText("Virtual Console")
        title_id = default_title_id(rom)
        self.title_id_edit.setText(f"{title_id:016X}")
        self.product_code_edit.setText(default_product_code(rom, system, title_id))
        self.save_combo.setCurrentIndex(0)
        self.output_edit.setText(str(path.with_suffix(".cia")))
        self.found = artwork.Artwork()
        self._apply_artwork()
        self.lookup_artwork()

    def lookup_artwork(self):
        self._lookup_id += 1
        lookup_id, rom, path, system = self._lookup_id, self.rom, self.rom_path, self.system
        Task(self, lambda _log: artwork.lookup(system.strip_header(rom.data), rom.game_code,
                                               systems.rom_name(path), system=system.database),
             lambda result, error: self._artwork_found(lookup_id, result, error))

    def _artwork_found(self, lookup_id: int, result, error: str):
        if lookup_id != self._lookup_id:  # a newer ROM was loaded meanwhile
            return
        if error:
            self.drop.release.setText("Artwork lookup failed (offline?)")
            self.art_status.setText(error)
            if self._qr_when_ready:
                then, self._qr_when_ready = self._qr_when_ready, None
                QTimer.singleShot(0, then)
            return
        self.found = result
        if result.match:
            self.drop.release.setText(f"✓ {result.match.name}")
            if self.title_edit.text() == library.plain_stem(self.rom_path)[:self.title_edit.maxLength()]:
                self.title_edit.setText(_display_title(result.match.name))
            if result.match.method == "retrogamesnexus":
                self.art_status.setText("Not in the No-Intro database (ROM hack?); box art from "
                                        "RetroGamesNexus.")
            else:
                self.art_status.setText(f"Matched by {result.match.method}; "
                                        f"{len(result.images)} images from the libretro database.")
            if result.year and not self.released_edit.text():
                self.released_edit.setText(result.year)
            if result.publisher and self.publisher_edit.text() == "Virtual Console":
                self.publisher_edit.setText(result.publisher)
        else:
            self.drop.release.setText("Release not in the No-Intro database")
            self.art_status.setText("No artwork found; using generated images.")
        cover = result.images.get("boxart") or result.images.get("title")
        if cover is None and self.system is not None:  # nothing anywhere: the console's box
            cover = art.placeholder_cover(self.system.key, self.title_edit.text()
                                          or library.plain_stem(self.rom_path))
        self.drop.set_cover(cover)
        self._show_ids()
        self._find_nexus_page()
        self._apply_artwork()
        if self._qr_when_ready:
            then, self._qr_when_ready = self._qr_when_ready, None
            QTimer.singleShot(0, then)

    def _apply_artwork(self):
        self.icon_picker.set_available(artwork.available(artwork.ICON_STYLES, self.found))
        flat = artwork.available(artwork.BANNER_STYLES, self.found)
        # The console's VC Console Look first once it is set up; otherwise offer it last.
        self.banner_picker.set_available([GBA_3D, *flat] if vcbanner.is_installed(self.system.key)
                                         else [*flat, GBA_3D])
        self.refresh_status()

    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CIA", self.output_edit.text(),
                                              "CIA (*.cia)")
        if path:
            self.output_edit.setText(path)

    def _toggle_advanced(self, on: bool):
        self.adv_body.setVisible(on)
        self.adv_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    # --- artwork -----------------------------------------------------------

    def _render(self, picker: ArtPicker, render) -> Image.Image:
        title = self.title_edit.text().strip() or "Game"
        if picker.style == CUSTOM and picker.custom_path:
            try:
                return art.load_image(picker.custom_path)
            except InjectError as exc:
                QMessageBox.warning(self, "Invalid image", str(exc))
                picker.combo.setCurrentIndex(0)
        style = picker.style if picker.style != CUSTOM else "generated"
        return render(style, self.found, title)

    def refresh_art(self):
        title = self.title_edit.text().strip() or "Game"
        self.icon_image = self._render(self.icon_picker, artwork.render_icon)
        icon = art.vc_icon_frame(self.icon_image) if self.frame_check.isChecked() else self.icon_image
        self.banner_screen = None
        system = self.system.key
        if self.banner_picker.style == GBA_3D and vcbanner.is_installed(system):
            imgs = self.found.images
            self.banner_screen = imgs.get("title") or imgs.get("snap") or imgs.get("boxart") \
                or art.default_banner(title, self.system.name)
            self.banner_image = None
            year, publisher = self.released_edit.text().strip(), self.publisher_edit.text().strip()
            lang = self.plate_lang.currentData()
            block = None
            try:
                block = vcbanner.block_parts(self.banner_screen, title, year, publisher, lang, system)
            except Exception as exc:  # never leave a stale preview behind
                self.art_status.setText(f"Preview failed: {exc}")
            self.preview.update_art(icon, None, title, None, block, vcbanner.preview_depth(system))
            return
        self.banner_image = self._render(
            self.banner_picker, lambda st, found, t: artwork.render_banner(st, found, t, self.system.name))
        self.preview.update_art(icon, self.banner_image, title)

    # --- template / tools --------------------------------------------------

    def refresh_status(self):
        need = self._missing_template()
        self.setup_button.setVisible(need is not None)
        if need == "vclook" and self._can_derive():
            self.setup_button.setText(f"Create VC Console Look for {self.system.name} (from GBA look)")
        elif need == "vclook":
            self.setup_button.setText(f"Set up VC Console Look for {self.system.name}…")
        elif need:
            self.setup_button.setText(f"Set up {systems.FORWARDER_KINDS[need]} emulator template…")
        missing = [name for name in tools.TOOLS if tools.find_tool(name) is None]
        emulators = forwarder.installed_kinds()
        parts = [f"vcinject {__version__}",
                 "template " + ("installed" if template.is_installed() else "will be downloaded"),
                 "VC looks: " + (", ".join(k.upper() for k in vcbanner.installed_systems()) or "none"),
                 f"emulators {len(emulators)}/{len(systems.FORWARDER_KINDS)}",
                 "tools OK" if not missing else "missing " + ", ".join(missing)]
        self.status_label.setText("  ·  ".join(parts))

    def _missing_template(self) -> str | None:
        """'vclook', a forwarder kind, or None when the current ROM has all it needs."""
        if self.system.forwarder and self.system.forwarder not in forwarder.installed_kinds():
            return self.system.forwarder
        return None if vcbanner.is_installed(self.system.key) else "vclook"

    def _can_derive(self) -> bool:
        return self.system.key in vcbanner.DERIVED_COLORS and vcbanner.is_installed("gba")

    def _setup_needed(self):
        need = self._missing_template()
        if need == "vclook" and self._can_derive():
            try:
                vcbanner.derive_look(self.system.key)
            except InjectError as exc:
                QMessageBox.critical(self, "VC Console Look", str(exc))
                return
            self.log_view.appendPlainText(
                f"Created the {self.system.name} VC Console Look from the GBA look. An official "
                f"{self.system.name} VC title can replace it later (Template…).")
            self._apply_artwork()
        elif need == "vclook":
            if self.install_banner_template():
                self._apply_artwork()
        elif need:
            self.install_forwarder_templates()

    def install_forwarder_templates(self):
        QMessageBox.information(
            self, "Emulator templates",
            "SNES, NES, Game Boy / Color and Mega Drive games run in an emulator "
            "(RetroArch, snes9x or VirtuaNES) packed into the CIA. vcinject reuses one existing "
            "inject per system as template, e.g. ones made with NSUI.\n\nChoose one or more "
            "such injects (.cia); the system is detected automatically.")
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose emulator injects", "", INJECT_FILTER)
        installed, errors = [], []
        for path in paths:
            try:
                installed.append(systems.FORWARDER_KINDS[forwarder.install_from(Path(path)).kind])
            except (OSError, InjectError) as exc:
                errors.append(f"{Path(path).name}: {exc}")
        if installed or errors:
            text = ("Installed: " + ", ".join(installed) if installed else "")
            if errors:
                text += ("\n\n" if text else "") + "Not usable:\n" + "\n".join(errors)
            QMessageBox.information(self, "Emulator templates", text)
        if self.rom_path is not None and installed:
            self.load_rom(self.rom_path)
        self.refresh_status()

    def open_setup(self, start_resource: str | None = None):
        from .wizard import SetupWizard
        SetupWizard(self, start_resource).exec()
        self.refresh_status()
        self._apply_artwork()

    def first_run_check(self):
        """Self-healing on start: open the setup when something required is missing
        or vcinject runs for the first time."""
        settings = self.library.settings
        missing_required = [c for c in health.check() if c.required and not c.ok]
        first_run = not settings.value("setup/done", False, type=bool)
        if missing_required or first_run:
            settings.setValue("setup/done", True)
            # first start: begin at the welcome page; later: jump to what is missing
            self.open_setup(None if first_run else missing_required[0].resource)
        self.download_covers_if_needed()

    def download_covers_if_needed(self):
        """First start (or postponed last time): download the covers, then show the library."""
        if coversync.needed():
            self.library.stop()
            coversync.CoverDownloadDialog(self).exec()
            self.library.rescan()  # instant now: everything comes from the cache
        elif not self.library.roms:
            self.library.rescan()

    def heal_and_retry(self, error: MissingResource):
        """A build hit a missing part: offer exactly that fix, then build again."""
        answer = QMessageBox.question(self, "Something is missing",
                                      f"{error}\n\nSet it up now and build again?")
        if answer != QMessageBox.Yes:
            return
        resource = error.resource

        def ask(question):
            path, _ = QFileDialog.getOpenFileName(self, question, "", INJECT_FILTER)
            return Path(path) if path else None

        needs_file = resource.startswith(("vclook:", "forwarder:"))
        donor = ask(health.question(resource)) if needs_file else None
        if needs_file and donor is None and not (resource.startswith("vclook:") and
                                                 resource.split(":")[1] in vcbanner.DERIVED_COLORS):
            return
        retry_qr = self._qr_after_build

        def fix(log):
            if not health.heal(resource, (lambda _q: donor) if donor else None, log):
                raise InjectError(f"{error} (still missing)")

        self._after_heal = lambda: self.build(then_qr=retry_qr)
        self._run(fix, "Fixed, building again...")

    def setup_template(self):
        box = QMessageBox(self)
        box.setWindowTitle("GBA VC template")
        box.setText("vcinject needs the exheader and boot logo of an official GBA VC title.\n\n"
                    "Download a ready-made template, or extract it from your own decrypted "
                    "GBA VC title (.cia/.cxi)?")
        download = box.addButton("Download", QMessageBox.AcceptRole)
        donor = box.addButton("From donor…", QMessageBox.ActionRole)
        banner = box.addButton("VC Console Look from inject…", QMessageBox.ActionRole)
        emulators = box.addButton("Emulator templates…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is banner:
            self.install_banner_template()
            return
        if box.clickedButton() is emulators:
            self.install_forwarder_templates()
            return
        if box.clickedButton() is download:
            self._run(lambda log: (log("Downloading template..."), template.download())[1],
                      "Template installed.")
        elif box.clickedButton() is donor:
            path, _ = QFileDialog.getOpenFileName(self, "Select donor", "",
                                                  "3DS titles (*.cia *.cxi *.app);;All files (*)")
            if path:
                self._run(lambda log: template.from_donor(Path(path)), "Template extracted.")

    def install_banner_template(self) -> bool:
        QMessageBox.information(
            self, "VC Console Look",
            "The VC Console Look is the 3D banner of Virtual Console titles: the console's "
            "cartridge or handheld showing the game, the 'Virtual Console' plate with title and "
            "release year, and the console's jingle.\n\nChoose a VC title or inject you own "
            "with such a banner (e.g. an official VC game or an NSUI v28+ GBA inject), one "
            "per console. Only its banner is used; picture and text are replaced per build.")
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose VC titles / injects", "",
                                                INJECT_FILTER)
        if not paths:
            return False
        done, errors = [], []
        for path in paths:
            try:
                system, _ = vcbanner.install_from(Path(path))
            except InjectError as exc:
                if "choose the system" not in str(exc):
                    errors.append(f"{Path(path).name}: {exc}")
                    continue
                try:  # banner without a detectable console: use the current ROM's
                    system, _ = vcbanner.install_from(Path(path), self.system.key)
                except (OSError, InjectError) as exc2:
                    errors.append(f"{Path(path).name}: {exc2}")
                    continue
            except OSError as exc:
                errors.append(f"{Path(path).name}: {exc}")
                continue
            done.append(systems.SYSTEMS[system].name)
        text = ("Installed for: " + ", ".join(done)) if done else ""
        if errors:
            text += ("\n\n" if text else "") + "Not usable:\n" + "\n".join(errors)
        QMessageBox.information(self, "VC Console Look", text)
        if not done:
            return False
        self.refresh_status()
        self._art_timer.start()
        return True

    def import_tickets(self):
        box = QMessageBox(self)
        box.setWindowTitle("Import ticket database")
        box.setTextFormat(Qt.RichText)
        box.setText(TICKET_HELP)
        choose = box.addButton("Choose ticket.db…", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not choose:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose ticket.db", "",
                                              "Ticket database (*.db *.tik);;All files (*)")
        if not path:
            return
        try:
            ids = tickets.read_title_ids([Path(path)])
        except (OSError, InjectError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        added = registry.import_title_ids(ids)
        if self.rom is not None:
            self.title_id_edit.setText(f"{default_title_id(self.rom):016X}")
        QMessageBox.information(self, "Tickets imported",
                                f"{len(ids)} title IDs found on your 3DS, {added} new.\n"
                                "New injects will not use any of them.")

    # --- build -------------------------------------------------------------

    def collect_options(self) -> GbaInjectOptions:
        if not self.rom_path:
            raise InjectError("Select a GBA ROM first.")
        tid_text = self.title_id_edit.text().strip()
        output = self.output_edit.text().strip()
        return GbaInjectOptions(
            rom_path=self.rom_path,
            output_path=Path(output) if output else None,
            title=self.title_edit.text().strip() or None,
            long_title=self.long_title_edit.text().strip() or None,
            publisher=self.publisher_edit.text().strip() or "Virtual Console",
            title_id=parse_title_id(tid_text) if tid_text else None,
            product_code=self.product_code_edit.text().strip() or None,
            save_type=self.save_combo.currentData(),
            ghosting=self.ghosting.value(),
            brightness=self.brightness.value() / 100,
            icon_image=self.icon_image,
            icon_frame=self.frame_check.isChecked(),
            banner_image=self.banner_image,
            banner_style=GBA_3D if self.banner_screen is not None else self.banner_picker.style,
            banner_screen=self.banner_screen,
            release_year=self.released_edit.text().strip(),
            plate_language=self.plate_lang.currentData(),
            gb_emulator=self.gb_emulator.currentData(),
            online_artwork=False,
        )

    def build(self, then_qr: bool = False):
        try:
            options = self.collect_options()
        except InjectError as exc:
            QMessageBox.warning(self, "Cannot build", str(exc))
            return
        self._qr_after_build = then_qr
        self.log_view.clear()

        def run(log):
            if self.system.key == "gba" and not template.is_installed():
                log("No GBA VC template installed yet, downloading it once...")
                template.download()
            return builder.build(options, log=log)

        self._run(run, None)

    def _run(self, func, success_message: str | None):
        if self._busy:
            return
        self._busy = True
        for button in (self.build_button, self.qr_button, self.wifi_button, self.template_button):
            button.setEnabled(False)
        self.build_button.setText("Building…" if success_message is None else "Working…")
        self._building = success_message is None
        self.build_progress.setVisible(self._building)
        self.build_progress.setValue(0)
        self.build_progress.setFormat("Preparing…")
        # a bound method of this window: Qt then delivers log lines on the GUI thread
        Task(self, func, lambda result, error: self._done(result, error, success_message),
             self._task_log)

    def _task_log(self, line: str):
        self.log_view.appendPlainText(line)
        if self._building:
            self._build_step(line)

    # log lines of builder.build() and how far the build is at each
    BUILD_STEPS = (("No GBA VC template", 5, "Downloading the GBA template…"),
                   ("Looking up artwork", 15, "Looking up artwork…"),
                   ("Release:", 30, "Artwork found"),
                   ("Building banner", 45, "Building the banner…"),
                   ("Building ExeFS", 65, "Building ExeFS…"),
                   ("Building CXI", 78, "Building the CXI…"),
                   ("Building CIA", 90, "Building the CIA…"),
                   ("Done:", 100, "Built"))

    def _build_step(self, line: str):
        for start, value, text in self.BUILD_STEPS:
            if line.startswith(start) and value >= self.build_progress.value():
                self.build_progress.setValue(value)
                self.build_progress.setFormat(f"{text}  %p%")
                return

    def _done(self, result, error: str, success_message: str | None):
        self._busy = False
        self.build_progress.setVisible(False)
        for button in (self.build_button, self.qr_button, self.wifi_button, self.template_button):
            button.setEnabled(True)
        self.build_button.setText("Build CIA")
        self.refresh_status()
        if isinstance(result, MissingResource):
            self.log_view.appendPlainText(f"Missing: {error}")
            self.heal_and_retry(result)
        elif error:
            self._wifi_after_build = None
            self._announce = False
            self.log_view.appendPlainText(f"Error: {error}")
            QMessageBox.critical(self, "Failed", error)
        elif success_message:
            self.log_view.appendPlainText(success_message)
            if self._announce:
                self._announce = False
                QMessageBox.information(self, "Installed", success_message)
            after, self._after_heal = getattr(self, "_after_heal", None), None
            if after:
                QTimer.singleShot(0, after)
        elif result is not None:
            self.last_output = result.output_path
            self.library.refresh_rom(self.rom_path)
            if self._wifi_after_build:
                consoles, self._wifi_after_build = self._wifi_after_build, None
                self._install_paths(consoles, [result.output_path])
            elif self._qr_after_build:
                self.show_qr(result.output_path)
            else:
                QMessageBox.information(self, "Done", f"Created {result.output_path}")

    def install_via_qr(self):
        """Build with the current settings, then show the QR code inline."""
        if self.rom_path is not None:
            self.build(then_qr=True)
            return
        chosen, _ = QFileDialog.getOpenFileName(self, "Select CIA to install", "", "CIA (*.cia)")
        if chosen:
            self.show_qr(Path(chosen))

    def install_over_wifi(self, consoles: list):
        """Build with the current settings, then install on ``consoles`` over Wi-Fi."""
        if self.rom_path is not None:
            self._wifi_after_build = consoles
            self.build()
            return
        chosen, _ = QFileDialog.getOpenFileNames(self, "Select CIAs to install", "", "CIA (*.cia)")
        if chosen:
            self._install_paths(consoles, [Path(p) for p in chosen])

    _wifi_progress = Signal(str, int, int)

    def _install_paths(self, consoles: list, paths: list[Path]):
        from . import wifi_install
        names = ", ".join(c.name for c in consoles)

        def run(log):
            results = wifi_install.install(consoles, paths, self._wifi_progress.emit)
            for line in results:
                log(line)
            if not any(line.endswith(": installed") for line in results):
                raise InjectError("\n".join(results))
            return results

        self._announce = True
        self._run(run, f"Installed on {names}. Press HOME on the 3DS to see it.")
        self.build_progress.setVisible(True)
        self.build_progress.setFormat(f"Installing on {names}…")

    def _show_wifi_progress(self, text: str, sent: int, total: int):
        self.build_progress.setVisible(True)
        self.build_progress.setValue(sent * 100 // max(total, 1))
        self.build_progress.setFormat(f"Installing {text}  %p%")

    def show_qr(self, path: Path):
        try:
            self.qr_panel.start(path)
        except InjectError as exc:
            QMessageBox.critical(self, "Cannot start server", str(exc))
            return
        self.log_view.appendPlainText(f"Serving {path.name} at {self.qr_panel.server.urls[0]}")

    def closeEvent(self, event):
        self.qr_panel.stop()
        self.library.stop()
        super().closeEvent(event)


def _display_title(release: str) -> str:
    """'Legend of Zelda, The - The Minish Cap (Europe) (En,Fr)' -> 'The Legend of Zelda - The Minish Cap'."""
    name = release.split(" (", 1)[0]
    head, sep, rest = name.partition(" - ")
    for article in ("The", "A", "An", "Die", "Der", "Das", "Le", "La", "Les"):
        if head.endswith(f", {article}"):
            head = f"{article} {head[:-len(article) - 2]}"
    return head + sep + rest


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("vcinject")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    QTimer.singleShot(300, window.first_run_check)
    code = app.exec()
    # Library analysis threads may still be downloading covers; they only read, so end
    # right away instead of letting Python finalize under them (avoids shutdown errors).
    for stream in (sys.stdout, sys.stderr):  # None in the windowed Windows app
        if stream is not None:
            stream.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())

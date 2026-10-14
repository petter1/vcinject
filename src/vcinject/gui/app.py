"""Qt GUI for vcinject, modelled on NSUI's GBA VC injector page."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PySide6.QtCore import (QLocale, QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer,
                                Signal)
    from PySide6.QtGui import (QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter,
                               QPainterPath, QPen, QPixmap, QPolygonF, QTransform)
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
        QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
        QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget,
    )
except ImportError:  # pragma: no cover - reported to the user at startup
    print("The GUI needs PySide6: pip install 'vcinject[gui]'", file=sys.stderr)
    raise

from PIL import Image

from .. import (__version__, art, artwork, gba, registry, serve, smdh, template, tickets, tools,
                vcbanner)
from .. import builder, forwarder, systems
from ..builder import (GBA_3D, GbaInjectOptions, default_product_code,
                       default_title_id, parse_title_id)
from ..errors import InjectError

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

def to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes(), rgba.width, rgba.height, QImage.Format_RGBA8888).copy()


def to_pixmap(image: Image.Image, width: int, height: int, smooth: bool = True) -> QPixmap:
    mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    return QPixmap.fromImage(to_qimage(image)).scaled(width, height, Qt.KeepAspectRatio, mode)


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
        text = QVBoxLayout()
        text.addStretch(1)
        text.addWidget(self.headline)
        text.addWidget(self.details)
        text.addWidget(self.release)
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
        self.angle = 0.0
        self._spin = QTimer(self, interval=33, timeout=self._tick)
        self.title = ""
        self.setFixedSize(self.TOP.width() + 8, self.TOP.height() + self.BOTTOM.height() + self.GAP + 8)

    def update_art(self, icon: Image.Image, banner: Image.Image | None, title: str,
                   top_screen: Image.Image | None = None, block=None):
        # Show the icon exactly as it will be encoded (48x48 RGB565).
        self.icon = to_qimage(smdh.fit_square(icon, 48))
        self.banner = to_qimage(art.letterbox(banner).convert("RGBA")) if banner else None
        self.top_screen = to_qimage(top_screen) if top_screen is not None else None
        self.block = None
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
        thickness = fw * 0.28
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
        if self.style == GBA_3D and not vcbanner.is_installed():
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


class QrPanel(QFrame):
    """Inline QR code for FBI's remote install; serves the CIA while visible."""
    status = Signal(str)  # emitted from server threads, delivered on the GUI thread

    def __init__(self):
        super().__init__(objectName="card")
        self.server: serve.CiaServer | None = None
        self.qr = QLabel(alignment=Qt.AlignCenter)
        self.qr.setFixedSize(QR_SIZE, QR_SIZE)
        self.title = QLabel(objectName="cardTitle")
        steps = QLabel("On the 3DS open <b>FBI</b> → <b>Remote Install</b> → <b>Scan QR Code</b>. "
                       "The 3DS must be on the same Wi-Fi.", wordWrap=True)
        self.url = QLabel(objectName="muted", textInteractionFlags=Qt.TextSelectableByMouse)
        self.state = QLabel("Waiting for the 3DS…", wordWrap=True)
        stop = QPushButton("Stop", objectName="secondary", clicked=self.stop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.addWidget(self.title)
        layout.addWidget(steps)
        layout.addWidget(self.qr, alignment=Qt.AlignHCenter)
        layout.addWidget(self.url, alignment=Qt.AlignHCenter)
        layout.addWidget(self.state)
        layout.addWidget(stop, alignment=Qt.AlignLeft)
        self.status.connect(self.state.setText)
        self.hide()

    shown = Signal(bool)

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
        self.server = serve.CiaServer([path], log=self.status.emit).start()
        image = QImage.fromData(self.server.qr_png(scale=10))
        self.qr.setPixmap(QPixmap.fromImage(image).scaled(QR_SIZE, QR_SIZE, Qt.KeepAspectRatio,
                                                          Qt.SmoothTransformation))
        self.title.setText(f"Install {path.name}")
        self.url.setText("<br>".join(self.server.urls))
        self.state.setText("Waiting for the 3DS…")
        self.show()

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
        actions = QHBoxLayout()
        actions.addWidget(self.build_button, 2)
        actions.addWidget(self.qr_button, 1)

        self.log_view = QPlainTextEdit(readOnly=True, placeholderText="Build log")
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log_view.setMinimumHeight(110)

        self.status_label = QLabel(objectName="muted")
        self.template_button = QPushButton("Template…", clicked=self.setup_template)
        self.tickets_button = QPushButton("Import tickets…", clicked=self.import_tickets)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
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
        self.qr_panel = QrPanel()
        right.insertWidget(1, self.qr_panel)  # takes the preview's place while serving
        right.addWidget(self.log_view, 1)
        self.qr_panel.shown.connect(lambda visible: self.preview_card.setVisible(not visible))
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(left, 5)
        columns.addLayout(right, 6)
        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 10)
        root.addLayout(columns, 1)
        root.addLayout(status_row)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.resize(1000, 800)

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

    def load_rom(self, path: Path):
        try:
            system, rom = builder.load_rom(path)
        except (OSError, InjectError) as exc:
            QMessageBox.warning(self, "Invalid ROM", str(exc))
            return
        self.rom, self.rom_path, self.system = rom, path, system
        for widget in self._gba_only:
            widget.setVisible(system.key == "gba")
        self.drop.headline.setText(path.name)
        self.drop.headline.setObjectName("cardTitle")
        self.drop.headline.style().unpolish(self.drop.headline)
        self.drop.headline.style().polish(self.drop.headline)
        size = f"{len(rom.data) / 1024 / 1024:.1f} MiB"
        if system.key == "gba":
            self.drop.details.setText(
                f"{rom.game_code or '----'} · {size} · {rom.detect_save_type().label}"
                + ("" if rom.header_valid else " · ⚠ bad header checksum"))
        else:
            ready = system.forwarder in forwarder.installed_kinds()
            self.drop.details.setText(f"{system.name} · {size} · "
                                      + ("emulator forwarder" if ready
                                         else "⚠ no emulator template yet"))
        self.drop.release.setText("Looking up release…")
        self.drop.set_cover(None, system.key.upper())
        self.title_edit.setText(path.stem)
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
                                               path.name, system=system.database),
             lambda result, error: self._artwork_found(lookup_id, result, error))

    def _artwork_found(self, lookup_id: int, result, error: str):
        if lookup_id != self._lookup_id:  # a newer ROM was loaded meanwhile
            return
        if error:
            self.drop.release.setText("Artwork lookup failed (offline?)")
            self.art_status.setText(error)
            return
        self.found = result
        if result.match:
            self.drop.release.setText(f"✓ {result.match.name}")
            if self.title_edit.text() == self.rom_path.stem:
                self.title_edit.setText(_display_title(result.match.name))
            self.art_status.setText(f"Matched by {result.match.method}; "
                                    f"{len(result.images)} images from the libretro database.")
            if result.year and not self.released_edit.text():
                self.released_edit.setText(result.year)
            if result.publisher and self.publisher_edit.text() == "Virtual Console":
                self.publisher_edit.setText(result.publisher)
        else:
            self.drop.release.setText("Release not in the No-Intro database")
            self.art_status.setText("No artwork found; using generated images.")
        self.drop.set_cover(result.images.get("boxart") or result.images.get("title"))
        self._apply_artwork()

    def _apply_artwork(self):
        self.icon_picker.set_available(artwork.available(artwork.ICON_STYLES, self.found))
        flat = artwork.available(artwork.BANNER_STYLES, self.found)
        if self.system.key != "gba":  # the VC Console Look model is a GBA block
            self.banner_picker.set_available(flat)
        else:  # prefer it once set up; otherwise offer it last
            self.banner_picker.set_available([GBA_3D, *flat] if vcbanner.is_installed()
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
        if self.banner_picker.style == GBA_3D and vcbanner.is_installed() \
                and self.system.key == "gba":
            imgs = self.found.images
            self.banner_screen = imgs.get("title") or imgs.get("snap") or imgs.get("boxart") \
                or art.default_banner(title)
            self.banner_image = None
            year, publisher = self.released_edit.text().strip(), self.publisher_edit.text().strip()
            lang = self.plate_lang.currentData()
            top = block = None
            try:
                block = vcbanner.block_parts(self.banner_screen, title, year, publisher, lang)
                if block is None:
                    top = vcbanner.mockup(self.banner_screen, title, year, publisher, lang)
            except InjectError as exc:
                self.art_status.setText(str(exc))
            self.preview.update_art(icon, None, title, top, block)
            return
        self.banner_image = self._render(self.banner_picker, artwork.render_banner)
        self.preview.update_art(icon, self.banner_image, title)

    # --- template / tools --------------------------------------------------

    def refresh_status(self):
        need = self._missing_template()
        self.setup_button.setVisible(need is not None)
        if need == "vclook":
            self.setup_button.setText("Set up VC Console Look (default look)…")
        elif need:
            self.setup_button.setText(f"Set up {systems.FORWARDER_KINDS[need]} emulator template…")
        missing = [name for name in tools.TOOLS if tools.find_tool(name) is None]
        emulators = forwarder.installed_kinds()
        parts = [f"vcinject {__version__}",
                 "template " + ("installed" if template.is_installed() else "will be downloaded"),
                 "VC Console Look " + ("installed" if vcbanner.is_installed() else "not set up"),
                 f"emulators {len(emulators)}/{len(systems.FORWARDER_KINDS)}",
                 "tools OK" if not missing else "missing " + ", ".join(missing)]
        self.status_label.setText("  ·  ".join(parts))

    def _missing_template(self) -> str | None:
        """'vclook', a forwarder kind, or None when the current ROM has all it needs."""
        if self.system.forwarder:
            return None if self.system.forwarder in forwarder.installed_kinds() else self.system.forwarder
        return None if vcbanner.is_installed() else "vclook"

    def _setup_needed(self):
        need = self._missing_template()
        if need == "vclook":
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
            "The VC Console Look (3D Game Boy Advance with the 'Virtual Console' plate) "
            "reuses the banner of a GBA VC inject you already have, e.g. one made with NSUI "
            "v28 or newer.\n\nChoose that inject's .cia (or a .cxi / banner.bin). Only its "
            "banner is used; picture and text are replaced for every build.")
        path, _ = QFileDialog.getOpenFileName(self, "Choose a GBA VC inject", "", INJECT_FILTER)
        if not path:
            return False
        try:
            vcbanner.install_from(Path(path))
        except (OSError, InjectError) as exc:
            QMessageBox.critical(self, "Not usable", str(exc))
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
        for button in (self.build_button, self.qr_button, self.template_button):
            button.setEnabled(False)
        self.build_button.setText("Building…" if success_message is None else "Working…")
        Task(self, func, lambda result, error: self._done(result, error, success_message),
             self.log_view.appendPlainText)

    def _done(self, result, error: str, success_message: str | None):
        self._busy = False
        for button in (self.build_button, self.qr_button, self.template_button):
            button.setEnabled(True)
        self.build_button.setText("Build CIA")
        self.refresh_status()
        if error:
            self.log_view.appendPlainText(f"Error: {error}")
            QMessageBox.critical(self, "Failed", error)
        elif success_message:
            self.log_view.appendPlainText(success_message)
        elif result is not None:
            self.last_output = result.output_path
            if self._qr_after_build:
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

    def show_qr(self, path: Path):
        try:
            self.qr_panel.start(path)
        except InjectError as exc:
            QMessageBox.critical(self, "Cannot start server", str(exc))
            return
        self.log_view.appendPlainText(f"Serving {path.name} at {self.qr_panel.server.urls[0]}")

    def closeEvent(self, event):
        self.qr_panel.stop()
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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

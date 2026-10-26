"""Pictures of the 3DS models, drawn in any colour – for colours without a free photo
(consolephotos.py): an open clamshell for the 3DS, 3DS XL, New 3DS and New 3DS XL,
the flat wedge of the 2DS, and the folding New 2DS XL. The New models have the
C-stick and the coloured ABXY buttons."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

# body colours that the models were most often sold in
_BODY = {"3DS": "#2b4a8c", "3DS XL": "#b3202a", "New 3DS": "#f2f2f2", "New 3DS XL": "#3a3f4a",
         "2DS": "#1f8fd6", "New 2DS XL": "#2d2f36"}
_ABXY = ("#3f6fd8", "#e04b3c", "#2fa35b", "#f2c230")  # X, A, Y, B on the New models


def console_picture(model: str, height: int = 150, body: str | None = None,
                    accent: str | None = None) -> QPixmap:
    """The model as a picture ``height`` pixels high (twice as sharp for HiDPI). ``accent``
    is the second colour: the 2DS's upper half, the New 2DS XL's buttons, else the
    lower half of two-tone models."""
    ratio = 2
    width = int(height * 0.82)
    pix = QPixmap(width * ratio, height * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    shell = QColor(body or _BODY.get(model, "#3a3f4a"))
    second = QColor(accent) if accent else None
    new = model.startswith("New")
    if model == "2DS":
        _slab(p, QRectF(4, 4, width - 8, height - 8), shell, second or QColor("#2a2c33"))
    else:
        # the XL models are bigger: the small ones leave room around them
        inset = 2 if "XL" in model else height * 0.09
        buttons = second if model == "New 2DS XL" else None
        lower = second if second is not None and buttons is None else shell
        _clamshell(p, QRectF(inset, inset, width - 2 * inset, height - 2 * inset), shell, new,
                   flat_hinge=model == "New 2DS XL", lower=lower, buttons=buttons)
    p.end()
    return pix


def _shell(p: QPainter, rect: QRectF, color: QColor, radius: float):
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, color.lighter(118))
    gradient.setColorAt(1, color.darker(112))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, gradient)
    p.setPen(QPen(color.darker(150), 1))
    p.drawPath(path)


def _screen(p: QPainter, rect: QRectF):
    path = QPainterPath()
    path.addRoundedRect(rect, 3, 3)
    p.fillPath(path, QColor("#14161c"))
    shine = QLinearGradient(rect.topLeft(), rect.bottomRight())
    shine.setColorAt(0, QColor(255, 255, 255, 40))
    shine.setColorAt(0.5, QColor(255, 255, 255, 0))
    p.fillPath(path, shine)


def _controls(p: QPainter, area: QRectF, color: QColor, new: bool, buttons: QColor | None = None):
    """Circle pad and D-pad on the left, ABXY (and the C-stick) on the right."""
    dark = buttons or (color.darker(170) if color.lightness() > 90 else color.lighter(160))
    unit = area.height() / 6
    left = area.left() + unit * 1.6
    p.setPen(Qt.NoPen)
    p.setBrush(dark)
    p.drawEllipse(QPointF(left, area.top() + unit * 1.7), unit * 0.95, unit * 0.95)  # circle pad
    pad = QPointF(left, area.top() + unit * 4.1)                                      # D-pad
    arm = unit * 0.32
    p.drawRect(QRectF(pad.x() - arm, pad.y() - arm * 3, arm * 2, arm * 6))
    p.drawRect(QRectF(pad.x() - arm * 3, pad.y() - arm, arm * 6, arm * 2))
    right = area.right() - unit * 1.6
    center = QPointF(right, area.top() + unit * 2.4)
    spread = unit * 0.75
    for (dx, dy), colour in zip(((0, -1), (1, 0), (-1, 0), (0, 1)), _ABXY):
        p.setBrush(QColor(colour) if new and buttons is None else dark)
        p.drawEllipse(center + QPointF(dx * spread, dy * spread), unit * 0.36, unit * 0.36)
    if new:  # the C-stick above the buttons
        p.setBrush(dark)
        p.drawEllipse(QPointF(right - unit * 0.6, area.top() + unit * 0.75), unit * 0.22, unit * 0.22)


def _clamshell(p: QPainter, rect: QRectF, body: QColor, new: bool, flat_hinge: bool = False,
               lower: QColor | None = None, buttons: QColor | None = None):
    lower = lower or body
    half = rect.height() * 0.48
    top = QRectF(rect.left() + rect.width() * 0.02, rect.top(), rect.width() * 0.96, half)
    bottom = QRectF(rect.left(), rect.bottom() - half, rect.width(), half)
    _shell(p, top, body, 10)
    _shell(p, bottom, lower, 12)
    if not flat_hinge:  # the hinge between the halves
        hinge = QRectF(rect.left() + rect.width() * 0.08, top.bottom() - 3,
                       rect.width() * 0.84, bottom.top() - top.bottom() + 6)
        _shell(p, hinge, body.darker(115), 4)
    margin = top.height() * 0.12
    _screen(p, QRectF(top.left() + top.width() * 0.13, top.top() + margin,
                      top.width() * 0.74, top.height() - 2 * margin))
    low = QRectF(bottom.left() + bottom.width() * 0.27, bottom.top() + margin * 0.9,
                 bottom.width() * 0.46, bottom.height() * 0.66)
    _screen(p, low)
    _controls(p, QRectF(bottom.left() + 2, bottom.top() + margin * 0.6,
                        bottom.width() - 4, bottom.height() * 0.62), lower, new, buttons)


def _slab(p: QPainter, rect: QRectF, body: QColor, upper: QColor):
    """The 2DS: one wedge with both screens, the controls beside the lower one."""
    path = QPainterPath()
    path.moveTo(rect.left() + 8, rect.top())
    path.lineTo(rect.right() - 8, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + 10)
    path.lineTo(rect.right(), rect.bottom() - 12)
    path.quadTo(rect.right(), rect.bottom(), rect.right() - 12, rect.bottom())
    path.lineTo(rect.left() + 12, rect.bottom())
    path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - 12)
    path.lineTo(rect.left(), rect.top() + 10)
    path.quadTo(rect.left(), rect.top(), rect.left() + 8, rect.top())
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, upper)
    gradient.setColorAt(0.5, upper)
    gradient.setColorAt(0.501, body)
    gradient.setColorAt(1, body.darker(115))
    p.fillPath(path, gradient)
    p.setPen(QPen(body.darker(160), 1))
    p.drawPath(path)
    h = rect.height()
    _screen(p, QRectF(rect.left() + rect.width() * 0.12, rect.top() + h * 0.06,
                      rect.width() * 0.76, h * 0.38))
    _screen(p, QRectF(rect.left() + rect.width() * 0.27, rect.top() + h * 0.55,
                      rect.width() * 0.46, h * 0.32))
    _controls(p, QRectF(rect.left() + 2, rect.top() + h * 0.54, rect.width() - 4, h * 0.36), body, False)

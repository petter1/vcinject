"""Small Qt helpers shared by the GUI modules."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage, QPixmap


def gui_settings() -> QSettings:
    """GUI preferences. With VCINJECT_DATA_DIR set (tests, portable use) they live in
    that folder instead of the user's system-wide preferences."""
    if os.environ.get("VCINJECT_DATA_DIR"):
        return QSettings(str(Path(os.environ["VCINJECT_DATA_DIR"]) / "gui.ini"), QSettings.IniFormat)
    return QSettings("vcinject", "vcinject")


def to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes(), rgba.width, rgba.height, QImage.Format_RGBA8888).copy()


def to_pixmap(image: Image.Image, width: int, height: int, smooth: bool = True) -> QPixmap:
    mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    return QPixmap.fromImage(to_qimage(image)).scaled(width, height, Qt.KeepAspectRatio, mode)

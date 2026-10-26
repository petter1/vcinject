"""Small Qt helpers shared by the GUI modules."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage, QPixmap

from ..template import data_dir


# space inside the light box that holds a page's content (tab panes, the games gallery)
CONTENT_MARGIN = 12


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


def thumbnail(key: str, width: int, load: Callable[[], bytes | None]) -> QImage:
    """A picture shrunk to at most ``width`` pixels, kept on disk for good: once made,
    it is never downloaded or scaled again. ``load()`` gives the original's bytes.
    Safe off the GUI thread (QImage, not QPixmap)."""
    name = hashlib.sha1(f"{key}@{width}".encode()).hexdigest()
    path = data_dir() / "thumbnails" / name[:2] / f"{name}.png"
    image = QImage()
    if path.is_file() and image.load(str(path)):
        return image
    data = load()
    if not data or not image.loadFromData(data):
        return QImage()
    if image.width() > width:
        image = image.scaledToWidth(width, Qt.SmoothTransformation)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path), "PNG")
    return image

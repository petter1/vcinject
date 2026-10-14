"""Icon and banner artwork: loading user images and generating defaults."""
from __future__ import annotations

import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .errors import InjectError

BANNER_SIZE = (256, 128)
ICON_SIZE = 48

_PALETTE = [
    ((88, 72, 168), (48, 36, 112)),
    ((32, 120, 176), (16, 64, 120)),
    ((200, 64, 72), (128, 32, 48)),
    ((40, 152, 96), (20, 88, 56)),
    ((224, 128, 32), (152, 72, 16)),
]


def load_image(path: Path | str) -> Image.Image:
    try:
        with Image.open(path) as img:
            img.load()
            return img.copy()
    except (OSError, ValueError) as exc:
        raise InjectError(f"Could not read image '{path}': {exc}") from exc


def letterbox(image: Image.Image, size: tuple[int, int] = BANNER_SIZE) -> Image.Image:
    """Scale an image to fit ``size`` and center it on a transparent canvas."""
    img = image.convert("RGBA")
    scale = min(size[0] / img.width, size[1] / img.height)
    resized = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(resized, ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2), resized)
    return canvas


def _colors(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return _PALETTE[sum(seed.encode()) % len(_PALETTE)]


def _gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        draw.line([(0, y), (size[0], y)],
                  fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no scalable default font
        return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int) -> ImageFont.ImageFont:
    size = start
    while size > 8:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 1
    return _font(8)


def default_icon(title: str) -> Image.Image:
    top, bottom = _colors(title)
    img = _gradient((ICON_SIZE * 4, ICON_SIZE * 4), top, bottom)
    draw = ImageDraw.Draw(img)
    words = title.split() or ["GBA"]
    initials = "".join(w[0] for w in words[:2]).upper()
    font = _fit_text(draw, initials, img.width - 40, 110)
    draw.text((img.width / 2, img.height / 2), initials, font=font, fill="white", anchor="mm")
    return img


def default_banner(title: str) -> Image.Image:
    top, bottom = _colors(title)
    img = _gradient(BANNER_SIZE, top, bottom)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 247, 119), radius=10, outline=(255, 255, 255), width=2)
    draw.text((128, 30), "GAME BOY ADVANCE", font=_font(12), fill=(255, 255, 255), anchor="mm")
    font = _fit_text(draw, title, 220, 26)
    draw.text((128, 72), title, font=font, fill="white", anchor="mm")
    return img


def write_silent_wav(path: Path, seconds: float = 0.5, rate: int = 22050) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\0\0" * int(rate * seconds))


def vc_icon_frame(image: Image.Image) -> Image.Image:
    """48x48 icon in the bevelled grey frame Virtual Console titles use.

    Outer 2 px fade from light (top) to dark (bottom), a 1 px inner line fades
    the other way, and the picture fills the remaining 42x42.
    """
    from .smdh import fit_square

    size = ICON_SIZE
    icon = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(icon)
    for y in range(size):
        t = y / (size - 1)
        outer = round(248 + (96 - 248) * t)
        draw.line([(0, y), (size - 1, y)], fill=(outer, outer + 2, outer))
    draw.rectangle((0, size - 2, size - 1, size - 1), fill=(100, 102, 100))
    for y in range(2, size - 2):
        t = (y - 2) / (size - 5)
        inner = round(96 + (192 - 96) * t)
        draw.line([(2, y), (size - 3, y)], fill=(inner, inner + 2, inner))
    icon.paste(fit_square(image, size - 6), (3, 3))
    return icon

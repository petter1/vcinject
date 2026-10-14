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


def default_banner(title: str, system_name: str = "Game Boy Advance") -> Image.Image:
    top, bottom = _colors(title)
    img = _gradient(BANNER_SIZE, top, bottom)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 247, 119), radius=10, outline=(255, 255, 255), width=2)
    draw.text((128, 30), system_name.upper(), font=_font(12), fill=(255, 255, 255), anchor="mm")
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


def write_game_boy_boot_sound(path: Path, rate: int = 32728) -> None:
    """The Game Boy start-up "ping": pulse channel 1 plays frequency register
    0x783 (~1049 Hz) briefly, then 0x7C1 (~2080 Hz) fading out (envelope 0xF3)."""
    import math
    import struct as _struct

    def tone(freq: float, seconds: float, fade: bool) -> list[int]:
        out = []
        steps = int(rate * seconds)
        period = rate / freq
        for i in range(steps):
            # envelope 0xF3: volume 15 -> 0, one step every 3/64 s
            volume = max(0, 15 - int(i / rate / (3 / 64))) if fade else 15
            square = 1 if (i % period) < period / 2 else -1
            out.append(int(square * volume / 15 * 9000))
        return out

    samples = tone(131072 / (2048 - 0x783), 0.068, False) + tone(131072 / (2048 - 0x7C1), 0.75, True)
    # soften the hard edges slightly so it doesn't click on the 3DS speakers
    smooth = [int((samples[i - 1] + 2 * samples[i] + samples[(i + 1) % len(samples)]) / 4)
              for i in range(len(samples))]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(_struct.pack(f"<{len(smooth)}h", *smooth))
    del math



# --- placeholder box art per console ------------------------------------------------
#
# Shown when no cover exists anywhere: a box in the console's typical style with
# the game's title, so the library still reads like a shelf of boxes.

_BOLD = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/Library/Fonts/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf")
_SHORT = {"gba": "GBA", "gb": "GB", "gbc": "GBC", "snes": "SNES", "nes": "NES", "gen": "MD"}

# key: (box size, body top, body bottom, band colour, band text colour, band text)
_BOXES = {
    "gba": ((256, 256), (78, 62, 160), (32, 24, 84), (205, 208, 216), (60, 60, 70), "GAME BOY ADVANCE"),
    "gb": ((256, 256), (96, 112, 92), (44, 54, 44), (196, 196, 196), (40, 40, 40), "GAME BOY"),
    "gbc": ((256, 256), (40, 132, 150), (70, 40, 120), (226, 226, 230), (120, 40, 120), "GAME BOY COLOR"),
    "snes": ((256, 192), (150, 150, 158), (88, 88, 98), (24, 24, 28), (235, 235, 235), "SUPER NINTENDO"),
    "nes": ((192, 256), (30, 30, 34), (10, 10, 12), (196, 30, 36), (255, 255, 255), "NES"),
    "gen": ((192, 256), (34, 34, 38), (10, 10, 12), (16, 16, 18), (226, 40, 40), "MEGA DRIVE"),
}


def _bold(size: int) -> ImageFont.ImageFont:
    for path in _BOLD:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return _font(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, width: int, start: int, max_lines: int = 4):
    """Largest font size at which ``text`` fits in ``max_lines`` lines of ``width``."""
    for size in range(start, 9, -1):
        font = _bold(size)
        lines, line = [], ""
        for word in text.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
        if len(lines) <= max_lines and all(draw.textlength(l, font=font) <= width for l in lines):
            return font, lines
    return _bold(10), lines[:max_lines]


def placeholder_cover(system_key: str, title: str = "") -> Image.Image:
    """A box in the style of the console's packaging, with ``title`` on it
    (without a title: the console's short name, faint)."""
    size, top, bottom, band, band_text, label = _BOXES.get(
        system_key, ((256, 256), (90, 90, 100), (40, 40, 48), (210, 210, 215), (50, 50, 60), "GAME"))
    w, h = size
    img = _gradient(size, top, bottom)
    draw = ImageDraw.Draw(img)
    if system_key in ("gba", "gb", "gbc"):  # Game Boy boxes: vertical band on the left
        band_w = w // 7
        draw.rectangle((0, 0, band_w, h), fill=band)
        text = Image.new("RGBA", (h, band_w), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(text)
        font = _fit_text_bold(tdraw, label, h - 24, band_w - 10)
        tdraw.text((h / 2, band_w / 2), label, font=font, fill=band_text, anchor="mm")
        img.paste(text.rotate(90, expand=True), (0, 0), text.rotate(90, expand=True))
        area = (band_w + 14, 14, w - 14, h - 14)
    else:  # SNES, NES, Mega Drive: band across the top
        band_h = h // 7
        draw.rectangle((0, 0, w, band_h), fill=band)
        font = _fit_text_bold(draw, label, w - 24, band_h - 12)
        draw.text((w / 2, band_h / 2), label, font=font, fill=band_text, anchor="mm")
        if system_key == "snes":  # the four button colours
            for i, colour in enumerate(((210, 40, 50), (240, 200, 40), (40, 160, 80), (40, 90, 200))):
                cx = w - 16 - i * 11
                draw.ellipse((cx - 4, h - 16, cx + 4, h - 8), fill=colour)
        area = (14, band_h + 14, w - 14, h - 14)
    if not title:
        font = _bold(max(18, (area[3] - area[1]) // 3))
        draw.text(((area[0] + area[2]) / 2, (area[1] + area[3]) / 2), _SHORT.get(system_key, "?"),
                  font=font, fill=tuple(min(255, c + 45) for c in top), anchor="mm")
        draw.rectangle((0, 0, w - 1, h - 1), outline=(0, 0, 0))
        return img
    # the title, centred in the free area
    font, lines = _wrap(draw, title, area[2] - area[0], start=max(14, (area[3] - area[1]) // 5))
    line_h = font.getbbox("Ag")[3] + 4
    y = (area[1] + area[3]) / 2 - line_h * len(lines) / 2
    for line in lines:
        draw.text(((area[0] + area[2]) / 2, y + line_h / 2), line, font=font,
                  fill=(255, 255, 255), anchor="mm")
        y += line_h
    draw.rectangle((0, 0, w - 1, h - 1), outline=(0, 0, 0))
    return img


def _fit_text_bold(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int):
    for size in range(start, 7, -1):
        font = _bold(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _bold(8)

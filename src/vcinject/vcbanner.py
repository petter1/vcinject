"""Virtual Console style 3D banner: a Game Boy Advance model showing the game
on its screen, with the "Virtual Console" plate (title + release year) below.

The 3D model is not part of vcinject. It is taken from a GBA VC inject the
user already owns (e.g. one made with NSUI v28+), whose banner is reused as a
template: only its screen texture (COMMON1) and the plate texture(s) (COMMON2)
are replaced; model, animation and the jingle stay as they are.

Two layouts are known: NSUI v28 keeps one plate per console language in the
CBMD's language slots and stretches the game picture over COMMON1; newer
NSUI banners have a single plate in the common CGFX and draw the picture
inside a coloured frame on a transparent COMMON1. Both are handled.
"""
from __future__ import annotations

import functools
import locale
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import cgfx, ncch
from .errors import InjectError
from .template import data_dir

SCREEN_TEXTURE = "COMMON1"
PLATE_TEXTURE = "COMMON2"

# "Released: 2004" per CBMD language slot (see cgfx.REGIONS).
RELEASED = {
    "en": "Released: {}", "fr": "Sortie : {}", "de": "Erschienen: {}", "it": "Uscita: {}",
    "es": "Lanzamiento: {}", "nl": "Uitgebracht: {}", "pt": "Lançamento: {}",
    "ru": "Выпуск: {}", "ja": "{}",
}

_BOLD_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
)


def template_path() -> Path:
    return data_dir() / "templates" / "gba_banner.bin"


def is_installed() -> bool:
    return template_path().is_file()


def _validate(banner: bytes) -> cgfx.Cbmd:
    cbmd = cgfx.Cbmd.parse(banner)
    if SCREEN_TEXTURE not in cgfx.textures(cbmd.common):
        raise InjectError("This banner has no screen texture; it is not a 3D GBA VC banner.")
    if not any(PLATE_TEXTURE in cgfx.textures(c) for c in [cbmd.common, *cbmd.regions.values()]):
        raise InjectError("This banner has no 'Virtual Console' plate texture.")
    return cbmd


def install_from(path: Path) -> Path:
    """Install the banner of a GBA VC inject (.cia/.cxi) or a raw banner.bin."""
    data = path.read_bytes()
    if data[:4] == b"CBMD":
        banner = data
    else:
        cxi = ncch.first_cia_content(data) if data[0x100:0x104] != b"NCCH" else data
        banner = ncch.parse_ncch(cxi).exefs_files.get("banner", b"")
        if banner[:4] != b"CBMD":
            raise InjectError("No banner found in this title.")
    _validate(banner)
    target = template_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(banner)
    return target


@functools.lru_cache(maxsize=1)
def _load(mtime: float) -> cgfx.Cbmd:
    return _validate(template_path().read_bytes())


def load_template() -> cgfx.Cbmd:
    if not is_installed():
        raise InjectError("VC Console Look not set up. Use 'vcinject setup "
                          "--banner-donor <your GBA VC inject.cia>'.")
    return _load(template_path().stat().st_mtime)


# --- the "Virtual Console" plate ---------------------------------------------------

@functools.lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.ImageFont:
    for path in _BOLD_FONTS:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


@dataclass(frozen=True)
class _PlateLayout:
    text_left: int
    text_right: int
    rows: tuple[int, ...]  # rows inside the plate that may be repainted


def _layout(plate: Image.Image) -> _PlateLayout:
    """Find the free text area right of the "Virtual Console" badge."""
    lum, alpha = plate.convert("L"), plate.getchannel("A")
    w, h = plate.size
    mid = h // 2
    right = next(x for x in range(w - 1, w // 2, -1)
                 if alpha.getpixel((x, mid)) == 255 and lum.getpixel((x, mid)) >= 225)
    badge_end = next((x for x in range(w // 2, 0, -1) if lum.getpixel((x, mid)) < 180), w // 3)
    left = badge_end + 3
    # The column right of the badge never holds text; its rows tell us where
    # the plate's inner area (and its background gradient) is.
    rows = tuple(y for y in range(h)
                 if alpha.getpixel((left, y)) == 255 and lum.getpixel((left, y)) >= 205)
    return _PlateLayout(left, right, rows)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int, smallest: int):
    for size in range(start, smallest - 1, -1):
        font = _font(size)
        if draw.textlength(text, font=font) <= width:
            return font
    return None


def render_plate(template: Image.Image, title: str, subtitle: str) -> Image.Image:
    """Repaint the text area of a plate texture with a new title and subtitle."""
    plate = template.convert("LA").convert("RGBA")
    layout = _layout(plate)
    px = plate.load()
    for y in layout.rows:  # erase old text with the plate's own background gradient
        bg = px[layout.text_left, y]
        for x in range(layout.text_left, layout.text_right + 1):
            px[x, y] = bg

    draw = ImageDraw.Draw(plate)
    width = layout.text_right - layout.text_left - 6
    center = (layout.text_left + layout.text_right) / 2
    top, bottom = min(layout.rows), max(layout.rows)
    ink = (40, 40, 40, 255)

    title_font = _fit(draw, title, width, 19, 12)
    lines = [title]
    if title_font is None:  # too long for one line: wrap into two smaller ones
        head, sep, tail = title.partition(" - ")
        if sep:  # "Series - Subtitle" reads best split at the dash
            lines = [head, tail]
        else:
            words = title.split()
            split = max(1, min(range(1, len(words) or 2),
                               key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:])))))
            lines = [" ".join(words[:split]), " ".join(words[split:])] if len(words) > 1 else [title]
        longest = max(lines, key=lambda line: draw.textlength(line, font=_font(17)))
        title_font = _fit(draw, longest, width, 17, 8) or _font(8)

    sub_font = _fit(draw, subtitle, width, 13, 8) if subtitle else None
    sub_height = 16 if sub_font else 0
    line_height = title_font.size + 2
    block = line_height * len(lines)
    y = top + (bottom - top - sub_height - block) / 2 + line_height / 2
    for line in lines:
        draw.text((center, y), line, font=title_font, fill=ink, anchor="mm")
        y += line_height
    if sub_font:
        draw.text((center, bottom - 9), subtitle, font=sub_font, fill=ink, anchor="mm")
    return plate


# --- banner ------------------------------------------------------------------------

def default_language() -> str:
    """Two-letter language of this computer, used for single-plate banners."""
    import os

    raw = locale.getlocale()[0] or os.environ.get("LC_ALL") or os.environ.get("LANG") or "en"
    lang = raw.split("_")[0].split(".")[0].lower()
    return lang if lang in RELEASED else "en"


def _crop_3_2(source: Image.Image) -> Image.Image:
    img = source.convert("RGB")
    target = 240 / 160
    if img.width / img.height > target:  # crop wider images to the GBA's 3:2
        w = round(img.height * target)
        img = img.crop(((img.width - w) // 2, 0, (img.width - w) // 2 + w, img.height))
    elif img.width / img.height < target:
        h = round(img.width / target)
        img = img.crop((0, (img.height - h) // 2, img.width, (img.height - h) // 2 + h))
    return img


def screen_image(source: Image.Image, size: tuple[int, int] = (128, 128)) -> Image.Image:
    """The game picture squeezed onto a texture the model's UVs stretch back to 3:2."""
    return _crop_3_2(source).resize(size, Image.LANCZOS)


def _close(a, b, tolerance: int = 40) -> bool:
    return sum(abs(x - y) for x, y in zip(a[:3], b[:3])) <= tolerance


def frame_content_box(texture: Image.Image) -> tuple[int, int, int, int] | None:
    """Inner box of a framed screen texture (opaque frame on transparency), or None."""
    alpha = texture.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return None  # fully opaque: the picture covers the whole texture
    box = alpha.point(lambda a: 255 if a > 128 else 0).getbbox()
    if box is None:
        return None
    left, top, right, bottom = box
    px = texture.load()
    mid_x, mid_y = (left + right) // 2, (top + bottom) // 2
    border = px[left, mid_y]
    side = next((t for t in range(1, (right - left) // 4) if not _close(px[left + t, mid_y], border)), 0)
    edge = next((t for t in range(1, (bottom - top) // 4) if not _close(px[mid_x, top + t], border)), 0)
    return left + side, top + edge, right - side, bottom - edge


def compose_screen(template: Image.Image, source: Image.Image) -> Image.Image:
    box = frame_content_box(template)
    if box is None:
        return screen_image(source, template.size)
    out = template.convert("RGBA").copy()
    left, top, right, bottom = box
    out.paste(_crop_3_2(source).resize((right - left, bottom - top), Image.LANCZOS), (left, top))
    return out


def released_text(lang: str, year: str | None, fallback: str = "") -> str:
    if not year:
        return fallback
    return RELEASED.get(lang, RELEASED["en"]).format(year)


def _replace_plate(cgfx_data: bytes, title: str, subtitle: str) -> bytes:
    tex = cgfx.textures(cgfx_data).get(PLATE_TEXTURE)
    if tex is None:
        return cgfx_data
    plate = render_plate(cgfx.decode_texture(cgfx_data, tex), title, subtitle)
    return cgfx.replace_texture(cgfx_data, PLATE_TEXTURE, plate)


def build(screen: Image.Image, title: str, year: str | None, publisher: str = "",
          lang: str | None = None) -> bytes:
    """Banner bytes. ``lang`` picks the language of a single shared plate; banners
    with per-language plates are localised for every console language."""
    cbmd = load_template()
    common_tex = cgfx.textures(cbmd.common)[SCREEN_TEXTURE]
    picture = compose_screen(cgfx.decode_texture(cbmd.common, common_tex), screen)
    common = cgfx.replace_texture(cbmd.common, SCREEN_TEXTURE, picture)
    common = _replace_plate(common, title, released_text(lang or default_language(), year, publisher))
    regions = {}
    for index, region_cgfx in cbmd.regions.items():
        region_lang = cgfx.REGIONS[index].split("-")[1] if index < len(cgfx.REGIONS) else "en"
        regions[index] = _replace_plate(region_cgfx, title, released_text(region_lang, year, publisher))
    return cgfx.Cbmd(common, regions, cbmd.cwav).build()


def has_localised_plates() -> bool:
    return any(PLATE_TEXTURE in cgfx.textures(c) for c in load_template().regions.values())


def plate_preview(title: str, year: str | None, publisher: str = "",
                  lang: str | None = None) -> Image.Image:
    cbmd = load_template()
    lang = lang or default_language()
    candidates = [i for i, r in enumerate(cgfx.REGIONS)
                  if r.endswith(lang) and PLATE_TEXTURE in cgfx.textures(cbmd.regions.get(i, b"CGFX"))] \
        if cbmd.regions else []
    source = cbmd.regions[candidates[0]] if candidates else (
        cbmd.common if PLATE_TEXTURE in cgfx.textures(cbmd.common)
        else next(c for c in cbmd.regions.values() if PLATE_TEXTURE in cgfx.textures(c)))
    tex = cgfx.textures(source)[PLATE_TEXTURE]
    return render_plate(cgfx.decode_texture(source, tex), title, released_text(lang, year, publisher))


def body_color() -> tuple[int, int, int]:
    """Main colour of the model, for the GUI mock-up."""
    cbmd = load_template()
    textures = cgfx.textures(cbmd.common)
    screen = cgfx.decode_texture(cbmd.common, textures[SCREEN_TEXTURE])
    box = frame_content_box(screen)
    if box is not None:  # the frame around the picture has the shell colour
        return screen.getpixel((box[0] - 1, (box[1] + box[3]) // 2))[:3]
    for name, tex in textures.items():
        if name in (SCREEN_TEXTURE, PLATE_TEXTURE):
            continue
        try:
            img = cgfx.decode_texture(cbmd.common, tex)
        except InjectError:
            continue
        return img.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
    return (88, 72, 168)


def mockup(screen: Image.Image, title: str, year: str | None, publisher: str = "",
           lang: str | None = None) -> Image.Image:
    """Approximate top-screen view of the 3D banner (400x240) for previews.

    The real banner is a 3D model rendered by the HOME Menu; this flat drawing
    only shows which picture and text end up where.
    """
    from PIL import ImageEnhance

    cbmd = load_template()
    screen_tex = cgfx.decode_texture(cbmd.common, cgfx.textures(cbmd.common)[SCREEN_TEXTURE])
    if frame_content_box(screen_tex) is not None:
        return _block_mockup(compose_screen(screen_tex, screen), title, year, publisher, lang)

    img = Image.new("RGBA", (400, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    body = body_color()
    if sum(body) < 360:  # shell textures are shaded; brighten for a flat drawing
        body = ImageEnhance.Brightness(Image.new("RGB", (1, 1), body)).enhance(1.35).getpixel((0, 0))
    shade = tuple(max(0, c - 28) for c in body)

    draw.rounded_rectangle((62, 26, 338, 166), radius=38, fill=shade)
    draw.rounded_rectangle((62, 22, 338, 160), radius=38, fill=body)
    draw.rounded_rectangle((128, 34, 272, 140), radius=8, fill=(52, 52, 60))
    draw.rectangle((140, 44, 260, 124), fill=(0, 0, 0))
    img.paste(screen_image(screen, (120, 80)), (140, 44))
    draw.text((200, 132), "GAME BOY ADVANCE", font=_font(7), fill=(200, 200, 210), anchor="mm")
    # D-pad and buttons
    draw.rounded_rectangle((90, 82, 122, 92), radius=2, fill=(220, 220, 228))
    draw.rounded_rectangle((101, 71, 111, 103), radius=2, fill=(220, 220, 228))
    for cx, cy in ((290, 78), (272, 94)):
        draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=(220, 220, 228))

    img.alpha_composite(plate_preview(title, year, publisher, lang), (72, 172))
    return img


def block_parts(screen: Image.Image, title: str, year: str | None, publisher: str = "",
                lang: str | None = None):
    """For block-style templates: (front face, plate, side colour) to animate; else None."""
    cbmd = load_template()
    screen_tex = cgfx.decode_texture(cbmd.common, cgfx.textures(cbmd.common)[SCREEN_TEXTURE])
    if frame_content_box(screen_tex) is None:
        return None
    face = compose_screen(screen_tex, screen)
    face = face.crop(face.getchannel("A").getbbox())
    return face, plate_preview(title, year, publisher, lang), body_color()


def _block_mockup(face: Image.Image, title: str, year: str | None, publisher: str,
                  lang: str | None) -> Image.Image:
    """Newer NSUI banners: a rotating block with the framed picture on its front."""
    img = Image.new("RGBA", (400, 240), (0, 0, 0, 0))
    face = face.crop(face.getchannel("A").getbbox())
    face = face.resize((round(face.width * 1.15), round(face.height * 1.15)), Image.LANCZOS)
    x, y = (400 - face.width) // 2, 30
    # side of the block: the frame colour, darker, offset to the lower right
    side = Image.new("RGBA", face.size, tuple(max(0, c - 45) for c in body_color()) + (255,))
    side.putalpha(face.getchannel("A"))
    for depth in range(10, 0, -1):
        img.alpha_composite(side, (x + depth, y + depth // 2))
    img.alpha_composite(face, (x, y))
    img.alpha_composite(plate_preview(title, year, publisher, lang), (72, 172))
    return img


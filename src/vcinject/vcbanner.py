"""VC Console Look: the 3D banner of Virtual Console titles - a model of the
console's cartridge / handheld showing the game, the "Virtual Console" plate
(title + release year) below and the console's jingle.

There is one template per system (GBA, NES, ...). The models are not part of
vcinject: each template is the banner of a VC title or inject the user owns
(official ones, or e.g. made with NSUI). Only the picture texture (COMMON1) and
the plate texture(s) (COMMON2) are replaced; model, animation and jingle stay.

Picture layouts that are handled:
- framed: an opaque frame on a transparent texture (newer NSUI GBA banners);
  the picture goes inside the frame, with the frame's rounded inner corners
- boxed: the picture covers part of an otherwise black texture (official
  Nintendo VC banners)
- full: the picture covers the whole texture (NSUI v28)
Plates are either one per console language (CBMD language slots) or a single
shared one in the common CGFX.
"""
from __future__ import annotations

import functools
import locale
import struct
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


# Shell colours for the preview when the template doesn't reveal one.
_SHELL = {"gba": (88, 72, 168), "nes": (150, 150, 156), "snes": (170, 170, 176),
          "gb": (176, 176, 168), "gbc": (176, 176, 168), "gen": (40, 40, 44)}


def template_path(system: str = "gba") -> Path:
    path = data_dir() / "templates" / "vc_looks" / f"{system}.bin"
    legacy = data_dir() / "templates" / "gba_banner.bin"  # before per-system looks
    if system == "gba" and not path.is_file() and legacy.is_file():
        return legacy
    return path


def is_installed(system: str = "gba") -> bool:
    return template_path(system).is_file()


def installed_systems() -> list[str]:
    from .systems import SYSTEMS
    return [key for key in SYSTEMS if is_installed(key)]


def _validate(banner: bytes) -> cgfx.Cbmd:
    cbmd = cgfx.Cbmd.parse(banner)
    if SCREEN_TEXTURE not in cgfx.textures(cbmd.common):
        raise InjectError("This banner has no picture texture; it is not a 3D VC banner.")
    if not any(_is_plate(cgfx.textures(c).get(PLATE_TEXTURE)) for c in [cbmd.common, *cbmd.regions.values()]):
        raise InjectError("This banner has no 'Virtual Console' plate texture.")
    return cbmd


def detect_system(parts: ncch.NcchParts) -> str | None:
    """Which console a VC title or inject is for, from what it contains."""
    from . import romfs

    code = parts.exefs_files.get(".code", b"")
    if code[-0x10:-0xC] == b".CAA":
        return "gba"
    if not parts.romfs:
        return None
    try:
        files = romfs.read(parts.romfs)
    except (InjectError, struct.error, ValueError):
        return None
    internal = files.get("/internal_name.txt", b"").decode("utf-8", "replace")
    prefix = {"snes": "snes", "gen": "gen", "md": "gen", "gb": "gb", "gbc": "gbc", "nes": "nes",
              "fds": "nes"}.get(internal.split("_", 1)[0])
    if prefix:
        return prefix
    for path, data in files.items():  # official VC emulators keep the game in /rom/
        if not path.startswith("/rom/"):
            continue
        if data[:4] == b"TNES":
            return "nes"
        if len(data) > 0x150 and data[0x104:0x108] == b"\xce\xed\x66\x66":
            return "gbc" if data[0x143] in (0x80, 0xC0) else "gb"
    return None


def install_from(path: Path, system: str | None = None) -> tuple[str, Path]:
    """Install the banner of a VC title / inject (.cia/.cxi) or a raw banner.bin as
    the look for its system (detected unless given)."""
    data = Path(path).read_bytes()
    if data[:4] == b"CBMD":
        banner, detected = data, None
    else:
        cxi = ncch.first_cia_content(data) if data[0x100:0x104] != b"NCCH" else data
        parts = ncch.parse_ncch(cxi)
        banner = parts.exefs_files.get("banner", b"")
        if banner[:4] != b"CBMD":
            raise InjectError("No banner found in this title.")
        detected = detect_system(parts)
    _validate(banner)
    system = system or detected
    if system is None:
        raise InjectError("Can't tell which console this banner is for; choose the system.")
    target = data_dir() / "templates" / "vc_looks" / f"{system}.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(banner)
    return system, target


@functools.lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> cgfx.Cbmd:
    return _validate(Path(path).read_bytes())


def load_template(system: str = "gba") -> cgfx.Cbmd:
    if not is_installed(system):
        raise InjectError(f"VC Console Look for {system.upper()} not set up. Use 'vcinject setup "
                          f"--banner-donor <a {system.upper()} VC title or inject.cia>'.")
    path = template_path(system)
    return _load(str(path), path.stat().st_mtime)


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

    one_line = _fit(draw, title, width, 19, 12)
    head, sep, tail = title.partition(" - ")
    if sep:  # "Series - Subtitle" reads best split at the dash
        wrapped = [head, tail]
    elif len(title.split()) > 1:
        words = title.split()
        split = min(range(1, len(words)),
                    key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        wrapped = [" ".join(words[:split]), " ".join(words[split:])]
    else:  # a single word can't be wrapped
        wrapped = [title]
    longest = max(wrapped, key=lambda line: draw.textlength(line, font=_font(16)))
    two_lines = _fit(draw, longest, width, 16, 8) if len(wrapped) == 2 else None
    # Two lines when one line would have to shrink noticeably more.
    if one_line is not None and (two_lines is None or one_line.size + 2 >= two_lines.size):
        lines, title_font = [title], one_line
    else:
        lines, title_font = wrapped, two_lines or _font(8)

    sub_font = _fit(draw, subtitle, width, 13, 8) if subtitle else None
    # Title lines and subtitle form one block, centred vertically, so a short or
    # small title doesn't leave a gap above the subtitle.
    line_height = title_font.size + 2
    gap = 3 if sub_font else 0
    sub_height = sub_font.size + 2 if sub_font else 0
    block = line_height * len(lines) + gap + sub_height
    y = top + (bottom - top + 1 - block) / 2
    for line in lines:
        draw.text((center, y + line_height / 2), line, font=title_font, fill=ink, anchor="mm")
        y += line_height
    if sub_font:
        draw.text((center, y + gap + sub_height / 2), subtitle, font=sub_font, fill=ink, anchor="mm")
    return plate


# --- banner ------------------------------------------------------------------------

def default_language() -> str:
    """Two-letter language of this computer, used for single-plate banners."""
    import os

    raw = locale.getlocale()[0] or os.environ.get("LC_ALL") or os.environ.get("LANG") or "en"
    lang = raw.split("_")[0].split(".")[0].lower()
    return lang if lang in RELEASED else "en"


_ASPECT = {"gba": 240 / 160, "nes": 256 / 224, "snes": 256 / 224, "gb": 160 / 144,
           "gbc": 160 / 144, "gen": 320 / 224}


def _crop_aspect(source: Image.Image, aspect: float) -> Image.Image:
    img = source.convert("RGB")
    if img.width / img.height > aspect * 1.02:  # crop wider images to the console's aspect
        w = round(img.height * aspect)
        img = img.crop(((img.width - w) // 2, 0, (img.width - w) // 2 + w, img.height))
    elif img.width / img.height < aspect / 1.02:
        h = round(img.width / aspect)
        img = img.crop((0, (img.height - h) // 2, img.width, (img.height - h) // 2 + h))
    return img


def screen_image(source: Image.Image, size: tuple[int, int] = (128, 128),
                 system: str = "gba") -> Image.Image:
    """The game picture squeezed onto a texture the model's UVs stretch back."""
    return _crop_aspect(source, _ASPECT.get(system, 4 / 3)).resize(size, Image.LANCZOS)


def _close(a, b, tolerance: int = 40) -> bool:
    return sum(abs(x - y) for x, y in zip(a[:3], b[:3])) <= tolerance


def frame_content_box(texture: Image.Image) -> tuple[int, int, int, int] | None:
    """Inner box of a framed picture texture (opaque frame on transparency), or None."""
    alpha = texture.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return None  # fully opaque: no frame
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


def _inner_radius(texture: Image.Image, box: tuple[int, int, int, int]) -> int:
    """Radius of the frame's rounded inner corners: how far the frame colour
    reaches along the diagonal into the content box's corner."""
    left, top, right, bottom = box
    px = texture.load()
    border = px[left - 1, (top + bottom) // 2]
    reach = next((d for d in range(0, min(right - left, bottom - top) // 4)
                  if not _close(px[left + d, top + d], border)), 0)
    # a quarter circle of radius r leaves r * (1 - 1/sqrt(2)) frame pixels on the diagonal
    return min(round(reach / 0.2929), (bottom - top) // 3) if reach else 0


def picture_box(texture: Image.Image) -> tuple[tuple[int, int, int, int], str]:
    """Where the game picture sits in a template's picture texture, and how."""
    box = frame_content_box(texture)
    if box is not None:
        return box, "framed"
    rgb = texture.convert("RGB")
    content = rgb.point(lambda v: 255 if v > 12 else 0).convert("L").point(
        lambda v: 255 if v else 0).getbbox()
    w, h = texture.size
    if content is not None:
        left, top, right, bottom = content
        margins = (left, top, w - right, h - bottom)
        area = (right - left) * (bottom - top)
        if max(margins) >= 3 and area >= 0.4 * w * h:
            return content, "boxed"
    return (0, 0, w, h), "full"


def compose_screen(template: Image.Image, source: Image.Image, system: str = "gba") -> Image.Image:
    box, kind = picture_box(template)
    if kind == "full":
        return screen_image(source, template.size, system)
    left, top, right, bottom = box
    aspect = _ASPECT.get(system, 4 / 3)
    width, height = right - left, bottom - top
    game = _crop_aspect(source, aspect)
    if kind == "framed" and abs(width / height - aspect) > 0.15 * aspect:
        # e.g. a 10:9 Game Boy screen in the GBA's 3:2 frame: black bars, no stretching
        picture = Image.new("RGB", (width, height), (0, 0, 0))
        fit = game.copy()
        fit.thumbnail((width, height), Image.LANCZOS)
        if fit.height < height and fit.width < width:
            fit = game.resize((round(height * aspect), height), Image.LANCZOS)
        picture.paste(fit, ((width - fit.width) // 2, (height - fit.height) // 2))
    else:
        picture = game.resize((width, height), Image.LANCZOS)
    out = template.convert("RGBA").copy()
    mask = None
    if kind == "framed":
        radius = _inner_radius(template, box)
        if radius:
            mask = Image.new("L", picture.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, picture.width - 1, picture.height - 1),
                                                   radius=radius, fill=255)
    out.paste(picture, (left, top), mask)
    return out


def released_text(lang: str, year: str | None, fallback: str = "") -> str:
    if not year:
        return fallback
    return RELEASED.get(lang, RELEASED["en"]).format(year)


def _is_plate(tex) -> bool:
    """A real plate texture; official banners also carry tiny placeholders by that name."""
    return tex is not None and tex.width >= 128 and tex.height >= 32


def _replace_plate(cgfx_data: bytes, title: str, subtitle: str) -> bytes:
    tex = cgfx.textures(cgfx_data).get(PLATE_TEXTURE)
    if not _is_plate(tex):
        return cgfx_data
    plate = render_plate(cgfx.decode_texture(cgfx_data, tex), title, subtitle)
    return cgfx.replace_texture(cgfx_data, PLATE_TEXTURE, plate)


def build(screen: Image.Image, title: str, year: str | None, publisher: str = "",
          lang: str | None = None, system: str = "gba") -> bytes:
    """Banner bytes. ``lang`` picks the language of a single shared plate; banners
    with per-language plates are localised for every console language."""
    cbmd = load_template(system)
    common_tex = cgfx.textures(cbmd.common)[SCREEN_TEXTURE]
    picture = compose_screen(cgfx.decode_texture(cbmd.common, common_tex), screen, system)
    common = cgfx.replace_texture(cbmd.common, SCREEN_TEXTURE, picture)
    common = _replace_plate(common, title, released_text(lang or default_language(), year, publisher))
    regions = {}
    sources = _region_sources(cbmd)
    for index, region_cgfx in sources.items():
        region_lang = cgfx.REGIONS[index].split("-")[1] if index < len(cgfx.REGIONS) else "en"
        regions[index] = _replace_plate(region_cgfx, title, released_text(region_lang, year, publisher))
    return cgfx.Cbmd(common, regions, cbmd.cwav).build()


def _region_sources(cbmd: cgfx.Cbmd) -> dict[int, bytes]:
    """Per-language CGFX for every console language. Banners made for one region
    (e.g. a US title with en/fr/es/pt plates) would show no plate elsewhere, so
    missing languages get a copy of an existing plate CGFX, localised later."""
    with_plate = {i: c for i, c in cbmd.regions.items() if _is_plate(cgfx.textures(c).get(PLATE_TEXTURE))}
    if not with_plate:
        return dict(cbmd.regions)
    sources = dict(cbmd.regions)
    for index, region in enumerate(cgfx.REGIONS):
        if index in sources:
            continue
        lang = region.split("-")[1]
        same_lang = [i for i in with_plate if cgfx.REGIONS[i].endswith(lang)]
        sources[index] = with_plate[(same_lang or sorted(with_plate))[0]]
    return sources


def has_localised_plates(system: str = "gba") -> bool:
    return any(_is_plate(cgfx.textures(c).get(PLATE_TEXTURE)) for c in load_template(system).regions.values())


def plate_preview(title: str, year: str | None, publisher: str = "",
                  lang: str | None = None, system: str = "gba") -> Image.Image:
    cbmd = load_template(system)
    lang = lang or default_language()
    sources = _region_sources(cbmd)
    candidates = [i for i in sorted(sources) if cgfx.REGIONS[i].endswith(lang)
                  and _is_plate(cgfx.textures(sources[i]).get(PLATE_TEXTURE))]
    source = sources[candidates[0]] if candidates else (
        cbmd.common if _is_plate(cgfx.textures(cbmd.common).get(PLATE_TEXTURE))
        else next(c for c in sources.values() if _is_plate(cgfx.textures(c).get(PLATE_TEXTURE))))
    tex = cgfx.textures(source)[PLATE_TEXTURE]
    return render_plate(cgfx.decode_texture(source, tex), title, released_text(lang, year, publisher))


def body_color(system: str = "gba") -> tuple[int, int, int]:
    """Main colour of the model, for the GUI preview."""
    cbmd = load_template(system)
    textures = cgfx.textures(cbmd.common)
    screen = cgfx.decode_texture(cbmd.common, textures[SCREEN_TEXTURE])
    box = frame_content_box(screen)
    if box is not None:  # the frame around the picture has the shell colour
        return screen.getpixel((box[0] - 1, (box[1] + box[3]) // 2))[:3]
    return _SHELL.get(system, (150, 150, 156))


def block_parts(screen: Image.Image, title: str, year: str | None, publisher: str = "",
                lang: str | None = None, system: str = "gba"):
    """(front face, plate, shell colour) for the rotating preview of a VC look."""
    cbmd = load_template(system)
    screen_tex = cgfx.decode_texture(cbmd.common, cgfx.textures(cbmd.common)[SCREEN_TEXTURE])
    face = compose_screen(screen_tex, screen, system)
    box, kind = picture_box(screen_tex)
    if kind == "framed":
        face = face.crop(face.getchannel("A").getbbox())
    elif kind == "boxed":
        face = face.crop(box)
    else:
        face = _crop_aspect(screen, _ASPECT.get(system, 4 / 3)).resize((128, 96)).convert("RGBA")
    return face, plate_preview(title, year, publisher, lang, system), body_color(system)


def mockup(screen: Image.Image, title: str, year: str | None, publisher: str = "",
           lang: str | None = None, system: str = "gba") -> Image.Image:
    """Static top-screen approximation (400x240) of the 3D banner."""
    face, plate, shell = block_parts(screen, title, year, publisher, lang, system)
    img = Image.new("RGBA", (400, 240), (0, 0, 0, 0))
    face = face.resize((round(face.width * 1.15), round(face.height * 1.15)), Image.LANCZOS)
    x, y = (400 - face.width) // 2, 30
    side = Image.new("RGBA", face.size, tuple(max(0, c - 45) for c in shell) + (255,))
    side.putalpha(face.getchannel("A"))
    for depth in range(10, 0, -1):
        img.alpha_composite(side, (x + depth, y + depth // 2))
    img.alpha_composite(face, (x, y))
    img.alpha_composite(plate, (72, 172))
    return img


# --- looks derived from another console's look ---------------------------------------

# Cartridge colours for looks derived from the GBA block.
DERIVED_COLORS = {"gb": (168, 168, 172), "gbc": (78, 78, 86)}


def _recolor(source_ref: tuple[int, int, int], target: tuple[int, int, int]):
    """Map a colour of the source shell to the target colour, keeping its brightness
    relative to the reference (max channel, so saturated blues don't wash out)."""
    reference = max(source_ref) or 1

    def transform(color):
        scale = max(color[:3]) / reference
        return tuple(max(0, min(255, round(c * scale))) for c in target)
    return transform


def derive_look(target: str, source: str = "gba",
                color: tuple[int, int, int] | None = None) -> Path:
    """Create a look for ``target`` (e.g. Game Boy) from an installed look by
    recolouring its shell and frame; the jingle becomes the target's start-up
    sound where known. Returns the installed template path."""
    from . import tools

    cbmd = load_template(source)
    color = color or DERIVED_COLORS.get(target, (150, 150, 156))
    textures = cgfx.textures(cbmd.common)
    transform = _recolor(body_color(source), color)

    common = cbmd.common
    for name, tex in textures.items():
        if tex.format in (cgfx.FMT_ETC1, cgfx.FMT_ETC1A4) and name not in (SCREEN_TEXTURE, PLATE_TEXTURE):
            common = cgfx.recolor_etc1(common, tex, transform)
    screen_tex = textures[SCREEN_TEXTURE]
    screen = cgfx.decode_texture(common, screen_tex)
    box = frame_content_box(screen)
    if box is not None:  # recolour the frame around the picture, keep the picture area
        px = screen.load()
        for y in range(screen.height):
            for x in range(screen.width):
                r, g, b, a = px[x, y]
                if a and not (box[0] <= x < box[2] and box[1] <= y < box[3]):
                    px[x, y] = transform((r, g, b)) + (a,)
        common = cgfx.replace_texture(common, SCREEN_TEXTURE, screen)

    cwav = cbmd.cwav
    if target in ("gb", "gbc"):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="vcinject-") as tmp:
            wav, out = Path(tmp) / "boot.wav", Path(tmp) / "boot.bcwav"
            from .art import write_game_boy_boot_sound
            write_game_boy_boot_sound(wav)
            tools.run_tool("bannertool", "makecwav", "-i", wav, "-o", out)
            cwav = out.read_bytes()

    target_path = data_dir() / "templates" / "vc_looks" / f"{target}.bin"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(cgfx.Cbmd(common, dict(cbmd.regions), cwav).build())
    return target_path


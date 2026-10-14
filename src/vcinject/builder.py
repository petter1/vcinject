"""End-to-end GBA VC inject pipeline: ROM -> CXI -> CIA."""
from __future__ import annotations

import hashlib
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from . import art, artwork, cgfx, gba, ncch, registry, smdh, tools, vcbanner
from .errors import InjectError
from .template import GbaTemplate

Log = Callable[[str], None]

_TITLE_ID_HIGH = 0x00040000
GBA_3D = "gba3d"


def rom_sha1(rom: gba.GbaRom) -> str:
    return hashlib.sha1(rom.data).hexdigest()


def default_title_id(rom: gba.GbaRom) -> int:
    """A title ID derived from the ROM that no other registered inject uses.

    Rebuilding the same ROM returns the same ID, so the installed title is
    updated in place instead of appearing twice.
    """
    return registry.suggest_title_id(rom_sha1(rom))


def parse_title_id(text: str) -> int:
    text = text.strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{16}", text):
        raise InjectError("Title ID must be 16 hex digits, e.g. 00040000F0A1B200.")
    value = int(text, 16)
    if value >> 32 != _TITLE_ID_HIGH:
        raise InjectError("Title ID must start with 00040000 (application).")
    return value


def default_product_code(rom: gba.GbaRom) -> str:
    code = re.sub(r"[^A-Z0-9]", "", rom.game_code.upper())
    return f"CTR-N-{code[:4] or 'VCIN'}"


@dataclass
class GbaInjectOptions:
    rom_path: Path
    output_path: Path | None = None
    title: str | None = None
    long_title: str | None = None
    publisher: str = "Virtual Console"
    title_id: int | None = None
    product_code: str | None = None
    save_type: gba.SaveType | None = None  # None = auto-detect
    ghosting: int = gba.DEFAULT_GHOSTING
    brightness: float = gba.DEFAULT_BRIGHTNESS
    icon_path: Path | None = None
    banner_path: Path | None = None
    # Ready-made artwork (e.g. what the GUI previewed); wins over everything else.
    icon_image: Image.Image | None = None
    banner_image: Image.Image | None = None
    online_artwork: bool = True
    icon_style: str | None = None  # see artwork.ICON_STYLES, None = best available
    # artwork.BANNER_STYLES or GBA_3D; None = GBA_3D if its template is installed
    banner_style: str | None = None
    icon_frame: bool = True  # Virtual Console style bevelled frame
    # GBA_3D banner: image for the model's screen and the plate's "Released" year
    # (None = from the database, "" = none).
    banner_screen: Image.Image | None = None
    release_year: str | None = None
    plate_language: str | None = None  # for banners with one shared plate; None = system
    keep_cxi: bool = False


@dataclass
class BuildResult:
    output_path: Path
    title_id: int
    product_code: str
    save_type: gba.SaveType
    warnings: list[str] = field(default_factory=list)


def resolve_title(options: GbaInjectOptions, rom: gba.GbaRom) -> str:
    return (options.title or options.rom_path.stem or rom.title or "GBA Game").strip()


def build_gba(options: GbaInjectOptions, template: GbaTemplate | None = None,
              log: Log = print) -> BuildResult:
    template = template or GbaTemplate.load()
    rom_path = Path(options.rom_path)
    if not rom_path.is_file():
        raise InjectError(f"ROM not found: {rom_path}")
    rom = gba.GbaRom.parse(rom_path.read_bytes())
    warnings = []
    if not rom.header_valid:
        warnings.append("ROM header checksum is invalid; the ROM may be corrupt or a hack.")

    title = resolve_title(options, rom)
    long_title = (options.long_title or title).strip()
    sha1 = rom_sha1(rom)
    title_id = options.title_id if options.title_id is not None else default_title_id(rom)
    clash = registry.conflict(title_id, sha1)
    if clash is not None and clash.imported:
        warnings.append(f"title ID {title_id:016X} is already installed on your 3DS "
                        f"(ticket import); installing replaces that title")
    elif clash is not None:
        warnings.append(f"title ID {title_id:016X} is already used by '{clash.title}' "
                        f"({clash.rom_name}); installing replaces that title")
    product_code = options.product_code or default_product_code(rom)
    save_type = options.save_type if options.save_type is not None else rom.detect_save_type()
    output = Path(options.output_path or rom_path.with_suffix(".cia"))

    log(f"ROM:          {rom_path.name} ({len(rom.data) / 1024 / 1024:.2f} MiB, "
        f"code {rom.game_code or '----'})")
    log(f"Title:        {title}")
    log(f"Title ID:     {title_id:016X}")
    log(f"Product code: {product_code}")
    log(f"Save type:    {save_type.label} (0x{save_type:X})"
        + ("" if options.save_type is not None else " [auto]"))
    for warning in warnings:
        log(f"Warning:      {warning}")

    icon_img, banner_art = _resolve_artwork(options, rom, title, log)

    with tempfile.TemporaryDirectory(prefix="vcinject-") as tmp:
        work = Path(tmp)
        log("Building banner...")
        banner = banner_art if isinstance(banner_art, bytes) else _make_banner(banner_art, work)

        log("Building ExeFS...")
        code = gba.build_code_bin(rom, save_type, ghosting=options.ghosting,
                                  brightness=options.brightness)
        icon = smdh.build_smdh(title, long_title, options.publisher, icon_img)
        exefs = ncch.build_exefs([(".code", code), ("banner", banner), ("icon", icon),
                                  ("logo", template.logo)])

        log("Building CXI...")
        app_title = product_code.rsplit("-", 1)[-1]
        exheader = ncch.patch_exheader(template.exheader, title_id, app_title,
                                       save_data_size=save_type.sd_save_size)
        cxi = ncch.build_ncch(title_id=title_id, product_code=product_code, exheader=exheader,
                              exefs=exefs, romfs=template.romfs)
        cxi_path = work / "game.cxi"
        cxi_path.write_bytes(cxi)

        log("Building CIA...")
        output.parent.mkdir(parents=True, exist_ok=True)
        tools.run_tool("makerom", "-f", "cia", "-ignoresign", "-target", "t",
                       "-content", f"{cxi_path}:0:0", "-o", output)
        if options.keep_cxi:
            output.with_suffix(".cxi").write_bytes(cxi)

    registry.record(registry.Entry(
        title_id=title_id, title=title, rom_sha1=sha1, rom_name=rom_path.name,
        product_code=product_code, save_type=save_type.name, output=str(output.resolve()),
        built_at=time.time()))
    log(f"Done:         {output} ({output.stat().st_size / 1024 / 1024:.2f} MiB)")
    return BuildResult(output, title_id, product_code, save_type, warnings)


def banner_style_for(options: GbaInjectOptions) -> str | None:
    if options.banner_style is None and vcbanner.is_installed():
        return GBA_3D
    return options.banner_style


def _resolve_artwork(options: GbaInjectOptions, rom: gba.GbaRom, title: str,
                     log: Log) -> tuple[Image.Image, Image.Image | bytes]:
    """The icon and either a flat banner image or a finished 3D banner."""
    icon = options.icon_image or (art.load_image(options.icon_path) if options.icon_path else None)
    custom_banner = options.banner_image or (art.load_image(options.banner_path)
                                             if options.banner_path else None)
    style = banner_style_for(options)
    three_d = style == GBA_3D
    screen = options.banner_screen or (custom_banner if three_d else None)

    needs_lookup = icon is None or (three_d and (screen is None or options.release_year is None)) \
        or (not three_d and custom_banner is None)
    found = artwork.Artwork()
    if options.online_artwork and needs_lookup:
        log("Looking up artwork...")
        try:
            found = artwork.lookup(rom.data, rom.game_code, Path(options.rom_path).name)
        except InjectError as exc:
            log(f"Warning:      artwork lookup failed, using generated art ({exc})")
        if found.match:
            log(f"Release:      {found.match.name} (matched by {found.match.method}, "
                f"{len(found.images)} images)")
        else:
            log("Release:      not found in the No-Intro database, using generated art")

    if icon is None:
        icon = artwork.render_icon(options.icon_style or artwork.available(artwork.ICON_STYLES, found)[0],
                                   found, title)
    if options.icon_frame:
        icon = art.vc_icon_frame(icon)

    if three_d:
        if screen is None:
            imgs = found.images
            screen = imgs.get("title") or imgs.get("snap") or imgs.get("boxart") \
                or art.default_banner(title)
        year = options.release_year if options.release_year is not None else found.year
        return icon, vcbanner.build(screen, title, year or None,
                                    found.publisher or options.publisher, options.plate_language)

    if custom_banner is not None:
        return icon, custom_banner
    return icon, artwork.render_banner(style or artwork.available(artwork.BANNER_STYLES, found)[0],
                                       found, title)


def _make_banner(image: Image.Image, work: Path) -> bytes:
    png, wav, out = work / "banner.png", work / "banner.wav", work / "banner.bnr"
    art.letterbox(image).save(png)
    art.write_silent_wav(wav)
    tools.run_tool("bannertool", "makebanner", "-i", png, "-a", wav, "-o", out)
    banner = out.read_bytes()
    if vcbanner.is_installed():  # borrow the template's jingle (the GBA start-up sound)
        flat = cgfx.Cbmd.parse(banner)
        flat.cwav = vcbanner.load_template().cwav or flat.cwav
        banner = flat.build()
    return banner

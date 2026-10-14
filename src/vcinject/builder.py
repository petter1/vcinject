"""End-to-end inject pipeline: ROM -> CXI -> CIA.

GBA ROMs become native Virtual Console titles (AGB_FIRM); every other system
is packed into an emulator forwarder built from a template inject.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from . import art, artwork, cgfx, forwarder, gba, ncch, registry, smdh, systems, tools, vcbanner
from .errors import InjectError
from .template import GbaTemplate

Log = Callable[[str], None]

_TITLE_ID_HIGH = 0x00040000
GBA_3D = "gba3d"


@dataclass(frozen=True)
class PlainRom:
    """A non-GBA ROM: vcinject only needs its bytes."""
    data: bytes
    title: str = ""
    game_code: str = ""
    header_valid: bool = True


def load_rom(path: Path) -> tuple[systems.System, "gba.GbaRom | PlainRom"]:
    system = systems.for_rom(path)
    data = systems.read_rom(path)
    if system.key == "gba":
        return system, gba.GbaRom.parse(data)
    if not data:
        raise InjectError("The ROM file is empty.")
    return system, PlainRom(data)


def rom_sha1(rom) -> str:
    return hashlib.sha1(rom.data).hexdigest()


def default_title_id(rom) -> int:
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


def default_product_code(rom, system: systems.System | None = None,
                         title_id: int | None = None) -> str:
    if system is None or system.key == "gba":
        code = re.sub(r"[^A-Z0-9]", "", rom.game_code.upper())
        return f"CTR-N-{code[:4] or 'VCIN'}"
    suffix = f"{(title_id or 0) >> 8 & 0xFFFF:04X}"
    return f"{system.product_prefix}-{suffix}"


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
    save_type: gba.SaveType | None  # None for forwarders
    warnings: list[str] = field(default_factory=list)


InjectOptions = GbaInjectOptions  # the same options serve every system


def resolve_title(options: GbaInjectOptions, rom) -> str:
    return (options.title or options.rom_path.stem or rom.title or "Game").strip()


def build(options: GbaInjectOptions, log: Log = print) -> BuildResult:
    """Build an inject for any supported ROM type."""
    if systems.for_rom(options.rom_path).key == "gba":
        return build_gba(options, log=log)
    return build_forwarder(options, log=log)


@dataclass
class _Common:
    rom_path: Path
    system: systems.System
    rom: object
    title: str
    long_title: str
    sha1: str
    title_id: int
    product_code: str
    output: Path
    warnings: list[str]


def _prepare(options: GbaInjectOptions, log: Log) -> _Common:
    rom_path = Path(options.rom_path)
    if not rom_path.is_file():
        raise InjectError(f"ROM not found: {rom_path}")
    system, rom = load_rom(rom_path)
    warnings = []
    if not rom.header_valid:
        warnings.append("ROM header checksum is invalid; the ROM may be corrupt or a hack.")
    title = resolve_title(options, rom)
    sha1 = rom_sha1(rom)
    title_id = options.title_id if options.title_id is not None else default_title_id(rom)
    clash = registry.conflict(title_id, sha1)
    if clash is not None and clash.imported:
        warnings.append(f"title ID {title_id:016X} is already installed on your 3DS "
                        f"(ticket import); installing replaces that title")
    elif clash is not None:
        warnings.append(f"title ID {title_id:016X} is already used by '{clash.title}' "
                        f"({clash.rom_name}); installing replaces that title")
    common = _Common(rom_path, system, rom, title, (options.long_title or title).strip(), sha1,
                     title_id, options.product_code or default_product_code(rom, system, title_id),
                     Path(options.output_path or rom_path.with_suffix(".cia")), warnings)
    log(f"ROM:          {rom_path.name} ({system.name}, {len(rom.data) / 1024 / 1024:.2f} MiB"
        + (f", code {rom.game_code or '----'})" if system.key == "gba" else ")"))
    log(f"Title:        {title}")
    log(f"Title ID:     {title_id:016X}")
    log(f"Product code: {common.product_code}")
    return common


def _finish(options: GbaInjectOptions, c: _Common, *, code: bytes, exheader: bytes, romfs: bytes,
            logo: bytes, save_type_name: str, log: Log) -> Path:
    for warning in c.warnings:
        log(f"Warning:      {warning}")
    icon_img, banner_art = _resolve_artwork(options, c, log)

    with tempfile.TemporaryDirectory(prefix="vcinject-") as tmp:
        work = Path(tmp)
        log("Building banner...")
        banner = banner_art if isinstance(banner_art, bytes) else \
            _make_banner(banner_art, work, jingle_from=c.system.key)

        log("Building ExeFS...")
        icon = smdh.build_smdh(c.title, c.long_title, options.publisher, icon_img)
        exefs = ncch.build_exefs([(".code", code), ("banner", banner), ("icon", icon),
                                  ("logo", logo)])

        log("Building CXI...")
        cxi = ncch.build_ncch(title_id=c.title_id, product_code=c.product_code, exheader=exheader,
                              exefs=exefs, romfs=romfs)
        cxi_path = work / "game.cxi"
        cxi_path.write_bytes(cxi)

        log("Building CIA...")
        c.output.parent.mkdir(parents=True, exist_ok=True)
        tools.run_tool("makerom", "-f", "cia", "-ignoresign", "-target", "t",
                       "-content", f"{cxi_path}:0:0", "-o", c.output)
        if options.keep_cxi:
            c.output.with_suffix(".cxi").write_bytes(cxi)

    registry.record(registry.Entry(
        title_id=c.title_id, title=c.title, rom_sha1=c.sha1, rom_name=c.rom_path.name,
        product_code=c.product_code, save_type=save_type_name, output=str(c.output.resolve()),
        built_at=time.time()))
    log(f"Done:         {c.output} ({c.output.stat().st_size / 1024 / 1024:.2f} MiB)")
    return c.output


def build_gba(options: GbaInjectOptions, template: GbaTemplate | None = None,
              log: Log = print) -> BuildResult:
    template = template or GbaTemplate.load()
    c = _prepare(options, log)
    if c.system.key != "gba":
        raise InjectError(f"{c.rom_path.name} is not a GBA ROM.")
    rom = c.rom
    save_type = options.save_type if options.save_type is not None else rom.detect_save_type()
    log(f"Save type:    {save_type.label} (0x{save_type:X})"
        + ("" if options.save_type is not None else " [auto]"))
    code = gba.build_code_bin(rom, save_type, ghosting=options.ghosting,
                              brightness=options.brightness)
    app_title = c.product_code.rsplit("-", 1)[-1]
    exheader = ncch.patch_exheader(template.exheader, c.title_id, app_title,
                                   save_data_size=save_type.sd_save_size)
    _finish(options, c, code=code, exheader=exheader, romfs=template.romfs, logo=template.logo,
            save_type_name=save_type.name, log=log)
    return BuildResult(c.output, c.title_id, c.product_code, save_type, c.warnings)


def build_forwarder(options: GbaInjectOptions, log: Log = print) -> BuildResult:
    c = _prepare(options, log)
    if c.system.forwarder is None:
        raise InjectError(f"{c.system.name} ROMs are not built as forwarders.")
    template = forwarder.ForwarderTemplate.load(c.system.forwarder)
    exheader, romfs_image, internal = template.build(c.rom.data, Path(systems.rom_name(c.rom_path)).suffix, c.title_id,
                                                     c.title)
    log(f"Emulator:     {systems.FORWARDER_KINDS[template.kind]} forwarder")
    log(f"Save folder:  sdmc:/nsui_formwarders_data/{internal}")
    _finish(options, c, code=template.code, exheader=exheader, romfs=romfs_image,
            logo=template.logo, save_type_name="", log=log)
    return BuildResult(c.output, c.title_id, c.product_code, None, c.warnings)


def banner_style_for(options: GbaInjectOptions, system_key: str = "gba") -> str | None:
    """GBA_3D (the VC Console Look) by default when the system has a look installed."""
    if options.banner_style is None and vcbanner.is_installed(system_key):
        return GBA_3D
    return options.banner_style


def _resolve_artwork(options: GbaInjectOptions, c: _Common,
                     log: Log) -> tuple[Image.Image, Image.Image | bytes]:
    """The icon and either a flat banner image or a finished 3D banner."""
    icon = options.icon_image or (art.load_image(options.icon_path) if options.icon_path else None)
    custom_banner = options.banner_image or (art.load_image(options.banner_path)
                                             if options.banner_path else None)
    style = banner_style_for(options, c.system.key)
    three_d = style == GBA_3D and vcbanner.is_installed(c.system.key)
    title = c.title
    screen = options.banner_screen or (custom_banner if three_d else None)

    needs_lookup = icon is None or (three_d and (screen is None or options.release_year is None)) \
        or (not three_d and custom_banner is None)
    found = artwork.Artwork()
    if options.online_artwork and needs_lookup:
        log("Looking up artwork...")
        try:
            found = artwork.lookup(c.system.strip_header(c.rom.data), c.rom.game_code,
                                   systems.rom_name(c.rom_path), system=c.system.database)
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
                or art.default_banner(title, c.system.name)
        year = options.release_year if options.release_year is not None else found.year
        return icon, vcbanner.build(screen, title, year or None,
                                    found.publisher or options.publisher, options.plate_language,
                                    system=c.system.key)

    if custom_banner is not None:
        return icon, custom_banner
    if style not in artwork.BANNER_STYLES:
        style = artwork.available(artwork.BANNER_STYLES, found)[0]
    return icon, artwork.render_banner(style, found, title, c.system.name)


def _make_banner(image: Image.Image, work: Path, jingle_from: str | None = "gba") -> bytes:
    png, wav, out = work / "banner.png", work / "banner.wav", work / "banner.bnr"
    art.letterbox(image).save(png)
    art.write_silent_wav(wav)
    tools.run_tool("bannertool", "makebanner", "-i", png, "-a", wav, "-o", out)
    banner = out.read_bytes()
    if jingle_from and vcbanner.is_installed(jingle_from):  # the console's VC jingle
        flat = cgfx.Cbmd.parse(banner)
        flat.cwav = vcbanner.load_template(jingle_from).cwav or flat.cwav
        banner = flat.build()
    return banner

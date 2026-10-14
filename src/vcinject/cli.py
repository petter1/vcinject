"""Command line interface: ``vcinject gba``, ``vcinject setup``, ``vcinject info``."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import (__version__, artwork, backup, forwarder, gba, health, library, registry, serve,
               systems, template, tickets, tools, vcbanner)
from .builder import GbaInjectOptions, build, parse_title_id
from .errors import InjectError, MissingResource


def _save_type(value: str) -> gba.SaveType | None:
    key = value.lower()
    if key in gba.SAVE_ALIASES:
        return gba.SAVE_ALIASES[key]
    try:
        return gba.SaveType(int(key, 0))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"unknown save type '{value}' (use {', '.join(gba.SAVE_ALIASES)} or 0x0-0xF)")


def _title_id(value: str) -> int:
    try:
        return parse_title_id(value)
    except InjectError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _add_serve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", help="address the 3DS should connect to (default: auto-detected LAN IP)")
    parser.add_argument("--port", type=int, default=serve.DEFAULT_PORT,
                        help=f"HTTP port (default: {serve.DEFAULT_PORT})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vcinject",
                                     description="3DS Virtual Console injector for macOS and Linux.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("build", aliases=["gba"],
                       help="build a CIA from a ROM (GBA natively; SNES, NES, GB/GBC and "
                            "Mega Drive through an emulator forwarder template)")
    g.add_argument("rom", type=Path, help="ROM file (.gba .sfc .smc .nes .gb .gbc .md .gen .smd)")
    g.add_argument("-o", "--output", type=Path, help="output .cia (default: next to the ROM)")
    g.add_argument("-t", "--title", help="short title shown on the HOME Menu (default: ROM file name)")
    g.add_argument("--long-title", help="long title (default: same as --title)")
    g.add_argument("-p", "--publisher", default="Virtual Console")
    g.add_argument("--icon", type=Path, help="icon image (any size, cropped to a square)")
    g.add_argument("--banner", type=Path, help="banner image (letterboxed to 256x128)")
    g.add_argument("--icon-style", choices=list(artwork.ICON_STYLES),
                   help="icon from online artwork (default: best available)")
    g.add_argument("--banner-style", choices=["gba3d", *artwork.BANNER_STYLES],
                   help="gba3d = VC Console Look, 3D GBA banner (default when its template is "
                        "installed), otherwise a flat banner from online artwork")
    g.add_argument("--released", metavar="YEAR",
                   help="year on the Virtual Console plate (default: from the database)")
    g.add_argument("--plate-lang", choices=sorted(vcbanner.RELEASED),
                   help="language of the 'Released' line when the banner template has a "
                        "single plate (default: system language)")
    g.add_argument("--no-icon-frame", action="store_true",
                   help="don't put the icon in the Virtual Console frame")
    g.add_argument("--offline", action="store_true",
                   help="don't look up artwork online, generate it from the title")
    g.add_argument("--title-id", type=_title_id, help="16 hex digits (default: derived from the ROM)")
    g.add_argument("--product-code", help="e.g. CTR-N-ABCD (default: from the ROM game code)")
    g.add_argument("--save-type", type=_save_type, default=None, metavar="TYPE",
                   help=f"auto (default), {', '.join(k for k in gba.SAVE_ALIASES if k != 'auto')} "
                        "or a raw value 0x0-0xF")
    g.add_argument("--ghosting", type=lambda v: int(v, 0), default=gba.DEFAULT_GHOSTING,
                   help="LCD ghosting blend, 1-255; lower = more ghosting (default: 0x80)")
    g.add_argument("--brightness", type=float, default=gba.DEFAULT_BRIGHTNESS,
                   help="color LUT scale 0.1-1.0 (default: 1.0; official titles use 0.7)")
    g.add_argument("--keep-cxi", action="store_true", help="also write the intermediate .cxi")
    g.add_argument("--qr", action="store_true",
                   help="after building, serve the CIA and show a QR code for FBI")
    _add_serve_options(g)

    q = sub.add_parser("serve", help="serve CIAs on the LAN and show a QR code for FBI")
    q.add_argument("files", type=Path, nargs="+", help=".cia files")
    _add_serve_options(q)

    s = sub.add_parser("setup", help="install the GBA VC base template")
    s.add_argument("--donor", type=Path,
                   help="extract the template from your own decrypted GBA VC .cia/.cxi "
                        "instead of downloading it")
    s.add_argument("--banner-donor", type=Path, nargs="+", metavar="TITLE",
                   help="VC Console Look: use the 3D banner of VC titles or injects you own "
                        "(.cia/.cxi/banner.bin), one per console; the console is detected")
    s.add_argument("--derive-looks", action="store_true",
                   help="create Game Boy / Color looks from the GBA look (recoloured shell, "
                        "Game Boy start-up sound) until you have official GB VC templates")
    s.add_argument("--banner-system", choices=sorted(systems.SYSTEMS),
                   help="console for --banner-donor when it can't be detected")
    s.add_argument("--forwarder-donor", type=Path, nargs="+", metavar="INJECT",
                   help="use existing emulator injects (RetroArch / snes9x / VirtuaNES, e.g. "
                        "made with NSUI) as templates for SNES, NES, GB/GBC and Mega Drive")

    sub.add_parser("list", help="list all injects built so far with their title IDs")
    lib = sub.add_parser("library", help="list every ROM in a folder and its sub folders")
    lib.add_argument("folder", type=Path)
    lib.add_argument("--offline", action="store_true", help="don't identify ROMs online")
    t = sub.add_parser("import-tickets",
                       help="mark all title IDs on your 3DS as taken (ticket.db from GodMode9)")
    t.add_argument("paths", type=Path, nargs="+", help="ticket.db, .tik files or folders")
    f = sub.add_parser("forget", help="remove a title ID from the local register")
    f.add_argument("title_id", type=_title_id)

    sub.add_parser("info", help="show tool and template status")
    t = sub.add_parser("templates", help="back up or restore all templates as one file")
    t.add_argument("action", choices=["export", "import"])
    t.add_argument("file", type=Path, help="backup .zip (keep it private)")
    d = sub.add_parser("doctor", help="check everything vcinject needs; --fix sets missing parts up")
    d.add_argument("--fix", action="store_true", help="repair interactively")
    sub.add_parser("save-types", help="list GBA save types")
    sub.add_parser("gui", help="start the graphical interface")
    return parser


def cmd_gba(args: argparse.Namespace) -> int:
    options = GbaInjectOptions(
        rom_path=args.rom, output_path=args.output, title=args.title,
        long_title=args.long_title, publisher=args.publisher, title_id=args.title_id,
        product_code=args.product_code, save_type=args.save_type, ghosting=args.ghosting,
        brightness=args.brightness, icon_path=args.icon, banner_path=args.banner,
        online_artwork=not args.offline, icon_style=args.icon_style,
        banner_style=args.banner_style, icon_frame=not args.no_icon_frame,
        release_year=args.released, plate_language=args.plate_lang, keep_cxi=args.keep_cxi)
    if systems.for_rom(args.rom).key == "gba" and not template.is_installed():
        print("No GBA VC template installed yet, downloading it once...")
        template.download()
    try:
        result = build(options)
    except MissingResource as exc:  # self-healing: offer the fix, then try again
        print(f"{exc}")
        if not _confirm("Set it up now?") or not health.heal(exc.resource, _ask_file):
            raise
        result = build(options)
    if args.qr:
        return _serve([result.output_path], args.host, args.port)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    return _serve(args.files, args.host, args.port)


def _serve(files: list[Path], host: str | None, port: int) -> int:
    server = serve.CiaServer(files, host=host, port=port)
    print()
    server.qr().terminal(compact=True)
    print("On the 3DS: FBI -> Remote Install -> Scan QR Code")
    for url in server.urls:
        print(f"  {url}")
    print("Press Ctrl+C to stop the server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.stop()
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    if args.forwarder_donor:
        for donor in args.forwarder_donor:
            t = forwarder.install_from(donor)
            print(f"{systems.FORWARDER_KINDS[t.kind]} template installed from {donor.name}")
    if args.derive_looks:
        for target in ("gb", "gbc"):
            vcbanner.derive_look(target)
            print(f"VC Console Look for {systems.SYSTEMS[target].name} created from the GBA look")
        if not (args.donor or args.banner_donor or args.forwarder_donor):
            return 0
    for donor in args.banner_donor or []:
        system, _path = vcbanner.install_from(donor, args.banner_system)
        print(f"VC Console Look for {systems.SYSTEMS[system].name} installed from {donor.name}")
    if (args.banner_donor or args.forwarder_donor) and not args.donor:
        return 0
    if args.donor:
        template.from_donor(args.donor)
        print(f"Template extracted from {args.donor}")
    else:
        print("Downloading GBA VC template...")
        template.download()
    print(f"Installed into {template.template_dir()}")
    return 0


def _ask_file(question: str) -> Path | None:
    """Terminal prompt for one of the user's files (drag & drop into the terminal works)."""
    if not sys.stdin.isatty():
        return None
    answer = input(f"{question}\nPath (Enter to skip): ").strip().strip("'\"")
    return Path(answer).expanduser() if answer else None


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes", "j", "ja")


def cmd_doctor(args: argparse.Namespace) -> int:
    results = health.check()
    for c in results:
        mark = "✓" if c.ok else ("✗" if c.required else "–")
        print(f" {mark} {c.label:40} {'' if c.ok else c.detail}")
    broken = [c for c in results if not c.ok]
    if not args.fix:
        if any(c.required for c in broken):
            print("\nRequired parts are missing: run 'vcinject doctor --fix'.")
        return 1 if any(c.required for c in broken) else 0
    for c in broken:
        if not c.required and not _confirm(f"\nSet up '{c.label}'?"):
            continue
        print(f"\n→ {c.label}")
        print("   ok" if health.heal(c.resource, _ask_file) else "   still missing")
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    if args.action == "export":
        path, contents = backup.export(args.file)
        print(f"Exported {len(contents)} templates to {path}:")
    else:
        contents = backup.restore(args.file)
        print(f"Imported from {args.file}; usable now:")
    for label in contents:
        print(f"  ✓ {label}")
    if args.action == "export":
        print("Keep this file private: it contains data from your own games.")
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    ok = True
    for name in tools.TOOLS:
        path = tools.find_tool(name)
        ok &= path is not None
        print(f"{name:12} {path or 'MISSING (run scripts/build-tools.sh)'}")
    installed = template.is_installed()
    ok &= installed
    print(f"{'template':12} {template.template_dir() if installed else 'MISSING (run vcinject setup)'}")
    looks = vcbanner.installed_systems()
    print(f"{'VC look':12} " + (", ".join(systems.SYSTEMS[k].name for k in looks) if looks
                                else "none (optional: vcinject setup --banner-donor <title.cia>)"))
    installed = forwarder.installed_kinds()
    for kind, label in systems.FORWARDER_KINDS.items():
        state = "installed" if kind in installed else "not set up (vcinject setup --forwarder-donor <inject.cia>)"
        print(f"{kind:12} {label}: {state}")
    return 0 if ok else 1


def cmd_list(_args: argparse.Namespace) -> int:
    all_entries = registry.load().values()
    entries = sorted((e for e in all_entries if not e.imported), key=lambda e: e.built_at)
    imported = sum(e.imported for e in all_entries)
    if not entries:
        print("No injects built yet.")
        return 0
    print(f"{'Title ID':16}  {'Built':16}  {'Save type':24}  Title")
    for e in entries:
        built = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.built_at))
        print(f"{e.title_id:016X}  {built:16}  {e.save_type:24}  {e.title}  [{e.rom_name}]")
    if imported:
        print(f"\n+ {imported} title IDs imported from your 3DS tickets (reserved)")
    print(f"\nRegister: {registry.registry_path()}")
    return 0


def cmd_import_tickets(args: argparse.Namespace) -> int:
    ids = tickets.read_title_ids(args.paths)
    added = registry.import_title_ids(ids)
    print(f"Found {len(ids)} application title IDs, {added} new in the register.")
    print("vcinject will not hand out these IDs to new injects.")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    roms = library.scan(args.folder)
    if not roms:
        print("No supported ROMs found.")
        return 0
    for rom in roms:
        library.analyze(rom, online=not args.offline, cover=False)
        built = f"built {rom.built[-1].title_id:016X}" if rom.built else ""
        print(f"{rom.system.key.upper():5} {rom.title[:44]:44} {rom.year or '':4}  "
              f"{rom.folder[:24]:24} {built}")
    print(f"\n{len(roms)} ROMs, {sum(1 for r in roms if r.built)} built")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    if not registry.forget(args.title_id):
        raise InjectError(f"{args.title_id:016X} is not in the register.")
    print(f"Removed {args.title_id:016X}; the ID may be reused by future builds.")
    return 0


def cmd_save_types(_args: argparse.Namespace) -> int:
    aliases = {v: k for k, v in gba.SAVE_ALIASES.items() if v is not None}
    for save in gba.SaveType:
        print(f"0x{save:X}  {aliases.get(save, ''):13} {save.label}")
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    from .gui.app import main as gui_main
    return gui_main()


COMMANDS = {"gba": cmd_gba, "build": cmd_gba, "serve": cmd_serve, "setup": cmd_setup,
            "list": cmd_list, "library": cmd_library, "forget": cmd_forget, "doctor": cmd_doctor,
            "templates": cmd_templates, "import-tickets": cmd_import_tickets, "info": cmd_info,
            "save-types": cmd_save_types, "gui": cmd_gui}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except InjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

"""Entry point of the packaged app: the GUI, or the command line when arguments
are given (e.g. `vcinject build game.gba`)."""
import sys

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a code page
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # macOS Finder adds -psn_…
    if args:
        from vcinject.cli import main as cli_main
        sys.exit(cli_main(args))
    from vcinject.gui.app import main as gui_main
    sys.exit(gui_main())

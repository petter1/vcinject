"""Back up and restore all templates as one file.

Templates come from titles the user owns and must not be shared publicly, so
they are never part of the repository. A backup is a plain zip of the
templates folder plus a manifest; keep it private (iCloud, USB stick ...) and
import it on another computer instead of setting everything up again.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from . import __version__, health
from .errors import InjectError
from .template import data_dir

MANIFEST = "vcinject-templates.json"
_PARTS = ("gba", "vc_looks", "forwarders")  # sub folders of the templates folder


def templates_root() -> Path:
    return data_dir() / "templates"


def _files() -> list[Path]:
    root = templates_root()
    found = []
    for part in _PARTS:
        base = root / part
        if base.is_dir():
            found += sorted(p for p in base.rglob("*") if p.is_file() and ".broken-" not in str(p))
    legacy = root / "gba_banner.bin"  # GBA look from before per-console looks
    if legacy.is_file() and not (root / "vc_looks" / "gba.bin").is_file():
        found.append(legacy)
    return found


def export(target: Path) -> tuple[Path, list[str]]:
    """Write every installed template into ``target`` (.zip). Returns the file and
    the parts it contains."""
    # check first: damaged templates are set aside there and so never exported
    contents = sorted(c.label for c in health.check()
                      if c.ok and not c.resource.startswith("tool:"))
    files = _files()
    if not files:
        raise InjectError("No templates are set up yet, so there is nothing to export.")
    target = Path(target)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    manifest = {"format": 1, "vcinject": __version__, "created": time.strftime("%Y-%m-%d %H:%M"),
                "contents": contents}
    root = templates_root()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        for path in files:
            zf.write(path, path.relative_to(root).as_posix())
    return target, contents


def restore(source: Path, log=print) -> list[str]:
    """Import a backup made by :func:`export`. Existing templates of the same kind
    are replaced; everything else is kept. Returns what is usable afterwards."""
    source = Path(source)
    try:
        zf = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InjectError(f"{source.name} is not a vcinject template backup ({exc}).") from exc
    with zf:
        if MANIFEST not in zf.namelist():
            raise InjectError(f"{source.name} is not a vcinject template backup (no manifest).")
        names = [n for n in zf.namelist() if n != MANIFEST and not n.endswith("/")]
        for name in names:  # only our own folders, no absolute paths or "..": never write elsewhere
            parts = Path(name).parts
            if not parts or (parts[0] not in _PARTS and name != "gba_banner.bin") or ".." in parts \
                    or Path(name).is_absolute():
                raise InjectError(f"Unexpected file in the backup: {name}")
        with tempfile.TemporaryDirectory(prefix="vcinject-restore-") as tmp:
            zf.extractall(tmp, names)
            root = templates_root()
            root.mkdir(parents=True, exist_ok=True)
            for item in sorted(Path(tmp).iterdir()):
                target = root / item.name
                if item.is_dir():
                    target.mkdir(exist_ok=True)
                    for child in item.iterdir():  # replace per template, keep others
                        destination = target / child.name
                        if destination.is_dir():
                            shutil.rmtree(destination)
                        elif destination.exists():
                            destination.unlink()
                        shutil.move(str(child), destination)
                else:
                    shutil.copy2(item, target)
                log(f"Restored {item.name}")
    # validate what arrived; damaged parts are set aside by the checks themselves
    return [c.label for c in health.check() if c.ok and not c.resource.startswith("tool:")]

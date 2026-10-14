"""Self-healing: check everything vcinject needs and set missing parts up again.

``check()`` lists every resource with its state. ``heal(resource, ask_file)``
repairs one: things vcinject can do alone (download the GBA template, build the
tools, derive Game Boy looks) happen automatically; for everything that needs
one of the user's own files, ``ask_file(question)`` is called, so the CLI can
prompt in the terminal and the GUI can open a file dialog. Builds raise
``MissingResource`` for whatever is missing, and both front ends offer exactly
that fix and retry.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import forwarder, gbvc, systems, template, tools, vcbanner
from .errors import InjectError, MissingResource

AskFile = Callable[[str], "Path | None"]
Log = Callable[[str], None]

BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-tools.sh"


@dataclass
class Check:
    resource: str
    label: str
    ok: bool
    required: bool       # needed for any build (else only for some systems / looks)
    detail: str = ""
    auto: bool = False   # heal() can fix it without asking the user


def check() -> list[Check]:
    results = []
    for name in tools.TOOLS:
        path = tools.find_tool(name)
        works = path is not None and _runs(path)
        results.append(Check(f"tool:{name}", name, works, True,
                             str(path) if works else ("does not run" if path else "missing"),
                             auto=can_build_tools()))
    results.append(_check_load("gba-template", "GBA VC base template", True, template.GbaTemplate.load,
                               auto=True))
    for key, system in systems.SYSTEMS.items():
        derivable = key in vcbanner.DERIVED_COLORS and vcbanner.is_installed("gba")
        results.append(_check_load(f"vclook:{key}", f"VC Console Look – {system.name}", False,
                                   lambda key=key: vcbanner.load_template(key), auto=derivable))
    for kind, label in systems.FORWARDER_KINDS.items():
        results.append(_check_load(f"forwarder:{kind}", f"Emulator template – {label}", False,
                                   lambda kind=kind: forwarder.ForwarderTemplate.load(kind)))
    for kind, label in gbvc.KINDS.items():
        results.append(_check_load(f"gbvc:{kind}", f"Nintendo emulator – {label}", False,
                                   lambda kind=kind: gbvc.GbVcTemplate.load(kind)))
    return results


def _check_load(resource: str, label: str, required: bool, load, auto: bool = False) -> Check:
    try:
        load()
        return Check(resource, label, True, required, "ok", auto)
    except MissingResource as exc:
        return Check(resource, label, False, required, str(exc), auto)
    except InjectError as exc:
        return Check(resource, label, False, required, str(exc), auto)


def _runs(path: Path) -> bool:
    try:
        subprocess.run([str(path)], capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                       **tools.NO_WINDOW)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def can_build_tools() -> bool:
    if tools.frozen():
        return False  # the packaged app ships the tools; nothing to build
    return BUILD_SCRIPT.is_file() and all(shutil.which(t) for t in ("git", "make", "cc", "c++"))


def needed_for(system: systems.System, vc_look: bool) -> list[str]:
    """Resources a build for ``system`` cannot do without."""
    needs = [f"tool:{t}" for t in tools.TOOLS]
    needs.append("gba-template" if system.key == "gba" else f"forwarder:{system.forwarder}")
    if vc_look:
        needs.append(f"vclook:{system.key}")
    return needs


def missing(resources: list[str]) -> list[Check]:
    states = {c.resource: c for c in check()}
    return [states[r] for r in resources if r in states and not states[r].ok]


def question(resource: str) -> str:
    """What to ask the user for when a resource needs one of their files."""
    kind, _, name = resource.partition(":")
    if kind == "vclook":
        return (f"Choose a VC title or inject for {systems.SYSTEMS[name].name} with a 3D banner "
                f"(.cia/.cxi), e.g. an official VC game you dumped with GodMode9")
    if kind == "forwarder":
        return (f"Choose an existing {systems.FORWARDER_KINDS[name]} emulator inject (.cia), "
                f"e.g. one made with NSUI")
    if kind == "gbvc":
        return ("Choose an official Game Boy / Game Boy Color Virtual Console title you own "
                "(.cia/.cxi), e.g. Pokémon Blue or Gold, dumped with GodMode9")
    if kind == "gba-template":
        return "Choose a decrypted GBA VC title (.cia/.cxi), or cancel to download the template"
    return f"Set up {resource}"


def heal(resource: str, ask_file: AskFile | None = None, log: Log = print) -> bool:
    """Set ``resource`` up again. Returns True when it is usable afterwards."""
    kind, _, name = resource.partition(":")
    try:
        if kind == "tool":
            if not can_build_tools():
                log("Building the tools needs git, make and a C/C++ compiler "
                    "(macOS: xcode-select --install, Linux: build-essential).")
                return False
            log(f"Building {name} from source (this takes a minute)...")
            build_tools(log)
        elif kind == "gba-template":
            donor = ask_file(question(resource)) if ask_file else None
            if donor:
                template.from_donor(Path(donor))
            else:
                log("Downloading the GBA VC template...")
                template.download()
        elif kind == "vclook":
            donor = ask_file(question(resource)) if ask_file else None
            if donor:
                vcbanner.install_from(Path(donor), name)
            elif name in vcbanner.DERIVED_COLORS and vcbanner.is_installed("gba"):
                log(f"Creating the {systems.SYSTEMS[name].name} look from the GBA look...")
                vcbanner.derive_look(name)
            else:
                return False
        elif kind == "gbvc":
            donor = ask_file(question(resource)) if ask_file else None
            if not donor:
                return False
            installed = gbvc.install_from(Path(donor))
            log(f"Installed Nintendo's {gbvc.KINDS[installed.kind]} emulator.")
            if installed.kind != name:
                log("That title runs the other Game Boy emulator build; it was set up for that "
                    "one (both work for Game Boy games).")
                return True
        elif kind == "forwarder":
            donor = ask_file(question(resource)) if ask_file else None
            if not donor:
                return False
            installed = forwarder.install_from(Path(donor))
            if installed.kind != name:
                log(f"That inject is for {systems.FORWARDER_KINDS[installed.kind]}, not "
                    f"{systems.FORWARDER_KINDS[name]}; installed it for that system.")
                return False
        else:
            return False
    except InjectError as exc:
        log(f"Could not set up {resource}: {exc}")
        return False
    return next((c.ok for c in check() if c.resource == resource), False)


def build_tools(log: Log = print) -> None:
    """Run scripts/build-tools.sh, streaming its output."""
    env = dict(os.environ)
    process = subprocess.Popen(["bash", str(BUILD_SCRIPT)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, env=env)
    assert process.stdout is not None
    for line in process.stdout:
        log(line.rstrip())
    if process.wait() != 0:
        raise InjectError("Building the tools failed; see the log above.")

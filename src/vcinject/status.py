"""The status report of a 3DS: what is up to date and what could be updated.

vcinject puts it together (it has the internet), shows it in the "My 3DS"
window and sends it to vcinject Link, which shows the same on the 3DS.
"""
from __future__ import annotations

import re
import socket
from dataclasses import dataclass

from . import homebrew, remote
from .errors import InjectError

LATEST_SYSTEM = (11, 17, 0)  # the last system update Nintendo released
LOW_SPACE = 2 * 10**9

OK, UPDATE, WARN, INFO = "ok", "update", "warn", "info"


@dataclass
class Finding:
    level: str
    title: str
    detail: str
    action: str = ""      # "link", "luma", "app:<key>" – what the Update button does
    hint: str = ""        # longer explanation for the GUI


def version_text(version: int) -> str:
    """A title version as the 3DS shows it (major.minor.micro)."""
    return f"{version >> 10}.{(version >> 4) & 0x3F}.{version & 0xF}"


def _numbers(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def check(link: remote.Link, console: remote.Console, titles: list[remote.Title]) -> list[Finding]:
    """Everything worth knowing about ``console``, most important first."""
    info = console.info
    found: list[Finding] = []

    if console.outdated:
        found.append(Finding(UPDATE, "vcinject Link", f"version {info.get('version', '?')} -> "
                             f"{remote.LINK_VERSION}", "link",
                             "Installs the new vcinject Link over Wi-Fi; then it restarts."))

    system = info.get("system", "?")
    if _numbers(system) and _numbers(system) < LATEST_SYSTEM:
        found.append(Finding(UPDATE, "System", f"{system} -> 11.17", hint="On the 3DS: System "
                             "Settings > Other Settings > System Update. Luma3DS keeps working."))
    else:
        found.append(Finding(OK, "System", f"{system}, the latest"))

    found.append(_luma(info.get("luma", "")))

    installed = {t.id: t for t in titles}
    for app, newest in zip(homebrew.CATALOG, _newest_releases()):
        if newest is None:
            continue  # offline or the release changed: nothing to say
        title_id, version = newest
        title = installed.get(title_id)
        if title is None:
            continue
        if title.version < version:
            found.append(Finding(UPDATE, app.name, f"{version_text(title.version)} -> "
                                 f"{version_text(version)}", f"app:{app.key}",
                                 f"Installs {app.name} {version_text(version)} over Wi-Fi."))
        else:
            found.append(Finding(OK, app.name, f"{version_text(title.version)}, up to date"))

    try:
        sound = any(name == "dspfirm.cdc" for name, _dir, _size in
                    link.list_dir(remote.ARCHIVE_SDMC, None, "/3ds"))
    except InjectError:
        sound = False
    if not sound:
        dsp1 = homebrew.APPS["dsp1"]
        found.append(Finding(WARN, "Sound firmware", "missing: run DSP1 once", "app:dsp1",
                             f"RetroArch injects need sdmc:/3ds/dspfirm.cdc for sound. Install "
                             f"{dsp1.name}, start it once from the HOME Menu."))

    free, total = int(info.get("sd_free", 0)), int(info.get("sd_total", 0))
    if total:
        level = WARN if free < LOW_SPACE else OK
        found.append(Finding(level, "SD card", f"{free / 1e9:.1f} GB free of {total / 1e9:.0f} GB"))
    if info.get("battery") in ("0", "1") and info.get("charging") != "1":
        found.append(Finding(WARN, "Battery", "low: plug in before installing"))

    order = {UPDATE: 0, WARN: 1, OK: 2, INFO: 3}
    return sorted(found, key=lambda f: order[f.level])


def _newest_releases() -> list[tuple[int, int] | None]:
    """Title ID and version of each catalog app's newest release, looked up in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    def look_up(app):
        try:
            return homebrew.release_title(app)
        except (InjectError, OSError, ValueError):
            return None

    with ThreadPoolExecutor(len(homebrew.CATALOG)) as pool:
        return list(pool.map(look_up, homebrew.CATALOG))


def _luma(version: str) -> Finding:
    if not version:
        return Finding(INFO, "Luma3DS", "not found (update vcinject Link)")
    try:
        tag, _url = homebrew.luma_release()
    except (InjectError, OSError, ValueError):
        return Finding(INFO, "Luma3DS", version)
    newest = tag.lstrip("v")
    if _numbers(version) < _numbers(newest):
        return Finding(UPDATE, "Luma3DS", f"{version} -> {newest}", "luma",
                       f"Puts boot.firm of Luma3DS {newest} on the SD card (the current one is "
                       f"backed up on this computer); the 3DS restarts.")
    return Finding(OK, "Luma3DS", f"{version}, up to date")


def report_text(findings: list[Finding]) -> str:
    """What vcinject Link shows (see ui_set_report in companion/source/ui.c)."""
    lines = [f"from\t{socket.gethostname().split('.')[0]}"]
    lines += [f"{f.level}\t{f.title}\t{f.detail}".replace("\n", " ") for f in findings]
    return "\n".join(lines) + "\n"


RESTART_APP, REBOOT = "restart app", "reboot"


def apply(link: remote.Link, console: remote.Console, finding: Finding,
          progress=None) -> tuple[str, str | None]:
    """Carries out ``finding``'s action over ``link``. Returns what to tell the user and
    what has to restart for the update to count (RESTART_APP, REBOOT or None)."""
    if finding.action == "link":
        link.note("Updating vcinject Link")
        link.install(remote.CIA, progress=progress)
        return "vcinject Link is updated; it runs the new version once it restarts.", RESTART_APP
    if finding.action.startswith("app:"):
        app = homebrew.APPS[finding.action[4:]]
        link.note(f"Installing {app.name}")
        link.install(homebrew.download(app), progress=progress)
        return f"{app.name} is installed.", None
    if finding.action == "luma":
        return _update_luma(link, console)
    raise InjectError(f"Nothing to do for {finding.title}.")


def _update_luma(link: remote.Link, console: remote.Console) -> tuple[str, str]:
    import io
    import time
    import zipfile

    from .artwork import _download
    tag, url = homebrew.luma_release()
    data = _download(url)
    if not data:
        raise InjectError(f"Luma3DS {tag} could not be downloaded.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        firm = archive.read("boot.firm")
    if firm[:4] != b"FIRM":
        raise InjectError(f"The boot.firm of Luma3DS {tag} is not a FIRM file.")
    old = link.read_file(remote.ARCHIVE_SDMC, None, "/boot.firm")
    backup = console.folder / f"boot.firm {time.strftime('%Y-%m-%d %H-%M-%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(old)
    link.note(f"Updating Luma3DS to {tag}")
    link.write_file(remote.ARCHIVE_SDMC, None, "/boot.firm", firm)
    if link.read_file(remote.ARCHIVE_SDMC, None, "/boot.firm") != firm:
        link.write_file(remote.ARCHIVE_SDMC, None, "/boot.firm", old)
        raise InjectError("boot.firm did not arrive intact; the old one was put back.")
    return (f"Luma3DS {tag} is on the SD card; it starts with the next restart of the 3DS. "
            f"The old boot.firm is kept at {backup}", REBOOT)

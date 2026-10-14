"""3DS homebrew to install over the same QR code as the injects.

Each app comes straight from its author's latest GitHub release; vcinject
offers the download only when the project has an open-source licence that
allows passing it on (checked on GitHub each time). Nothing is bundled.

Also lists what the 3DS needs for injects, e.g. DSP1 for RetroArch
forwarders: they need the DSP firmware (sdmc:/3ds/dspfirm.cdc) for sound,
which DSP1 dumps once.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import InjectError
from .template import data_dir

API = "https://api.github.com/repos/{repo}"
# licences that allow redistributing the program (as published by its author)
OPEN_LICENCES = {"MIT", "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "BSD-2-Clause",
                 "BSD-3-Clause", "Apache-2.0", "MPL-2.0", "Zlib", "ISC", "Unlicense", "0BSD"}
_RELEASE_MAX_AGE = 24 * 3600


@dataclass(frozen=True)
class App:
    key: str
    name: str
    repo: str
    asset: str      # the release file to install (a .cia)
    purpose: str
    needed_for: str = ""  # what of vcinject needs it, if anything


CATALOG = [
    App("dsp1", "DSP1", "zoogie/DSP1", "DSP1.cia",
        "Dumps the 3DS's sound firmware to sdmc:/3ds/dspfirm.cdc. Run it once from the HOME "
        "Menu; afterwards it can be deleted.",
        needed_for="RetroArch forwarders (Game Boy via RetroArch, Mega Drive): without it they "
                   "have no sound or don't start"),
    App("fbi", "FBI Reloaded", "TheRealZora/FBI-Reloaded", "FBI-Reloaded_light.cia",
        "Installs CIAs – also over the QR codes vcinject shows. An up-to-date FBI.",
        needed_for="installing everything via QR code"),
    App("universal-updater", "Universal-Updater", "Universal-Team/Universal-Updater",
        "Universal-Updater.cia",
        "An app store on the 3DS: installs and updates hundreds of homebrew apps directly."),
    App("checkpoint", "Checkpoint", "BernardoGiordano/Checkpoint", "Checkpoint.cia",
        "Backs up and restores save data of games and injects."),
    App("pksm", "PKSM", "FlagBrew/PKSM", "PKSM.cia",
        "Manages Pokémon in the saves of the main series games, including VC titles."),
    App("ftpd", "ftpd", "mtheall/ftpd", "ftpd.cia",
        "FTP server: copy files to and from the SD card over Wi-Fi."),
    App("anemone3ds", "Anemone3DS", "astronautlevel2/Anemone3DS", "Anemone3DS.cia",
        "Installs HOME Menu themes and splash screens."),
]
APPS = {app.key: app for app in CATALOG}

# Not offered for download, explained instead (no CIA, or no licence to pass it on).
REQUIREMENTS = (
    ("Luma3DS (custom firmware)", "https://3ds.hacks.guide",
     "Needed for every inject. Its signature patches are on by default; injects don't start "
     "without them."),
    ("FBI", "", "Installs the CIAs vcinject builds, also over the QR code (Remote Install)."),
)


@dataclass
class Release:
    app: App
    tag: str
    url: str
    size: int
    licence: str

    @property
    def allowed(self) -> bool:
        return self.licence in OPEN_LICENCES


def _folder() -> Path:
    return data_dir() / "homebrew"


def _get_json(url: str, cache: Path) -> dict:
    from .artwork import _download
    if cache.is_file() and time.time() - cache.stat().st_mtime < _RELEASE_MAX_AGE:
        return json.loads(cache.read_text())
    try:
        data = _download(url)
    except InjectError:
        if cache.is_file():
            return json.loads(cache.read_text())  # offline: what was known
        raise
    if data is None:
        raise InjectError(f"{url} was not found.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return json.loads(data)


def release(app: App) -> Release:
    """The newest release of ``app`` and its licence (looked up at most daily)."""
    repo = _get_json(API.format(repo=app.repo), _folder() / f"{app.key}.repo.json")
    latest = _get_json(API.format(repo=app.repo) + "/releases/latest",
                       _folder() / f"{app.key}.release.json")
    asset = next((a for a in latest.get("assets", []) if a.get("name") == app.asset), None)
    if asset is None:
        raise InjectError(f"{app.name} {latest.get('tag_name', '')} has no {app.asset}.")
    licence = (repo.get("license") or {}).get("spdx_id") or "none"
    return Release(app, latest.get("tag_name", "?"), asset["browser_download_url"],
                   int(asset.get("size", 0)), licence)


def download(app: App) -> Path:
    """The app's .cia, downloaded once per version from the author's release."""
    from .artwork import _download
    found = release(app)
    if not found.allowed:
        raise InjectError(f"{app.name} has no open licence ({found.licence}); get it from "
                          f"https://github.com/{app.repo} yourself.")
    tag = re.sub(r"[^A-Za-z0-9._-]", "_", found.tag)
    target = _folder() / app.key / tag / app.asset
    if target.is_file() and (not found.size or target.stat().st_size == found.size):
        return target
    data = _download(found.url)
    if not data or (found.size and len(data) != found.size):
        raise InjectError(f"Downloading {app.name} failed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target

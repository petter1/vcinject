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
    store_repo: str = ""  # its entry in Universal-Updater's database, when not under ``repo``


CATALOG = [
    App("dsp1", "DSP1", "zoogie/DSP1", "DSP1.cia",
        "Dumps the 3DS's sound firmware to sdmc:/3ds/dspfirm.cdc. Run it once from the HOME "
        "Menu; afterwards it can be deleted.",
        needed_for="RetroArch forwarders (Game Boy via RetroArch, Mega Drive): without it they "
                   "have no sound or don't start"),
    App("fbi", "FBI Reloaded", "TheRealZora/FBI-Reloaded", "FBI-Reloaded_light.cia",
        "Installs CIAs – also over the QR codes vcinject shows. An up-to-date FBI.",
        needed_for="installing everything via QR code",
        store_repo="nh-server/FBI-NH"),  # its predecessor: the same app
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


def release_title(app: App) -> tuple[int, int]:
    """Title ID and version of the app's newest release, read from the first bytes of
    its CIA (kept per release, so it is fetched once)."""
    from .artwork import conditional_get
    from .ncch import cia_title
    found = release(app)
    tag = re.sub(r"[^A-Za-z0-9._-]", "_", found.tag)
    cache = _folder() / app.key / tag / "title.json"
    if cache.is_file():
        known = json.loads(cache.read_text())
        return int(known["title_id"], 16), known["version"]
    data, _etag = conditional_get(found.url, headers={"Range": "bytes=0-65535"})
    if not isinstance(data, bytes):
        raise InjectError(f"{app.name} {found.tag} could not be downloaded.")
    title_id, version = cia_title(data)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"title_id": f"{title_id:016X}", "version": version}))
    return title_id, version


LUMA_REPO = "LumaTeam/Luma3DS"


def luma_release() -> tuple[str, str]:
    """Tag and download URL (a .zip with boot.firm) of the newest Luma3DS."""
    latest = _get_json(API.format(repo=LUMA_REPO) + "/releases/latest", _folder() / "luma.release.json")
    asset = next((a for a in latest.get("assets", []) if a.get("name", "").endswith(".zip")), None)
    if asset is None:
        raise InjectError(f"Luma3DS {latest.get('tag_name', '')} has no download.")
    return latest.get("tag_name", "?"), asset["browser_download_url"]


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


# --- what the Homebrew page shows: pictures, descriptions, news ------------------------

STORE_DB = "https://db.universal-team.net/data/full.json"  # Universal-Updater's database


@dataclass(frozen=True)
class StoreInfo:
    """What Universal-Updater's database knows about an app."""
    description: str = ""
    long_description: str = ""
    author: str = ""
    icon_url: str = ""
    image_url: str = ""          # a 2D banner, used as the app's cover
    screenshots: tuple = ()      # (description, url)


def store_info(app: App) -> StoreInfo:
    """The app's entry in Universal-Updater's database (matched by its GitHub repo)."""
    entries = _get_json(STORE_DB, _folder() / "universal-db.json")
    repo = (app.store_repo or app.repo).lower()
    entry = next((e for e in entries if str(e.get("github", "")).lower() == repo), None)
    if entry is None:
        return StoreInfo(app.purpose)
    shots = tuple((s.get("description", ""), s["url"]) for s in entry.get("screenshots") or []
                  if isinstance(s, dict) and s.get("url"))
    description = str(entry.get("description", "")).replace(" (Archive)", "") or app.purpose
    return StoreInfo(description, entry.get("long_description", ""),
                     entry.get("author", ""), entry.get("icon", ""), entry.get("image", ""), shots)


def picture(url: str) -> bytes | None:
    """An image of the database or a release, downloaded once."""
    import hashlib
    from .artwork import _download
    if not url:
        return None
    cache = _folder() / "images" / hashlib.sha1(url.encode()).hexdigest()
    if cache.is_file():
        return cache.read_bytes() or None
    data = _download(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data or b"")
    return data or None


@dataclass(frozen=True)
class News:
    project: str       # "Checkpoint", "Luma3DS", "vcinject"
    key: str           # catalog app key, "luma" or "vcinject"
    tag: str
    date: str          # ISO date
    notes: str         # release notes (Markdown)
    url: str


NEWS_SOURCES = [(app.name, app.key, app.repo) for app in CATALOG] + [
    ("Luma3DS", "luma", LUMA_REPO), ("vcinject", "vcinject", "petter1/vcinject")]


def news(per_project: int = 3) -> list[News]:
    """The newest releases of the catalog's apps, Luma3DS and vcinject, newest first
    (looked up at most daily)."""
    found: list[News] = []
    for name, key, repo in NEWS_SOURCES:
        try:
            releases = _get_json(API.format(repo=repo) + f"/releases?per_page={per_project}",
                                 _folder() / f"{key}.news.json")
        except (InjectError, ValueError):
            continue
        for release in releases[:per_project]:
            if release.get("draft"):
                continue
            found.append(News(name, key, release.get("tag_name", ""),
                              str(release.get("published_at") or "")[:10],
                              release.get("body") or "", release.get("html_url", "")))
    return sorted(found, key=lambda n: n.date, reverse=True)


# --- every 3DS app of Universal-Updater's database ---------------------------------------

# the database's licence keys -> SPDX, for the ones that allow passing the app on
_DB_LICENCES = {"gpl-3.0": "GPL-3.0", "gpl-3.0+": "GPL-3.0", "gpl-2.0": "GPL-2.0", "gpl-2.0+": "GPL-2.0",
                "mit": "MIT", "agpl-3.0": "AGPL-3.0", "apache-2.0": "Apache-2.0", "lgpl-3.0": "LGPL-3.0",
                "lgpl-2.1": "LGPL-2.1", "zlib": "Zlib", "bsd-2-clause": "BSD-2-Clause",
                "bsd-3-clause": "BSD-3-Clause", "unlicense": "Unlicense", "mpl-2.0": "MPL-2.0",
                "isc": "ISC", "0bsd": "0BSD"}
OPEN_LICENCES.add("AGPL-3.0")


@dataclass(frozen=True)
class StoreApp:
    """A 3DS app listed in Universal-DB (https://db.universal-team.net), the database of
    Universal-Updater by Universal-Team, with a .cia download."""
    slug: str
    title: str
    author: str
    description: str
    long_description: str
    categories: tuple
    licence: str           # SPDX when known, else the database's key ("other", "")
    version: str           # the newest release's tag
    cia_url: str
    cia_name: str
    title_ids: tuple       # from the database's unique IDs
    icon_url: str
    image_url: str
    screenshots: tuple
    stars: int
    updated: str
    source: str
    color: str

    @property
    def allowed(self) -> bool:
        return self.licence in OPEN_LICENCES


def store_apps() -> list[StoreApp]:
    """Every 3DS app of Universal-DB with a .cia, the most starred first (the database is
    looked up at most daily)."""
    entries = _get_json(STORE_DB, _folder() / "universal-db.json")
    apps = []
    for entry in entries:
        if "3DS" not in (entry.get("systems") or []):
            continue
        cia = next(((name, d) for name, d in (entry.get("downloads") or {}).items()
                    if str(name).lower().endswith(".cia") and isinstance(d, dict) and d.get("url")), None)
        if cia is None:
            continue
        key = str(entry.get("license") or "").lower()
        shots = tuple((s.get("description", ""), s["url"]) for s in entry.get("screenshots") or []
                      if isinstance(s, dict) and s.get("url"))
        ids = tuple((APPLICATION << 32) | (int(u) << 8) for u in entry.get("unique_ids") or []
                    if str(u).isdigit())
        apps.append(StoreApp(
            str(entry.get("slug") or entry.get("title")), str(entry.get("title", "")),
            str(entry.get("author", "")), str(entry.get("description", "")),
            str(entry.get("long_description", "")), tuple(entry.get("categories") or ()),
            _DB_LICENCES.get(key, key), str(entry.get("version", "")), cia[1]["url"], str(cia[0]),
            ids, str(entry.get("icon", "")), str(entry.get("image", "")), shots,
            int(entry.get("stars") or 0), str(entry.get("updated", ""))[:10],
            str(entry.get("source") or entry.get("github") or ""), str(entry.get("color", ""))))
    return sorted(apps, key=lambda a: (-a.stars, a.title.lower()))


APPLICATION = 0x00040000


def store_download(app: StoreApp) -> Path:
    """The app's .cia, downloaded once per version."""
    from .artwork import _download
    if not app.allowed:
        raise InjectError(f"{app.title} has no open licence; get it from {app.source or 'its author'}.")
    tag = re.sub(r"[^A-Za-z0-9._-]", "_", app.version or "latest")
    target = _folder() / "store" / re.sub(r"[^A-Za-z0-9._-]", "_", app.slug) / tag / app.cia_name
    if target.is_file():
        return target
    data = _download(app.cia_url)
    if not data:
        raise InjectError(f"Downloading {app.title} failed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def cia_title_at(url: str) -> tuple[int, int]:
    """Title ID and version of the .cia at ``url``, from its first 64 KiB (kept)."""
    import hashlib

    from .artwork import conditional_get
    from .ncch import cia_title
    cache = _folder() / "titles" / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
    if cache.is_file():
        known = json.loads(cache.read_text())
        return int(known["title_id"], 16), known["version"]
    data, _etag = conditional_get(url, headers={"Range": "bytes=0-65535"})
    if not isinstance(data, bytes):
        raise InjectError(f"{url} could not be downloaded.")
    title_id, version = cia_title(data)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"title_id": f"{title_id:016X}", "version": version}))
    return title_id, version

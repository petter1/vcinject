"""Talking to vcinject Link, the app on the 3DS (source in companion/).

Once installed and started, vcinject Link answers a UDP broadcast in the same
Wi-Fi and serves requests over TCP: the installed titles with their icons,
files in the 3DS's archives (e.g. the HOME Menu layout), installs, deletes
and a reboot. It only moves raw data; everything is parsed here.
"""
from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import InjectError

PORT = 34013
LINK_VERSION = 12  # companion/source/version.h
CIA = Path(__file__).parent / "data" / "vcinject-link.cia"

MEDIA_NAND, MEDIA_SD, MEDIA_GAME_CARD = 0, 1, 2

# FS archive IDs and path types (libctru FS_ArchiveID / FS_PathType)
ARCHIVE_SDMC = 0x9
ARCHIVE_EXTDATA = 0x6
ARCHIVE_SYSTEM_SAVEDATA = 0x8
ARCHIVE_NAND_CTR_FS = 0x567890AB  # the NAND's file system (needs FsMountNand)
PATH_EMPTY, PATH_BINARY, PATH_ASCII, PATH_UTF16 = 1, 2, 3, 4

(_INFO, _TITLES, _SMDH, _READ, _WRITE, _LIST, _INSTALL, _DELETE, _REBOOT, _REPORT, _NOTE, _SMDHS,
 _PROGRESS, _RESTART) = range(1, 15)  # 15-17 were experiments with the HOME Menu, now unused
_ICON_BATCH = 20
_UNKNOWN_COMMAND = 0xE0000004
_ERRORS = {
    0xE0000001: "the 3DS did not understand the request",
    0xE0000002: "the 3DS ran out of memory",
    0xE0000003: "the file is too large",
    _UNKNOWN_COMMAND: "vcinject Link on the 3DS is too old for this; update it",
}


class RemoteError(InjectError):
    def __init__(self, result: int, what: str):
        self.result = result
        reason = _ERRORS.get(result, f"3DS error 0x{result:08X}")
        super().__init__(f"{what}: {reason}.")


# CFGU_GetSystemModel
_MODELS = {"0": "3DS", "1": "3DS XL", "2": "New 3DS", "3": "2DS", "4": "New 3DS XL", "5": "New 2DS XL"}


@dataclass
class Console:
    """A 3DS running vcinject Link."""
    host: str
    port: int = PORT
    info: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.info.get("name") or self.host

    @property
    def model(self) -> str:
        return _MODELS.get(self.info.get("model", ""), "3DS")

    @property
    def label(self) -> str:
        """Name and model, e.g. "Petter1 (3DS XL)": names alone are often alike."""
        return f"{self.name} ({self.model})"

    @property
    def region(self) -> int:
        return int(self.info.get("region", "-1"))

    @property
    def key(self) -> str:
        """Tells consoles apart: the 3DS's device ID (vcinject Link 1 didn't report it)."""
        device = self.info.get("id")
        return device.upper() if device else f"ip-{self.host}"

    @property
    def outdated(self) -> bool:
        try:
            return int(self.info.get("version", "0")) < LINK_VERSION
        except ValueError:
            return True

    @property
    def folder(self) -> Path:
        """Where vcinject keeps what belongs to this 3DS (icons, HOME Menu backups)."""
        from .template import data_dir
        return data_dir() / "3ds" / self.key


def _consoles_file() -> Path:
    from .template import data_dir
    return data_dir() / "3ds" / "consoles.json"


def known_consoles() -> dict[str, dict]:
    """Every 3DS seen so far, by key: name, last address, last seen."""
    try:
        return json.loads(_consoles_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(console: Console) -> None:
    consoles = known_consoles()
    consoles[console.key] = {"name": console.name, "host": console.host, "last_seen": time.time()}
    path = _consoles_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(consoles, indent=2, ensure_ascii=False), encoding="utf-8")


def reserve_title_ids(console: Console, titles: list[Title]) -> int:
    """Puts the games and apps installed on ``console`` into the title ID register,
    so new injects never take their IDs; returns how many were new."""
    from . import registry
    return registry.import_title_ids({t.id for t in titles if t.is_app},
                                     label=f"(installed on {console.name})")


@dataclass(frozen=True)
class Title:
    id: int
    size: int
    version: int
    media: int
    product_code: str

    @property
    def category(self) -> int:
        """The title ID's high word: 0x00040000 games and apps, 0x0004000E updates,
        0x0004008C DLC, 0x00040010 system apps, ..."""
        return self.id >> 32

    @property
    def is_app(self) -> bool:
        return self.category in (0x00040000, 0x00040002)  # applications and demos

    @property
    def is_system(self) -> bool:
        return bool(self.category & 0x10)


def _is_smdh(data: bytes) -> bool:
    return len(data) >= 0x36C0 and data[:4] == b"SMDH"


def _parse_info(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _broadcast_addresses() -> list[str]:
    from .serve import lan_ip
    addresses = ["255.255.255.255"]
    ip = lan_ip()
    if not ip.startswith("127."):
        addresses.append(ip.rsplit(".", 1)[0] + ".255")  # most home networks are /24
    return addresses


def discover(timeout: float = 2.0, hosts: list[str] | None = None, scan: bool = True) -> list[Console]:
    """The 3DSs running vcinject Link that answer within ``timeout`` seconds.

    Asks ``hosts`` (default: the broadcast addresses and every 3DS seen before)
    and repeats the question,
    since a 3DS in Wi-Fi power saving misses broadcasts now and then. When no
    one answers a broadcast, every address of the local network is asked
    directly (``scan``)."""
    found: dict[str, Console] = {}
    targets = hosts or _broadcast_addresses() + sorted({c["host"] for c in known_consoles().values()})
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        deadline = time.monotonic() + timeout
        next_ask = 0.0
        while (left := deadline - time.monotonic()) > 0:
            if time.monotonic() >= next_ask:
                for address in targets:
                    try:
                        sock.sendto(b"vcinject?", (address, PORT))
                    except OSError:
                        pass
                next_ask = time.monotonic() + 0.4
            sock.settimeout(max(0.01, min(left, next_ask - time.monotonic())))
            try:
                data, (host, _port) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode("utf-8", "replace")
            if text.startswith("vcinject-link\n"):
                info = _parse_info(text)
                found[host] = Console(host, int(info.get("port", PORT)), info)
                deadline = min(deadline, time.monotonic() + 0.6)  # others answer about as fast
    if not found and scan and not hosts:
        listening = _scan_network()
        if listening:
            return discover(timeout, hosts=listening, scan=False)
    for console in found.values():
        remember(console)
    return list(found.values())


def _scan_network() -> list[str]:
    """Addresses in the local /24 network where vcinject Link's port is open."""
    from concurrent.futures import ThreadPoolExecutor

    from .serve import lan_ip
    ip = lan_ip()
    if ip.startswith("127."):
        return []
    prefix = ip.rsplit(".", 1)[0]

    def probe(last: int) -> str | None:
        address = f"{prefix}.{last}"
        try:
            with socket.create_connection((address, PORT), timeout=0.8):
                return address
        except OSError:
            return None

    with ThreadPoolExecutor(64) as pool:
        return [a for a in pool.map(probe, range(1, 255)) if a and a != ip]


def binary_path(*words: int) -> tuple[int, bytes]:
    return PATH_BINARY, struct.pack(f"<{len(words)}I", *words)


def file_path(path: str) -> tuple[int, bytes]:
    return PATH_UTF16, (path.encode("utf-16-le") + b"\0\0")


def _path(path: tuple[int, bytes] | None) -> bytes:
    kind, data = path or (PATH_EMPTY, b"")
    return struct.pack("<II", kind, len(data)) + data


class Link:
    """A connection to vcinject Link; use as a context manager."""

    def __init__(self, host: str, port: int = PORT, timeout: float = 15.0):
        self.host = host
        self.on_note: Callable[[str], None] | None = None
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise InjectError(f"Could not connect to the 3DS at {host}: {exc}. Is vcinject Link "
                              f"open on it?") from exc

    def __enter__(self) -> "Link":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._sock.close()

    # --- protocol -------------------------------------------------------------

    def _recv(self, size: int) -> bytes:
        chunks, left = [], size
        while left:
            chunk = self._sock.recv(min(left, 1 << 20))
            if not chunk:
                raise InjectError("The 3DS closed the connection.")
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def _response(self, what: str) -> bytes:
        result, size = struct.unpack("<II", self._recv(8))
        if result:
            raise RemoteError(result, what)
        return self._recv(size)

    def _request(self, command: int, payload: bytes, what: str) -> bytes:
        try:
            self._sock.sendall(struct.pack("<II", command, len(payload)) + payload)
            return self._response(what)
        except OSError as exc:
            raise InjectError(f"{what}: the connection to the 3DS failed ({exc}).") from exc

    # --- commands -------------------------------------------------------------

    def info(self) -> dict[str, str]:
        return _parse_info(self._request(_INFO, b"", "Reading the 3DS's info").decode("utf-8", "replace"))

    def titles(self, media: int, product_codes: bool = True) -> list[Title]:
        """The titles on ``media``; leaving out the product codes is much faster."""
        data = self._request(_TITLES, bytes([media, product_codes]), "Listing the titles")
        out = []
        for offset in range(0, len(data) - 39, 40):
            tid, size, version, media_ = struct.unpack_from("<QQHB", data, offset)
            code = data[offset + 24:offset + 40].split(b"\0", 1)[0].decode("ascii", "replace")
            out.append(Title(tid, size, version, media_, code))
        return out

    def all_titles(self, product_codes: bool = True) -> list[Title]:
        return [t for media in (MEDIA_SD, MEDIA_NAND, MEDIA_GAME_CARD)
                for t in self.titles(media, product_codes)]

    def smdh(self, title: Title) -> bytes:
        return self._request(_SMDH, struct.pack("<BQ", title.media, title.id),
                             f"Reading the icon of {title.id:016X}")

    def cached_smdh(self, title: Title, console: Console) -> bytes | None:
        """The title's SMDH, kept on this computer per console, title and version
        (two 3DSs may have different injects under one title ID); None when the
        title has none."""
        return self.cached_smdhs([title], console)[title.id]

    def cached_smdhs(self, titles: list[Title], console: Console,
                     on_batch: Callable[[dict[int, bytes | None], int, int], None] | None = None
                     ) -> dict[int, bytes | None]:
        """``cached_smdh`` for many titles: what is not cached yet is fetched in
        batches, with a bar on the 3DS. ``on_batch(found, done, total)`` gets what
        is known so far – first the cached ones, then after each batch."""
        folder = console.folder / "icons"
        found: dict[int, bytes | None] = {}
        missing = []
        for title in titles:
            cache = folder / f"{title.id:016X}-v{title.version}.smdh"
            if cache.is_file():
                data = cache.read_bytes()  # empty: the title has no (usable) icon
                found[title.id] = data if _is_smdh(data) else None
            else:
                missing.append(title)
        if on_batch:
            on_batch(dict(found), 0, len(missing))
        if missing:
            folder.mkdir(parents=True, exist_ok=True)
        for start in range(0, len(missing), _ICON_BATCH):
            batch = missing[start:start + _ICON_BATCH]
            self.progress(start, len(missing), f"Loading icons {start}/{len(missing)}")
            fetched = {}
            for title, data in zip(batch, self._smdhs(batch)):
                data = data if _is_smdh(data) else b""  # some homebrew ships a broken icon
                (folder / f"{title.id:016X}-v{title.version}.smdh").write_bytes(data)
                fetched[title.id] = data or None
            found.update(fetched)
            if on_batch:
                on_batch(fetched, start + len(batch), len(missing))
        if missing:
            self.progress(0, 0, "")
        return found

    def _smdhs(self, titles: list[Title]) -> list[bytes]:
        try:
            data = self._request(_SMDHS, b"".join(struct.pack("<BQ", t.media, t.id) for t in titles),
                                 "Reading icons")
        except RemoteError as exc:
            if exc.result != _UNKNOWN_COMMAND:
                raise
            out = []  # vcinject Link before version 4: one by one
            for title in titles:
                try:
                    out.append(self.smdh(title))
                except RemoteError:
                    out.append(b"")
            return out
        out, offset = [], 0
        for _title in titles:
            size = struct.unpack_from("<I", data, offset)[0]
            out.append(data[offset + 4:offset + 4 + size])
            offset += 4 + size
        return out

    def read_file(self, archive: int, archive_path, path: str) -> bytes:
        return self._request(_READ, struct.pack("<I", archive) + _path(archive_path)
                             + _path(file_path(path)), f"Reading {path}")

    def write_file(self, archive: int, archive_path, path: str, data: bytes,
                   commit: bool = False) -> None:
        self._request(_WRITE, struct.pack("<I", archive) + _path(archive_path)
                      + _path(file_path(path)) + struct.pack("<I", int(commit)) + data,
                      f"Writing {path}")

    def list_dir(self, archive: int, archive_path, path: str = "/") -> list[tuple[str, bool, int]]:
        """(name, is_dir, size) of each entry."""
        data = self._request(_LIST, struct.pack("<I", archive) + _path(archive_path)
                             + _path(file_path(path)), f"Listing {path}")
        out, offset = [], 0
        while offset + 16 <= len(data):
            is_dir, size, name_len = struct.unpack_from("<B3xQI", data, offset)
            name = data[offset + 16:offset + 16 + name_len].decode("utf-8", "replace")
            out.append((name, bool(is_dir), size))
            offset += 16 + name_len
        return out

    def install(self, cia: Path, media: int = MEDIA_SD,
                progress: Callable[[int, int], None] | None = None) -> None:
        """Streams ``cia`` to the 3DS, which installs it while it arrives."""
        size = Path(cia).stat().st_size
        progress = progress or (lambda sent, total: None)
        what = f"Installing {Path(cia).name}"
        try:
            self._sock.sendall(struct.pack("<IIB", _INSTALL, size + 1, media))
            sent = 0
            progress(0, size)
            with Path(cia).open("rb") as f:
                while chunk := f.read(256 * 1024):
                    self._sock.sendall(chunk)
                    sent += len(chunk)
                    progress(sent, size)
            self._sock.settimeout(120)  # finishing a large install takes a while
            self._response(what)
        except OSError as exc:
            raise InjectError(f"{what}: the connection to the 3DS failed ({exc}).") from exc

    def delete(self, title: Title) -> None:
        self._request(_DELETE, struct.pack("<BQ", title.media, title.id),
                      f"Deleting {title.id:016X}")

    def show(self, command: int, text: str) -> None:
        """Shows ``text`` on the 3DS; versions of vcinject Link that can't are left alone."""
        try:
            self._request(command, text.encode("utf-8"), "Showing on the 3DS")
        except RemoteError as exc:
            if exc.result != _UNKNOWN_COMMAND:
                raise

    def progress(self, done: int, total: int, text: str) -> None:
        """What vcinject is doing, with a bar on the 3DS (total 0: done)."""
        try:
            self._request(_PROGRESS, struct.pack("<II", done, total) + text.encode("utf-8"),
                          "Showing on the 3DS")
        except RemoteError as exc:
            if exc.result != _UNKNOWN_COMMAND:
                raise

    def note(self, text: str) -> None:
        """A line in the activity log on the 3DS (and ``on_note``, e.g. the same log
        in the GUI)."""
        if self.on_note:
            self.on_note(text)
        self.show(_NOTE, text)

    def report(self, text: str) -> None:
        """The status report on the 3DS (status.report_text)."""
        self.show(_REPORT, text)

    def restart_app(self) -> None:
        """vcinject Link quits and starts again, e.g. as the version just installed.
        Raises RemoteError (too old) for versions before 5: then it has to be restarted
        on the 3DS."""
        self._request(_RESTART, b"", "Restarting vcinject Link")

    def reboot(self) -> None:
        self._request(_REBOOT, b"", "Restarting the 3DS")

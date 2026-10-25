"""vcinject Link client against a fake 3DS that speaks the same protocol."""
import socket
import struct
import threading

import pytest
from PIL import Image

from vcinject import remote, smdh
from vcinject.errors import InjectError

GAME = remote.Title(0x000400000F783500, 16 << 20, 0, remote.MEDIA_SD, "CTR-P-AXVP")
ICON = smdh.build_smdh("Saphir", "Pokémon Saphir", "Nintendo", Image.new("RGB", (48, 48), (255, 0, 0)))


class Fake3DS:
    """Answers like vcinject Link (companion/source/main.c) on a free local port."""

    def __init__(self, old=False):
        self.old = old  # vcinject Link before version 4: no batches, no progress
        self.batches = 0
        self.progress = []
        self.files = {}
        self.installed = []
        self.deleted = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.listener.accept()
        with conn:
            while header := self._recv(conn, 8):
                command, size = struct.unpack("<II", header)
                payload = self._recv(conn, size)
                result, body = self._answer(command, payload)
                conn.sendall(struct.pack("<II", result, len(body)) + body)

    @staticmethod
    def _recv(conn, size):
        data = b""
        while len(data) < size:
            chunk = conn.recv(size - len(data))
            if not chunk:
                return b""
            data += chunk
        return data

    @staticmethod
    def _file(payload):
        archive, offset = struct.unpack_from("<I", payload)[0], 4
        paths = []
        for _ in range(2):
            kind, size = struct.unpack_from("<II", payload, offset)
            paths.append(payload[offset + 8:offset + 8 + size])
            offset += 8 + size
        name = paths[1][:-2].decode("utf-16-le")
        return (archive, paths[0], name), payload[offset:]

    def _answer(self, command, payload):
        if command == 1:
            return 0, b"app=vcinject-link\nname=Yanik\nregion=2\n"
        if command == 2:
            if payload[0] != remote.MEDIA_SD:
                return 0, b""
            return 0, struct.pack("<QQHB5x16s", GAME.id, GAME.size, GAME.version, GAME.media,
                                  GAME.product_code.encode())
        if command == 3:
            return (0, ICON) if struct.unpack("<BQ", payload)[1] == GAME.id else (0xC8804478, b"")
        if command == 12 and not self.old:
            out = b""
            for offset in range(0, len(payload), 9):
                tid = struct.unpack_from("<BQ", payload, offset)[1]
                out += struct.pack("<I", len(ICON)) + ICON if tid == GAME.id else struct.pack("<I", 0)
            self.batches += 1
            return 0, out
        if command == 13 and not self.old:
            self.progress.append(payload[8:].decode())
            return 0, b""
        if command == 4:
            key, _ = self._file(payload)
            return (0, self.files[key]) if key in self.files else (0xC8804478, b"")
        if command == 5:
            key, rest = self._file(payload)
            self.files[key] = rest[4:]
            return 0, b""
        if command == 6:
            entries = [("Nintendo 3DS", True, 0), ("boot.firm", False, 1234)]
            return 0, b"".join(struct.pack("<B3xQI", d, size, len(n)) + n.encode()
                               for n, d, size in entries)
        if command == 7:
            self.installed.append((payload[0], payload[1:]))
            return 0, b""
        if command == 8:
            self.deleted.append(struct.unpack("<BQ", payload))
            return 0, b""
        return 0xE0000004, b""


@pytest.fixture
def fake():
    return Fake3DS()


def test_lists_titles_with_names_and_icons(fake):
    with remote.Link("127.0.0.1", fake.port) as link:
        assert link.info()["name"] == "Yanik"
        [title] = link.all_titles()
        assert title == GAME and title.is_app and not title.is_system
        data = link.smdh(title)
    assert smdh.read_titles(data, "de") == ("Saphir", "Pokémon Saphir", "Nintendo")
    assert smdh.read_icon(data).getpixel((10, 10))[0] > 240


def test_files_round_trip(fake):
    archive_path = remote.binary_path(remote.MEDIA_SD, 0x98, 0)
    with remote.Link("127.0.0.1", fake.port) as link:
        link.write_file(remote.ARCHIVE_EXTDATA, archive_path, "/SaveData.dat", b"\x04layout", commit=True)
        assert link.read_file(remote.ARCHIVE_EXTDATA, archive_path, "/SaveData.dat") == b"\x04layout"
        assert link.list_dir(remote.ARCHIVE_SDMC, None) == [("Nintendo 3DS", True, 0),
                                                            ("boot.firm", False, 1234)]
        with pytest.raises(remote.RemoteError, match="0xC8804478"):
            link.read_file(remote.ARCHIVE_EXTDATA, archive_path, "/Missing.dat")


def test_install_streams_the_cia_and_delete(fake, tmp_path):
    cia = tmp_path / "game.cia"
    cia.write_bytes(bytes(range(256)) * 4000)
    seen = []
    with remote.Link("127.0.0.1", fake.port) as link:
        link.install(cia, progress=lambda sent, total: seen.append((sent, total)))
        link.delete(GAME)
        with pytest.raises(remote.RemoteError, match="too old"):
            link._request(99, b"", "Testing")
    assert fake.installed == [(remote.MEDIA_SD, cia.read_bytes())]
    assert fake.deleted == [(GAME.media, GAME.id)]
    assert seen[0] == (0, cia.stat().st_size) and seen[-1][0] == cia.stat().st_size


def test_discovery_reads_the_reply():
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        responder.bind(("127.0.0.1", remote.PORT))
    except OSError:
        pytest.skip("discovery port is in use")

    def answer():
        query, sender = responder.recvfrom(64)
        if query == b"vcinject?":
            responder.sendto(b"vcinject-link\nport=34013\nname=Yanik\n", sender)

    threading.Thread(target=answer, daemon=True).start()
    try:
        [console] = remote.discover(timeout=2, hosts=["127.0.0.1"])
    finally:
        responder.close()
    assert (console.host, console.port, console.name) == ("127.0.0.1", remote.PORT, "Yanik")


def test_unreachable_console_is_a_clear_error():
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with pytest.raises(InjectError, match="vcinject Link"):
        remote.Link("127.0.0.1", port, timeout=2)


def test_consoles_are_kept_apart(fake, tmp_path, monkeypatch):
    from vcinject import registry
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    first = remote.Console("127.0.0.1", fake.port, {"id": "0000beef", "name": "Yanik", "version": str(remote.LINK_VERSION)})
    second = remote.Console("127.0.0.1", fake.port, {"id": "0000CAFE", "name": "Yanik", "version": str(remote.LINK_VERSION)})
    assert first.key != second.key and not first.outdated
    assert remote.Console("10.0.0.5", info={"version": "1"}).key == "ip-10.0.0.5"
    assert remote.Console("10.0.0.5", info={"version": "1"}).outdated
    with remote.Link("127.0.0.1", fake.port) as link:
        [title] = link.all_titles()
        assert link.cached_smdh(title, first) == ICON
    assert (tmp_path / "3ds" / "0000BEEF" / "icons" / f"{GAME.id:016X}-v0.smdh").is_file()
    assert not (tmp_path / "3ds" / "0000CAFE").exists()
    # the IDs taken on any 3DS are kept free for new injects
    assert remote.reserve_title_ids(first, [title]) == 1
    assert registry.load()[GAME.id].title == "(installed on Yanik)"
    remote.remember(first)
    assert remote.known_consoles()["0000BEEF"]["host"] == "127.0.0.1"


@pytest.mark.parametrize("old", [False, True])
def test_icons_come_in_batches(tmp_path, monkeypatch, old):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    fake = Fake3DS(old=old)
    console = remote.Console("127.0.0.1", fake.port, {"id": "0000BEEF"})
    others = [remote.Title(0x0004000000100000 + i * 0x100, 1, 0, remote.MEDIA_SD, "") for i in range(24)]
    batches = []
    with remote.Link("127.0.0.1", fake.port) as link:
        found = link.cached_smdhs([GAME] + others, console,
                                  on_batch=lambda got, done, total: batches.append((len(got), done, total)))
        again = link.cached_smdhs([GAME] + others, console)   # from the cache now
    assert found[GAME.id] == ICON and found[others[0].id] is None and again == found
    assert batches == [(0, 0, 25), (20, 20, 25), (5, 25, 25)]
    assert fake.batches == (0 if old else 2)
    assert fake.progress == ([] if old else ["Loading icons 0/25", "Loading icons 20/25", ""])


def test_the_app_is_shipped():
    assert remote.CIA.is_file() and remote.CIA.stat().st_size > 10_000

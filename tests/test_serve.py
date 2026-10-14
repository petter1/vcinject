import urllib.error
import urllib.request

import pytest

from vcinject.serve import CiaServer


def test_serves_only_listed_files(tmp_path):
    cia = tmp_path / "My Game (EU).cia"
    cia.write_bytes(b"CIA" * 1000)
    (tmp_path / "secret.txt").write_text("nope")

    messages = []
    server = CiaServer([cia], host="127.0.0.1", port=0, log=messages.append).start()
    try:
        [url] = server.urls
        assert url == f"http://127.0.0.1:{server.port}/0/My_Game_EU.cia"
        with urllib.request.urlopen(url) as response:
            assert response.headers["Content-Length"] == "3000"
            assert response.read() == cia.read_bytes()
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{server.port}/secret.txt")
        assert exc.value.code == 404
        assert server.qr_png()[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        server.stop()
    assert any("finished" in m for m in messages)


def test_server_reports_download_progress(tmp_path):
    import urllib.request

    from vcinject import serve

    cia = tmp_path / "game.cia"
    cia.write_bytes(b"\x01" * (700 * 1024))  # a few chunks
    seen = []
    server = serve.CiaServer([cia], host="127.0.0.1", port=0, log=lambda msg: None,
                             progress=lambda path, sent, total: seen.append((sent, total))).start()
    try:
        url = server.urls[0]
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.read() == cia.read_bytes()
    finally:
        server.stop()
    total = 700 * 1024
    assert seen[0] == (0, total) and seen[-1] == (total, total)
    assert [s for s, _t in seen] == sorted(s for s, _t in seen) and len(seen) >= 4

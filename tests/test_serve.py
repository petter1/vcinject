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

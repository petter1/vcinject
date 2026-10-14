import json

import pytest

from vcinject import artwork, homebrew
from vcinject.errors import InjectError


def _github(monkeypatch, licence):
    cia = b"\x20\x20\x00\x00" + b"\0" * 60
    responses = {
        "https://api.github.com/repos/zoogie/DSP1": {"license": {"spdx_id": licence} if licence else None},
        "https://api.github.com/repos/zoogie/DSP1/releases/latest": {
            "tag_name": "v1.0",
            "assets": [{"name": "DSP1.cia", "size": len(cia),
                        "browser_download_url": "https://example.invalid/DSP1.cia"}]},
    }
    downloads = []

    def download(url):
        downloads.append(url)
        return cia if url.endswith(".cia") else json.dumps(responses[url]).encode()

    monkeypatch.setattr(artwork, "_download", download)
    return downloads


def test_open_source_app_is_downloaded_once(monkeypatch):
    downloads = _github(monkeypatch, "MIT")
    found = homebrew.release(homebrew.APPS["dsp1"])
    assert (found.tag, found.licence, found.allowed) == ("v1.0", "MIT", True)
    path = homebrew.download(homebrew.APPS["dsp1"])
    assert path.name == "DSP1.cia" and path.read_bytes()[:2] == b"\x20\x20"
    homebrew.download(homebrew.APPS["dsp1"])
    assert sum(url.endswith(".cia") for url in downloads) == 1


def test_no_licence_no_download(monkeypatch):
    _github(monkeypatch, None)
    assert not homebrew.release(homebrew.APPS["dsp1"]).allowed
    with pytest.raises(InjectError):
        homebrew.download(homebrew.APPS["dsp1"])

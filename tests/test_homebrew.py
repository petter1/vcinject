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


def test_store_info_and_news(tmp_path, monkeypatch):
    import json

    from vcinject import artwork, homebrew

    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))
    db = [{"github": "BernardoGiordano/Checkpoint", "description": "Save manager.", "author": "Bernardo",
           "icon": "https://x/icon.png", "image": "https://x/banner.png",
           "screenshots": [{"description": "Main", "url": "https://x/shot.png"}]},
          {"github": "nh-server/FBI-NH", "description": "Open source title manager for the 3DS. (Archive)"}]
    releases = {"checkpoint": [{"tag_name": "v5.3.0", "published_at": "2026-10-13T10:00:00Z",
                                "body": "**New**", "html_url": "https://github.com/x"}],
                "luma": [{"tag_name": "v13.4", "published_at": "2026-09-01T00:00:00Z", "body": "",
                          "html_url": "u", "draft": False},
                         {"tag_name": "v13.5-draft", "published_at": "2026-10-20", "draft": True}]}

    def download(url):
        if url == homebrew.STORE_DB:
            return json.dumps(db).encode()
        for key, repo in (("checkpoint", "BernardoGiordano/Checkpoint"), ("luma", homebrew.LUMA_REPO)):
            if url.startswith(homebrew.API.format(repo=repo) + "/releases?"):
                return json.dumps(releases[key]).encode()
        if url == "https://x/banner.png":
            return b"PNG!"
        return None

    monkeypatch.setattr(artwork, "_download", download)
    checkpoint = homebrew.store_info(homebrew.APPS["checkpoint"])
    assert (checkpoint.description, checkpoint.author, checkpoint.screenshots) == \
        ("Save manager.", "Bernardo", (("Main", "https://x/shot.png"),))
    assert homebrew.store_info(homebrew.APPS["fbi"]).description == "Open source title manager for the 3DS."
    assert homebrew.store_info(homebrew.APPS["pksm"]).description == homebrew.APPS["pksm"].purpose
    assert homebrew.picture("https://x/banner.png") == b"PNG!"
    assert homebrew.picture("https://x/missing.png") is None

    items = homebrew.news()
    assert [(n.project, n.tag, n.date) for n in items] == [("Checkpoint", "v5.3.0", "2026-10-13"),
                                                           ("Luma3DS", "v13.4", "2026-09-01")]

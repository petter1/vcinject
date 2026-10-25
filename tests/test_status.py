from vcinject import homebrew, remote, status

FBI = 0x000400000F800100


class FakeLink:
    def __init__(self, sd_files=("boot.3dsx", "dspfirm.cdc")):
        self.sd_files = sd_files

    def list_dir(self, archive, archive_path, path):
        return [(name, False, 1) for name in self.sd_files]


def console(**info):
    base = {"version": str(remote.LINK_VERSION), "system": "11.17.0-50E", "luma": "13.4.0",
            "sd_free": str(80 * 10**9), "sd_total": str(128 * 10**9), "battery": "4"}
    return remote.Console("192.168.1.150", info={**base, **info})


def offline_releases(monkeypatch, fbi_version=0x861):
    def release_title(app):
        if app.key == "fbi":
            return FBI, fbi_version
        raise OSError("offline")
    monkeypatch.setattr(homebrew, "release_title", release_title)
    monkeypatch.setattr(homebrew, "luma_release", lambda: ("v13.4", "https://example.invalid/luma.zip"))


def test_everything_up_to_date(monkeypatch):
    offline_releases(monkeypatch)
    fbi = remote.Title(FBI, 1, 0x861, remote.MEDIA_SD, "CTR-P-FBI")
    found = {f.title: f for f in status.check(FakeLink(), console(), [fbi])}
    assert {f.level for f in found.values()} == {status.OK}
    assert found["FBI Reloaded"].detail == "2.6.1, up to date"
    assert found["SD card"].detail == "80.0 GB free of 128 GB"


def test_updates_and_warnings_come_first(monkeypatch):
    offline_releases(monkeypatch, fbi_version=0x863)
    fbi = remote.Title(FBI, 1, 0x861, remote.MEDIA_SD, "CTR-P-FBI")
    old = console(version="2", system="11.16.0-49E", luma="13.1.2", sd_free="10", battery="1")
    found = status.check(FakeLink(sd_files=()), old, [fbi])
    levels = [f.level for f in found]
    assert levels == sorted(levels, key=[status.UPDATE, status.WARN, status.OK, status.INFO].index)
    by_title = {f.title: f for f in found}
    assert by_title["vcinject Link"].action == "link"
    assert by_title["Luma3DS"].detail == "13.1.2 -> 13.4" and by_title["Luma3DS"].action == "luma"
    assert by_title["FBI Reloaded"].detail == "2.6.1 -> 2.6.3"
    assert by_title["System"].level == status.UPDATE
    assert by_title["Sound firmware"].action == "app:dsp1"
    assert by_title["SD card"].level == by_title["Battery"].level == status.WARN

    text = status.report_text(found)
    assert text.startswith("from\t") and "update\tFBI Reloaded\t2.6.1 -> 2.6.3\n" in text

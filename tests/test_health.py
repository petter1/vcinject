import pytest

from vcinject import forwarder, health, template, vcbanner
from vcinject.errors import MissingResource


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))


def states():
    return {c.resource: c for c in health.check()}


def test_empty_setup_reports_required_and_optional_parts():
    s = states()
    assert not s["gba-template"].ok and s["gba-template"].required
    assert not s["vclook:nes"].ok and not s["vclook:nes"].required
    assert not s["forwarder:snes"].ok


def test_missing_parts_raise_missing_resource():
    with pytest.raises(MissingResource) as exc:
        forwarder.ForwarderTemplate.load("gen")
    assert exc.value.resource == "forwarder:gen"
    with pytest.raises(MissingResource) as exc:
        vcbanner.load_template("gb")
    assert exc.value.resource == "vclook:gb"


def test_damaged_files_are_set_aside(tmp_path):
    folder = template.template_dir()
    folder.mkdir(parents=True)
    for name in ("exheader.bin", "romfs.bin", "logo.bin"):
        (folder / name).write_bytes(b"broken")
    with pytest.raises(MissingResource, match="damaged"):
        template.GbaTemplate.load()
    assert not folder.exists()
    assert list(folder.parent.glob("gba.broken-*"))

    look = vcbanner.template_path("nes")
    look.parent.mkdir(parents=True)
    look.write_bytes(b"not a banner")
    assert not states()["vclook:nes"].ok
    assert not look.exists()


def test_heal_without_a_file_gives_up_cleanly():
    logs = []
    assert health.heal("forwarder:snes", lambda _q: None, logs.append) is False
    assert "emulator inject" in health.question("forwarder:snes")

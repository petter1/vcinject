import json
import zipfile

import pytest

from vcinject import backup, vcbanner
from vcinject.template import GbaTemplate
from vcinject.errors import InjectError


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "data"))


def test_export_needs_templates(tmp_path):
    with pytest.raises(InjectError, match="nothing to export"):
        backup.export(tmp_path / "b.zip")


def test_roundtrip_restores_files(tmp_path, monkeypatch):
    GbaTemplate(exheader=bytes(0x800), romfs=b"IVFC" + bytes(60), logo=b"L").save()
    broken = vcbanner.template_path("nes")       # damaged look: must not be exported
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a banner")
    path, contents = backup.export(tmp_path / "b")
    assert path.suffix == ".zip" and contents == ["GBA VC base template"]
    with zipfile.ZipFile(path) as zf:
        assert "gba/exheader.bin" in zf.namelist()
        assert not any("nes" in n or "broken" in n for n in zf.namelist())
        assert json.loads(zf.read(backup.MANIFEST))["format"] == 1

    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "other"))
    assert backup.restore(path, log=lambda _m: None) == ["GBA VC base template"]
    assert GbaTemplate.load().logo == b"L"


def test_restore_rejects_foreign_files(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(backup.MANIFEST, "{}")
        zf.writestr("../../evil.txt", "x")
    with pytest.raises(InjectError, match="Unexpected file"):
        backup.restore(bad)
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(InjectError, match="not a vcinject template backup"):
        backup.restore(plain)

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    """Tests never go online and never see the user's data; a test that needs a
    server or its own data folder sets that up itself."""
    from vcinject import artwork
    from vcinject.errors import InjectError

    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setattr(artwork, "_DB_CACHE", {})
    monkeypatch.setattr(artwork, "_META_CACHE", {})

    def offline(*args, **kwargs):
        raise InjectError("no network in tests")

    monkeypatch.setattr(artwork, "_download", offline)
    monkeypatch.setattr(artwork, "conditional_get", offline)
    try:  # the GUI looks for 3DSs on the network by itself
        from vcinject.gui import wifi_install
        monkeypatch.setattr(wifi_install.ConsoleWatcher, "refresh", lambda self: None)
    except ImportError:
        pass
    yield
    _finish_library_threads()


def _finish_library_threads():
    """Stop library scans a GUI test started, so they cannot run into the next
    test (which may have replaced the databases they use)."""
    import sys
    import threading

    view = sys.modules.get("vcinject.gui.libraryview")
    if view is None:
        return
    for panel in list(view.LibraryPanel._panels):
        panel.stop()
    for thread in threading.enumerate():
        if thread.name == view.SCAN_THREAD:
            thread.join(timeout=10)


def make_rom(size=0x40000, game_code=b"BTSE", save_string=b"", extra=b""):
    data = bytearray(size)
    data[0xA0:0xAC] = b"TESTGAME".ljust(12, b"\0")
    data[0xAC:0xB0] = game_code
    data[0xB0:0xB2] = b"01"
    data[0xB2] = 0x96
    data[0xBD] = (-sum(data[0xA0:0xBD]) - 0x19) & 0xFF
    blob = save_string + b"\0\0\0\0" + extra
    data[0x1000:0x1000 + len(blob)] = blob
    return bytes(data)


@pytest.fixture
def rom_bytes():
    return make_rom(save_string=b"SRAM_V113")

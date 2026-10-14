import pytest

from vcinject import registry


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VCINJECT_DATA_DIR", str(tmp_path))


def entry(title_id, sha1, built_at=1.0, title="Game"):
    return registry.Entry(title_id=title_id, title=title, rom_sha1=sha1, rom_name="g.gba",
                          product_code="CTR-N-TEST", save_type="SRAM_256K", output="g.cia",
                          built_at=built_at)


SHA_A = "a" * 40
SHA_B = "b" * 40


def test_preferred_id_is_stable_and_in_range():
    tid = registry.preferred_title_id(SHA_A)
    assert tid == registry.preferred_title_id(SHA_A)
    assert tid >> 32 == 0x00040000
    assert registry.UNIQUE_ID_BASE <= (tid >> 8) & 0xFFFFF < registry.UNIQUE_ID_BASE + registry.UNIQUE_ID_SPAN


def test_collision_moves_to_next_free_id():
    taken = registry.preferred_title_id(SHA_A)
    registry.record(entry(taken, SHA_B))  # a different ROM sits on A's preferred ID
    suggested = registry.suggest_title_id(SHA_A)
    assert suggested != taken
    assert suggested == taken + 0x100  # next unique ID
    assert registry.conflict(taken, SHA_A).rom_sha1 == SHA_B
    assert registry.conflict(taken, SHA_B) is None


def test_same_rom_keeps_most_recent_id():
    registry.record(entry(0x000400000F000100, SHA_A, built_at=1))
    registry.record(entry(0x000400000F000200, SHA_A, built_at=2))
    assert registry.suggest_title_id(SHA_A) == 0x000400000F000200
    assert len(registry.load()) == 2  # both installs stay listed


def test_roundtrip_forget_and_broken_file():
    registry.record(entry(0x000400000F000100, SHA_A, title="Pokémon"))
    assert registry.load()[0x000400000F000100].title == "Pokémon"
    assert registry.forget(0x000400000F000100)
    assert not registry.forget(0x000400000F000100)

    registry.registry_path().write_text("{not json")
    assert registry.load() == {}
    assert list(registry.registry_path().parent.glob("registry.broken-*.json"))

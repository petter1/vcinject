import zlib

from PIL import Image

from vcinject import art, artwork

DAT = '''
game (
	name "Metroid Fusion (USA)"
	serial "AMTE"
	rom ( name "Metroid Fusion (USA).gba" size 8388608 crc 11111111 serial "AMTE" )
)

game (
	name "Metroid Fusion (Europe) (En,Fr,De,Es,It)"
	serial "AMTP"
	rom ( name "Metroid Fusion (Europe) (En,Fr,De,Es,It).gba" size 8388608 crc 22222222 serial "AMTP" )
)

game (
	name "Pokemon - Emerald Version (USA, Europe) (Beta)"
	serial "BPEE"
	rom ( name "x.gba" size 1 crc 33333333 serial "BPEE" )
)

game (
	name "Pokemon - Emerald Version (USA, Europe)"
	serial "BPEE"
	rom ( name "y.gba" size 1 crc 44444444 serial "BPEE" )
)
'''


def test_identify_by_crc_serial_and_name():
    db = artwork.ReleaseDatabase(DAT)
    rom = b"some rom"
    db.by_crc[f"{zlib.crc32(rom):08X}"] = "Metroid Fusion (USA)"
    assert db.identify(rom) == artwork.Match("Metroid Fusion (USA)", "crc")
    # hacks keep the serial: prefer the retail release over the beta
    assert db.identify(b"hack", "BPEE").name == "Pokemon - Emerald Version (USA, Europe)"
    # file name: the region in the name wins, otherwise World > Europe > USA
    assert db.identify(b"x", "", "Metroid Fusion (USA).gba").name == "Metroid Fusion (USA)"
    assert db.identify(b"x", "", "Metroid Fusion.gba").name.startswith("Metroid Fusion (Europe)")
    assert db.identify(b"x", "ZZZZ", "unknown.gba") is None


def test_thumbnail_url_escapes_names():
    url = artwork.thumbnail_url("Zelda: A/B & C", "boxart")
    assert url.endswith("/Named_Boxarts/Zelda_%20A_B%20_%20C.png")


def test_styles_fall_back_when_art_missing():
    found = artwork.Artwork(images={"boxart": Image.new("RGB", (512, 512), "red")})
    assert artwork.available(artwork.ICON_STYLES, found) == ["boxart", "boxart-full", "generated"]
    assert artwork.available(artwork.BANNER_STYLES, found) == ["boxart", "generated"]
    icon, banner = artwork.default_art(found, "Game")
    assert icon.size == (192, 192)
    assert banner.size == art.BANNER_SIZE
    # asking for a style without its artwork yields generated art, not an error
    assert artwork.render_banner("logo", artwork.Artwork(), "Game").size == art.BANNER_SIZE

import os

from PIL import Image, ImageDraw

from vcinject import cgfx, vcbanner


def test_lz11_roundtrip():
    data = os.urandom(3000) + b"ABCD" * 5000 + bytes(range(256)) * 20 + b"\0" * 70000
    packed = cgfx.lz11_compress(data)
    assert len(packed) < len(data) // 3
    assert cgfx.lz11_decompress(packed) == data


def test_texture_codec_roundtrip():
    img = Image.new("RGBA", (16, 8))
    for x in range(16):
        for y in range(8):
            img.putpixel((x, y), (x * 16, y * 32, 128, 255))
    for fmt in (cgfx.FMT_RGB565, cgfx.FMT_RGBA8, cgfx.FMT_LA8):
        bpp = cgfx._BYTES_PER_PIXEL[fmt]
        tex = cgfx.TextureInfo("T", 16, 8, fmt, 4, 16 * 8 * bpp)
        blob = b"HEAD" + cgfx.encode_texture(img, tex)
        again = cgfx.decode_texture(blob, tex)
        assert cgfx.encode_texture(again, tex) == blob[4:]


def test_cbmd_roundtrip():
    cbmd = cgfx.Cbmd(common=b"CGFX" + b"x" * 500, regions={0: b"CGFX" + b"y" * 300, 9: b"CGFX"},
                     cwav=b"CWAV" + b"z" * 100)
    again = cgfx.Cbmd.parse(cbmd.build())
    assert again == cbmd


def fake_plate():
    """A plate like the VC one: bordered box, grey badge left, old text right."""
    plate = Image.new("RGBA", (256, 64), (255, 255, 255, 0))
    d = ImageDraw.Draw(plate)
    d.rounded_rectangle((1, 2, 254, 61), radius=6, fill=(240, 240, 240, 255), outline=(170, 170, 170, 255), width=2)
    d.rounded_rectangle((8, 8, 92, 56), radius=5, fill=(140, 140, 140, 255))
    d.text((150, 20), "OLD TITLE", fill=(30, 30, 30, 255))
    return plate


def test_render_plate_replaces_only_text_area():
    plate = fake_plate()
    out = vcbanner.render_plate(plate, "New Game", "Released: 2004")
    assert out.size == plate.size
    # badge untouched
    assert out.crop((8, 8, 92, 56)).tobytes() == plate.crop((8, 8, 92, 56)).convert("LA").convert("RGBA").tobytes()
    # some ink in the text area, and the old text gone from its spot
    assert out.crop((100, 10, 250, 54)) != plate.crop((100, 10, 250, 54))


def test_released_text_localised():
    assert vcbanner.released_text("de", "2004") == "Erschienen: 2004"
    assert vcbanner.released_text("xx", "2004") == "Released: 2004"
    assert vcbanner.released_text("de", None, "Nintendo") == "Nintendo"


def test_mipmapped_texture_size():
    tex = cgfx.TextureInfo("M", 32, 32, cgfx.FMT_RGBA8, 0, (32 * 32 + 16 * 16 + 8 * 8) * 4)
    assert len(cgfx.encode_texture(Image.new("RGBA", (32, 32), "red"), tex)) == tex.data_size


def framed_screen():
    """Like newer NSUI banners: purple 4 px frame around the picture, transparent outside."""
    tex = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    d = ImageDraw.Draw(tex)
    d.rounded_rectangle((2, 21, 125, 106), radius=4, fill=(126, 100, 197, 255))
    d.rectangle((6, 25, 121, 102), fill=(10, 200, 10, 255))
    return tex


def test_frame_content_box_and_compose():
    tex = framed_screen()
    assert vcbanner.frame_content_box(tex) == (6, 25, 122, 103)
    out = vcbanner.compose_screen(tex, Image.new("RGB", (240, 160), (255, 0, 0)))
    assert out.getpixel((64, 64))[:3] == (255, 0, 0)         # picture inside
    assert out.getpixel((3, 64)) == (126, 100, 197, 255)     # frame kept
    assert out.getpixel((64, 5))[3] == 0                     # transparency kept


def test_unframed_screen_is_stretched():
    tex = Image.new("RGBA", (128, 128), (1, 2, 3, 255))
    assert vcbanner.frame_content_box(tex) is None
    assert vcbanner.compose_screen(tex, Image.new("RGB", (240, 160), "blue")).size == (128, 128)

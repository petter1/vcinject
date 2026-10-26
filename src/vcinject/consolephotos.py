"""The colours each 3DS model was sold in, and free photos of them.

The 3DS reports its model but not its colour, so the colour is chosen once per
console (GUI). Photos come from Wikimedia Commons and are public domain or CC0;
they are downloaded once and kept. A colour without a photo is drawn
(gui/consoleart.py) in that colour.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class Colour:
    key: str
    name: str
    body: str             # main colour of the shell
    accent: str = ""      # second colour (2DS front, buttons), "" = none
    photo: str = ""       # file on Wikimedia Commons


@dataclass(frozen=True)
class Photo:
    file: str
    author: str
    licence: str


PHOTOS = {
    "Nintendo3DSAqua.png": Photo("Nintendo3DSAqua.png", "Wikimedia Commons contributor", "CC0"),
    "Nintendo-3DS-XL-angled.png": Photo("Nintendo-3DS-XL-angled.png", "Evan-Amos", "Public domain"),
    "Nintendo-2DS-angle.png": Photo("Nintendo-2DS-angle.png", "Evan-Amos", "Public domain"),
    "New Nintendo 3DS.png": Photo("New Nintendo 3DS.png", "Wikimedia Commons contributor", "CC0"),
    "New Nintendo 2ds XL.png": Photo("New Nintendo 2ds XL.png", "LBDCOM12", "CC0"),
    "New Nintendo 2DS LL (White & Orange).png": Photo("New Nintendo 2DS LL (White & Orange).png",
                                                       "Lemonix1004", "CC0"),
}

# the first colour of a model is shown until one is chosen
COLOURS: dict[str, tuple[Colour, ...]] = {
    "3DS": (Colour("aqua", "Aqua Blue", "#18a7c9", photo="Nintendo3DSAqua.png"),
            Colour("black", "Cosmo Black", "#2a2a30"),
            Colour("red", "Flame Red", "#c8202a"),
            Colour("pink", "Pearl Pink", "#f2b8c6"),
            Colour("white", "Ice White", "#eef0f2"),
            Colour("purple", "Midnight Purple", "#4b2a6b"),
            Colour("cobalt", "Cobalt Blue", "#2b4a8c")),
    "3DS XL": (Colour("black", "Black", "#2a2a30", photo="Nintendo-3DS-XL-angled.png"),
               Colour("red", "Red / Black", "#b3202a", "#2a2a30"),
               Colour("blue", "Blue / Black", "#2350a8", "#2a2a30"),
               Colour("silver", "Silver / Black", "#b8bcc4", "#2a2a30"),
               Colour("white", "White", "#eef0f2"),
               Colour("pink", "Pink / White", "#f2a8c0", "#f4f4f4"),
               Colour("mint", "Mint / White", "#9ad8c0", "#f4f4f4")),
    "New 3DS": (Colour("white", "White", "#f2efe6", photo="New Nintendo 3DS.png"),
                Colour("black", "Black", "#2a2a30")),
    "New 3DS XL": (Colour("black", "Metallic Black", "#2e3038"),
                   Colour("blue", "Metallic Blue", "#2b4e9a"),
                   Colour("red", "Metallic Red", "#a51f2c"),
                   Colour("white", "Pearl White", "#eef0f2"),
                   Colour("galaxy", "Galaxy Style", "#3a2c5c")),
    "2DS": (Colour("blue", "Black + Blue", "#1f6fd6", "#2a2c33", photo="Nintendo-2DS-angle.png"),
            Colour("red", "Black + Red", "#c8202a", "#2a2c33"),
            Colour("whitered", "White + Red", "#c8202a", "#eeeeee"),
            Colour("electric", "Electric Blue", "#2d8be0", "#2d8be0"),
            Colour("crimson", "Crimson Red", "#c0182c", "#c0182c"),
            Colour("green", "Sea Green", "#2fae8a", "#2fae8a")),
    "New 2DS XL": (Colour("turquoise", "Black + Turquoise", "#2d2f36", "#2fb6c8", photo="New Nintendo 2ds XL.png"),
                   Colour("orange", "White + Orange", "#f2f2f2", "#f08a24",
                          photo="New Nintendo 2DS LL (White & Orange).png"),
                   Colour("purple", "Purple + Silver", "#5a3f8c", "#c0c4cc"),
                   Colour("lime", "Black + Lime", "#2d2f36", "#9bd13c")),
}


def colours(model: str) -> tuple[Colour, ...]:
    return COLOURS.get(model, COLOURS["New 3DS XL"])


def colour(model: str, key: str | None) -> Colour:
    """The colour ``key`` of ``model``, or the model's first."""
    found = colours(model)
    return next((c for c in found if c.key == key), found[0])


def photo_url(file: str, width: int = 640) -> str:
    """A downscaled copy of a Commons file (Special:FilePath redirects to the thumbnail)."""
    return (f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(file)}"
            f"?width={width}")


def photo_page(file: str) -> str:
    return f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(file.replace(' ', '_'))}"


def download(file: str) -> bytes | None:
    from .artwork import _download
    return _download(photo_url(file))

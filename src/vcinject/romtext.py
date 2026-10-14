"""Text inside ROMs, for naming ROM hacks whose file name and header say nothing.

Pokémon ROM hacks keep the header of their base game ("POKEMON FIRE", BPRE), but
usually greet the player with their own name ("Welcome to Sword and Shield GBA
ULTIMATE!"). The games of the third generation store text in their own character
set, decoded here.
"""
from __future__ import annotations

import re

# game codes (without region letter) of Ruby, Sapphire, Emerald, FireRed, LeafGreen
GEN3_POKEMON = {"AXV", "AXP", "BPE", "BPR", "BPG"}

_CHARS = ["\n"] * 256
_CHARS[0x00] = " "
for _i in range(10):
    _CHARS[0xA1 + _i] = str(_i)
for _i in range(26):
    _CHARS[0xBB + _i] = chr(ord("A") + _i)
    _CHARS[0xD5 + _i] = chr(ord("a") + _i)
for _byte, _char in {0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xB4: "'", 0xB8: ",",
                     0xBA: "/", 0xF0: ":", 0x1B: "e"}.items():
    _CHARS[_byte] = _char
_TABLE = {i: c for i, c in enumerate(_CHARS)}
_RUN = re.compile(r"[A-Za-z0-9'][A-Za-z0-9 '!?.,:/-]{5,}")


def is_gen3_pokemon(rom: bytes) -> bool:
    return rom[0xAC:0xAF].decode("ascii", "replace") in GEN3_POKEMON


def gen3_text(rom: bytes) -> str:
    """All runs of readable text, lower case, without apostrophes."""
    text = rom.decode("latin-1").translate(_TABLE)
    return " ".join(_RUN.findall(text)).lower().replace("'", "")

# vcinject

A cross-platform (macOS / Linux) alternative to **NSUI** (New Super Ultimate
Injector) for building Nintendo 3DS Virtual Console injects. It has a CLI and a
Qt GUI on top of the same Python core.

Currently supported:

| System | Method | Status |
|--------|--------|--------|
| Game Boy Advance | Native GBA VC (AGB_FIRM) | ✅ |

## How it works

A GBA VC title is an ordinary 3DS application whose `ExeFS:/.code` contains the
GBA ROM followed by a 0x360-byte `.CAA` footer (save type, save chip timings,
LCD ghosting, color LUT). The console boots it into AGB_FIRM, which runs the
ROM on the 3DS's built-in GBA hardware.

vcinject builds everything in Python (footer, SMDH icon, ExeFS, unencrypted
NCCH with correct hashes) and uses two external tools:

- **bannertool** to build the HOME Menu banner
- **makerom** to wrap the CXI in an installable CIA

The only parts taken from an official title are the **exheader** (its access
descriptor is signed by Nintendo), the **boot logo** and an empty RomFS. They
are not bundled with vcinject. `vcinject setup` either downloads them (pinned
and SHA-256 verified, from [vcoven](https://github.com/vedoot/vcoven)) or
extracts them from a decrypted GBA VC title you dumped yourself.

The generated footer is byte-identical to the ones in official titles for the
same settings (see `tests/test_gba.py`).

## Installation

Requirements: Python ≥ 3.9, git, make, and a C/C++ compiler
(`xcode-select --install` on macOS, `build-essential` on Debian/Ubuntu).

```sh
./scripts/build-tools.sh            # builds makerom + bannertool into ./bin
python3 -m venv .venv
.venv/bin/pip install -e '.[gui]'   # drop [gui] for CLI-only
.venv/bin/vcinject setup            # or: vcinject setup --donor my_gba_vc.cia
.venv/bin/vcinject info             # check that everything is found
```

## Usage

GUI: double-click `start-gui.command` in Finder, or from a terminal:

```sh
./start-gui.command          # or: .venv/bin/vcinject gui
```

Drop a `.gba` file on the window, adjust the title, icon and banner, then click **Build CIA**.

CLI:

```sh
vcinject gba "Golden Sun.gba" \
    --title "Golden Sun" --publisher "Camelot" \
    --icon icon.png --banner banner.png \
    -o golden_sun.cia
```

| Option | Default |
|--------|---------|
| `--title`, `--long-title`, `--publisher` | ROM file name / same / "Virtual Console" |
| `--icon`, `--banner` | official artwork from the libretro database (see below) |
| `--icon-style`, `--banner-style` | best available: box art icon, logo banner |
| `--offline` | skip the online lookup, generate artwork from the title |
| `--title-id` | derived from the ROM hash (`00040000 0F0xxxx00`), see [Title IDs](#title-ids) |
| `--product-code` | `CTR-N-<game code>` |
| `--save-type` | auto-detected from the SDK save library string (`vcinject save-types` lists all values) |
| `--ghosting` | `0x80` (lower = more LCD ghosting, `0xFF` = none) |
| `--brightness` | `1.0` = full brightness (official titles use `0.7`, which looks darker) |

### Title IDs

Each inject gets a title ID `00040000 0FxxxxNN` with a unique ID in
`0xF0000`–`0xF7FFF`, a range official titles don't use. vcinject keeps a local
register of every build (`registry.json` in the data directory):

- Rebuilding the same ROM reuses its ID, so installing updates the existing title and keeps its save.
- A different ROM never gets an ID that is already registered. If its preferred ID is taken, the next free one is used.
- Choosing an ID that belongs to another ROM with `--title-id` prints a warning.

```sh
vcinject list                       # all injects with ID, save type and build date
vcinject forget 000400000F6B2B00    # free an ID after deleting the title on the 3DS
```

### Artwork

Icons and banners come from the [libretro thumbnail database](https://thumbnails.libretro.com),
which has box art, title screens, screenshots and logos for nearly every
release. The ROM is matched against the No-Intro DAT from
[libretro-database](https://github.com/libretro/libretro-database), by CRC32
first, then by cartridge serial (hacks and translations), then by file name.
No API key is needed. Downloads are cached in the vcinject data directory, so
later builds work offline.

| Style | Icon | Banner |
|-------|------|--------|
| `boxart` | cover illustration, without the GBA side stripe and rating bar | whole box on a blurred backdrop |
| `boxart-full` | whole box | – |
| `logo` | game logo on a blurred backdrop | game logo on a blurred backdrop |
| `title` | title screen | framed title screen |
| `generated` | initials | title text |

If no match is found, generated art is used. `--icon`/`--banner` or
"Custom image…" in the GUI override any style.

### VC Console Look (3D GBA banner)

GBA VC injects made with NSUI v28+ show a 3D Game Boy Advance on the HOME
Menu with the game on its screen and the "Virtual Console" plate (title +
release year) below. vcinject can build the same banner, using the banner of
an inject you already have as template. The model isn't bundled.

```sh
vcinject setup --banner-donor "My NSUI inject.cia"   # .cia, .cxi or banner.bin
```

To get such a file from the console: GodMode9 → HOME → *Title manager* →
`[A:] SD CARD` → pick the inject → *Build CIA (standard)*; it lands in `gm9/out`.

Once installed, every build uses it by default (`--banner-style gba3d`):
the title screen goes on the GBA's screen, and the plate shows the title and
"Released: YEAR" in each console language (`--released 2004` to override the
year from the database). The icon gets the bevelled VC frame
(`--no-icon-frame` to disable).

### Avoiding title IDs already on your 3DS

Import the console's ticket database so that new injects never reuse a title
ID of something installed, including injects from other tools:

1. Boot GodMode9 (hold START while powering on).
2. `[1:] SYSNAND CTRNAND` → `dbs` → select `ticket.db`, press Y to copy.
3. Go to `[0:] SDCARD` → `gm9` → `out`, press Y to paste.
4. Copy `gm9/out/ticket.db` to your computer and run
   `vcinject import-tickets ticket.db` (GUI: **Import tickets…**).

GodMode9 only reads the NAND here, so it won't ask for write permission.
Tickets of deleted titles may still be in the file; those IDs are simply
skipped as well.

### Install via QR code

Instead of copying the CIA to the SD card, let FBI download it over Wi-Fi:

```sh
vcinject gba game.gba --qr           # build, then serve + show QR code
vcinject serve a.cia b.cia           # serve existing CIAs (one QR code for all)
```

In the GUI, click **Install via QR**: it builds the CIA with the current
settings and shows the QR code right in the window, together with the
download status. **Stop** (or closing the window) ends the server.
On the 3DS open **FBI → Remote Install → Scan QR Code** and scan the code.
The 3DS and the computer must be on the same network. The server runs until
you press Ctrl+C or close the dialog. On macOS, allow incoming connections if
the firewall asks. Use `--host`/`--port` if the auto-detected address is wrong.

Install the CIA with FBI on a console running Luma3DS. As with every inject,
the NCCH header signature can't be valid, so this relies on Luma's signature
patches (on by default).

## Save games

AGB_FIRM writes the save to NAND when you leave the game. On the next boot the
system copies it to the SD card as the title's save data, where Checkpoint or
GodMode9 can back it up. The real-time clock is reported not to work under
the official AGB_FIRM, so time-based events (e.g. berries in Pokémon
Ruby/Sapphire/Emerald) may not advance.

If a game doesn't save, override the auto-detection with `--save-type`.
Repro-cart hacks in particular can carry misleading SDK strings.

## Development

```sh
.venv/bin/pip install -e '.[gui,dev]'
.venv/bin/pytest
```

Layout: `gba.py` (ROM parsing, save detection, footer), `smdh.py`, `ncch.py`
(ExeFS/NCCH/CIA parsing), `art.py`, `template.py`, `builder.py` (pipeline),
`cli.py`, `gui/app.py`.

## References

- [3dbrew: 3DS Virtual Console](https://www.3dbrew.org/wiki/3DS_Virtual_Console) (footer format, save types)
- [libn3ds / open_agb_firm](https://github.com/profi200/open_agb_firm) (save chip timings, save detection heuristics)
- [vcoven](https://github.com/vedoot/vcoven) (template, NCCH gotchas)
- [Project_CTR](https://github.com/3DSGuy/Project_CTR), [bannertool](https://github.com/carstene1ns/3ds-bannertool)

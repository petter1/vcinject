# vcinject

**Virtual Console injects for the Nintendo 3DS – on macOS, Windows and Linux.**

vcinject turns your own game backups into installable 3DS titles that look and
behave like Nintendo's Virtual Console releases: their own HOME Menu icon, the
rotating 3D banner with the "Virtual Console" plate, the console's jingle and
working saves. It is an open, cross-platform alternative to the Windows-only
[New Super Ultimate Injector (NSUI)](https://gbatemp.net/threads/discussion-new-super-ultimate-injector-nsui.500376/),
with a GUI and a command line built on the same Python core.

| System | How it runs | |
| --- | --- | --- |
| Game Boy Advance | natively on the 3DS's GBA hardware (AGB_FIRM) | ✅ |
| Super Nintendo | snes9x_3ds forwarder | ✅ needs an emulator template |
| Mega Drive / Genesis | RetroArch forwarder | ✅ needs an emulator template |
| Game Boy / Color | RetroArch forwarder | ✅ needs an emulator template |
| NES | VirtuaNES / RetroArch forwarder | ✅ needs an emulator template |
| NES, GB/GBC with Nintendo's own VC emulator | | planned |

## Features

- **One click from ROM to console:** build the CIA and install it over Wi-Fi by
  scanning a QR code with FBI – no SD card shuffling.
- **ROM library:** point it at your ROM folder (sub folders included) and pick
  games from a list with covers; zipped ROMs (one game per .zip) work like
  unpacked ones, and one click packs each unpacked game into its own .zip;
  optionally list every game of a system from
  the No-Intro database to see what your collection is missing.
- **VC Console Look:** the official 3D banner per console – rotating cartridge
  or handheld with your game on it, the plate with title and release year in all
  13 console languages, the console's jingle and the bevelled VC icon frame.
- **Artwork and metadata automatically:** box art, title screens, logos,
  release year and publisher from the libretro databases, matched by checksum;
  ROM hacks and games missing there get their cover from
  [RetroGamesNexus](https://retrogamesnexus.com).
- **GBA saves that work,** including 1 Mbit flash games like Pokémon
  Ruby/Sapphire/Emerald/FireRed/LeafGreen; save types are detected
  automatically.
- **No title ID clashes:** a local register of every build, plus an import of
  your console's ticket database.
- **Setup assistant and self-healing:** everything needed is checked on every
  start; missing or damaged parts are set up again, and a build that hits a
  missing part asks for exactly that and retries.

## What you need

- A 3DS with custom firmware ([Luma3DS](https://3ds.hacks.guide)) and
  [FBI](https://github.com/TheRealZora/FBI-Reloaded) to install CIAs.
  Luma's signature patches (on by default) are required, as for every inject.
- macOS 12+, Windows 10/11 (64-bit) or Linux.
- Your own game dumps. vcinject contains **no** games, emulators, Nintendo
  banners or keys.

## Installation

### Download (recommended)

Get the installer for your system from the
[latest release](https://github.com/petter1/vcinject/releases/latest). It
contains everything, including makerom and bannertool; nothing else to install.

| System | File | How |
| --- | --- | --- |
| macOS, Apple silicon (M1 and newer) | `vcinject-…-macos-arm64.dmg` | open it, drag **vcinject** to **Applications** |
| macOS, Intel | `vcinject-…-macos-x86_64.dmg` | same |
| Windows 10/11 | `vcinject-…-windows-setup.exe` | run it and click through the setup |
| Ubuntu 22.04+, Debian 12+, Linux Mint … | `vcinject_…_amd64.deb` | `sudo apt install ./vcinject_…_amd64.deb` (pulls in the dependencies), or double-click it |

The app is not signed with a paid Apple / Microsoft certificate, so the system
warns the first time:

- **macOS:** "vcinject cannot be opened" → open **System Settings → Privacy &
  Security**, scroll down and click **Open Anyway** (once). Or in the
  Terminal: `xattr -dr com.apple.quarantine /Applications/vcinject.app`.
- **Windows:** "Windows protected your PC" → **More info → Run anyway**.

Then start vcinject from Launchpad / the Start menu / your app menu. On the
first start the **setup assistant** opens and walks you through the rest.
On Linux the command line is available as `vcinject` as well.

### From source (macOS, Linux)

Needs Python 3.9+, git, make and a C/C++ compiler (macOS:
`xcode-select --install`; Debian/Ubuntu:
`sudo apt install build-essential git python3-venv`).

```sh
git clone https://github.com/petter1/vcinject.git
cd vcinject
./scripts/install.sh        # builds makerom + bannertool, creates .venv, installs vcinject
```

`./scripts/install.sh --cli` skips the Qt GUI. The script only touches the
project folder (`bin/`, `build/`, `.venv/`); user data lives in
`~/Library/Application Support/vcinject` (macOS), `%APPDATA%\vcinject`
(Windows) or `~/.local/share/vcinject` (Linux), for the installed app as well.

Start the GUI with `./start-gui.command` (on macOS also by double-clicking it in
Finder) or `.venv/bin/vcinject gui`. On the first start the **setup assistant**
opens and walks you through the rest.

## First-time setup

The assistant (GUI: **Setup…**, terminal: `vcinject doctor --fix`) shows every
part and its state:

| Part | Needed for | Where it comes from |
| --- | --- | --- |
| makerom, bannertool | everything | built by `scripts/build-tools.sh` |
| GBA base template | GBA | downloaded (pinned, SHA-256 checked) or taken from a GBA VC title you own |
| VC Console Look per console | the 3D banner (optional) | the banner of a VC title or inject you own, e.g. an official VC game dumped with GodMode9 or an NSUI v28+ inject |
| Emulator template per system | SNES, NES, GB/GBC, Mega Drive | an existing emulator inject you own, e.g. made with NSUI |
| ROM folder | the library (optional) | your folder |

To dump one of your installed VC titles as a template: boot GodMode9 (hold
START while powering on) → HOME → *Title manager* → `[A:] SD CARD` → the title
→ *Build CIA (standard)*; the file appears in `gm9/out` on the SD card.

No Game Boy VC title at hand? **Create from GBA look** derives Game Boy and
Game Boy Color looks (grey and dark cartridges, the Game Boy start-up sound)
until you install an official one.

### Backing up your templates

Templates come from your own games, so they are never part of this repository.
To move them to another computer, export them once and import them there:

```sh
vcinject templates export ~/vcinject-templates.zip   # GUI: last page of the setup assistant
vcinject templates import ~/vcinject-templates.zip   # GUI: first page of the setup assistant
```

Keep the backup private (e.g. iCloud Drive or a USB stick) – don't share or
upload it.

## Using the GUI

1. Choose your ROM folder in the **Library** (or drop a ROM on the window).
   Arrow keys browse; the preview shows the HOME Menu icon and the rotating
   banner as they will appear.
2. Adjust title, publisher, release year or artwork if you like. **Advanced**
   holds the title ID, save type, plate language, LCD ghosting and brightness.
3. **Install via QR** builds the CIA and shows a QR code (click it to enlarge).
   On the 3DS: **FBI → Remote Install → Scan QR Code**. Double-clicking a game
   in the library does all of this in one go.

The library remembers what it found out about each file – release, year,
checksum and a small cover – so unchanged files appear instantly on the next
start without being read again; renamed or moved files are recognised by their
checksum. Nothing expires: selecting a ROM checks it online again in the
background (once per session, can be switched off in the **↻** menu),
right-click → *Reload cover and info* does it on demand, and **↻ → Reload all
covers and info** refreshes the whole library.

**Show missing games** in the library lists every game of the system from the
No-Intro database below your ROMs, with a region filter and an
"owned X of Y" count. Covers of missing games are loaded for what is on screen
and shown greyed out or in black & white (your choice).

**One entry per game:** language versions and copies of a game are folded
into one entry ("3 versions"); the version menu under the ROM picks which one
to build. Copies of the same release are flagged – bit-identical or not – and
**Versions and duplicates…** (right-click) moves the spare ones to the trash.

**Sort and series:** sort by name, release year, system or folder. Series
are recognised from the titles (e.g. all Pokémon games); the series menu
filters by one (*Pokemon (5/7)* = owned/known) or by compilations. With
**Group series** on, each series is shown as one stack of covers – click it to
open it in place, click again to close it. With missing games shown, *Owned
first* keeps your own games on top or mixes them into the order.

**Zipping:** **Zip** packs every unpacked ROM in the library into its own
.zip file next to it – one game per zip, named like the ROM
(`Golden Sun (Europe).gba` → `Golden Sun (Europe).zip`), never one archive for
the whole folder. Each original is deleted only after its zip was read back
and verified; zips that already exist are left alone. vcinject reads zipped
ROMs directly, so nothing needs unpacking to build an inject.

**File names:** **Names** renames identified ROMs to their official release
name – optionally after every scan – and right-click → *Self-dumped* marks a
ROM as dumped from your own cartridge by adding ` - selfdumped` to its file
name (the mark is kept when renaming). Zipped ROMs are renamed inside, too.

**⧉** opens the library in its own resizable window with three views:
**Grid** (large covers), **Details** (a table with system, year, series,
release, folder, size and status; click a column header to sort, stacks are
tree nodes) and **Cover Flow** (arrow keys, mouse wheel or the scroll bar;
Enter or a double-click builds and installs). Clicking a game there opens it in the main
window as usual.

## Command line

```sh
vcinject build "Golden Sun.gba" --qr        # any supported ROM; build and install via QR
vcinject build "Mega Man X.sfc" -o mmx.cia  # just build
vcinject library ~/ROMs                     # list a ROM folder (zipped ROMs included)
vcinject library ~/ROMs --zip               # zip each game individually (one .zip per ROM)
vcinject library ~/ROMs --clean-names       # rename to release names, report duplicates
vcinject doctor --fix                       # check and repair the setup
vcinject setup --banner-donor vc_title.cia  # install a VC Console Look
vcinject setup --forwarder-donor inject.cia # install an emulator template
vcinject import-tickets ticket.db           # reserve title IDs used on your 3DS
vcinject templates export backup.zip        # back up all templates (keep it private)
vcinject list                               # everything built so far
vcinject serve a.cia b.cia                  # serve existing CIAs via QR
```

Useful `build` options: `--title`, `--publisher`, `--released`,
`--icon`/`--banner` (own images), `--icon-style`/`--banner-style`,
`--save-type`, `--title-id`, `--ghosting`, `--brightness`, `--plate-lang`,
`--offline`. Run `vcinject build --help` for all of them.

## Saves

- **GBA:** AGB_FIRM writes the save when you leave the game with **HOME → A**;
  on the next normal boot the 3DS copies it to the SD card, where Checkpoint can
  back it up. Exit games that way, not by holding POWER.
- **Emulator forwarders:** saves live on the SD card in
  `nsui_formwarders_data/<system>_<id>_<title>/` (compatible with NSUI injects).
- Rebuilding a game keeps its title ID, so installing the new CIA updates the
  title and keeps the save.

If a GBA game doesn't save, set its save type by hand under **Advanced**. The
GBA real-time clock is reported not to run under AGB_FIRM.

## Avoiding title IDs already on your 3DS

vcinject never reuses a title ID of something it built. To also respect titles
from other sources, import your console's ticket database: in GodMode9 copy
`[1:] SYSNAND CTRNAND/dbs/ticket.db` to `[0:] SDCARD/gm9/out`, move it to your
computer and use **Import tickets…** (or `vcinject import-tickets ticket.db`).

## Troubleshooting

| Problem | Try |
| --- | --- |
| Something is missing or a template seems broken | `vcinject doctor --fix` or **Setup…**; damaged files are moved aside as `*.broken-<time>` and can be set up again |
| The 3DS can't download via QR | same Wi-Fi? allow incoming connections in the macOS firewall; set `--host`/`--port` |
| A GBA game doesn't save | set the save type under **Advanced**, rebuild, reinstall |
| A GBA game always starts with "New Game" after a vcinject update | delete the title and its ticket in FBI, then reinstall |
| Install fails with `0xC8E083FC` ("already exists") | delete the old title and ticket in FBI first, or use another title ID |

## How it works

A GBA VC title is an ordinary 3DS application whose `ExeFS:/.code` holds the
GBA ROM followed by a 0x360-byte `.CAA` footer (save type, save chip timings,
LCD ghosting, colour LUT); the console boots it into AGB_FIRM. vcinject builds
the footer, SMDH icon, ExeFS, RomFS and the unencrypted NCCH with all hashes in
Python. Only the GBA exheader (whose access descriptor Nintendo signed), the
boot logo and an empty RomFS come from an official title. The SD save size is
set per save type (two slots of 0x200 + save size; 0x40400 for 1 Mbit flash),
matching NSUI. The generated footers are byte-identical to official ones.

Emulator forwarders keep the emulator of the template inject and swap the ROM,
the title ID and the save folder name in the RomFS; rebuilding NSUI injects
this way reproduces them byte for byte.

The VC Console Look edits textures inside the template banner (CBMD/CGFX,
LZ11, PICA200 texture formats including ETC1): the game picture is placed into
the model's screen area – a frame, a box or a device display, detected
automatically – and the "Virtual Console" plate is redrawn per language.

`makerom` ([Project_CTR](https://github.com/3DSGuy/Project_CTR)) wraps the
result into a CIA, `bannertool` ([3ds-bannertool](https://github.com/carstene1ns/3ds-bannertool))
builds flat banners.

## Development

```sh
.venv/bin/python -m pip install -e '.[gui,dev]'
.venv/bin/pytest
```

| Module | |
| --- | --- |
| `gba.py` | GBA ROM parsing, save detection, footer |
| `ncch.py`, `romfs.py`, `smdh.py`, `cgfx.py` | 3DS container and banner formats |
| `vcbanner.py` | VC Console Look |
| `forwarder.py` | emulator forwarders |
| `artwork.py`, `library.py` | databases, artwork, ROM library |
| `registry.py`, `tickets.py` | title IDs |
| `health.py` | setup checks and self-healing |
| `builder.py` | build pipeline |
| `cli.py`, `gui/app.py` | front ends |

A hardware test plan is in [docs/TESTPLAN.md](docs/TESTPLAN.md).

**Installers:** `python packaging/build_app.py` builds the one for the current
system into `dist/` (needs `pip install pyinstaller`, plus Inno Setup on
Windows). Pushing a tag `v1.2.3` builds all of them on GitHub Actions and
publishes a release; *Actions → Installers → Run workflow* builds them without
releasing.

In the installed app, the command line is the app binary with arguments, e.g.
`/Applications/vcinject.app/Contents/MacOS/vcinject doctor` (macOS) or
`vcinject doctor` (Linux).

## Credits

- [3dbrew](https://www.3dbrew.org) – file format documentation
- [open_agb_firm / libn3ds](https://github.com/profi200/open_agb_firm) – save chip timings and detection
- [vcoven](https://github.com/vedoot/vcoven) – GBA template and NCCH notes
- [NSUI Banner Fixer](https://github.com/pivotiiii/nsui_banner_fixer) – banner plate languages
- [libretro-database](https://github.com/libretro/libretro-database) and
  [libretro thumbnails](https://thumbnails.libretro.com) – game data and artwork
- [RetroGamesNexus](https://retrogamesnexus.com) – fallback covers, e.g. for ROM hacks
- [Project_CTR](https://github.com/3DSGuy/Project_CTR), [3ds-bannertool](https://github.com/carstene1ns/3ds-bannertool)

## Legal

vcinject is not affiliated with Nintendo. It ships no games, emulators,
firmware, keys or Nintendo artwork; templates are taken from titles you own.
Only use it with games you own.

## License

[MIT](LICENSE)

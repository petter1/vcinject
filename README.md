# vcinject

**Virtual Console injects for the Nintendo 3DS – on macOS, Windows and Linux.**

vcinject turns your own game backups into installable 3DS titles that look and
behave like Nintendo's Virtual Console releases: their own HOME Menu icon, the
rotating 3D banner with the "Virtual Console" plate, the console's jingle and
working saves. It is an open, cross-platform alternative to the Windows-only
[New Super Ultimate Injector (NSUI)](https://gbatemp.net/threads/discussion-new-super-ultimate-injector-nsui.500376/),
with a GUI and a command line built on the same Python core.

With **vcinject Link**, a small app for the 3DS that comes with it, vcinject
also looks after your consoles over Wi-Fi: it installs games on one or all of
them with a click, shows what is installed and what could be updated,
backs up saves and arranges the HOME Menu.

| System | How it runs | |
| --- | --- | --- |
| Game Boy Advance | natively on the 3DS's GBA hardware (AGB_FIRM) | ✅ |
| Super Nintendo | snes9x_3ds forwarder | ✅ needs an emulator template |
| Mega Drive / Genesis | RetroArch forwarder | ✅ needs an emulator template |
| Game Boy / Color | RetroArch forwarder; Nintendo's own VC emulator is experimental (see below) | ✅ needs an emulator template |
| NES | VirtuaNES / RetroArch forwarder | ✅ needs an emulator template |
| NES with Nintendo's own VC emulator | | planned |

**Game Boy with Nintendo's emulator (experimental):** the emulator from an official
GB/GBC VC title runs the game, but it expects its save data (`sav.dat`) to exist
already – the eShop creates it, FBI does not – so a fresh install stops with
"the SD card was removed". Until that is solved, Game Boy games use RetroArch by
default (Advanced → Emulator).

**Planned: Wii and Wii U.** Besides the 3DS, vcinject is meant to also build
Virtual Console injects for the **Wii** (WAD channels) and the **Wii U**
(installable with WUP Installer), from the same library and artwork.

## Features

- **One click from ROM to console:** choose a game, click **Install on
  Petter1 (3DS XL)** – vcinject builds the CIA and installs it over Wi-Fi on
  the 3DS that runs vcinject Link (or on all of them). A QR code for FBI still
  works for consoles without vcinject Link.
- **Your collection as a cover gallery:** point vcinject at your ROM folder
  (sub folders included); every game shows on which 3DS it is installed, and
  its page has a short description in your language (from RetroGamesNexus or
  Wikipedia) – games you don't have yet too.
  Zipped ROMs (one game per .zip) work like unpacked ones; optionally the
  No-Intro database shows what your collection is missing.
- **My 3DS:** one card per console; per console a status report (system,
  Luma3DS, vcinject Link and homebrew up to date, sound firmware, SD space)
  with *Update* and *Update all*, mirrored on the 3DS's own screen; the
  installed titles as a game database with what FBI offers – start a title,
  back up and restore its save, save its icon and banner, delete it; and the
  **HOME Menu** as on the console, to arrange games and apps.
- **VC Console Look:** the official 3D banner per console – rotating cartridge
  or handheld with your game on it, the plate with title and release year in all
  13 console languages, the console's jingle and the bevelled VC icon frame.
- **Artwork and metadata automatically:** box art, title screens, logos,
  release year and publisher from the libretro databases, matched by checksum;
  ROM hacks and games missing there get their cover from
  [RetroGamesNexus](https://retrogamesnexus.com) – Pokémon hacks even when the
  file name says nothing, by the name they show in their own text.
- **GBA saves that work,** including 1 Mbit flash games like Pokémon
  Ruby/Sapphire/Emerald/FireRed/LeafGreen; save types are detected
  automatically.
- **No title ID clashes:** a local register of every build; the titles on
  every 3DS that runs vcinject Link are taken into account automatically.
- **Setup as one checklist** that heals itself: everything needed is checked on
  every start; missing or damaged parts are set up again, and a build that hits
  a missing part asks for exactly that and retries.

## What you need

- A 3DS with custom firmware ([Luma3DS](https://3ds.hacks.guide)); its
  signature patches (on by default) are required, as for every inject.
  [FBI](https://github.com/TheRealZora/FBI-Reloaded) installs vcinject Link
  once (QR code); after that vcinject installs over Wi-Fi by itself.
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

## What your 3DS needs

- **Every inject:** [Luma3DS](https://3ds.hacks.guide) (its signature patches are on
  by default) and FBI to install the `.cia`.
- **GBA** and **Game Boy with Nintendo's emulator:** nothing else.
- **SNES (snes9x) and NES (VirtuaNES) forwarders:** nothing else – the emulator is
  inside each `.cia`; saves go to `sdmc:/nsui_forwarders_data/<game>`.
- **RetroArch forwarders** (Game Boy via RetroArch, Mega Drive): the 3DS's sound
  firmware `sdmc:/3ds/dspfirm.cdc`, dumped once with **DSP1** – otherwise they have
  no sound or don't start. RetroArch itself does not need to be installed.

**Homebrew** in the GUI (or `vcinject homebrew`) lists this and installs
homebrew over Wi-Fi or by QR code, downloaded from each author's latest GitHub release and only
for projects with an open-source licence: DSP1, FBI Reloaded, Universal-Updater,
Checkpoint, PKSM, ftpd and Anemone3DS.

```sh
vcinject homebrew                # what the 3DS needs, and the apps
vcinject homebrew dsp1 --qr      # download DSP1 and show a QR code for FBI
```

## First-time setup

**Setup** in the GUI (terminal: `vcinject doctor --fix`) shows every part and
its state:

| Part | Needed for | Where it comes from |
| --- | --- | --- |
| makerom, bannertool | everything | built by `scripts/build-tools.sh` |
| GBA base template | GBA | downloaded (pinned, SHA-256 checked) or taken from a GBA VC title you own |
| VC Console Look per console | the 3D banner (optional) | the banner of a VC title or inject you own, e.g. an official VC game dumped with GodMode9 or an NSUI v28+ inject |
| Emulator template per system | SNES, NES, GB/GBC, Mega Drive | an existing emulator inject you own, e.g. made with NSUI |
| Nintendo's GB/GBC emulator | Game Boy / Color (instead of RetroArch) | an official GB or GBC VC title you own, e.g. Pokémon Blue / Gold (`vcinject setup --gbvc-donor title.cia`); the GBC one also runs GB games |
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

One window, four sections in the side bar:

- **Games** – your collection as a cover gallery; chips switch each console
  on and off. Click a game for its page, laid out like the Homebrew pages:
  cover and name, one line saying on which 3DS it is installed, the preview of
  the HOME Menu icon and the rotating banner, and the big button that installs
  it where **Install to** (top right) says – its menu offers each 3DS, all of
  them, the QR code for FBI or only the .cia. Title, artwork and the advanced settings
  (title ID, save type, plate language, LCD ghosting, brightness) are folded
  away under *Customize*. Double-clicking a game in the gallery installs it
  right away; **Build CIA** only writes the file.
- **My 3DS** – one card per console that runs vcinject Link, each saying
  what could be updated (read in the background; *Refresh* reads again). Open one for
  its **Overview** (status report with *Update* / *Update all*, and the
  activity log, the same as on the 3DS), its **Titles** (tiles with filters
  and search; start a title, back up or restore its save, save icon and
  banner, delete) and its **HOME Menu**. After updates that need a restart,
  vcinject offers it, reconnects by itself and says whether everything
  worked.
- **Homebrew** – apps for the 3DS like a small store. *Recommended* holds the
  apps vcinject itself suggests; *All*, *Games*, *Utilities*, *Emulators* … list
  every 3DS app of [Universal-DB](https://db.universal-team.net), the database
  of Universal-Team's Universal-Updater (over 200 with a .cia), searchable and
  sorted by popularity, with a check mark on what is installed on one of your
  3DSs. Each app has cover, description and screenshots, the version on each
  3DS and one button that installs or updates where needed – over Wi-Fi or by
  QR code, straight from the authors' releases. Apps without a known open
  licence link to their author instead. **News** lists the latest releases of
  the recommended apps, Luma3DS and vcinject.
- **Setup** – everything vcinject needs as one checklist, each part with its
  state and a button to set it up.

On the first start with a ROM folder, **Download game covers** offers to
fetch every cover – your games and every missing game of the same consoles –
or only the covers of your own games, with a progress bar (about 20 covers a
second); *Continue on next startup* stops and carries on next time without
fetching anything twice.

The library remembers what it found out about each file – release, year,
checksum and a small cover – so unchanged files appear instantly on the next
start without being read again; renamed or moved files are recognised by their
checksum. Nothing expires: selecting a ROM checks it online again in the
background (once per session; *Check covers on selection* switches it off),
right-click → *Reload cover and info* does it on demand, and **Covers**
refreshes the whole library – it only asks the servers whether each picture
changed (ETag) and downloads just the ones that did. The game lists and the covers of missing games
are cached the same way. **Rescan** reads the folder again.

**Dump check:** every ROM is checked against the known good dumps – the
No-Intro checksums (SHA-1, via libretro) and, for Game Boy, Color and Advance,
the database of [FlashGBX](https://github.com/lesserkuma/FlashGBX), the GBxCart
RW software (it finds the cartridge by its header and compares CRC32 and
size). A ROM is *verified*, *differs from the known dump* (bad dump or
modified) or *unverified* (in no list: hacks, translations, homebrew).
`vcinject verify <files or folders>` does the same on the command line.

Under the ROM, vcinject shows its **CRC32** and cartridge **serial** – the
keys No-Intro and libretro identify a dump by – with links to the release's
entry in the libretro database and to the game's page on RetroGamesNexus; both
are also in the right-click menu and the Details view. Games without any cover
get a box in their console's style.

**Show missing games** lists every game of the system from the No-Intro
database next to your ROMs, with a region filter and an "owned X of Y" count;
covers of missing games are shown greyed out or in black & white.

**One entry per game:** language versions and copies of a game are folded
into one entry ("3 versions"); the version menu under the ROM picks which one
to build. Copies of the same release are flagged – bit-identical or not – and
**Versions and duplicates…** (right-click) moves the spare ones to the trash.

**View options** (next to the search field) holds the filters, sorting and
display settings; the gallery can also show **Details** (a sortable table) or
**Cover Flow**. With *Version symbols on covers*, each cover shows the regions
of the versions you have (DE, US/EU, JP …, DE×2 for two copies); purple
labels in the top right corner name the 3DS a game is installed on.

**Sort and series:** sort by name, release year, system or folder. Series
are recognised from the titles (e.g. all Pokémon games); the series menu
filters by one (*Pokemon (5/7)* = owned/known) or by compilations. With
**Group series** on, each series is shown as one stack of covers – click it to
open it in place. *Owned first*, *Only series I own* and *Betas & prototypes*
shape the list of missing games.

**Zipping and file names:** **Zip** packs every unpacked ROM into its own .zip
next to it (each original is deleted only after its zip was verified);
**Names** renames identified ROMs to their official release name, and
right-click → *Self-dumped* marks a ROM as dumped from your own cartridge.

## Command line

```sh
vcinject build "Golden Sun.gba" --qr        # any supported ROM; build and install via QR
vcinject build "Mega Man X.sfc" -o mmx.cia  # just build
vcinject library ~/ROMs                     # list a ROM folder (zipped ROMs included)
vcinject library ~/ROMs --zip               # zip each game individually (one .zip per ROM)
vcinject library ~/ROMs --clean-names       # rename to release names, report duplicates
vcinject verify ~/ROMs                      # check dumps against No-Intro and FlashGBX
vcinject doctor --fix                       # check and repair the setup
vcinject setup --banner-donor vc_title.cia  # install a VC Console Look
vcinject setup --forwarder-donor inject.cia # install an emulator template
vcinject import-tickets ticket.db           # reserve title IDs used on your 3DS
vcinject templates export backup.zip        # back up all templates (keep it private)
vcinject list                               # everything built so far
vcinject serve a.cia b.cia                  # serve existing CIAs via QR
vcinject 3ds titles                         # what is installed (vcinject Link)
```

Useful `build` options: `--title`, `--publisher`, `--released`,
`--icon`/`--banner` (own images), `--icon-style`/`--banner-style`,
`--save-type`, `--title-id`, `--ghosting`, `--brightness`, `--plate-lang`,
`--offline`. Run `vcinject build --help` for all of them.

## Managing the 3DS over Wi-Fi (vcinject Link)

vcinject ships a small 3DS app, **vcinject Link**. Install it once
(**Setup → Install vcinject Link…** or `vcinject 3ds setup` shows a QR code for
FBI) and start it from the HOME Menu: vcinject finds the 3DS in the same Wi-Fi
by itself, and every 3DS that runs it shows up under **My 3DS** and in
**Install to**. On the 3DS, vcinject Link shows what vcinject is doing, the
status report and an activity log; it updates and restarts itself when
vcinject asks.

What it can do: install CIAs (several 3DSs at once), list titles with their
icons, start a title, delete titles, back up and restore saves, check and
install updates of Luma3DS and homebrew, restart the 3DS, and read and arrange
the HOME Menu.

**HOME Menu:** games and apps on the SD card can be arranged and moved into
the existing folders; *Save to 3DS* backs up the current layout on the
computer, writes the new one and restarts the 3DS (the HOME Menu reads it only
when it starts), then checks that every icon is where it was put. Folders,
system apps and the Game Card stay where they are for now: the 3DS keeps their
file locked while it runs (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md);
a GodMode9 script is planned, [docs/TODO.md](docs/TODO.md)).

```sh
vcinject 3ds setup                 # install vcinject Link by QR code
vcinject 3ds find                  # 3DSs running vcinject Link
vcinject 3ds titles                # installed games and apps
vcinject 3ds install a.cia b.cia   # install over Wi-Fi
vcinject 3ds delete 000400000F783500
vcinject 3ds restart-app           # restart vcinject Link (e.g. after updating it)
```

vcinject Link is built from [companion/](companion/) with
`scripts/build-companion.sh` (devkitARM, or Docker); the built CIA is part of
vcinject.

## Saves

- **GBA:** AGB_FIRM writes the save when you leave the game with **HOME → A**;
  on the next normal boot the 3DS copies it to the SD card, where Checkpoint can
  back it up. Exit games that way, not by holding POWER.
- **Emulator forwarders:** saves live on the SD card in
  `nsui_formwarders_data/<system>_<id>_<title>/` (compatible with NSUI injects).
- Rebuilding a game keeps its title ID, so installing the new CIA updates the
  title and keeps the save.
- **My 3DS → Titles → Back up save now** copies a title's save data to this
  computer (one zip per console and title); *Restore a save…* puts one back.

If a GBA game doesn't save, set its save type by hand under **Advanced**. The
GBA real-time clock is reported not to run under AGB_FIRM.

## Avoiding title IDs already on your 3DS

vcinject never reuses a title ID of something it built, nor one installed on
a 3DS that runs vcinject Link – it reads them each time it connects. For a 3DS
without vcinject Link, import its ticket database: in GodMode9 copy
`[1:] SYSNAND CTRNAND/dbs/ticket.db` to `[0:] SDCARD/gm9/out`, move it to your
computer and use **Setup → Import ticket.db…** (or `vcinject import-tickets ticket.db`).

## Troubleshooting

| Problem | Try |
| --- | --- |
| Something is missing or a template seems broken | `vcinject doctor --fix` or **Setup**; damaged files are moved aside as `*.broken-<time>` and can be set up again |
| The 3DS can't download via QR | same Wi-Fi? allow incoming connections in the macOS firewall; set `--host`/`--port` |
| My 3DS finds no 3DS | vcinject Link open on it, both in the same Wi-Fi? Its bottom screen shows its address: *Connect by IP…* |
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

How it fits together – components, the vcinject Link protocol, where data is
kept, the HOME Menu formats and the decisions behind them – is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); a hardware test plan is in
[docs/TESTPLAN.md](docs/TESTPLAN.md), open work in [docs/TODO.md](docs/TODO.md).

**Git workflow:** main stays linear. Work happens on a branch per feature
(`feature/<name>`, `fix/<name>`); when it is done, its commits are tidied up,
rebased onto main and main is fast-forwarded – no merge commits. Pull with
rebase so local commits never create merges:

```sh
git config pull.rebase true
git config rebase.autoStash true
```

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
- [FlashGBX](https://github.com/lesserkuma/FlashGBX) – database of verified Game Boy dumps
- [Project_CTR](https://github.com/3DSGuy/Project_CTR), [3ds-bannertool](https://github.com/carstene1ns/3ds-bannertool)
- [devkitPro](https://devkitpro.org) – devkitARM, libctru and citro2d for vcinject Link
- [Luma3DS](https://github.com/LumaTeam/Luma3DS) – the process information vcinject Link reads
- [Universal-DB](https://db.universal-team.net) by [Universal-Team](https://github.com/Universal-Team) –
  the list of 3DS homebrew, its pictures and descriptions (the database of Universal-Updater)
- [3DSort](https://github.com/SalustLab/3DSort), [Checkpoint](https://github.com/BernardoGiordano/Checkpoint),
  [Cthulhu](https://github.com/Ryuzaki-MrL/Cthulhu), [GodMode9](https://github.com/d0k3/GodMode9) –
  references for the HOME Menu save files, save data access and scripting (no code taken)

## Legal

vcinject is not affiliated with Nintendo. It ships no games, emulators,
firmware, keys or Nintendo artwork; templates are taken from titles you own.
Only use it with games you own.

## License

[MIT](LICENSE)

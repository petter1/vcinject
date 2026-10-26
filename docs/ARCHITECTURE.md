# vcinject – technical documentation

How vcinject is put together: the two programs, the protocol between them,
where data is kept, the 3DS formats it reads and writes, and why things are
done the way they are. User documentation is in the [README](../README.md).

## Two programs

```
 computer                                         3DS (Luma3DS)
┌───────────────────────────────┐   Wi-Fi    ┌─────────────────────────────┐
│ vcinject (Python)             │            │ vcinject Link (C, libctru)  │
│  GUI (Qt) ─┐                  │  UDP 34013 │  answers "vcinject?"        │
│  CLI ──────┼─ core modules ───┼──────────► │  serves requests over TCP   │
│            │  remote.py       │  TCP 34013 │  draws its screens (citro2d)│
└────────────┴──────────────────┘ ◄────────► └─────────────────────────────┘
```

- **vcinject** builds injects (CIAs) and manages consoles. One Python core;
  the CLI (`cli.py`) and the GUI (`gui/`) are thin front ends on top of it.
- **vcinject Link** ([companion/](../companion/)) is a 3DS app that only moves
  raw data: titles, icons, files, installs. Everything is parsed and decided
  on the computer, where it can be tested; the 3DS side stays small.

## Core modules (`src/vcinject/`)

| Module | Does |
| --- | --- |
| `builder.py` | the build pipeline: ROM → icon, banner, ExeFS, RomFS, NCCH → CIA (makerom) |
| `gba.py`, `forwarder.py`, `gbvc.py` | GBA (AGB_FIRM footer, saves), emulator forwarders, Nintendo's GB/GBC emulator |
| `ncch.py`, `romfs.py`, `smdh.py`, `cgfx.py`, `vcbanner.py` | 3DS container, icon and banner formats; the VC Console Look |
| `artwork.py`, `library.py`, `dumpdb.py`, `nexus.py` | game databases, covers, the ROM library, dump verification |
| `registry.py`, `tickets.py` | title IDs: every build, plus IDs taken on consoles |
| `template.py`, `health.py`, `tools.py` | templates, setup checks and self-healing, makerom / bannertool |
| `serve.py` | the HTTP server behind the QR code for FBI |
| `remote.py` | the client of vcinject Link: discovery, connection, every command |
| `status.py` | the status report of a console and the updates it offers |
| `homebrew.py` | the homebrew catalog: releases, licences, downloads |
| `homemenu.py`, `disa.py` | the HOME Menu layout; reading save data containers |
| `saves.py` | backing up and restoring save data of titles |
| `installed.py` | which game of the library is installed on which 3DS |
| `about.py` | a game's description: RetroGamesNexus (in the user's language, with genres), else Wikipedia |

## The GUI (`src/vcinject/gui/`)

`app.py` holds the main window: a side bar (`nav` buttons) and a
`QStackedWidget` of sections, created when first opened.

| Section | Module | Notes |
| --- | --- | --- |
| Games | `app.py`, `libraryview.py` | the library (`LibraryPanel(grid=True)`) as gallery; a game's page (the former main window: drop zone, *On your 3DS*, *About the game*, *Customize*, preview, Install); `MissingGamePage` for games not in the folder |
| My 3DS | `console_view.py` | `DevicesPage` (a card per console) → `ConsoleWindow` pages: Overview (`MirrorPanel`), Titles (tiles, FBI-like actions), HOME Menu (`HomeGrid`) |
| Homebrew | `homebrew_view.py` | the catalog; install over Wi-Fi or by QR code |
| Setup | `setup_view.py` | `health.check()` as a checklist |

- **`DevicesPage`** reads each online console's titles and status report in
  the background (on finding it, every 10 minutes, or *Refresh*) for the
  summary on its card; the console open in the window is left alone, as
  vcinject Link serves one connection at a time.
- **`wifi_install.py`**: `ConsoleWatcher` (shared) asks the network every 10 s
  which consoles run vcinject Link (broadcast only); a console stays online for
  35 s after its last answer, as a busy vcinject Link does not answer
  broadcasts. It reads each console's title list in the background at most
  every 5 minutes (→ `installed.py`). `InstallTarget` is the global *Install
  to*; `install()` installs on several consoles in turn.
- **Never blocking**: work runs in `Task`/`Worker` threads (`app.py`). In
  `ConsoleWindow`, `_run()` counts running jobs; while any runs, only buttons
  that need the 3DS are disabled. Progress goes through signals (`_progress`,
  `_icons_arrived`) to a bar in the window, in the Overview and on the 3DS.
- **After a restart** (vcinject Link updated, Luma3DS updated, HOME Menu
  saved), `_await_console()` looks for *that* console (by device ID) for up to
  ten minutes, loads it as usual and then runs a check (`_updated`,
  `_arranged`) whose outcome is shown in the window and on the 3DS.

## vcinject Link

| File | |
| --- | --- |
| `companion/source/main.c` | sockets, discovery, the commands |
| `companion/source/ui.c` | the two screens: status bar (version, Wi-Fi, battery, clock), what is going on with a progress bar, status report, activity log |
| `companion/source/version.h` | `VERSION`; vcinject expects `remote.LINK_VERSION` and offers an update when the app is older |
| `companion/meta/app.rsf` | makerom settings: all FS access categories, `FsMountNand` (NAND and its system saves), services (`am:net`, `soc:U`, `ptm:sysm` …) |

`scripts/build-companion.sh` compiles with devkitARM (installed, or the
`devkitpro/devkitarm` Docker image) and packs the CIA with makerom and
bannertool into `src/vcinject/data/vcinject-link.cia`, which ships with
vcinject. Restarting itself (after an update) and starting a title both use
`aptSetChainloader`: the app quits and the HOME Menu starts the title.

## Protocol

**Discovery:** a UDP datagram `vcinject?` to port 34013 (broadcast, the /24
broadcast address and every console seen before; repeated every 0.4 s, as a
3DS in power saving misses broadcasts). The answer is `vcinject-link\n`
followed by `key=value` lines – the same as command 1. When no one answers,
vcinject tries a TCP connection to port 34013 on every address of the /24
network.

**Requests** over TCP, little endian: `u32 command, u32 length, payload`.
**Answers**: `u32 result, u32 length, payload` – result 0 is success, else a
3DS `Result` or one of vcinject Link's own codes (`0xE0000001` bad request,
`…02` out of memory, `…03` too large, `…04` unknown command). One connection
is served at a time; requests on it run one after the other.

| # | Command | Payload | Answer |
| --- | --- | --- | --- |
| 1 | info | – | `app, version, name, region, model, new3ds, system, id` (device ID), `luma, battery, charging, sd_free, sd_total` |
| 2 | titles | `u8 media[, u8 product codes]` | 40 bytes per title: `u64 id, u64 size, u16 version, u8 media, 5×pad, char product_code[16]` |
| 3 | SMDH | `u8 media, u64 id` | the title's SMDH |
| 4 | read file | archive (below), file path | the file |
| 5 | write file | archive, file path, `u32 flags` (1 = commit save data), bytes | – |
| 6 | list directory | archive, path | per entry `u8 is_dir, 3×pad, u64 size, u32 name length, UTF-8 name` |
| 7 | install | `u8 media`, then the CIA, streamed into AM | – |
| 8 | delete | `u8 media, u64 id` | – |
| 9 | reboot | – | – (then `APT_HardwareResetAsync`) |
| 10 | report | text: `from\t<computer>` and `level\ttitle\tdetail` lines | – (shown on the 3DS) |
| 11 | note | text | – (a line in the activity log) |
| 12 | SMDHs | `u8 media, u64 id` per title | per title `u32 size` (0: none) + SMDH |
| 13 | progress | `u32 done, u32 total`, text | – (bar on the 3DS; total 0 clears it) |
| 14 | restart | – | – (quits; the HOME Menu starts it again) |
| 18 | launch | `u8 media, u64 id` | – (quits; the HOME Menu starts the title) |

An *archive* is `u32 archive ID` + an FS path (`u32 type, u32 length,
bytes`; type 1 = empty, 2 = binary, 3 = ASCII, 4 = UTF-16). Commands 15–17
were experiments and are unused. vcinject stays compatible with older
vcinject Link versions: unknown commands answer `0xE0000004`, and the client
falls back (icons one by one, no notes or progress) or asks for an update.

Archives vcinject uses:

| Archive | ID | Path | For |
| --- | --- | --- | --- |
| SD card | `0x9` | empty | files, Luma3DS `boot.firm`, `3ds/dspfirm.cdc` |
| Extdata | `0x6` | `media 1, extdata ID, 0` | HOME Menu `SaveData.dat` |
| System save data | `0x8` | `media 0, save ID` | `Launcher.dat` (locked while the HOME Menu runs) |
| NAND (CTR) | `0x567890AB` | empty | the HOME Menu's save container, read-only |
| Title content | `0x2345678A` | `ID low, ID high, media, 0`; file `{0, 0, 2, name}` | ExeFS `icon`, `banner`, `logo`, `.code` (decrypted) |
| User save data | `0x567890B2` | `media, ID low, ID high` | save backups |

## Data on the computer

Everything lives in the data folder (`~/Library/Application Support/vcinject`,
`%APPDATA%\vcinject`, `~/.local/share/vcinject`; `VCINJECT_DATA_DIR`
overrides it):

| Path | |
| --- | --- |
| `registry.json` | every title ID built or taken on a console (`source`: vcinject / tickets) |
| `templates/` | GBA template, VC looks, emulator templates (from the user's own titles) |
| `homebrew/` | release info (a day), downloaded CIAs, `title.json` per release (title ID and version) |
| `about/<language>/` | game descriptions, found or not (each game is looked up once) |
| `3ds/consoles.json` | every console seen: name, last address |
| `3ds/<device ID>/icons/` | SMDHs per title and version (empty file: the title has none) |
| `3ds/<device ID>/titles.json` | the console's installed titles, for *installed on* |
| `3ds/<device ID>/homemenu-backups/` | `SaveData.dat` + `Launcher.dat` before each HOME Menu write |
| `3ds/<device ID>/saves/<title ID>/` | save backups, one zip each |
| `3ds/<device ID>/boot.firm <time>` | the Luma3DS `boot.firm` replaced by an update |

The **device ID** (`AM_GetDeviceId`) keeps consoles apart; names are often
alike. Title IDs taken on any console go into the one register, so a new
inject never clashes with anything on any of them.

## The HOME Menu

The layout is in two files (3dbrew *Home Menu*); both use the same linear
positions per container (the main grid and each folder):

| File | Where | Holds |
| --- | --- | --- |
| `SaveData.dat` (v4, 0x2DA0) | extdata `0x000000xx` on the SD card | titles on the SD card: position, folder |
| `Launcher.dat` (≥ 0x2490) | system save `0x000200xx` on the NAND | system titles, Game Card slot, folders (position, name, rows) |

`xx` is `82` JPN, `8F` USA, `98` EUR/AUS, `A1` CHN, `A9` KOR, `B1` TWN.
`homemenu.Layout` keeps both files byte for byte and rewrites only the known
arrays; `validate()` refuses places taken twice and folders that don't exist.

**Reading `Launcher.dat`**: the running HOME Menu keeps its system save open
(`0xC92044E7`), so vcinject reads the save's container from the NAND and
unpacks it (`disa.py`): the current partition table (by the SHA-256 in the
header), the DPFS levels (two copies of every block; bitmaps of u32 words,
first block in the highest bit, choose the current one), the IVFC hash tree
(every block of level 4 checked against level 3) and the SAVE file system in
level 4 (file table, FAT; a node of several blocks names its last block in its
second entry).

**Writing**: only `SaveData.dat`, through FS, followed by a restart – the HOME
Menu reads it when it starts and does not write its own copy on the way down
(tested). Extdata files must be opened without `FS_OPEN_CREATE`
(`0xE0C046F8`). So a default `Layout` locks system titles, folders and the
Game Card. Tried and dropped on a real console:

- stopping the HOME Menu's process to free `Launcher.dat`: `ns` fails with a
  fatal error (`0xD960CF84`)
- changing the HOME Menu's copy of its files in memory (found with the debug
  SVCs Luma3DS allows): it is the load buffer – neither shown nor saved

What remains is GodMode9 (no Wi-Fi, full access): see [TODO.md](TODO.md).

## Status report and updates

`status.check()` compares the console's info with: the last system update
(11.17), the newest Luma3DS release (GitHub), `remote.LINK_VERSION`, and the
newest release of each catalog app – whose title ID and version come from the
first 64 KiB of its CIA (an HTTP range request; the TMD sits after header,
certificates and ticket). The report goes to the GUI and, as text, to the 3DS.
`status.apply()` installs an update and says what has to restart:
`RESTART_APP` (vcinject Link), `REBOOT` (Luma3DS: `boot.firm` written to the SD
card, read back and compared; the old one is kept on the computer) or nothing.

## Testing

- `pytest` (in CI on every push): `tests/test_remote.py` has `Fake3DS`, a TCP
  server speaking the protocol; `test_homemenu.py` and `test_saves.py` fake a
  link; `test_disa.py` builds a container with DPFS copies, a split FAT chain
  and a broken hash. GUI tests run Qt offscreen; tests never go online and the
  console watcher is switched off (`conftest.py`).
- **Azahar** (3DS emulator) runs vcinject Link: sockets map to the host, so
  `127.0.0.1:34013` works, installs and SD access too. It has no HOME Menu
  (nothing restarts or starts a title) and no real system saves.
- **Real consoles** (New 3DS XL and 3DS, 11.17, Luma3DS 13.x): discovery,
  installs, updates with self-restart, HOME Menu read and SD write, save
  reading – see [TESTPLAN.md](TESTPLAN.md).

3DS results met along the way:

| Result | Meaning here |
| --- | --- |
| `0xC8804478` | not found (archive or file); a title without save data |
| `0xC8A04555` | save data exists but is not formatted (title never started) |
| `0xC92044E7` | archive in use (a system save its owner keeps open) |
| `0xE0E046BE` | invalid argument – e.g. the wrong system save archive ID (`0x1234567C` instead of `0x8`) |
| `0xE0C046F8` | not supported – `FS_OPEN_CREATE` on extdata |
| `0xD9004676` | not allowed – a title's RomFS through the content archive |
| `0xD960CF84` | `ns` fatal error after the HOME Menu's process was stopped |

# TODO

## Next: rethink the whole GUI

vcinject grew window by window (build, library side panel and window, 3DS
Homebrew, My 3DS with tabs, QR panel, setup wizard). It should feel like one
piece and not overwhelm new users. Decide the structure first, then build the
items below into it.

## Wishes waiting for the new GUI

- **Titles as a game database**, not a table: covers / big icons, nice to
  browse, filters (games, Virtual Console, homebrew, system), search.
- **Titles clickable, with what FBI offers**: details, launch, delete, back up
  and restore save data, export icon / banner. Works over vcinject Link:
  - save data: `ARCHIVE_USER_SAVEDATA` (`0x567890B2`, path media / ID low / ID high)
    reads fine (Pokémon Ultra Moon: `main`, 445 KiB); unformatted saves answer
    `0xC8A04555`
  - launching needs a new vcinject Link command (`aptSetChainloader` + quit)
- **Recognise templates on the 3DS** ("first start, dump automatically"):
  - VC Console Look: works over Wi-Fi – ExeFS `banner`, `.code` and `logo`
    of installed titles read decrypted through `ARCHIVE_SAVEDATA_AND_CONTENT`
    (file path `{0, 0, 2, name}`); the system from `.code` (GBA) or the
    product code of injects (`SNES-`, `GEN-`, `GB-`, `GBC-`, `NES-`, `FDS-`)
  - forwarder templates and Nintendo's GB/GBC emulator need the exheader and
    RomFS, which FS does not hand out (`0xD9004676` for RomFS) → GodMode9

## Only with GodMode9

GodMode9 runs instead of the system: full access, but no Wi-Fi. vcinject Link
would put the data and a script on the SD card (`sdmc:/gm9/scripts/`), the
user starts GodMode9 (hold START) and runs it. Script commands checked against
GodMode9's samples: `allow -a 1:`, `sha <file> <hash>`, `imgmount`, `inject`,
`imgumount`, `fixcmac`, `if`/`elif`/`else`/`end`, `reboot`.

- **HOME Menu: folders, system apps, Game Card.** Launcher.dat is in the HOME
  Menu's system save (`1:/data/<SYSID0>/sysdata/000200xx/00000000`). Mounted,
  GodMode9 shows its inner file system as `D:/partitionA.bin` (writable, the
  IVFC hash chain is fixed on unmount); vcinject builds that image with the
  new Launcher.dat (`disa.py` reads the format). `fixcmac` afterwards.
  SaveData.dat in the same go, so the HOME Menu never starts with half a
  change: it is its own DIFF container in the extdata on the SD card
  (`A:/extdata/00000000/000000xx/...`); which file it is, the script finds by
  SHA-256.
- **Templates that need the exheader / RomFS**: forwarder templates from
  injects, Nintendo's GB/GBC emulator – GodMode9 can build a decrypted CIA of
  an installed title.

Tried and dropped (on a real console): stopping the HOME Menu's process to
write Launcher.dat (ns fails with a fatal error); patching the HOME Menu's
in-memory copy of its files (it neither shows nor saves it).

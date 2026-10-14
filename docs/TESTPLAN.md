# vcinject – Testplan auf der 3DS

Ziel: Jede Funktion einmal auf echter Hardware bestätigen. Bisher sind auf der
Konsole nur GBA-Injects mit 1-Mbit-Flash (Pokémon Smaragd) getestet, inklusive
Speichern. Alles andere ist nur am Rechner geprüft.

Legende: ☐ offen · ✅ ok · ❌ Fehler (Foto und Notiz in die Ergebnistabelle)

## 0. Vorbereitung

- ☐ Spielstände sichern: Checkpoint (normale Titel) und GodMode9 → `[S:] agbsave.bin`
  → *AGBSAVE options → Dump GBA VC save* (GBA)
- ☐ `vcinject info` zeigt: template, VC look (GBA, NES), snes/nes/gbc/gen installiert
- ☐ Optional: `ticket.db` importieren (GUI: *Import tickets…*), damit keine Title ID
  mit vorhandenen Titeln kollidiert
- ☐ Konsolensprache notieren (für die Textbox-Sprache)

**Ablauf je Test-ROM:** in der GUI laden → Vorschau prüfen → *Install via QR* →
in FBI *Remote Install → Scan QR Code* → Test-Schritte → Ergebnis eintragen.

## 1. Installation und Title IDs

| # | Test | Erwartung |
|---|------|-----------|
| 1.1 | Installation per QR-Code | FBI lädt und installiert, Icon im HOME-Menü |
| 1.2 | Installation per SD-Karte (CIA kopieren, FBI → SD) | wie 1.1 |
| 1.3 | Gleiche ROM neu bauen und installieren (nach dem Speichern) | gleiche Title ID, Titel wird ersetzt, **Spielstand bleibt** |
| 1.4 | Andere ROM, deren Title ID per *Advanced* auf eine belegte ID gesetzt ist | Build-Log warnt „already used by …“ |
| 1.5 | `vcinject list` nach einigen Builds | alle Builds mit ID und Save-Typ aufgelistet |

## 2. GBA (nativ, AGB_FIRM)

Standard-Ablauf je ROM: starten → im Spiel speichern → **HOME → A** zum Beenden →
Konsole normal neu starten → Spiel starten → Spielstand da?
Zusätzlich: Deckel zu/auf (Sleep), Bild hell genug, kein auffälliges Nachziehen.

| # | Save-Typ | ROM (vorhanden) | Besonderheit | Ergebnis |
|---|----------|-----------------|--------------|----------|
| 2.1 | Flash 1 Mbit + RTC | Pokémon Smaragd | bereits ✅; Uhr-Ereignisse prüfen (Beeren) | ✅ Speichern |
| 2.2 | Flash 1 Mbit + RTC | Pokémon Saphir | Uhr im Spiel stellen, nach 1 Tag prüfen | ☐ |
| 2.3 | Flash 1 Mbit | Pokémon Blattgrün | | ☐ |
| 2.4 | Flash 1 Mbit, **32 MiB** | Pokémon Mystery Dungeon Rotes Rettungsteam | größte ROM-Größe | ☐ |
| 2.5 | Flash 512 Kbit | *keine eigene .gba* → z. B. Golden Sun (siehe Abschnitt 7) | | ☐ |
| 2.6 | EEPROM 64 Kbit | Meine Tierpension | | ☐ |
| 2.7 | EEPROM 64 Kbit | Pferd & Pony – Mein Pferdehof | 4 MiB ROM | ☐ |
| 2.8 | EEPROM 4 Kbit | Harry Potter – Kammer des Schreckens | | ☐ |
| 2.9 | EEPROM 4 Kbit | Herr der Ringe – Rückkehr des Königs | 16 MiB ROM | ☐ |
| 2.10 | SRAM | Game & Watch Gallery Advance | | ☐ |
| 2.11 | SRAM, Bootleg | POKEPIN R/S (13,3 MiB) | ungerade ROM-Größe | ☐ |
| 2.12 | kein Save | Bionicle, Peter Pan | startet und läuft | ☐ |

Falls ein Spiel nicht speichert: Save-Typ unter *Advanced* von Hand setzen, neu
bauen und Ergebnis mit beiden Save-Typen notieren.

## 3. VC Console Look und Icon

| # | Test | Erwartung |
|---|------|-----------|
| 3.1 | GBA-Banner (Feuerrot-Vorlage) | rotierendes lila Modul, Titelbild mit **runden Ecken**, Textbox fix darunter |
| 3.2 | Textbox | Titel korrekt, „Erschienen: Jahr“ in Konsolensprache |
| 3.3 | Jingle beim Anwählen | GBA-Startsound |
| 3.4 | Icon | Boxart im grauen VC-Rahmen, scharf, keine Farbfehler |
| 3.5 | NES-Banner (Yoshi-Vorlage) | NES-Modell mit Titelbild, Textbox in Konsolensprache, NES-Jingle |
| 3.6 | Konsolensprache kurz auf Englisch/Französisch stellen | Textbox wechselt Sprache (beim NES-Look) |
| 3.7 | ROM ohne Datenbank-Treffer (z. B. Bootleg) | generiertes Bild, Titel lesbar |

## 4. Emulator-Forwarder (SNES, Mega Drive, GB/GBC, NES)

Ablauf je ROM: starten → steuern (alle Tasten) → im Spiel speichern (Batterie-Save)
→ über das Emulator-Menü (Touchscreen antippen) beenden → neu starten →
Spielstand da? Auf der SD prüfen: Ordner
`nsui_formwarders_data/<system>_<id>_<titel>/` existiert.

| # | System / Art | ROM | Ergebnis |
|---|--------------|-----|----------|
| 4.1 | SNES, LoROM mit Save | **besorgen** (siehe 6.1) | ☐ |
| 4.2 | SNES, HiROM mit Save | **besorgen** | ☐ |
| 4.3 | SNES, Spezialchip (SA-1 / Super FX) | **besorgen** – darf ruckeln, nur Start prüfen | ☐ |
| 4.4 | Mega Drive, ohne Save | **besorgen** | ☐ |
| 4.5 | Mega Drive, mit Batterie-Save | **besorgen** | ☐ |
| 4.6 | Game Boy (DMG) | **besorgen** | ☐ |
| 4.7 | Game Boy Color mit Save | **besorgen** | ☐ |
| 4.8 | Game Boy Color mit Echtzeituhr | **besorgen** | ☐ |
| 4.9 | NES, einfacher Mapper (NROM) | **besorgen** | ☐ |
| 4.10 | NES, mit Batterie-Save (MMC1) | **besorgen** | ☐ |
| 4.11 | NES, MMC3 | **besorgen** | ☐ |
| 4.12 | Zwei Spiele desselben Systems installiert | getrennte Save-Ordner, kein Überschreiben | ☐ |

## 5. GUI und Sonderfälle (am Rechner)

- ☐ ROM per Drag & Drop und per Klick laden, alle Endungen (.gba .sfc .smc .md .gen .gb .gbc .nes)
- ☐ Unbekannte Endung → verständliche Fehlermeldung
- ☐ Fehlende Vorlage → Button „Set up … template“ erscheint und funktioniert
- ☐ Ohne Internet bauen → generierte Bilder, Build klappt trotzdem
- ☐ *Install via QR* → QR an Stelle der Vorschau, Fenster bleibt gleich groß, *Stop* bringt die Vorschau zurück
- ☐ `start-gui.command` per Doppelklick

## 6. ROMs, die du noch besorgen musst

Nur von eigenen Modulen dumpen, z. B. mit GB Operator (GB/GBC/GBA), Retrode
(SNES/Mega Drive) oder einem Sanni Cart Reader (alle Systeme). Die Beispiele
sind Vorschläge; jedes Spiel mit derselben Eigenschaft geht.

### 6.1 SNES (`.sfc`)

| Eigenschaft | Beispiele |
|-------------|-----------|
| LoROM + Batterie-Save | Super Mario World, Zelda: A Link to the Past |
| HiROM + Batterie-Save | Donkey Kong Country, Secret of Mana |
| Spezialchip | Kirby Super Star (SA-1), Star Fox / Yoshi's Island (Super FX) |

### 6.2 Mega Drive / Genesis (`.md`)

| Eigenschaft | Beispiele |
|-------------|-----------|
| ohne Save | Sonic the Hedgehog 2, Streets of Rage 2 |
| Batterie-Save | Phantasy Star IV, Sonic 3 |
| 6-Tasten-Pad | Street Fighter II' Special Champion Edition |

### 6.3 Game Boy / Color (`.gb` / `.gbc`)

| Eigenschaft | Beispiele |
|-------------|-----------|
| Game Boy (DMG) ohne Save | Tetris, Super Mario Land |
| Game Boy mit Save | Pokémon Rot/Blau, Link's Awakening |
| Game Boy Color mit Save | Link's Awakening DX, Oracle of Ages |
| Game Boy Color mit Uhr | Pokémon Gold/Silber/Kristall |

### 6.4 NES (`.nes`, mit iNES-Header)

| Eigenschaft | Beispiele |
|-------------|-----------|
| NROM (einfach) | Super Mario Bros., Donkey Kong |
| MMC1 + Batterie | The Legend of Zelda, Zelda II |
| MMC3 | Super Mario Bros. 3, Kirby's Adventure |

### 6.5 GBA – Lücken

| Fehlt | Beispiele |
|-------|-----------|
| Flash 512 Kbit als eigene .gba | Golden Sun, Sonic Advance, Mother 3 |
| EEPROM + ROM über 16 MiB | seltener Sonderfall, nur falls zufällig vorhanden |

Tipp: Die ROMs deiner vorhandenen Injects (Golden Sun, Sonic Advance, Super
Mario World, Sonic 3 …) stecken in den CIAs in `Ressources/`. Für Tests der
Abschnitte 2.5 und 4 können sie wiederverwendet werden; vcinject hat dafür
noch keinen Export-Befehl, den baue ich bei Bedarf ein.

### 6.6 VC-Console-Look-Vorlagen (optional)

Für ein 3D-Banner pro Konsole je ein Titel mit 3D-VC-Banner:

| Konsole | Vorhanden | Besorgen |
|---------|-----------|----------|
| GBA | ✅ NSUI-Inject (Feuerrot) | – |
| NES | ✅ offizieller VC-Titel (Yoshi) | – |
| Game Boy / Color | – | offizieller GB/GBC-VC-Titel aus dem eShop, als CIA gedumpt |
| SNES | – | offizieller SNES-VC-Titel (New 3DS) |
| Mega Drive | – | 3D-Classics- oder NSUI-Inject mit 3D-Banner |

## 7. Ergebnisse

| Datum | Test-Nr. | Ergebnis | Notiz / Foto |
|-------|----------|----------|--------------|
| | | | |

"""Setup assistant: one page per part vcinject uses, each explaining what it is,
whether it is needed, where it comes from, its current state and the button
that sets it up."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWizard,
                               QWizardPage)

from .. import backup, health, registry, systems, template, tickets, tools, vcbanner
from ..errors import InjectError

REQUIRED = "<span style='color:#c0392b'><b>REQUIRED</b></span>"
OPTIONAL = "<span style='color:#2e86c1'><b>OPTIONAL</b></span>"

GODMODE9_DUMP = (
    "<b>How to get the file from your 3DS:</b><ol>"
    "<li>Hold <b>START</b> while switching the 3DS on – GodMode9 opens.</li>"
    "<li>Press <b>HOME</b> → <i>Title manager</i> → <i>[A:] SD CARD</i>.</li>"
    "<li>Select the title → <i>Build CIA (standard)</i>.</li>"
    "<li>Copy the new file from <code>gm9/out</code> on the SD card to this computer.</li></ol>")

VC_LOOK_EXAMPLES = {
    "gba": "an inject made with <b>NSUI v28 or newer</b> (e.g. one of your Pokémon GBA injects)",
    "nes": "an <b>official NES Virtual Console game</b> from the eShop (e.g. Yoshi, Super Mario Bros.)",
    "gb": "an <b>official Game Boy Virtual Console game</b> (e.g. Pokémon Red, Blue or Yellow)",
    "gbc": "an <b>official Game Boy Color Virtual Console game</b> (e.g. Pokémon Gold, Silver or "
           "Crystal, Link's Awakening DX)",
    "snes": "an <b>official Super Nintendo Virtual Console game</b> (New 3DS eShop)",
    "gen": "an inject with a 3D banner, if you have one (Nintendo never released Mega Drive VC "
           "titles on the 3DS)",
}


class SetupPage(QWizardPage):
    """Title, REQUIRED/OPTIONAL badge, explanation, live status, action buttons and a
    small log for whatever the buttons do."""

    def __init__(self, wizard: "SetupWizard", title: str, required: bool | None, text: str,
                 status=None, actions=(), preview=None):
        super().__init__()
        self.setup = wizard
        self.setTitle(title)
        self.setSubTitle(" ")
        self._status = status        # callable -> (ok: bool, text: str)
        self._preview = preview      # callable -> QPixmap | None
        badge = QLabel("" if required is None else REQUIRED if required else OPTIONAL)
        badge.setVisible(required is not None)
        body = QLabel(text, wordWrap=True, textFormat=Qt.RichText)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(body)
        self.state = QLabel(wordWrap=True, textFormat=Qt.RichText)
        self.picture = QLabel(alignment=Qt.AlignCenter)
        buttons = QHBoxLayout()
        for caption, handler in actions:
            buttons.addWidget(QPushButton(caption, clicked=lambda _=False, h=handler: h(self)))
        buttons.addStretch(1)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log.setMaximumHeight(110)
        self.log.hide()
        layout = QVBoxLayout(self)
        layout.addWidget(badge)
        layout.addWidget(scroll, 1)       # long explanations scroll instead of being cut off
        layout.addWidget(self.state)
        layout.addWidget(self.picture)
        layout.addLayout(buttons)
        layout.addWidget(self.log)

    def initializePage(self):
        ids = self.wizard().pageIds()
        self.setSubTitle(f"Page {ids.index(self.wizard().currentId()) + 1} of {len(ids)}")
        self.refresh()

    def refresh(self):
        if self._status is not None:
            ok, text = self._status()
            mark = "✅" if ok else "⚠️"
            self.state.setText(f"<b>Status:</b> {mark} {html.escape(text)}")
        pixmap = self._preview() if self._preview else None
        self.picture.setVisible(pixmap is not None)
        if pixmap is not None:
            self.picture.setPixmap(pixmap)

    def run(self, func, done_text="Done."):
        """Run ``func(log)`` in the background, show its output, refresh the status."""
        from .app import Task

        self.log.show()
        self.setEnabled(False)

        def finished(_result, error):
            self.setEnabled(True)
            self.log.appendPlainText(f"Error: {error}" if error else done_text)
            self.refresh()
            self.setup.main.refresh_status()
            self.setup.main._apply_artwork()

        Task(self, func, finished, self.log.appendPlainText)


def _check(resource: str):
    def status():
        state = next(c for c in health.check() if c.resource == resource)
        return state.ok, "set up" if state.ok else state.detail
    return status


class SetupWizard(QWizard):
    def __init__(self, window, start_resource: str | None = None):
        super().__init__(window)
        self.main = window
        self.setWindowTitle("vcinject setup")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.HaveFinishButtonOnEarlyPages, True)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.resize(820, 760)
        self._ids: dict[str, int] = {}

        self._add("welcome", SetupPage(
            self, "Welcome to vcinject", None,
            "vcinject turns games you own into 3DS titles that look like Nintendo's Virtual "
            "Console releases. This assistant sets up what it needs – <b>one step per page</b>."
            "<br><br>Pages marked " + REQUIRED + " are needed before you can build anything. "
            "Pages marked " + OPTIONAL + " unlock more consoles or the nicer 3D look; skip them "
            "with <i>Next</i> and come back any time with <b>Setup…</b> in the main window."
            "<br><br><b>Setting up a second computer?</b> If you exported your templates before "
            "(last page of this assistant), <b>Import templates from backup…</b> restores all of "
            "them at once."
            "<br><br><b>Current state:</b>" + self._overview(),
            actions=[("Import templates from backup…", self._import_backup)]))

        self._add("tool", SetupPage(
            self, "Build tools", True,
            "<b>What it is:</b> two small open-source programs that do the 3DS-specific "
            "packaging: <i>makerom</i> creates the installable <code>.cia</code> file, "
            "<i>bannertool</i> creates flat HOME Menu banners. They are compiled from source on "
            "this computer and stored in the project folder (<code>bin/</code>).<br><br>"
            "<b>Needed for:</b> every build, for every console.<br><br>"
            "<b>What you need:</b> nothing but a compiler. On macOS it comes with Apple's "
            "command line tools – if building fails, open Terminal and run "
            "<code>xcode-select --install</code>. On Linux install <code>build-essential</code> "
            "and <code>git</code>. Building takes about a minute.",
            status=self._tools_status,
            actions=[("Build tools", self._build_tools)]))

        self._add("gba-template", SetupPage(
            self, "GBA base template", True,
            "<b>What it is:</b> three small files (about 30 KB) from an official Game Boy Advance "
            "Virtual Console title: the program header Nintendo signed, which tells the 3DS to "
            "start its built-in GBA hardware, the Nintendo boot logo and an empty data area. "
            "It contains no game.<br><br>"
            "<b>Needed for:</b> Game Boy Advance games. Other consoles don't use it.<br><br>"
            "<b>How to get it:</b> click <b>Download</b> (recommended – the files are checked "
            "against known checksums). If you dumped a GBA Virtual Console title yourself, you "
            "can take the files from it instead with <b>From my own title…</b>.",
            status=_check("gba-template"),
            actions=[("Download", lambda p: p.run(lambda log: template.download(),
                                                  "Template downloaded.")),
                     ("From my own title…", lambda p: self._pick(p, "gba-template"))]))

        for key, system in systems.SYSTEMS.items():
            derive = key in vcbanner.DERIVED_COLORS
            text = (
                f"<b>What it is:</b> the 3D banner of Nintendo's Virtual Console titles for "
                f"{system.name}: a model of the console or cartridge showing your game's title "
                f"screen, the grey <i>Virtual Console</i> plate with title and release year "
                f"(written in every console language) and the console's jingle.<br><br>"
                f"<b>Needed for:</b> nothing – without it, {system.name} games get a flat banner "
                f"with the game's logo. It is the default look once it is set up.<br><br>"
                f"<b>How to get it:</b> choose a file you own that already has this banner: "
                f"{VC_LOOK_EXAMPLES[key]}. Only its banner is used; the game in it doesn't "
                f"matter. The console is detected automatically.<br><br>{GODMODE9_DUMP}")
            if derive:
                text += ("<b>No such game?</b> <b>Create from GBA look</b> recolours the GBA look "
                         f"for {system.name} and adds the Game Boy start-up sound, until you "
                         "have an official one.")
            actions = [("Choose title…", lambda p, key=key: self._pick(p, f"vclook:{key}"))]
            if derive:
                actions.append(("Create from GBA look", lambda p, key=key: p.run(
                    lambda log, key=key: vcbanner.derive_look(key), "Look created.")))
            self._add(f"vclook:{key}", SetupPage(
                self, f"VC Console Look: {system.name}", False, text,
                status=_check(f"vclook:{key}"), actions=actions,
                preview=lambda key=key: self._look_preview(key)))

        for kind, label in systems.FORWARDER_KINDS.items():
            names = ", ".join(s.name for s in systems.SYSTEMS.values() if s.forwarder == kind)
            self._add(f"forwarder:{kind}", SetupPage(
                self, f"Emulator: {label}", False,
                f"<b>What it is:</b> {names} games don't run natively on the 3DS. They run in an "
                f"emulator (RetroArch, snes9x or VirtuaNES) that is packed into the "
                f"<code>.cia</code> together with the game. vcinject takes this emulator from an "
                f"inject you already have and puts your game into it – the result is the same "
                f"as an NSUI inject.<br><br>"
                f"<b>Needed for:</b> only for {names} games.<br><br>"
                f"<b>How to get it:</b> choose any existing {names} inject (<code>.cia</code>), "
                f"for example one made with NSUI. Which game is in it doesn't matter. If it is "
                f"installed on your 3DS, dump it with GodMode9:<br><br>{GODMODE9_DUMP}",
                status=_check(f"forwarder:{kind}"),
                actions=[("Choose inject…", lambda p, kind=kind: self._pick(p, f"forwarder:{kind}"))]))

        self._add("library", SetupPage(
            self, "ROM folder", False,
            "<b>What it is:</b> the folder on this computer with your game backups. vcinject "
            "lists every game in it and its sub folders in the <b>Library</b> on the left, with "
            "cover and whether you already built it.<br><br>"
            "<b>Needed for:</b> nothing – you can always drag single ROMs onto the window "
            "instead.<br><br><b>How to set it:</b> click <b>Choose folder…</b>.",
            status=self._library_status,
            actions=[("Choose folder…", self._choose_library)]))

        self._add("tickets", SetupPage(
            self, "Title IDs already on your 3DS", False,
            "<b>What it is:</b> every 3DS title has an ID. vcinject never reuses IDs of games it "
            "built itself; importing your console's ticket database also keeps it away from "
            "everything else that is installed (e.g. injects made with other tools), so a new "
            "inject can never overwrite an existing title.<br><br>"
            "<b>Needed for:</b> nothing, but recommended if you also use other injectors.<br><br>"
            "<b>How to get the file:</b><ol>"
            "<li>Hold <b>START</b> while switching the 3DS on – GodMode9 opens.</li>"
            "<li>Open <i>[1:] SYSNAND CTRNAND</i> → <i>dbs</i>, select <i>ticket.db</i> and "
            "press <b>Y</b> to copy.</li>"
            "<li>Go to <i>[0:] SDCARD</i> → <i>gm9</i> → <i>out</i> and press <b>Y</b> to paste.</li>"
            "<li>Copy <code>gm9/out/ticket.db</code> to this computer.</li></ol>"
            "GodMode9 only reads here, it doesn't ask for write permission.",
            status=self._tickets_status,
            actions=[("Import ticket.db…", self._import_tickets)]))

        self._add("done", SetupPage(
            self, "All set", None,
            "You can open this assistant any time with <b>Setup…</b> in the main window. "
            "vcinject also checks everything on every start and asks again if something goes "
            "missing.<br><br><b>Back up your templates:</b> <b>Export templates…</b> saves every "
            "template in one file. Import it on another computer (first page of this assistant) "
            "instead of setting everything up again. Keep the file private – it contains data "
            "from your own games, so don't share or upload it."
            "<br><br><b>Overview:</b>" + self._overview(),
            actions=[("Export templates…", self._export_backup)]))

        if start_resource:
            key = start_resource if start_resource in self._ids else start_resource.split(":")[0]
            self.setStartId(self._ids.get(key, 0))

    # --- helpers ---------------------------------------------------------------

    def _add(self, key: str, page: SetupPage):
        self._ids[key] = self.addPage(page)

    @staticmethod
    def _overview() -> str:
        rows = []
        for c in health.check():
            mark = "✅" if c.ok else ("❌" if c.required else "➖")
            rows.append(f"{mark} {html.escape(c.label)}")
        return "<br>" + "<br>".join(rows)

    @staticmethod
    def _tools_status():
        missing = [n for n in tools.TOOLS if tools.find_tool(n) is None]
        return (not missing, "makerom and bannertool are ready" if not missing
                else "missing: " + ", ".join(missing))

    def _library_status(self):
        folder = self.main.library.settings.value("library/folder", "")
        ok = bool(folder) and Path(folder).is_dir()
        return ok, folder if ok else "no folder chosen"

    @staticmethod
    def _tickets_status():
        imported = sum(1 for e in registry.load().values() if e.imported)
        return imported > 0, (f"{imported} title IDs from your 3DS are reserved" if imported
                              else "not imported")

    def _look_preview(self, key: str) -> QPixmap | None:
        if not vcbanner.is_installed(key):
            return None
        from PIL import Image

        from .app import to_pixmap
        try:
            sample = Image.new("RGB", (240, 160), (90, 140, 200))
            image = vcbanner.mockup(sample, systems.SYSTEMS[key].name, "1998", "", None, key)
        except InjectError:
            return None
        return to_pixmap(image, 260, 156)

    def _pick(self, page: SetupPage, resource: str):
        from .app import INJECT_FILTER
        path, _ = QFileDialog.getOpenFileName(self, health.question(resource), "", INJECT_FILTER)
        if path:
            page.run(lambda log: health.heal(resource, lambda _q: Path(path), log)
                     or log("That file could not be used for this step."))

    def _build_tools(self, page: SetupPage):
        if not health.can_build_tools():
            QMessageBox.warning(self, "Build tools",
                                "A compiler is missing.\n\nmacOS: open Terminal and run "
                                "'xcode-select --install', then try again.\n"
                                "Linux: install build-essential and git.")
            return
        page.run(health.build_tools, "Tools built.")

    def _choose_library(self, page: SetupPage):
        self.main.library.choose_folder()
        page.refresh()

    def _export_backup(self, page: SetupPage):
        path, _ = QFileDialog.getSaveFileName(self, "Export templates", "vcinject-templates.zip",
                                              "Template backup (*.zip)")
        if path:
            page.run(lambda log: log("Exported {} templates to {}".format(
                *(lambda r: (len(r[1]), r[0]))(backup.export(Path(path))))), "Keep the file private.")

    def _import_backup(self, page: SetupPage):
        path, _ = QFileDialog.getOpenFileName(self, "Import templates", "",
                                              "Template backup (*.zip)")
        if path:
            page.run(lambda log: log("Usable now: " + ", ".join(backup.restore(Path(path), log))))

    def _import_tickets(self, page: SetupPage):
        path, _ = QFileDialog.getOpenFileName(self, "Choose ticket.db", "",
                                              "Ticket database (*.db *.tik);;All files (*)")
        if not path:
            return

        def work(log):
            ids = tickets.read_title_ids([Path(path)])
            added = registry.import_title_ids(ids)
            log(f"{len(ids)} title IDs found, {added} new.")

        page.run(work)

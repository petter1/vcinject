"""The Homebrew page: apps for the 3DS like a small store – cover, description and
status per 3DS, installed over Wi-Fi or by QR code –, the news of their releases,
and what the 3DS needs for injects."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from .. import forwarder, homebrew, installed, status, systems
from ..errors import InjectError
from . import wifi_install

REQUIREMENTS_HTML = """
<p><b>Every inject</b> needs a 3DS with <a href="https://3ds.hacks.guide">Luma3DS</a>
(custom firmware; its signature patches are on by default) and <b>FBI</b> to install the
<code>.cia</code> – also over the QR codes shown here.</p>
<p><b>What else, per system:</b></p>
<ul>
<li><b>Game Boy Advance</b> and <b>Game Boy / Color with Nintendo's emulator</b>: nothing –
they run like eShop Virtual Console games. Saves are kept in the title's own save data.</li>
<li><b>SNES</b> (snes9x) and <b>NES</b> (VirtuaNES) forwarders: nothing. The emulator is inside
each <code>.cia</code>; saves go to <code>sdmc:/nsui_forwarders_data/&lt;game&gt;</code>.</li>
<li><b>RetroArch forwarders</b> ({retroarch}): the 3DS's <b>sound firmware</b> must be on the SD
card (<code>sdmc:/3ds/dspfirm.cdc</code>), otherwise they have no sound or don't start.
Install <b>DSP1</b> below, start it once from the HOME Menu, done. RetroArch itself does
<i>not</i> need to be installed – it is inside each forwarder.</li>
</ul>
"""

COVER = QSize(260, 130)


@dataclass
class AppData:
    """What the page knows about one app, gathered in the background."""
    app: homebrew.App
    release: homebrew.Release | None = None
    info: homebrew.StoreInfo = field(default_factory=homebrew.StoreInfo)
    title_id: int = 0
    version: int = 0
    icon: bytes | None = None
    cover: bytes | None = None
    error: str = ""


def _pixmap(data: bytes | None, size: QSize, enlarge: bool = True) -> QPixmap | None:
    pix = QPixmap()
    if not data or not pix.loadFromData(data):
        return None
    if not enlarge and pix.width() <= size.width() and pix.height() <= size.height():
        return pix  # small pictures (pixel art banners) stay sharp
    return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
    area.setWidget(widget)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


class HomebrewPage(QWidget):
    _loaded = Signal(object)           # AppData
    _news_loaded = Signal(object)      # [homebrew.News]
    _shot_loaded = Signal(str, object)  # app key, image bytes
    _done = Signal(str, str)           # app key, what happened
    _progress = Signal(str, str)       # app key, "Installing… 40 %"
    _downloaded = Signal(str, object, str)  # app key, Path or None, error (QR)

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.watcher = wifi_install.ConsoleWatcher.shared()
        self.apps: dict[str, AppData] = {key: AppData(app) for key, app in homebrew.APPS.items()}
        self.state: dict[str, str] = {}  # app key -> "Installing…" etc.
        self._loaded.connect(self._app_loaded)
        self._news_loaded.connect(self._show_news)
        self._shot_loaded.connect(self._add_shot)
        self._done.connect(self._install_done)
        self._progress.connect(self._show_progress)
        self._downloaded.connect(self._serve)
        self.watcher.changed.connect(lambda _c: self._refresh_apps())
        self.watcher.installed_changed.connect(self._refresh_apps)

        title = QLabel("Homebrew", objectName="pageTitle")
        subtitle = QLabel("Open-source apps for the 3DS, straight from their authors' releases",
                          objectName="muted")
        self.update_all = QPushButton(objectName="primary", clicked=self.update_everything, visible=False)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(subtitle, 1)
        head.addWidget(self.update_all)

        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        grid_widget = QWidget()
        grid_box = QVBoxLayout(grid_widget)
        grid_box.addLayout(self.grid)
        grid_box.addStretch(1)
        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.apps_stack = QStackedWidget()
        self.apps_stack.addWidget(_scroll(grid_widget))
        self.apps_stack.addWidget(_scroll(self.detail))
        self.shown: str | None = None  # app on the detail page
        self._news = None

        self.news_box = QVBoxLayout()
        self.news_box.addWidget(QLabel("Loading the news…", objectName="muted"))
        news_widget = QWidget()
        news_layout = QVBoxLayout(news_widget)
        news_layout.addLayout(self.news_box)
        news_layout.addStretch(1)

        retroarch = ", ".join(sorted({s.name for s in systems.SYSTEMS.values()
                                      if s.forwarder in retroarch_kinds()})) or "Game Boy, Mega Drive"
        needs = QLabel(REQUIREMENTS_HTML.format(retroarch=retroarch), wordWrap=True,
                       openExternalLinks=True, alignment=Qt.AlignTop)
        needs.setTextFormat(Qt.RichText)
        needs.setContentsMargins(8, 8, 8, 8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.apps_stack, "Apps")
        self.tabs.addTab(_scroll(news_widget), "News")
        self.tabs.addTab(_scroll(needs), "Needed for injects")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.addLayout(head)
        layout.addWidget(self.tabs, 1)

        self._refresh_apps()
        for data in self.apps.values():
            threading.Thread(target=self._load, args=(data.app,), daemon=True).start()
        threading.Thread(target=lambda: self._news_loaded.emit(self._fetch_news()), daemon=True).start()

    # --- data ----------------------------------------------------------------------

    def _load(self, app: homebrew.App):
        data = AppData(app)
        try:
            data.release = homebrew.release(app)
            data.info = homebrew.store_info(app)
            if data.release.allowed:
                data.title_id, data.version = homebrew.release_title(app)
            data.icon = homebrew.picture(data.info.icon_url)
            data.cover = homebrew.picture(data.info.image_url)
        except (InjectError, OSError, ValueError) as exc:
            data.error = str(exc)
        self._loaded.emit(data)

    @staticmethod
    def _fetch_news():
        try:
            return homebrew.news()
        except (InjectError, OSError, ValueError):
            return None

    def _app_loaded(self, data: AppData):
        self.apps[data.app.key] = data
        self._refresh_apps()
        if self._news is not None and all(d.release or d.error for d in self.apps.values()):
            self._show_news(self._news)  # now with the apps' icons

    def _on_consoles(self, data: AppData) -> list[tuple[str, str, int | None]]:
        """(console key, label, installed version or None) for every known 3DS."""
        known = {c.key: c.label for c in installed.known_consoles()}
        known.update({c.key: c.label for c in self.watcher.consoles})
        return [(key, label, installed.versions(key).get(data.title_id) if data.title_id else None)
                for key, label in sorted(known.items(), key=lambda kv: kv[1].lower())]

    def _needing(self, data: AppData) -> tuple[list, list]:
        """Online 3DSs where the app is outdated, and where it is missing."""
        if not data.title_id or not (data.release and data.release.allowed):
            return [], []
        versions = {c.key: installed.versions(c.key).get(data.title_id) for c in self.watcher.consoles}
        outdated = [c for c in self.watcher.consoles if versions[c.key] is not None
                    and 0 <= versions[c.key] < data.version]
        missing = [c for c in self.watcher.consoles if versions[c.key] is None]
        return outdated, missing

    # --- the apps ------------------------------------------------------------------

    def _refresh_apps(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        for index, data in enumerate(self.apps.values()):
            self.grid.addWidget(self._card(data), index // 3, index % 3)
        updates = sum(len(self._needing(d)[0]) for d in self.apps.values())
        self.update_all.setText(f"Update all ({updates})")
        self.update_all.setVisible(updates > 0)
        if self.shown and self.apps_stack.currentIndex() == 1:
            self.show_app(self.shown)

    def _status_html(self, data: AppData) -> str:
        if data.error:
            return f"<span style='color:gray'>Not available right now</span>"
        if data.release is None:
            return "<span style='color:gray'>Looking up the newest version…</span>"
        if not data.release.allowed:
            return "<span style='color:gray'>No open-source licence: get it from the author</span>"
        chips = []
        for _key, label, version in self._on_consoles(data):
            name = label.split(" (")[0]
            if version is None:
                chips.append(f"<span style='color:gray'>{name}: not installed</span>")
            elif version < 0:
                chips.append(f"<span style='color:#1E9E5A'>{name}: ✓ installed</span>")
            elif version < data.version:
                chips.append(f"<span style='color:#1D4E99'>{name}: {status.version_text(version)} → "
                             f"{status.version_text(data.version)}</span>")
            else:
                chips.append(f"<span style='color:#1E9E5A'>{name}: ✓ {status.version_text(version)}</span>")
        return " &nbsp;·&nbsp; ".join(chips) or "<span style='color:gray'>No 3DS known yet</span>"

    def _action(self, data: AppData) -> QToolButton:
        """Install / Update where it is needed; the menu offers every 3DS and the QR code."""
        button = QToolButton(objectName="install")
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        allowed = bool(data.release and data.release.allowed)
        busy = self.state.get(data.app.key)
        outdated, missing = self._needing(data)
        if busy:
            button.setText(busy)
            button.setEnabled(False)
            return button
        if outdated:
            text, default = (f"Update on {outdated[0].label}" if len(outdated) == 1
                             else f"Update on {len(outdated)} 3DS"), outdated
        elif missing:
            text, default = (f"Install on {missing[0].label}" if len(missing) == 1
                             else f"Install on {len(missing)} 3DS"), missing
        elif self.watcher.consoles:
            text, default = "Up to date", []
        else:
            text, default = "Install via QR code", None
        button.setText(text)
        button.setEnabled(allowed)
        menu = QMenu(button)
        for console in self.watcher.consoles:
            menu.addAction(f"Install on {console.label}", lambda c=console: self.install_over_wifi(data.app.key, [c]))
        if len(self.watcher.consoles) > 1:
            menu.addAction(f"Install on all {len(self.watcher.consoles)}",
                           lambda: self.install_over_wifi(data.app.key, list(self.watcher.consoles)))
        menu.addSeparator()
        menu.addAction("QR code (FBI)", lambda: self.install(data.app.key))
        button.setMenu(menu)
        button.setPopupMode(QToolButton.MenuButtonPopup)
        if default is None:
            button.clicked.connect(lambda: self.install(data.app.key))
        elif default:
            button.clicked.connect(lambda: self.install_over_wifi(data.app.key, default))
        return button

    def _card(self, data: AppData) -> QFrame:
        frame = QFrame(objectName="card")
        frame.setCursor(Qt.PointingHandCursor)
        frame.mousePressEvent = lambda _event, key=data.app.key: self.show_app(key)
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 14, 14, 14)
        cover = QLabel(alignment=Qt.AlignCenter)
        cover.setFixedHeight(COVER.height())
        pix = _pixmap(data.cover, COVER)
        if pix:
            cover.setPixmap(pix)
        else:
            cover.setText(data.app.name)
            cover.setStyleSheet("background: #3D2A99; color: white; font-size: 20px; font-weight: 700;"
                                " border-radius: 8px;")
        box.addWidget(cover)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setFixedSize(48, 48)
        icon_pix = _pixmap(data.icon, QSize(48, 48))
        if icon_pix:
            icon.setPixmap(icon_pix)
        head.addWidget(icon)
        facts = []
        if data.release:
            facts += [data.release.tag, data.release.licence]
        if data.info.author:
            facts.append(data.info.author)
        name = QLabel(f"<span style='font-size:15px; font-weight:700'>{data.app.name}</span><br>"
                      f"<span style='color:gray'>{' · '.join(facts)}</span>")
        head.addWidget(name, 1)
        box.addLayout(head)
        about = QLabel(data.info.description or data.app.purpose, wordWrap=True)
        about.setMinimumHeight(34)
        box.addWidget(about)
        state = QLabel(self._status_html(data), wordWrap=True)
        state.setTextFormat(Qt.RichText)
        box.addWidget(state)
        box.addStretch(1)
        box.addWidget(self._action(data))
        return frame

    def show_app(self, key: str):
        """The app's own page: big cover, the long description, screenshots, status."""
        data = self.apps[key]
        self.shown = key
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0).widget()
                    if child:
                        child.setParent(None)
        back = QPushButton("◀  Homebrew", objectName="link", clicked=lambda: self.apps_stack.setCurrentIndex(0))
        self.detail_layout.addWidget(back, alignment=Qt.AlignLeft)
        cover = QLabel()
        pix = _pixmap(data.cover, QSize(520, 260), enlarge=False)
        if pix:
            cover.setPixmap(pix)
        self.detail_layout.addWidget(cover)
        facts = [data.release.tag, f"licence {data.release.licence}"] if data.release else []
        if data.info.author:
            facts.insert(0, f"by {data.info.author}")
        heading = QLabel(f"<span style='font-size:24px; font-weight:700'>{data.app.name}</span>"
                         f"<br><span style='color:gray'>{' · '.join(facts)}</span>")
        self.detail_layout.addWidget(heading)
        text = data.info.long_description or data.info.description or data.app.purpose
        if data.app.needed_for:
            text += f"\n\nvcinject needs it for {data.app.needed_for}."
        body = QLabel(text, wordWrap=True, openExternalLinks=True)
        body.setTextFormat(Qt.MarkdownText)  # the database writes Markdown
        body.setMaximumWidth(760)
        self.detail_layout.addWidget(body)
        link = QLabel(f"<a href='https://github.com/{data.app.repo}'>github.com/{data.app.repo} ↗</a>",
                      openExternalLinks=True)
        self.detail_layout.addWidget(link)
        state = QLabel(self._status_html(data), wordWrap=True)
        state.setTextFormat(Qt.RichText)
        self.detail_layout.addWidget(state)
        action = self._action(data)
        self.detail_layout.addWidget(action, alignment=Qt.AlignLeft)
        if data.info.screenshots:
            self.shots = QHBoxLayout()
            self.shots.setSpacing(10)
            self.detail_layout.addWidget(QLabel("<b>Screenshots</b>"))
            self.detail_layout.addLayout(self.shots)
            for _text, url in data.info.screenshots[:6]:
                threading.Thread(target=lambda url=url: self._shot_loaded.emit(key, homebrew.picture(url)),
                                 daemon=True).start()
        self.detail_layout.addStretch(1)
        self.apps_stack.setCurrentIndex(1)

    def _add_shot(self, key: str, image):
        if key != self.shown or not hasattr(self, "shots"):
            return
        pix = _pixmap(image, QSize(240, 180))
        if pix:
            label = QLabel()
            label.setPixmap(pix)
            self.shots.addWidget(label)

    # --- installing ----------------------------------------------------------------

    def install_over_wifi(self, key: str, consoles: list):
        """Downloads the app and installs it on ``consoles`` over Wi-Fi."""
        app = homebrew.APPS[key]
        self.state[key] = "Downloading…"
        self._refresh_apps()

        def progress(text, sent, total):
            percent = sent * 100 // max(total, 1) // 10 * 10
            text = f"Installing… {percent} %"
            if self.state.get(key) != text:
                self._progress.emit(key, text)

        def work():
            try:
                path = homebrew.download(app)
                self._done.emit(key, " · ".join(wifi_install.install(consoles, [path], progress)))
            except (InjectError, OSError) as exc:
                self._done.emit(key, f"Failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, key: str, text: str):
        self.state[key] = text
        self._refresh_apps()

    def _install_done(self, key: str, message: str):
        self.state.pop(key, None)
        self.main.statusBar().showMessage(f"{homebrew.APPS[key].name}: {message}", 15000)
        self._refresh_apps()

    def update_everything(self):
        for data in self.apps.values():
            outdated, _missing = self._needing(data)
            if outdated:
                self.install_over_wifi(data.app.key, outdated)

    def install(self, key: str):
        """By QR code: download, then the QR code appears on the game page."""
        self.state[key] = "Downloading…"
        self._refresh_apps()

        def work():
            try:
                self._downloaded.emit(key, homebrew.download(homebrew.APPS[key]), "")
            except (InjectError, OSError) as exc:
                self._downloaded.emit(key, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _serve(self, key: str, path, error: str):
        self.state.pop(key, None)
        self._refresh_apps()
        if path is None:
            self.main.statusBar().showMessage(f"Download failed: {error}", 15000)
            return
        self.main.show_section("games")  # the QR code appears on the game page
        self.main.games.setCurrentWidget(self.main.game_page)
        self.main.show_qr(path)

    # --- news ----------------------------------------------------------------------

    def _show_news(self, items):
        self._news = items
        while self.news_box.count():
            widget = self.news_box.takeAt(0).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        if not items:
            self.news_box.addWidget(QLabel("No news right now (offline?).", objectName="muted"))
            return
        import datetime
        today = datetime.date.today()
        for item in items[:30]:
            frame = QFrame(objectName="card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(16, 12, 16, 12)
            data = self.apps.get(item.key)
            pix = _pixmap(data.icon if data else None, QSize(32, 32))
            head = QHBoxLayout()
            if pix:
                icon = QLabel()
                icon.setPixmap(pix)
                head.addWidget(icon)
            try:
                age = (today - datetime.date.fromisoformat(item.date)).days
            except ValueError:
                age = 999
            new = " <span style='color:white; background:#6B4FD8'>&nbsp;NEW&nbsp;</span>" if age <= 14 else ""
            head.addWidget(QLabel(f"<span style='font-size:15px; font-weight:700'>{item.project} "
                                  f"{item.tag}</span>{new}<br><span style='color:gray'>{item.date}</span>"), 1)
            box.addLayout(head)
            notes = item.notes.strip()
            if len(notes) > 1200:
                notes = notes[:1200].rsplit("\n", 1)[0] + "\n\n…"
            text = QLabel(notes or "_No release notes._", wordWrap=True, openExternalLinks=True)
            text.setTextFormat(Qt.MarkdownText)
            box.addWidget(text)
            if item.url:
                box.addWidget(QLabel(f"<a href='{item.url}'>Read on GitHub ↗</a>", openExternalLinks=True))
            self.news_box.addWidget(frame)


def retroarch_kinds() -> set[str]:
    """Forwarder templates that are RetroArch builds (they need the DSP firmware)."""
    kinds = set()
    for kind in forwarder.installed_kinds():
        try:
            if forwarder.ForwarderTemplate.load(kind).needs_dsp_firmware:
                kinds.add(kind)
        except InjectError:
            continue
    return kinds


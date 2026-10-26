"""The Homebrew page: apps for the 3DS like a small store – cover, description and
status per 3DS, installed over Wi-Fi or by QR code –, the news of their releases,
and what the 3DS needs for injects."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QPushButton, QScrollArea, QStackedWidget, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import forwarder, homebrew, installed, status, systems
from ..errors import InjectError
from . import wifi_install
from .app import ThreeDsPreview, card
from .cardview import BLUE, GREEN, Card, CardView, ViewControls
from .qtutil import CONTENT_MARGIN, thumbnail

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

PICTURE_WIDTH = 480   # pictures are kept at most this wide (memory)
PICTURE_LOADERS = 4
# the chips of the Apps tab: Recommended (vcinject's catalog), then Universal-DB's categories
SHELVES = (("Recommended", None), ("All", ""), ("Games", "game"), ("Utilities", "utility"),
           ("Apps", "app"), ("Emulators", "emulator"), ("Media", "media"), ("Save tools", "save-tool"))
CREDIT = ("The list of apps, their pictures and descriptions come from "
          "<a href='https://db.universal-team.net'>Universal-DB</a>, the database of "
          "<a href='https://github.com/Universal-Team/Universal-Updater'>Universal-Updater</a> "
          "by Universal-Team. Thank you!")


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


def _smaller(image: QImage) -> QImage:
    return image.scaledToWidth(PICTURE_WIDTH, Qt.SmoothTransformation) \
        if image.width() > PICTURE_WIDTH else image


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
    _store_loaded = Signal(object)     # [homebrew.StoreApp] or None
    _picture_ready = Signal(str, object)  # url, QImage (null when there is none)
    _store_version = Signal(str, int)  # slug, newest version (from its .cia)
    _store_art = Signal(str, object, object)  # shown page, icon bytes, cover bytes

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
        # many things arrive at once (apps, versions, consoles): the page is updated once after
        self._refresh_timer = QTimer(self, singleShot=True, interval=80, timeout=self._refresh_now)
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

        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(*[CONTENT_MARGIN] * 4)
        self.store: list[homebrew.StoreApp] = []
        self.store_versions: dict[str, int] = {}   # slug -> newest version
        self._store_loaded.connect(self._show_store)
        self._store_version.connect(self._version_arrived)
        self._store_art.connect(self._store_art_arrived)
        # pictures of the cards: loaded when a card is first painted, the newest wish first
        self._pictures: dict[str, QPixmap] = {}
        self._wanted: set[str] = set()
        self._picture_queue: queue.LifoQueue = queue.LifoQueue()
        self._picture_ready.connect(self._picture_arrived)
        self._repaint_timer = QTimer(self, singleShot=True, interval=60,
                                     timeout=lambda: self.cards.repaint_cards())
        for _ in range(PICTURE_LOADERS):
            threading.Thread(target=self._picture_worker, daemon=True).start()
        self._versions: dict[str, dict[int, int]] = {}  # console name -> installed versions
        chips = QHBoxLayout()
        self.shelf = QButtonGroup(self)
        for index, (label, _category) in enumerate(SHELVES):
            chip = QPushButton(label, objectName="chip", checkable=True, checked=index == 0)
            self.shelf.addButton(chip, index)
            chips.addWidget(chip)
        self.shelf.idClicked.connect(lambda _id: self._fill_store())
        chips.addStretch(1)
        self.store_search = QLineEdit(placeholderText="Search all homebrew", clearButtonEnabled=True)
        self.store_search.setMinimumWidth(160)
        self.store_search.setMaximumWidth(220)
        self.store_search.textChanged.connect(self._search_typed)
        self.store_sort = QComboBox()
        for label in ("Popular", "Recently updated", "Name"):
            self.store_sort.addItem(label)
        self.store_sort.currentIndexChanged.connect(lambda _i: self._fill_store())
        # every shelf as the same cards (or rows), like the games and the titles on a 3DS
        self.cards = CardView(self._card_picture, cover_ratio=0.5, with_text=True)
        self.cards.card_clicked.connect(self._open_card)
        self.cards.card_activated.connect(self._open_card)
        self.cards.card_context.connect(self._card_menu)
        self.view_controls = ViewControls("homebrew", 260, [self.cards])
        chips.addWidget(self.store_search)
        # second row, as on Games: how many, then sorting and the look
        self.shelf_count = QLabel(objectName="muted")
        second = QHBoxLayout()
        second.addWidget(self.shelf_count, 1)
        second.addWidget(self.store_sort)
        second.addWidget(self.view_controls)
        credit = QLabel(CREDIT, wordWrap=True, openExternalLinks=True, objectName="muted")
        credit.setTextFormat(Qt.RichText)
        browse = QWidget()
        browse_layout = QVBoxLayout(browse)
        browse_layout.setContentsMargins(*[CONTENT_MARGIN] * 4)
        browse_layout.addLayout(chips)
        browse_layout.addLayout(second)
        browse_layout.addWidget(self.cards, 1)
        browse_layout.addWidget(credit)
        self.apps_stack = QStackedWidget()
        self.apps_stack.addWidget(browse)
        # an app's page like a game's: what it is on the left, how it looks on the 3DS right
        self.preview = ThreeDsPreview()
        preview_card, preview_layout = card("Preview")
        preview_layout.addWidget(self.preview, alignment=Qt.AlignHCenter)
        right = QVBoxLayout()
        right.addWidget(preview_card)
        right.addStretch(1)
        detail_page = QWidget()
        columns = QHBoxLayout(detail_page)
        columns.setContentsMargins(0, 0, CONTENT_MARGIN, CONTENT_MARGIN)
        columns.setSpacing(16)
        columns.addWidget(_scroll(self.detail), 5)
        columns.addLayout(right, 4)
        self.apps_stack.addWidget(detail_page)
        self.shown: str | None = None  # app on the detail page
        self._news = None

        self.news_box = QVBoxLayout()
        self.news_box.addWidget(QLabel("Loading the news…", objectName="muted"))
        news_widget = QWidget()
        news_layout = QVBoxLayout(news_widget)
        news_layout.setContentsMargins(*[CONTENT_MARGIN] * 4)
        news_layout.addLayout(self.news_box)
        news_layout.addStretch(1)

        retroarch = ", ".join(sorted({s.name for s in systems.SYSTEMS.values()
                                      if s.forwarder in retroarch_kinds()})) or "Game Boy, Mega Drive"
        needs = QLabel(REQUIREMENTS_HTML.format(retroarch=retroarch), wordWrap=True,
                       openExternalLinks=True, alignment=Qt.AlignTop)
        needs.setTextFormat(Qt.RichText)
        needs.setContentsMargins(*[CONTENT_MARGIN] * 4)

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
        threading.Thread(target=self._load_store, daemon=True).start()

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
        self._refresh_timer.start()

    def _refresh_now(self):
        self._fill_store()
        updates = sum(len(self._needing(d)[0]) for d in self.apps.values()) + sum(
            len(self._store_status(a)[1]) for a in self.store if a.allowed and a.slug in self.store_versions)
        self.update_all.setText(f"Update all ({updates})")
        self.update_all.setVisible(updates > 0)
        if self.shown and self.apps_stack.currentIndex() == 1:
            if self.shown.startswith("store:"):
                self.show_store_app(self.shown[6:])
            else:
                self.show_app(self.shown)
    def _status_html(self, data: AppData) -> str:
        if data.error:
            return "<span style='color:gray'>Not available right now</span>"
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
        outdated, missing = self._needing(data)
        return self._action_button(data.app.key, bool(data.release and data.release.allowed),
                                   outdated, missing, lambda: homebrew.download(data.app))

    def _action_button(self, key: str, allowed: bool, outdated: list, missing: list, fetch) -> QToolButton:
        """Install / Update where it is needed; the menu offers every 3DS and the QR code."""
        button = QToolButton(objectName="install")
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        busy = self.state.get(key)
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
            menu.addAction(f"Install on {console.label}", lambda c=console: self.install_over_wifi(key, [c], fetch))
        if len(self.watcher.consoles) > 1:
            menu.addAction(f"Install on all {len(self.watcher.consoles)}",
                           lambda: self.install_over_wifi(key, list(self.watcher.consoles), fetch))
        menu.addSeparator()
        menu.addAction("QR code (FBI)", lambda: self.install(key, fetch))
        button.setMenu(menu)
        button.setPopupMode(QToolButton.MenuButtonPopup)
        if default is None:
            button.clicked.connect(lambda: self.install(key, fetch))
        elif default:
            button.clicked.connect(lambda: self.install_over_wifi(key, default, fetch))
        return button

    # --- the cards ----------------------------------------------------------------------

    def _short_status(self, ids, newest: int | None) -> tuple[str, str]:
        """What a card says about the 3DSs: where it is (green) or needs an update (blue)."""
        have, old = [], []
        for name, versions in self._versions.items():
            version = next((versions[i] for i in ids if i in versions), None)
            if version is None:
                continue
            (old if newest is not None and 0 <= version < newest else have).append(name)
        if old:
            return f"↑ Update for {', '.join(old)}", BLUE
        if have:
            return f"✓ {', '.join(have)}", GREEN
        return "", ""

    def _app_card(self, data: AppData) -> Card:
        key = data.app.key
        facts = [data.info.author] if data.info.author else []
        if data.release:
            facts.append(data.release.tag)
        if self.state.get(key):
            line, color = self.state[key], BLUE
        elif data.error:
            line, color = "Not available right now", ""
        elif data.release is None:
            line, color = "Looking up the newest version…", ""
        else:
            line, color = self._short_status((data.title_id,) if data.title_id else (),
                                             data.version if data.release.allowed else None)
        return Card(f"app:{key}", data.app.name, " · ".join(facts),
                    data.info.description or data.app.purpose, line, color,
                    badge="NEEDED" if data.app.needed_for else "", tint=QColor("#3D2A99"),
                    tooltip=data.app.purpose, data=data)

    def _store_card(self, app: homebrew.StoreApp) -> Card:
        key = f"store:{app.slug}"
        if self.state.get(key):
            line, color = self.state[key], BLUE
        else:
            line, color = self._short_status(app.title_ids, self.store_versions.get(app.slug))
        tint = QColor(app.color) if app.color.startswith("#") else QColor(90, 80, 140)
        facts = [f for f in (app.author, app.version) if f]
        return Card(key, app.title, " · ".join(facts), app.description, line, color,
                    tint=tint.darker(160), tooltip=f"{app.title} – {app.author}\n{app.description}",
                    data=app)

    def _card_picture(self, card_: Card, _size: QSize) -> QPixmap | None:
        """The cover (or else the icon) of a card; what is not there yet is fetched."""
        if isinstance(card_.data, AppData):
            data = card_.data
            key = f"{card_.key}:{len(data.cover or data.icon or b'')}"
            if key not in self._pictures:
                image = QImage()
                if image.loadFromData(data.cover or data.icon or b""):
                    self._pictures[key] = QPixmap.fromImage(_smaller(image))
                else:
                    return None
            return self._pictures[key]
        app: homebrew.StoreApp = card_.data
        # a small square (rows) shows the icon, a card the picture
        urls = (app.icon_url, app.image_url) if _size.width() < 100 else (app.image_url, app.icon_url)
        for url in urls:
            if url and url not in self._pictures and url not in self._wanted:
                self._wanted.add(url)
                self._picture_queue.put(url)
        for url in urls:
            pix = self._pictures.get(url)
            if pix is not None and not pix.isNull():
                return pix
        return None

    def _picture_worker(self):
        while True:
            url = self._picture_queue.get()
            try:
                image = thumbnail(url, PICTURE_WIDTH, lambda: homebrew.picture(url))
            except (InjectError, OSError, ValueError):
                image = QImage()
            try:
                self._picture_ready.emit(url, image)
            except RuntimeError:  # the page is gone
                return

    def _picture_arrived(self, url: str, image: QImage):
        self._pictures[url] = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self._repaint_timer.start()

    def _open_card(self, key):
        if not key:
            return
        kind, _, name = key.partition(":")
        if kind == "app":
            self.show_app(name)
        else:
            self.show_store_app(name)

    def _card_menu(self, key, at):
        """Right click on a card: open it, or install it like the button on its page."""
        kind, _, name = key.partition(":")
        if kind == "app":
            button = self._action(self.apps[name])
        else:
            app = self._store_app(name)
            _line, outdated, missing = self._store_status(app)
            button = self._store_action(app, outdated, missing)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._open_card(key))
        if button.isEnabled():
            menu.addSeparator()
            menu.addAction(button.text(), button.click)
            for action in button.menu().actions():
                menu.addAction(action)
        menu.exec(at)
        button.deleteLater()
    def show_app(self, key: str):
        """The app's own page: big cover, the long description, screenshots, status."""
        data = self.apps[key]
        self.shown = key
        self._clear_detail()
        back = QPushButton("◀  Homebrew", objectName="link", clicked=lambda: self.apps_stack.setCurrentIndex(0))
        self.detail_layout.addWidget(back, alignment=Qt.AlignLeft)
        cover = QLabel()
        pix = _pixmap(data.cover, QSize(520, 260), enlarge=False)
        if pix:
            cover.setPixmap(pix)
        self.detail_layout.addWidget(cover)
        self._show_preview(data.icon, data.cover, data.app.name)
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
        self._add_screenshots(key, data.info.screenshots)
        self.detail_layout.addStretch(1)
        self.apps_stack.setCurrentIndex(1)

    def _clear_detail(self):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0).widget()
                    if child:
                        child.setParent(None)

    def _add_screenshots(self, key: str, shots: tuple):
        if not shots:
            return
        self.shots = QHBoxLayout()
        self.shots.setSpacing(10)
        self.detail_layout.addWidget(QLabel("<b>Screenshots</b>"))
        self.detail_layout.addLayout(self.shots)
        for _text, url in shots[:6]:
            threading.Thread(target=lambda url=url: self._shot_loaded.emit(key, homebrew.picture(url)),
                             daemon=True).start()

    def _show_preview(self, icon: bytes | None, banner: bytes | None, title: str):
        """The app's icon in the HOME Menu and its picture on the top screen."""
        import io

        from PIL import Image

        def image(data):
            try:
                return Image.open(io.BytesIO(data)).convert("RGBA") if data else None
            except OSError:
                return None

        self.preview_icon = icon
        self.preview.update_art(image(icon) or Image.new("RGBA", (48, 48), "#3D2A99"), image(banner), title)

    def _store_art_arrived(self, shown: str, icon, cover):
        if shown != self.shown:
            return
        pix = _pixmap(cover or None, QSize(520, 260), enlarge=False)
        if pix:
            self.store_cover.setPixmap(pix)
        self._show_preview(icon or None, cover or None, self.preview.title)

    def _add_shot(self, key: str, image):
        if key != self.shown or not hasattr(self, "shots"):
            return
        pix = _pixmap(image, QSize(240, 180))
        if pix:
            label = QLabel()
            label.setPixmap(pix)
            self.shots.addWidget(label)

    # --- all homebrew (Universal-DB) ---------------------------------------------------

    def _load_store(self):
        try:
            apps = homebrew.store_apps()
        except (InjectError, OSError, ValueError):
            apps = None
        self._store_loaded.emit(apps)
        if not apps:
            return
        curated = {a.repo.lower() for a in homebrew.CATALOG}
        known = {}
        for console in installed.known_consoles():
            known.update(installed.versions(console.key))
        for app in apps:  # newest versions of what is installed somewhere: for updates
            if app.allowed and set(app.title_ids) & set(known) and app.source.lower().split("github.com/")[-1] not in curated:
                try:
                    self._store_version.emit(app.slug, homebrew.cia_title_at(app.cia_url)[1])
                except (InjectError, OSError, ValueError):
                    continue

    def _show_store(self, apps):
        curated = {a.repo.lower() for a in homebrew.CATALOG}
        self.store = [a for a in apps or [] if a.source.lower().split("github.com/")[-1] not in curated]
        self._fill_store()

    def _search_typed(self, text: str):
        if text and self.shelf.checkedId() == 0:  # searching means all homebrew
            self.shelf.button(1).setChecked(True)
        self._fill_store()

    def _fill_store(self):
        self._versions = {}
        known = {c.key: c.label for c in installed.known_consoles()}
        known.update({c.key: c.label for c in self.watcher.consoles})
        for key, label in sorted(known.items(), key=lambda kv: kv[1].lower()):
            self._versions[label.split(" (")[0]] = installed.versions(key)
        category = SHELVES[max(0, self.shelf.checkedId())][1]
        if category is None:
            self.cards.set_cards([self._app_card(d) for d in self.apps.values()])
            self.shelf_count.setText(f"{len(self.apps)} apps vcinject recommends")
            return
        words = self.store_search.text().lower().split()
        apps = [a for a in self.store if (not category or category in a.categories)
                and all(w in f"{a.title} {a.author} {a.description}".lower() for w in words)]
        if self.store_sort.currentIndex() == 1:
            apps.sort(key=lambda a: a.updated, reverse=True)
        elif self.store_sort.currentIndex() == 2:
            apps.sort(key=lambda a: a.title.lower())
        self.cards.set_cards([self._store_card(a) for a in apps])
        self.shelf_count.setText(f"{len(apps)} apps" if self.store else "Loading the list of all homebrew…")
    def _installed_ids() -> set[int]:
        ids = set()
        for console in installed.known_consoles():
            ids |= set(installed.versions(console.key))
        return ids

    def _version_arrived(self, slug: str, version: int):
        self.store_versions[slug] = version
        self._refresh_apps()

    def _store_app(self, slug: str) -> homebrew.StoreApp:
        return next(a for a in self.store if a.slug == slug)

    def _store_status(self, app: homebrew.StoreApp) -> tuple[str, list, list]:
        """Status line, online 3DSs where it is outdated, and where it is missing."""
        known = {c.key: c.label for c in installed.known_consoles()}
        known.update({c.key: c.label for c in self.watcher.consoles})
        newest = self.store_versions.get(app.slug)
        chips, outdated, missing = [], [], []
        online = {c.key: c for c in self.watcher.consoles}
        for key, label in sorted(known.items(), key=lambda kv: kv[1].lower()):
            versions = installed.versions(key)
            version = next((versions[i] for i in app.title_ids if i in versions), None)
            name = label.split(" (")[0]
            if version is None:
                chips.append(f"<span style='color:gray'>{name}: not installed</span>")
                if key in online:
                    missing.append(online[key])
            elif newest is not None and 0 <= version < newest:
                chips.append(f"<span style='color:#1D4E99'>{name}: {status.version_text(version)} → "
                             f"{status.version_text(newest)}</span>")
                if key in online:
                    outdated.append(online[key])
            else:
                shown = status.version_text(version) if version >= 0 else "installed"
                chips.append(f"<span style='color:#1E9E5A'>{name}: ✓ {shown}</span>")
        line = " &nbsp;·&nbsp; ".join(chips) or "<span style='color:gray'>No 3DS known yet</span>"
        return line, outdated, missing

    def show_store_app(self, slug: str):
        """An app of Universal-DB on its own page, like the recommended ones."""
        app = self._store_app(slug)
        self.shown = f"store:{slug}"
        self._clear_detail()
        back = QPushButton("◀  Homebrew", objectName="link", clicked=lambda: self.apps_stack.setCurrentIndex(0))
        self.detail_layout.addWidget(back, alignment=Qt.AlignLeft)
        self.store_cover = QLabel()
        self.detail_layout.addWidget(self.store_cover)
        self._show_preview(None, None, app.title)
        shown = self.shown
        threading.Thread(target=lambda: self._store_art.emit(
            shown, app.icon_url and homebrew.picture(app.icon_url),
            app.image_url and homebrew.picture(app.image_url)), daemon=True).start()
        facts = [f"by {app.author}"] if app.author else []
        facts += [app.version, f"licence {app.licence or 'unknown'}", f"★ {app.stars}", f"updated {app.updated}"]
        heading = QLabel(f"<span style='font-size:24px; font-weight:700'>{app.title}</span>"
                         f"<br><span style='color:gray'>{' · '.join(f for f in facts if f)}</span>")
        self.detail_layout.addWidget(heading)
        body = QLabel(app.long_description or app.description, wordWrap=True, openExternalLinks=True)
        body.setTextFormat(Qt.MarkdownText)
        body.setMaximumWidth(760)
        self.detail_layout.addWidget(body)
        if app.source:
            self.detail_layout.addWidget(QLabel(f"<a href='{app.source}'>{app.source.split('//')[-1]} ↗</a>",
                                                openExternalLinks=True))
        line, outdated, missing = self._store_status(app)
        state = QLabel(line, wordWrap=True)
        state.setTextFormat(Qt.RichText)
        self.detail_layout.addWidget(state)
        if app.allowed:
            self.detail_layout.addWidget(self._store_action(app, outdated, missing), alignment=Qt.AlignLeft)
        else:  # vcinject passes on only what an open licence allows
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            page = QPushButton("Get it from its author ↗", objectName="primary")
            page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(app.source)))
            page.setEnabled(bool(app.source))
            self.detail_layout.addWidget(page, alignment=Qt.AlignLeft)
            self.detail_layout.addWidget(QLabel("No licence that allows passing it on is known, so vcinject "
                                                "doesn't download it itself.", objectName="muted"))
        self._add_screenshots(f"store:{slug}", app.screenshots)
        self.detail_layout.addStretch(1)
        self.apps_stack.setCurrentIndex(1)
        if app.allowed and slug not in self.store_versions and set(app.title_ids) & self._installed_ids():
            threading.Thread(target=self._read_store_version, args=(app,), daemon=True).start()

    def _read_store_version(self, app: homebrew.StoreApp):
        try:
            self._store_version.emit(app.slug, homebrew.cia_title_at(app.cia_url)[1])
        except (InjectError, OSError, ValueError):
            pass

    def _store_action(self, app: homebrew.StoreApp, outdated: list, missing: list) -> QToolButton:
        key = f"store:{app.slug}"
        fetch = lambda: homebrew.store_download(app)  # noqa: E731
        return self._action_button(key, app.allowed, outdated, missing, fetch)


    def install_over_wifi(self, key: str, consoles: list, fetch=None):
        """Downloads the app (``fetch()`` → .cia) and installs it on ``consoles`` over Wi-Fi."""
        fetch = fetch or (lambda: homebrew.download(homebrew.APPS[key]))
        self.state[key] = "Downloading…"
        self._refresh_apps()

        def progress(text, sent, total):
            percent = sent * 100 // max(total, 1) // 10 * 10
            text = f"Installing… {percent} %"
            if self.state.get(key) != text:
                self._progress.emit(key, text)

        def work():
            try:
                path = fetch()
                self._done.emit(key, " · ".join(wifi_install.install(consoles, [path], progress)))
            except (InjectError, OSError) as exc:
                self._done.emit(key, f"Failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _show_progress(self, key: str, text: str):
        self.state[key] = text
        self._refresh_apps()

    def _install_done(self, key: str, message: str):
        self.state.pop(key, None)
        name = homebrew.APPS[key].name if key in homebrew.APPS else key.split(":", 1)[-1]
        self.main.statusBar().showMessage(f"{name}: {message}", 15000)
        self._refresh_apps()

    def update_everything(self):
        for data in self.apps.values():
            outdated, _missing = self._needing(data)
            if outdated:
                self.install_over_wifi(data.app.key, outdated)
        for app in self.store:
            _line, outdated, _missing = self._store_status(app)
            if outdated and app.allowed:
                self.install_over_wifi(f"store:{app.slug}", outdated, lambda app=app: homebrew.store_download(app))

    def install(self, key: str, fetch=None):
        """By QR code: download, then the QR code appears on the game page."""
        fetch = fetch or (lambda: homebrew.download(homebrew.APPS[key]))
        self.state[key] = "Downloading…"
        self._refresh_apps()

        def work():
            try:
                self._downloaded.emit(key, fetch(), "")
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


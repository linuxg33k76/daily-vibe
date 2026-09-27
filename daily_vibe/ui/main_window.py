"""Main window: journal picker, calendar, tags + timeline/search on the left,
editor + preview on the right. Everything is scoped to the selected journal of
the library (except "All journals" search / tag scope)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import urllib.parse

from PySide6.QtCore import QDate, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QGuiApplication, QIcon,
    QKeySequence, QPixmap, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QComboBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QCheckBox, QProgressBar, QPushButton, QSplitter, QStackedWidget, QTextBrowser, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from daily_vibe import APP_DISPLAY_NAME, __version__, images, markers, migrate, security, theming
from daily_vibe import tag_rename
from daily_vibe import tags as tagmod
from daily_vibe.journal_meta import meta_path
from daily_vibe.library import Library, LibraryError
from daily_vibe.export import export_pdf, export_pdf_pairs
from daily_vibe.highlighter import MarkdownHighlighter
from daily_vibe.streak import StreakTracker
from daily_vibe.ui.dialogs import ExportRangeDialog, TransferJob, ask_relocation
from daily_vibe.ui.lock import IdleWatcher, LockScreen
from daily_vibe.ui.panels import OnThisDayPanel
from daily_vibe.config import Config, config_path, user_plugins_dir
from daily_vibe.plugin_manager import PluginManager
from daily_vibe.render import markdown_to_html
from daily_vibe.storage import Journal, render_template
from daily_vibe.ui.find_bar import FindBar
from daily_vibe.ui.editor import MarkdownEditor
from daily_vibe.ui.journal_settings import JournalSettingsDialog
from daily_vibe.ui.tag_rename_dialog import TagRenameDialog
from daily_vibe.ui.tag_entries import TagEntriesView
from daily_vibe.ui.tag_history_dialog import TagRenameHistoryDialog
from daily_vibe.ui.settings_dialog import SettingsDialog
from daily_vibe.workers import PluginRunner, TaskRunner

ICON_PATH = str(Path(__file__).resolve().parent.parent / "resources" / "icon.svg")


def migrate_target_name(root: Path) -> str:
    """Journal the old layout migrates into. An existing "Personal" journal is
    merged into (never overwritten: conflicting files are skipped and reported)."""
    return "Personal"


VIEW_MODES = [  # (config value, label, shortcut)
    ("live", "Live Preview", "Ctrl+1"),
    ("split", "Source + Preview", "Ctrl+2"),
    ("source", "Source Only", "Ctrl+3"),
    ("reading", "Reading", "Ctrl+4"),
]


def qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.tag_index = tagmod.TagIndex()
        self.tag_filter: list[str] = []   # active tag filter (AND), original spelling
        self._init_library()
        self.plugins = PluginManager(config)
        self.current_date: dt.date = dt.date.today()
        self._loading = False
        self._dirty = False
        self._highlighted: set[dt.date] = set()
        self._settings_dialog: SettingsDialog | None = None
        self.locked = False
        self.limiter = security.AttemptLimiter()
        self.streaks = StreakTracker()
        self.tasks = TaskRunner(self, max_threads=2)
        self._transfer_job: TransferJob | None = None
        self._last_summary_box: QMessageBox | None = None
        self._migration_box: QMessageBox | None = None
        self.last_migration = None
        self.last_tag_rename = None

        self.themes = theming.ThemeRegistry()
        self.theme = theming.theme_for_config(self.themes, config.data["appearance"], theming.system_prefers_dark())

        self.runner = PluginRunner(self.plugins, lambda: self.journal, self)
        self.runner.open_entry_handler = self._apply_block_to_open_entry
        self.runner.pending_changed.connect(self._on_pending_changed)
        self.runner.result_applied.connect(self._on_plugin_result)

        self.autosave_timer = QTimer(self, singleShot=True, interval=int(config.data.get("autosave_ms", 1000)))
        self.autosave_timer.timeout.connect(self.save_current)
        self.preview_timer = QTimer(self, singleShot=True, interval=250)
        self.preview_timer.timeout.connect(self.update_preview)

        self._build_ui()
        self._build_menus()
        self._build_status()
        self.apply_appearance()
        self.set_view_mode(self.config.data.get("view_mode", "live"), save=False)
        self.refresh_journals()
        try:  # follow OS light/dark switches live (Qt 6.5+)
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except AttributeError:
            pass

        self.idle = IdleWatcher(self)
        self.idle.idle.connect(self._on_idle)
        if QApplication.instance() is not None:
            QApplication.instance().installEventFilter(self.idle)
        self._apply_security_settings()

        if self.password_enabled():
            self.lock()  # before any entry content is loaded
        else:
            self.refresh_calendar()
            self.refresh_list()
            self.refresh_tags()
        QTimer.singleShot(0, self.cleanup_tag_backups)  # tag-rename backup retention
        for path, err in self.plugins.load_errors.items():
            self.statusBar().showMessage(f"Plugin load error in {Path(path).name}: {err}", 10000)

    # UI construction ---------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle(APP_DISPLAY_NAME)
        left_top = QWidget()
        lv = QVBoxLayout(left_top)
        lv.setContentsMargins(6, 6, 6, 0)

        jrow = QHBoxLayout()
        self.journal_combo = QComboBox()
        self.journal_combo.setObjectName("journalPicker")
        self.journal_combo.setToolTip("Journal (each journal is its own folder in the library)")
        self.journal_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.journal_combo.activated.connect(self._journal_combo_activated)
        self.new_journal_btn = QToolButton()
        self.new_journal_btn.setText("+")
        self.new_journal_btn.setToolTip("New journal…")
        self.new_journal_btn.clicked.connect(lambda: self.new_journal_dialog())
        self.journal_gear_btn = QToolButton()
        self.journal_gear_btn.setText("⚙")
        self.journal_gear_btn.setToolTip("Journal settings…")
        self.journal_gear_btn.clicked.connect(self.open_journal_settings)
        self.journal_combo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.journal_combo.customContextMenuRequested.connect(self._journal_context_menu)
        jrow.addWidget(self.journal_combo, 1)
        jrow.addWidget(self.journal_gear_btn)
        jrow.addWidget(self.new_journal_btn)
        lv.addLayout(jrow)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.clicked.connect(lambda q: self.open_date(pydate(q)))
        self.calendar.currentPageChanged.connect(lambda *_: self.refresh_calendar())
        lv.addWidget(self.calendar)

        buttons = QHBoxLayout()
        self.today_btn = QPushButton("Today")
        self.today_btn.setToolTip("Open today's entry (creates it from the template if missing)")
        self.today_btn.clicked.connect(lambda: self.open_today(create=True))
        self.refresh_btn = QPushButton("Refresh plugins")
        self.refresh_btn.setToolTip("Re-run enabled plugins and replace their blocks in this entry")
        self.refresh_btn.clicked.connect(self.refresh_plugin_blocks)
        buttons.addWidget(self.today_btn)
        buttons.addWidget(self.refresh_btn)
        lv.addLayout(buttons)

        self.streak_label = QLabel()
        self.streak_label.setObjectName("streak")
        self.streak_label.setWordWrap(True)
        lv.addWidget(self.streak_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search entries…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(lambda _t: self.search_timer.start())
        self.search_timer = QTimer(self, singleShot=True, interval=250)
        self.search_timer.timeout.connect(self.refresh_list)
        self.search_box.setToolTip("Words must all appear. Add tag:name to require a tag, e.g.  tag:work standup")
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("This journal", "journal")
        self.scope_combo.addItem("All journals", "all")
        self.scope_combo.setToolTip("Search / tag scope")
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(self.config.data.get("search_scope", "journal"))))
        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        srow = QHBoxLayout()
        srow.addWidget(self.search_box, 1)
        srow.addWidget(self.scope_combo)
        lv.addLayout(srow)

        # Tag panel (collapsible)
        self.tags_toggle = QToolButton()
        self.tags_toggle.setObjectName("tagsHeader")
        self.tags_toggle.setCheckable(True)
        self.tags_toggle.setChecked(True)
        self.tags_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tags_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.tags_toggle.setText("Tags")
        self.tags_toggle.setAutoRaise(True)
        self.tags_toggle.toggled.connect(self._toggle_tag_panel)
        self.clear_tags_btn = QToolButton()
        self.clear_tags_btn.setText("Clear filter")
        self.clear_tags_btn.setAutoRaise(True)
        self.clear_tags_btn.clicked.connect(self.clear_tag_filter)
        self.clear_tags_btn.setVisible(False)
        trow = QHBoxLayout()
        trow.addWidget(self.tags_toggle)
        trow.addStretch(1)
        trow.addWidget(self.clear_tags_btn)
        lv.addLayout(trow)
        self.tag_list = QListWidget()
        self.tag_list.setObjectName("tagList")
        self.tag_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tag_list.setToolTip("Tick tags to filter the timeline (entries must have all ticked tags)")
        self.tag_list.setMaximumHeight(130)
        self.tag_list.itemChanged.connect(self._tag_item_changed)
        self.tag_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tag_list.customContextMenuRequested.connect(self._tag_context_menu)
        self.tag_list.itemDoubleClicked.connect(
            lambda it: self.show_tag_entries(it.data(Qt.ItemDataRole.UserRole)))
        lv.addWidget(self.tag_list)

        self.list_label = QLabel("Timeline")
        lv.addWidget(self.list_label)
        self.entry_list = QListWidget()
        self.entry_list.setWordWrap(True)
        self.entry_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.entry_list.itemActivated.connect(self._list_item_opened)
        self.entry_list.itemClicked.connect(self._list_item_opened)
        lv.addWidget(self.entry_list, 1)

        self.on_this_day = OnThisDayPanel()
        self.on_this_day.open_requested.connect(self.open_date)
        self.left_split = QSplitter(Qt.Orientation.Vertical)
        self.left_split.addWidget(left_top)
        otd_wrap = QWidget()
        ov = QVBoxLayout(otd_wrap)
        ov.setContentsMargins(6, 0, 6, 6)
        ov.addWidget(self.on_this_day)
        self.left_split.addWidget(otd_wrap)
        self.left_split.setSizes([680, 160])
        self._otd_wrap = otd_wrap
        otd_wrap.setVisible(bool(self.config.data.get("show_on_this_day", True)))
        left = self.left_split

        self.editor = MarkdownEditor()
        self.editor.image_handler = self.import_image
        # contentsChange (not textChanged): Live Preview re-highlighting emits
        # textChanged without any edit; real edits always add/remove characters.
        self.editor.document().contentsChange.connect(self._on_contents_change)
        self.editor.setup_highlighting(self.theme, MarkdownHighlighter, live=False)
        self.highlighter = self.editor.source_hl
        self.editor.tag_activated.connect(self.show_tag_menu)
        self.editor.tag_entries_requested.connect(self.show_tag_entries)
        self.editor.tag_rename_requested.connect(lambda t: self.rename_tag_dialog(t))
        self.editor.link_activated.connect(self._open_link)

        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)  # tag: links filter; http(s) opens the browser
        self.preview.anchorClicked.connect(self._preview_link)

        self.date_label = QLabel()
        self.date_label.setObjectName("dateLabel")
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.date_label)
        self.editor_split = QSplitter(Qt.Orientation.Horizontal)
        self.editor_split.addWidget(self.editor)
        self.editor_split.addWidget(self.preview)
        self.editor_split.setSizes([550, 550])
        rv.addWidget(self.editor_split, 1)
        self.find_bar = FindBar(self.editor)
        self.find_bar.message.connect(lambda m: self.statusBar().showMessage(m, 4000))
        rv.addWidget(self.find_bar)
        self.preview.setVisible(bool(self.config.data.get("show_preview", True)))

        self.tag_view = TagEntriesView()
        self.tag_view.setup(self.library, lambda: self.journal, self.tag_index, self.theme)
        self.tag_view.back_requested.connect(self.close_tag_entries)
        self.tag_view.open_requested.connect(self._open_from_tag_view)
        self.tag_view.export_requested.connect(self.export_tag_entries_dialog)
        self.tag_view.rename_requested.connect(lambda t: self.rename_tag_dialog(t))
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(right)
        self.right_stack.addWidget(self.tag_view)
        self._editor_page = right

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(left)
        main_split.addWidget(self.right_stack)
        main_split.setSizes([320, 1080])
        main_split.setStretchFactor(1, 1)
        self.lock_screen = LockScreen(ICON_PATH)
        self.lock_screen.unlock_requested.connect(self.try_unlock)
        self.stack = QStackedWidget()
        self.stack.addWidget(main_split)
        self.stack.addWidget(self.lock_screen)
        self.setCentralWidget(self.stack)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        self._add(file_menu, "Change Library Folder…", self.change_journal_root)
        self._add(file_menu, "Open Library Folder", lambda: self._open_path(self.library.root))
        export = file_menu.addMenu("Export")
        self._add(export, "Current Entry to PDF…", self.export_current_pdf, "Ctrl+Shift+E")
        self._add(export, "Date Range to PDF…", self.export_range_pdf)
        self._add(export, "Whole Journal to PDF…", self.export_journal_pdf_dialog)
        self._add(export, "Journal Folder as Zip…", self.export_journal_zip_dialog)
        advanced = file_menu.addMenu("Advanced")
        self._add(advanced, "Edit Config File (config.toml)", lambda: self._open_path(config_path()))
        self._add(advanced, "Reload Config", self.reload_config)
        self._add(advanced, "Open User Themes Folder", self._open_user_themes)
        self._add(advanced, "Reload Themes", self._reload_themes)
        file_menu.addSeparator()
        quit_action = self._add(file_menu, "Quit", self.close, QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)

        edit = mb.addMenu("&Edit")
        self._add(edit, "Undo", self.editor.undo, QKeySequence.StandardKey.Undo)
        self._add(edit, "Redo", self.editor.redo, QKeySequence.StandardKey.Redo)
        edit.addSeparator()
        self._add(edit, "Cut", self.editor.cut, QKeySequence.StandardKey.Cut)
        self._add(edit, "Copy", self.editor.copy, QKeySequence.StandardKey.Copy)
        self._add(edit, "Paste", self.editor.paste, QKeySequence.StandardKey.Paste)
        edit.addSeparator()
        # "Ctrl+," is Cmd+, on macOS; PreferencesRole moves it into the app menu there.
        self._add(edit, "Find…", lambda: self.open_find(False), QKeySequence.StandardKey.Find)
        self._add(edit, "Replace…", lambda: self.open_find(True), "Ctrl+H")
        self._add(edit, "Find Next", self.find_next, "F3")
        self._add(edit, "Find Previous", self.find_prev, "Shift+F3")
        edit.addSeparator()
        self._add(edit, "Format Table", self.format_table, "Ctrl+Alt+T")
        self._add(edit, "Insert Emoji…", self.insert_emoji_dialog, "Ctrl+.")
        self._add(edit, "Rename Tag…", lambda: self.rename_tag_dialog())
        edit.addSeparator()
        self.prefs_action = self._add(edit, "Preferences…", self.open_settings, "Ctrl+,")
        self.prefs_action.setMenuRole(QAction.MenuRole.PreferencesRole)

        jm = mb.addMenu("&Journal")
        self.journal_menu = jm
        self._add(jm, "New Journal…", lambda: self.new_journal_dialog(), "Ctrl+Shift+N")
        self._add(jm, "Rename Journal…", self.rename_journal_dialog)
        self._add(jm, "Open Journal Folder", lambda: self._open_path(self.journal.root))
        self.journal_settings_action = self._add(jm, "Journal Settings…", self.open_journal_settings, "Ctrl+Shift+J")
        jexport = jm.addMenu("Export Journal")
        self._add(jexport, "As Zip (whole folder)…", self.export_journal_zip_dialog)
        self._add(jexport, "As PDF (all entries)…", self.export_journal_pdf_dialog)
        self._add(jm, "Move or Copy Journal To…", self.move_journal_dialog)
        jm.addSeparator()
        self.hidden_menu = jm.addMenu("Show Removed Journals")
        self.hidden_menu.aboutToShow.connect(self._rebuild_hidden_menu)
        self._add(jm, "Remove from Library (keep files)…", self.remove_journal_dialog)
        self._add(jm, "Delete Journal (move to Trash)…", self.delete_journal_dialog)
        jm.addSeparator()
        self._add(jm, "Rename Tag…", lambda: self.rename_tag_dialog())
        self.undo_rename_action = self._add(jm, "Undo Last Tag Rename", self.undo_last_tag_rename)
        self._add(jm, "Tag Rename History…", self.tag_history_dialog)
        self._add(jm, "Tag Browser…", lambda: self.show_tag_entries(None), "Ctrl+Shift+T")
        jm.aboutToShow.connect(self._update_undo_rename_action)
        jm.addSeparator()
        jadv = jm.addMenu("Advanced")
        self._add(jadv, "Edit Journal Settings File (.dailyvibe.toml)", self._edit_journal_meta)
        self._add(jadv, "Open Tag-Rename Backups Folder",
                  lambda: self._open_path(self._ensure_dir(self.journal.root / tag_rename.BACKUP_DIR)))
        self._add(jadv, "Migrate Old-Layout Entries…", lambda: self.maybe_migrate(force=True))

        entry = mb.addMenu("&Entry")
        self._add(entry, "Today", lambda: self.open_today(create=True), "Ctrl+T")
        self._add(entry, "Create Entry (with template)", lambda: self.create_entry(self.current_date))
        self._add(entry, "Previous Day", lambda: self.open_date(self.current_date - dt.timedelta(days=1)), "Alt+Left")
        self._add(entry, "Next Day", lambda: self.open_date(self.current_date + dt.timedelta(days=1)), "Alt+Right")
        entry.addSeparator()
        self._add(entry, "Insert Image…", self.insert_image_dialog, "Ctrl+Shift+I")
        self._add(entry, "Refresh Plugin Blocks", self.refresh_plugin_blocks, "Ctrl+R")
        self._add(entry, "Save", self.save_current, QKeySequence.StandardKey.Save)
        self._add(entry, "Find in Entries", lambda: self.search_box.setFocus(), "Ctrl+Shift+F")
        entry.addSeparator()
        self.lock_action = self._add(entry, "Lock Now", self.lock_now, "Ctrl+L")

        view = mb.addMenu("&View")
        mode_menu = view.addMenu("View Mode")
        self.view_group = QActionGroup(self)
        self.view_group.setExclusive(True)
        self.view_actions: dict[str, QAction] = {}
        for value, label, shortcut in VIEW_MODES:
            a = QAction(label, self, checkable=True)
            a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(f"{label} ({shortcut})")
            a.triggered.connect(lambda _c=False, v=value: self.set_view_mode(v))
            self.view_group.addAction(a)
            mode_menu.addAction(a)
            self.view_actions[value] = a
        self.view_toolbar = QToolBar("View Mode", self)
        self.view_toolbar.setObjectName("viewToolbar")
        self.view_toolbar.setMovable(False)
        self.view_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for a in self.view_actions.values():
            self.view_toolbar.addAction(a)
        self.view_toolbar.addSeparator()
        tb = QAction("Tags…", self)
        tb.setToolTip("Tag browser: all entries with a tag (Ctrl+Shift+T)")
        tb.triggered.connect(lambda: self.show_tag_entries(None))
        self.view_toolbar.addAction(tb)
        em = QAction("😀", self)
        em.setToolTip("Insert emoji (Ctrl+.)")
        em.triggered.connect(self.insert_emoji_dialog)
        self.view_toolbar.addAction(em)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.view_toolbar)
        self.preview_action = self._add(view, "Show Preview", self.toggle_preview, "Ctrl+P")
        self.preview_action.setCheckable(True)
        self.preview_action.setChecked(self.preview.isVisible())
        self.otd_action = self._add(view, "Show On This Day", self.toggle_on_this_day, "Ctrl+Shift+D")
        self.otd_action.setCheckable(True)
        self.otd_action.setChecked(bool(self.config.data.get("show_on_this_day", True)))
        view.addSeparator()
        theme_menu = view.addMenu("Theme Mode")
        self.mode_actions = {}
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            a = QAction(label, self, checkable=True)
            a.triggered.connect(lambda _c=False, v=value: self._set_theme_mode(v))
            theme_menu.addAction(a)
            self.mode_actions[value] = a

        self.plugins_menu = mb.addMenu("&Plugins")
        self._rebuild_plugins_menu()

        help_menu = mb.addMenu("&Help")
        about = self._add(help_menu, f"About {APP_DISPLAY_NAME}", self.show_about)
        about.setMenuRole(QAction.MenuRole.AboutRole)

    def _build_status(self) -> None:
        self.busy_label = QLabel()
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)  # indeterminate "spinner"
        self.busy_bar.setMaximumWidth(90)
        self.busy_bar.setMaximumHeight(12)
        self.busy_bar.setTextVisible(False)
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.busy_bar)
        self.busy_label.hide()
        self.busy_bar.hide()

    def _add(self, menu, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda *_: slot())
        menu.addAction(action)
        return action

    def _rebuild_plugins_menu(self) -> None:
        m = self.plugins_menu
        m.clear()
        enabled = set(self.config.enabled_plugins)
        for name, plugin in sorted(self.plugins.plugins.items(), key=lambda kv: kv[1].display_name.lower()):
            label = f"{plugin.display_name}  ({plugin.source}, v{plugin.version})"
            action = QAction(label, self, checkable=True, checked=name in enabled)
            action.setToolTip(plugin.description)
            action.setStatusTip(plugin.description)
            action.toggled.connect(lambda on, n=name: self._toggle_plugin(n, on))
            m.addAction(action)
        m.addSeparator()
        self._add(m, "Refresh Plugin Blocks", self.refresh_plugin_blocks)
        self._add(m, "Reload Plugins", self.reload_plugins)
        self._add(m, "Open User Plugins Folder", self._open_user_plugins)

    # Plugins -----------------------------------------------------------------
    def _toggle_plugin(self, name: str, on: bool) -> None:
        self.config.set_plugin_enabled(name, on)
        self.config.save()
        self.statusBar().showMessage(f"Plugin {name} {'enabled' if on else 'disabled'}", 4000)

    def reload_plugins(self) -> None:
        self.plugins.discover()
        self._rebuild_plugins_menu()
        self.statusBar().showMessage(f"Loaded {len(self.plugins.plugins)} plugins", 4000)

    def _open_user_plugins(self) -> None:
        d = user_plugins_dir()
        d.mkdir(parents=True, exist_ok=True)
        self._open_path(d)

    def refresh_plugin_blocks(self) -> None:
        """Re-run enabled plugins for the open entry in the background."""
        if not self.plugins.enabled(self.journal):
            self.statusBar().showMessage("No plugins enabled (Edit → Preferences → Plugins)", 5000)
            return
        self.runner.run(self.current_date, journal=self.journal)

    def _apply_block_to_open_entry(self, date: dt.date, name: str, block_md: str) -> bool:
        """Runner callback: patch the editor buffer if `date` is the open entry.
        Only the marker block is replaced, so unsaved edits elsewhere survive."""
        if self.locked or not self._loaded_once or date != self.current_date:
            return False  # while locked, results go straight to the file on disk
        text = self.editor.toPlainText()
        span = markers.block_span(text, name)
        if span is None:
            self._replace_editor_text(markers.replace_block(text, name, block_md))
            return True
        start, end = markers.utf16_len(text[: span[0]]), markers.utf16_len(text[: span[1]])
        cursor = QTextCursor(self.editor.document())
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(markers.wrap_block(name, block_md))
        cursor.endEditBlock()
        return True  # textChanged -> dirty -> autosave

    def _on_plugin_result(self, date: dt.date, result) -> None:
        if not result.ok:
            self.statusBar().showMessage(f"Plugin {result.name} failed for {date}: {result.error}", 8000)
        if date != self.current_date and not self.locked:
            self.refresh_list()

    def _on_pending_changed(self, pending: list) -> None:
        if not pending:
            self.busy_label.hide()
            self.busy_bar.hide()
            self.statusBar().showMessage("Plugins finished", 3000)
            return
        titles = ", ".join(dict.fromkeys(title for _d, title in pending))
        dates = {d for d, _t in pending}
        suffix = "" if dates == {self.current_date} else f" ({', '.join(sorted(d.isoformat() for d in dates))})"
        self.busy_label.setText(f"Fetching {titles}…{suffix}")
        self.busy_label.show()
        self.busy_bar.show()

    def _placeholder_blocks(self) -> str:
        blocks = []
        for p in self.plugins.enabled(self.journal):
            header = f"## {p.title}\n\n" if p.title else ""
            blocks.append(markers.wrap_block(p.name, header + f"_⏳ Fetching {p.title or p.name}…_"))
        return "\n\n".join(blocks)

    # Entries -----------------------------------------------------------------
    def create_entry(self, date: dt.date) -> None:
        """Write the templated entry immediately (with placeholder plugin blocks),
        then fill the blocks in as the background plugins finish."""
        if self.journal.exists(date) and self.journal.read(date).strip():
            self.open_date(date)
            return
        self.save_current()
        text = render_template(self.journal.template(self.config.data["template"]), date, self._placeholder_blocks())
        self.journal.write(date, text)
        self.refresh_calendar()
        self.refresh_list()
        self.open_date(date, force=True)
        self.runner.run(date, journal=self.journal)
        journal = self.journal
        self.tasks.submit(lambda: self.plugins.run_entry_created_hooks(date, journal),
                          lambda errs: errs and self.statusBar().showMessage("on_entry_created: " + "; ".join(errs), 8000))

    def open_today(self, create: bool = True) -> None:
        today = dt.date.today()
        if create and not self.journal.exists(today):
            self.create_entry(today)
        else:
            self.open_date(today, force=True)

    def open_date(self, date: dt.date, force: bool = False) -> None:
        if self.locked:
            self.current_date = date  # opened after unlock
            return
        if date == self.current_date and not force and self._loaded_once:
            return
        self.save_current()
        self.current_date = date
        self._loading = True
        self.editor.base_dir = self.journal.entry_dir(date)
        self.editor.setPlainText(self.journal.read(date))
        self._loading = False
        self._dirty = False
        self._loaded_once = True
        if self.calendar.selectedDate() != qdate(date):
            self.calendar.setSelectedDate(qdate(date))
        exists = self.journal.exists(date)
        self.date_label.setText(
            f"{date:%A, %B} {date.day}, {date:%Y}" + ("" if exists else "   — no entry yet")
            + f"      {self.journal.entry_path(date)}"
        )
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — {self.journal.name} — {date.isoformat()}")
        self.update_preview()
        if not self._otd_wrap.isHidden():
            self.on_this_day.update_for(self.journal, date)

    _loaded_once = False

    def _list_item_opened(self, item: QListWidgetItem) -> None:
        date = item.data(Qt.ItemDataRole.UserRole)
        folder = item.data(Qt.ItemDataRole.UserRole + 1)
        if folder and folder != self.journal.root.name:
            self.switch_journal(folder, date)
            return
        if date:
            self.open_date(date)

    def _on_contents_change(self, _pos: int, removed: int, added: int) -> None:
        if (removed or added) and not self.editor.formatting:
            self._on_text_changed()

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self.autosave_timer.start()
        self.preview_timer.start()

    def _replace_editor_text(self, text: str) -> None:
        cursor = self.editor.textCursor()
        pos = cursor.position()
        cursor.beginEditBlock()  # keeps it undoable
        cursor.select(cursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        cursor.setPosition(min(pos, len(text)))
        self.editor.setTextCursor(cursor)

    def save_current(self) -> None:
        if not self._dirty or self.locked:
            return
        text = self.editor.toPlainText()
        existed = self.journal.exists(self.current_date)
        if not existed and not text.strip():
            self._dirty = False
            return
        self.journal.write(self.current_date, text)
        self._dirty = False
        self.statusBar().showMessage(f"Saved {self.journal.entry_path(self.current_date).name}", 2000)
        if not existed:
            self.refresh_calendar()
            self.refresh_list()
        else:
            self._update_list_item(self.current_date)
        self.update_streak()
        self._refresh_tags_if_changed()

    # Preview -----------------------------------------------------------------
    def toggle_preview(self) -> None:
        """Ctrl+P: show/hide the rendered preview pane next to the source."""
        mode = self.view_mode
        target = {"split": "source", "source": "split", "live": "split", "reading": "live"}[mode]
        self.set_view_mode(target)

    @property
    def view_mode(self) -> str:
        return getattr(self, "_view_mode", "live")

    def set_view_mode(self, mode: str, save: bool = True) -> None:
        """live = Live Preview editor; split = source + rendered preview;
        source = plain source editor; reading = rendered preview only."""
        if mode not in {m for m, _l, _s in VIEW_MODES}:
            mode = "live"
        self._view_mode = mode
        self.editor.set_live(mode == "live")
        self.editor.setVisible(mode != "reading")
        if mode == "reading" and getattr(self, "find_bar", None) is not None and self.find_bar.is_open:
            self.find_bar.close_bar()
        self.preview.setVisible(mode in ("split", "reading"))
        if mode == "split":
            self.editor_split.setSizes([550, 550])
        self.preview_action.setChecked(self.preview.isVisible())
        if mode in self.view_actions:
            self.view_actions[mode].setChecked(True)
        if save:
            self.config.data["view_mode"] = mode
            if mode in ("split", "source"):
                self.config.data["show_preview"] = mode == "split"
            self.config.save()
        if self.right_stack.currentWidget() is not self._editor_page:
            self.close_tag_entries()
        self.update_preview()
        if mode == "reading":
            self.preview.setFocus()
        else:
            self.editor.setFocus()

    def update_preview(self) -> None:
        if not self.preview.isVisible() or self.locked:
            return
        base = self.journal.entry_dir(self.current_date)
        max_w = max(200, self.preview.viewport().width() - 40)
        css = theming.preview_css(self.theme, int(self.config.data["appearance"].get("preview_font_size", 14)))
        html = markdown_to_html(self.editor.toPlainText(), base, max_w, css,
                                stripe_bg=theming.table_stripe_bg(self.theme))
        bar = self.preview.verticalScrollBar()
        pos = bar.value()
        self.preview.document().setBaseUrl(QUrl.fromLocalFile(str(base) + "/"))
        self.preview.setHtml(html)
        bar.setValue(pos)

    # Images ------------------------------------------------------------------
    def import_image(self, source: Path | bytes) -> str | None:
        img_cfg = self.config.data.get("images", {})
        try:
            out = images.convert_to_webp(
                source,
                self.journal.assets_dir(self.current_date),
                quality=int(img_cfg.get("webp_quality", 80)),
                max_dimension=int(img_cfg.get("max_dimension", 1920)),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Image import failed", f"Could not import image:\n{exc}")
            return None
        alt = Path(source).stem if isinstance(source, Path) else "pasted image"
        self.statusBar().showMessage(f"Saved {out.name} ({out.stat().st_size // 1024} KB)", 5000)
        return images.markdown_link(out, self.journal.entry_dir(self.current_date), alt)

    def insert_image_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Insert image", str(Path.home()),
            "Images (" + " ".join(f"*{e}" for e in sorted(images.IMAGE_EXTENSIONS)) + ")")
        links = [self.import_image(Path(f)) for f in files]
        links = [l for l in links if l]
        if links:
            self.editor.insert_at_cursor("\n".join(links) + "\n")

    # Calendar / list -----------------------------------------------------------
    def refresh_calendar(self) -> None:
        dates = set() if self.locked else set(self.journal.list_dates())
        plain = QTextCharFormat()
        for d in self._highlighted - dates:
            self.calendar.setDateTextFormat(qdate(d), plain)
        fmt = QTextCharFormat()
        fmt.setFontWeight(700)
        fmt.setBackground(QBrush(QColor(self.theme.entry_bg)))
        fmt.setForeground(QBrush(QColor(self.theme.entry_fg)))
        for d in dates:
            self.calendar.setDateTextFormat(qdate(d), fmt)
        self._highlighted = dates

    def refresh_list(self) -> None:
        self.entry_list.clear()
        if self.locked:
            return
        self.update_streak()
        query = self.search_box.text().strip()
        all_scope = self.search_scope() == "all"
        tag_q = " ".join(f"tag:{t}" for t in self.tag_filter)
        if query:
            full = f"{query} {tag_q}".strip()
            if all_scope:
                hits = self.library.search(full, tag_index=self.tag_index)
            else:
                hits = self.journal.search(full, tag_index=self.tag_index)
            where = "all journals" if all_scope else self.journal.name
            self.list_label.setText(f"Search results in {where}: {len(hits)}" + self._filter_suffix())
            by_name = {j.name: j.root.name for j in self.library.journals()}
            for h in hits:
                prefix = f"[{h.journal}]  " if all_scope else ""
                item = QListWidgetItem(f"{prefix}{h.date:%a %Y-%m-%d}\n  {h.snippet}")
                item.setData(Qt.ItemDataRole.UserRole, h.date)
                item.setData(Qt.ItemDataRole.UserRole + 1, by_name.get(h.journal, self.journal.root.name))
                self.entry_list.addItem(item)
        elif self.tag_filter:
            journals = self.library.journals() if all_scope else [self.journal]
            pairs = self.tag_index.entries_with(journals, self.tag_filter)
            self.list_label.setText(f"Tagged entries: {len(pairs)}" + self._filter_suffix())
            for journal, d in pairs:
                summary = journal.summary(d)
                prefix = f"[{journal.name}]  " if all_scope else ""
                item = QListWidgetItem(f"{prefix}{d:%a %Y-%m-%d}" + (f"\n  {summary}" if summary else ""))
                item.setData(Qt.ItemDataRole.UserRole, d)
                item.setData(Qt.ItemDataRole.UserRole + 1, journal.root.name)
                self.entry_list.addItem(item)
        else:
            dates = self.journal.list_dates()
            self.list_label.setText(f"Timeline ({len(dates)} entries)")
            for d in dates:
                summary = self.journal.summary(d)
                item = QListWidgetItem(f"{d:%a %Y-%m-%d}" + (f"\n  {summary}" if summary else ""))
                item.setData(Qt.ItemDataRole.UserRole, d)
                self.entry_list.addItem(item)

    def _update_list_item(self, date: dt.date) -> None:
        if self.search_box.text().strip() or self.tag_filter:
            return
        for i in range(self.entry_list.count()):
            item = self.entry_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == date:
                summary = self.journal.summary(date)
                item.setText(f"{date:%a %Y-%m-%d}" + (f"\n  {summary}" if summary else ""))
                return

    # Library / journals --------------------------------------------------------
    def _init_library(self) -> None:
        self.library = Library(self.config.journal_root, self.config.data.get("hidden_journals", []))
        last = self.config.data.get("last_journal") or ""
        if last and (self.library.root / last).is_dir() and last not in self.library.hidden:
            self.journal = self.library.get(last)
        else:
            self.journal = self.library.ensure_default()

    def _journal_icon(self, journal) -> QIcon:
        meta = journal.meta
        color = QColor(meta.get("color") or self.theme.colors.get("accent", "#888888")) if hasattr(self, "theme") \
            else QColor(meta.get("color") or "#888888")
        pm = QPixmap(14, 14)
        pm.fill(color if color.isValid() else QColor("#888888"))
        return QIcon(pm)

    def refresh_journals(self) -> None:
        combo = self.journal_combo
        combo.blockSignals(True)
        combo.clear()
        for j in self.library.journals():
            icon = j.meta.get("icon", "")
            label = f"{icon}  {j.name}" if icon else j.name
            combo.addItem(self._journal_icon(j), label, j.root.name)
        idx = combo.findData(self.journal.root.name)
        combo.setCurrentIndex(max(idx, 0))
        combo.blockSignals(False)

    def _journal_combo_activated(self, index: int) -> None:
        folder = self.journal_combo.itemData(index)
        if folder and folder != self.journal.root.name:
            self.switch_journal(folder)

    def switch_journal(self, folder: str, date: dt.date | None = None) -> None:
        """Make another journal of the library current (saves the open entry first)."""
        if self.locked:
            return
        self.save_current()
        self.journal = self.library.get(folder)
        self.config.data["last_journal"] = folder
        self.config.save()
        self.refresh_journals()
        if self.search_scope() != "all":
            self.tag_filter = []
        self._highlighted_refresh_all()
        self.refresh_list()
        self.refresh_tags()
        self.open_date(date or self.current_date, force=True)
        self.statusBar().showMessage(f"Journal: {self.journal.name}", 3000)

    def create_journal(self, name: str, color: str = "", icon: str = ""):
        journal = self.library.create(name, color, icon)
        self.refresh_journals()
        self.switch_journal(journal.root.name)
        return journal

    def new_journal_dialog(self) -> None:
        name, ok = QInputDialog.getText(self, "New journal", "Name of the new journal:")
        if not ok or not name.strip():
            return
        try:
            self.create_journal(name)
        except (LibraryError, OSError) as exc:
            QMessageBox.warning(self, "New journal", str(exc))

    def rename_journal(self, new_name: str):
        self.save_current()
        old = self.journal.root.name
        journal = self.library.rename(old, new_name)
        hidden = self.config.data.get("hidden_journals", [])
        if old in hidden:
            hidden[hidden.index(old)] = journal.root.name
        self.journal = journal
        self.config.data["last_journal"] = journal.root.name
        self.config.save()
        self.tag_index = tagmod.TagIndex()  # paths changed
        self.refresh_journals()
        self.refresh_list()
        self.refresh_tags()
        self.open_date(self.current_date, force=True)
        return journal

    def rename_journal_dialog(self) -> None:
        if self._runner_busy_for_journal():
            return
        name, ok = QInputDialog.getText(self, "Rename journal", "New name (the folder is renamed too):",
                                        text=self.journal.name)
        if not ok or not name.strip() or name.strip() == self.journal.root.name:
            return
        try:
            self.rename_journal(name)
        except (LibraryError, OSError) as exc:
            QMessageBox.warning(self, "Rename journal", str(exc))

    def _runner_busy_for_journal(self) -> bool:
        if self.runner.is_busy():
            QMessageBox.information(self, "Please wait", "Plugins are still running; try again in a moment.")
            return True
        return False

    def _after_journal_gone(self, folder: str) -> None:
        self._dirty = False
        self.library = Library(self.config.journal_root, self.config.data.get("hidden_journals", []))
        remaining = [j for j in self.library.journals() if j.root.name != folder]
        self.journal = remaining[0] if remaining else self.library.ensure_default()
        self.config.data["last_journal"] = self.journal.root.name
        self.config.save()
        self.tag_filter = []
        self.refresh_journals()
        self._highlighted_refresh_all()
        self.refresh_list()
        self.refresh_tags()
        self.open_date(self.current_date, force=True)

    def remove_journal_from_library(self, folder: str) -> None:
        """Hide a journal from the picker; its folder stays on disk."""
        self.save_current()
        hidden = self.config.data.setdefault("hidden_journals", [])
        if folder not in hidden:
            hidden.append(folder)
        self.config.save()
        self._after_journal_gone(folder)

    def remove_journal_dialog(self) -> None:
        j = self.journal
        if QMessageBox.question(
                self, "Remove from library",
                f'Remove "{j.name}" from the journal list?\n\nNothing is deleted: the folder stays at\n{j.root}\n'
                "and you can bring it back from Journal → Show Removed Journals.") == QMessageBox.StandardButton.Yes:
            self.remove_journal_from_library(j.root.name)

    def restore_journal(self, folder: str) -> None:
        hidden = self.config.data.setdefault("hidden_journals", [])
        if folder in hidden:
            hidden.remove(folder)
        self.config.save()
        self.library.hidden.discard(folder)
        self.refresh_journals()
        self.switch_journal(folder)

    def _rebuild_hidden_menu(self) -> None:
        self.hidden_menu.clear()
        hidden = [h for h in self.config.data.get("hidden_journals", []) if (self.library.root / h).is_dir()]
        if not hidden:
            a = self.hidden_menu.addAction("(none)")
            a.setEnabled(False)
        for h in hidden:
            self._add(self.hidden_menu, h, lambda f=h: self.restore_journal(f))

    def delete_journal(self, folder: str, confirmation: str) -> str:
        """Move a journal folder to the system trash. `confirmation` must equal the
        journal's folder name (typed by the user). Returns the trashed path."""
        if confirmation.strip() != folder:
            raise LibraryError("The name you typed doesn't match; nothing was deleted.")
        self._dirty = False if folder == self.journal.root.name else self._dirty
        path = self.library.trash(folder)
        self._after_journal_gone(folder)
        return path

    def delete_journal_dialog(self) -> None:
        if self._runner_busy_for_journal():
            return
        j = self.journal
        self.save_current()
        n = len(j.list_dates())
        text, ok = QInputDialog.getText(
            self, "Delete journal",
            f'This moves the whole journal folder "{j.root.name}" ({n} entries, images and settings) '
            f"to the system Trash:\n{j.root}\n\nType the journal folder name to confirm:")
        if not ok:
            return
        try:
            path = self.delete_journal(j.root.name, text)
        except (LibraryError, OSError) as exc:
            QMessageBox.warning(self, "Delete journal", str(exc))
            return
        QMessageBox.information(self, "Journal moved to Trash", f"Moved to the Trash:\n{path}")

    def _edit_journal_meta(self) -> None:
        from daily_vibe.journal_meta import load_meta, save_meta
        p = meta_path(self.journal.root)
        if not p.exists():
            save_meta(self.journal.root, {"name": self.journal.name, **load_meta(self.journal.root)})
        self._open_path(p)

    def move_journal_dialog(self) -> None:
        if self._runner_busy_for_journal() or self._transfer_job is not None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Move or copy this journal into…", str(Path.home()))
        if folder:
            self.move_journal_to(Path(folder) / self.journal.root.name)

    def move_journal_to(self, target: Path) -> str:
        """Move/copy the current journal folder (with its .dailyvibe.toml) to `target`.
        Returns "pending" or "cancel"."""
        target = Path(target).expanduser()
        source = self.journal.root
        self.save_current()
        choice = ask_relocation(self, source, target, single_journal=True)
        if choice not in ("move", "copy"):
            return "cancel"
        self.editor.setReadOnly(True)
        job = TransferJob(self, source, target, choice, include_all=True)
        job.done.connect(lambda report, f=source.name: self._journal_transfer_finished(report, f))
        self._transfer_job = job
        job.start()
        return "pending"

    def _journal_transfer_finished(self, report, folder: str) -> None:
        self._transfer_job = None
        self.editor.setReadOnly(False)
        note = ""
        if report.mode == "move" and not (self.library.root / folder).exists():
            note = "\n\nThe journal is no longer in this library (use File → Change Library Folder to open it there)."
            self._after_journal_gone(folder)
        box = QMessageBox(QMessageBox.Icon.Information, "Journal transferred", report.summary() + note, parent=self)
        self._last_summary_box = box
        box.open()

    # Journal settings ----------------------------------------------------------
    @staticmethod
    def _ensure_dir(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _journal_context_menu(self, pos) -> None:
        menu = QMenu(self)
        self._add(menu, "Journal Settings…", self.open_journal_settings)
        self._add(menu, "Rename Journal…", self.rename_journal_dialog)
        self._add(menu, "Open Journal Folder", lambda: self._open_path(self.journal.root))
        menu.addSeparator()
        self._add(menu, "New Journal…", lambda: self.new_journal_dialog())
        self._journal_menu_popup = menu
        menu.popup(self.journal_combo.mapToGlobal(pos))

    def open_journal_settings(self) -> JournalSettingsDialog:
        dlg = getattr(self, "_journal_settings_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            return dlg
        self.save_current()
        self.plugins.discover()
        dlg = JournalSettingsDialog(self.journal, self.config, self.plugins, self.tasks, self)
        dlg.rename_handler = self.rename_journal
        dlg.applied.connect(self._journal_settings_applied)
        self._journal_settings_dialog = dlg
        dlg.open()
        return dlg

    def _journal_settings_applied(self, journal) -> None:
        if journal.root != self.journal.root:  # renamed elsewhere; follow it
            self.journal = journal
        self.refresh_journals()
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — {self.journal.name} — {self.current_date.isoformat()}")
        self.update_streak()
        self.statusBar().showMessage(f"Journal settings saved for {self.journal.name}", 4000)

    # Tag rename ------------------------------------------------------------------
    def _tag_context_menu(self, pos) -> None:
        item = self.tag_list.itemAt(pos)
        if item is None:
            return
        tag = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        self._add(menu, f"Rename #{tag}…", lambda t=tag: self.rename_tag_dialog(t))
        if tagmod.normalize(tag) in {tagmod.normalize(t) for t in self.tag_filter}:
            self._add(menu, "Remove from filter", lambda t=tag: self.remove_tag_filter(t))
        else:
            self._add(menu, "Filter by this tag", lambda t=tag: self.add_tag_filter(t))
        self._tag_menu_popup = menu
        menu.popup(self.tag_list.viewport().mapToGlobal(pos))

    def known_tags(self, scope: str = "all") -> list[str]:
        journals = self.library.journals() if scope == "all" else [self.journal]
        return [d for d, _n in self.tag_index.counts(journals).values()]

    def plan_tag_rename(self, old: str, new: str, nested: bool = True, scope: str = "journal"):
        self.save_current()
        journals = self.library.journals() if scope == "all" else [self.journal]
        return tag_rename.plan_rename(journals, old, new, nested, scope)

    def apply_tag_rename(self, plan) -> bool:
        """Apply a previewed rename; returns True if anything changed."""
        self.save_current()
        result = tag_rename.apply_rename(plan)
        self.last_tag_rename = result
        self.cleanup_tag_backups()
        open_path = self.journal.entry_path(self.current_date)
        if open_path in result.changed:
            self._reload_open_entry_keep_cursor()
        mapper = tag_rename.make_mapper(plan.old, plan.new, plan.nested)
        if self.tag_filter:
            self.tag_filter = [mapper(t) or t for t in self.tag_filter]
            self.set_tag_filter(self.tag_filter)  # de-duplicates merged tags
        self.refresh_tags()
        self.refresh_list()
        self.update_preview()
        box = QMessageBox(QMessageBox.Icon.Information, "Tag renamed", result.summary(), parent=self)
        self._last_summary_box = box
        box.open()
        return bool(result.changed)

    def _reload_open_entry_keep_cursor(self) -> None:
        cursor = self.editor.textCursor()
        pos = cursor.position()
        scroll = self.editor.verticalScrollBar().value()
        self._loading = True
        self.editor.setPlainText(self.journal.read(self.current_date))
        self._loading = False
        self._dirty = False
        cursor = self.editor.textCursor()
        cursor.setPosition(min(pos, len(self.editor.toPlainText())))
        self.editor.setTextCursor(cursor)
        self.editor.verticalScrollBar().setValue(scroll)
        self.update_preview()

    def rename_tag_dialog(self, tag: str | None = None) -> TagRenameDialog:
        self.save_current()
        dlg = TagRenameDialog(self.known_tags("all"), self.plan_tag_rename, self.apply_tag_rename,
                              old=tag or (self.tag_filter[0] if self.tag_filter else ""),
                              scope=self.search_scope(), journal_name=self.journal.name, parent=self)
        self._tag_rename_dialog = dlg
        dlg.open()
        return dlg

    def _update_undo_rename_action(self) -> None:
        backups = tag_rename.find_last_rename(self.library.journals(include_hidden=True))
        desc = tag_rename.describe(backups) if backups else ""
        self.undo_rename_action.setEnabled(bool(backups))
        self.undo_rename_action.setText(f"Undo Last Tag Rename ({desc})" if desc else "Undo Last Tag Rename")

    def undo_last_tag_rename(self, confirm: bool = True):
        backups = tag_rename.find_last_rename(self.library.journals(include_hidden=True))
        if not backups:
            QMessageBox.information(self, "Undo tag rename", "There's no tag rename to undo.")
            return None
        desc = tag_rename.describe(backups)
        if confirm and QMessageBox.question(
                self, "Undo tag rename",
                f"Undo the tag rename {desc}?\n\nEntries edited since then are left alone and reported.") \
                != QMessageBox.StandardButton.Yes:
            return None
        self.save_current()
        result = tag_rename.undo(backups)
        if self.journal.entry_path(self.current_date) in result.restored:
            self._reload_open_entry_keep_cursor()
        self.refresh_tags()
        self.refresh_list()
        self._refresh_tag_view()
        box = QMessageBox(QMessageBox.Icon.Information, "Tag rename undone", f"Undid {desc}.\n\n" + result.summary(),
                          parent=self)
        self._last_summary_box = box
        box.open()
        return result

    def cleanup_tag_backups(self) -> list:
        keep = int(self.config.data.get("tag_rename_keep", 20) or 0)
        days = int(self.config.data.get("tag_rename_keep_days", 0) or 0)
        if keep <= 0 and days <= 0:
            return []
        try:
            return tag_rename.cleanup(self.library.journals(include_hidden=True), keep, days)
        except OSError:
            return []

    def tag_rename_records(self) -> list:
        return tag_rename.history(self.library.journals(include_hidden=True))

    def undo_tag_rename(self, stamp: str):
        """Undo one rename from the history; raises TagRenameError if blocked."""
        self.save_current()
        result = tag_rename.undo_record(self.library.journals(include_hidden=True), stamp)
        if self.journal.entry_path(self.current_date) in result.restored:
            self._reload_open_entry_keep_cursor()
        self.refresh_tags()
        self.refresh_list()
        self._refresh_tag_view()
        return result

    def tag_history_dialog(self) -> TagRenameHistoryDialog:
        self.save_current()
        keep = int(self.config.data.get("tag_rename_keep", 20) or 0)
        days = int(self.config.data.get("tag_rename_keep_days", 0) or 0)
        bits = ([f"last {keep} renames"] if keep else []) + ([f"{days} days"] if days else [])
        text = ("Keeping backups for: " + " and ".join(bits)) if bits else "Keeping all backups"
        dlg = TagRenameHistoryDialog(self.tag_rename_records, self.undo_tag_rename, text + " (Preferences → General)",
                                     parent=self)
        self._tag_history_dialog = dlg
        dlg.open()
        return dlg

    # Tag entries view -------------------------------------------------------------
    def show_tag_menu(self, tag: str, global_pos, filter_item: bool = True) -> QMenu:
        menu = QMenu(self)
        self._add(menu, f"Show All Entries Tagged #{tag}", lambda t=tag: self.show_tag_entries(t))
        if filter_item:
            self._add(menu, f"Filter Timeline by #{tag}", lambda t=tag: self.add_tag_filter(t))
        self._add(menu, f"Rename #{tag}…", lambda t=tag: self.rename_tag_dialog(t))
        self._tag_menu_popup = menu
        menu.popup(global_pos)
        return menu

    def show_tag_entries(self, tag: str | None = None, scope: str | None = None) -> TagEntriesView:
        """Replace the editor area with the list of entries tagged `tag`
        (None = the tag browser, starting from the filter / most used tag)."""
        if self.locked:
            return self.tag_view
        self.save_current()
        if not tag:
            tag = self.tag_view.tag or (self.tag_filter[0] if self.tag_filter else "")
            if not tag:
                counts = self.tag_index.counts(self._scope_journals())
                if counts:
                    tag = max(counts.values(), key=lambda dn: dn[1])[0]
        self.tag_view.setup(self.library, lambda: self.journal, self.tag_index, self.theme)
        self.tag_view.show_tag(tag or "", scope or self.search_scope())
        self.right_stack.setCurrentWidget(self.tag_view)
        return self.tag_view

    def close_tag_entries(self) -> None:
        self.right_stack.setCurrentWidget(self._editor_page)
        (self.preview if self.view_mode == "reading" else self.editor).setFocus()

    def _refresh_tag_view(self) -> None:
        if self.right_stack.currentWidget() is self.tag_view:
            self.tag_view.refresh()

    def _open_from_tag_view(self, folder: str, date) -> None:
        self.close_tag_entries()
        if folder != self.journal.root.name:
            self.switch_journal(folder, date)
        else:
            self.open_date(date)

    def export_tag_entries_dialog(self) -> None:
        stamp = dt.date.today().isoformat()
        name = self.tag_view.tag.replace("/", "-")
        path, _ = QFileDialog.getSaveFileName(self, "Export tagged entries to PDF",
                                              str(Path.home() / f"tag-{name}-{stamp}.pdf"), "PDF (*.pdf)")
        if path:
            self.export_tag_entries(Path(path))

    def export_tag_entries(self, path: Path) -> int:
        self.save_current()
        pairs = [(c.journal, c.date) for c in self.tag_view.cards]
        try:
            n = export_pdf_pairs(pairs, path, one_per_page=True, title=f"#{self.tag_view.tag}")
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return 0
        self.statusBar().showMessage(f"Exported {n} entries tagged #{self.tag_view.tag} to {path}", 8000)
        return n

    # Emoji / links ---------------------------------------------------------------
    def insert_emoji_dialog(self):
        from daily_vibe.ui.emoji_picker import EmojiPickerDialog
        dlg = EmojiPickerDialog(self.config, self, "Insert Emoji")
        dlg.picker.picked.connect(lambda ch: self.editor.insert_at_cursor(ch))
        dlg.finished.connect(lambda _r: self.editor.setFocus())
        self._emoji_dialog = dlg
        dlg.open()
        return dlg

    def _open_link(self, target: str) -> None:
        if target.startswith("tag:"):
            self.show_tag_entries(urllib.parse.unquote(target[4:]))
            return
        url = QUrl(target)
        if url.scheme() in ("http", "https", "mailto", "file"):
            QDesktopServices.openUrl(url)
            return
        path = self.editor.resolve_image(target)
        if path is not None and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # Migration ---------------------------------------------------------------
    def maybe_migrate(self, force: bool = False) -> bool:
        """Offer to migrate an old single-journal layout (YYYY folders directly in
        the library root) into a "Personal" journal. Non-blocking dialog."""
        if self.locked:
            self._migrate_after_unlock = True
            return False
        root = self.library.root
        if not migrate.detect(root):
            if force:
                QMessageBox.information(self, "Migration", f"Nothing to migrate in\n{root}")
            return False
        if not force and self.config.data.get("migration_declined") == str(root):
            return False
        entries, assets = migrate.plan_counts(root)
        box = QMessageBox(QMessageBox.Icon.Question, "Upgrade journal folder", "", parent=self)
        box.setText(f"The Daily Vibe {__version__} supports multiple journals.\n\n"
                    f"Your folder has {entries} entries and {assets} images in the old layout:\n{root}\n\n"
                    f'Move them into a journal named "{migrate_target_name(root)}"?')
        box.setInformativeText("A full backup is made first (in a hidden .migration-backup-… folder). Images move to "
                               "the journal's assets folder and image links are rewritten. Nothing is overwritten.")
        go = box.addButton("Migrate now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        never = box.addButton("Don't ask again", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(go)
        self._migration_box = box

        def done(_result=None):
            clicked = box.clickedButton()
            if clicked is go:
                self.run_migration()
            elif clicked is never:
                self.config.data["migration_declined"] = str(root)
                self.config.save()
        box.finished.connect(done)
        box.open()
        return True

    def run_migration(self):
        self.save_current()
        root = self.library.root
        name = migrate_target_name(root)
        try:
            report = migrate.migrate(root, name)
        except OSError as exc:
            QMessageBox.warning(self, "Migration failed", f"Nothing was deleted.\n\n{exc}")
            return None
        self.last_migration = report
        self.config.data["last_journal"] = name
        self.config.save()
        self.tag_index = tagmod.TagIndex()
        self._apply_root()
        box = QMessageBox(QMessageBox.Icon.Information, "Migration finished", report.summary(), parent=self)
        self._last_summary_box = box
        box.open()
        return report

    # Tags --------------------------------------------------------------------
    def search_scope(self) -> str:
        return self.scope_combo.currentData() or "journal"

    def _scope_changed(self, *_a) -> None:
        self.config.data["search_scope"] = self.search_scope()
        self.config.save()
        self.refresh_tags()
        self.refresh_list()

    def _scope_journals(self) -> list:
        return self.library.journals() if self.search_scope() == "all" else [self.journal]

    def _filter_suffix(self) -> str:
        return ("  ·  tags: " + " + ".join("#" + t for t in self.tag_filter)) if self.tag_filter else ""

    def refresh_tags(self) -> None:
        if self.locked:
            return
        counts = self.tag_index.counts(self._scope_journals())
        self._tag_counts_snapshot = counts
        active = {tagmod.normalize(t) for t in self.tag_filter}
        self.tag_list.blockSignals(True)
        self.tag_list.clear()

        parents = {k.rsplit("/", 1)[0] for k in counts if "/" in k}

        def add(display: str, n: int, checked: bool) -> None:
            label = f"#{display}  ({n})"
            if tagmod.normalize(display) in parents:  # parent tag: also show the count incl. children
                total = len(self.tag_index.entries_with(self._scope_journals(), [display]))
                if total != n:
                    label = f"#{display}  ({n} · {total} incl. nested)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, display)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.tag_list.addItem(item)

        # filter tags that aren't literal tags (a parent like "project" of
        # "project/x", or a tag that no longer exists) stay visible and untickable
        for t in self.tag_filter:
            if tagmod.normalize(t) not in counts:
                add(t, len(self.tag_index.entries_with(self._scope_journals(), [t])), True)
        # ticked tags first, then by count
        for key, (display, n) in sorted(counts.items(), key=lambda kv: (kv[0] not in active, -kv[1][1], kv[0])):
            add(display, n, key in active)
        self.tag_list.blockSignals(False)
        self.tag_list.scrollToTop()
        where = "all journals" if self.search_scope() == "all" else self.journal.name
        self.tags_toggle.setText(f"Tags ({len(counts)}) — {where}")
        self.clear_tags_btn.setVisible(bool(self.tag_filter))
        # autocomplete from every journal (tags are handy across journals)
        all_counts = counts if self.search_scope() == "all" else self.tag_index.counts(self.library.journals())
        self.editor.set_tags([d for d, _n in all_counts.values()])

    def _refresh_tags_if_changed(self) -> None:
        counts = self.tag_index.counts(self._scope_journals())
        if counts != getattr(self, "_tag_counts_snapshot", None):
            self.refresh_tags()
            if self.tag_filter:
                self.refresh_list()

    def _toggle_tag_panel(self, on: bool) -> None:
        self.tag_list.setVisible(on)
        self.tags_toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def _tag_item_changed(self, item: QListWidgetItem) -> None:
        tag = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.add_tag_filter(tag)
        else:
            self.remove_tag_filter(tag)

    def set_tag_filter(self, tags: list[str]) -> None:
        seen, out = set(), []
        for t in tags:
            k = tagmod.normalize(t)
            if k and k not in seen:
                seen.add(k)
                out.append(t)
        self.tag_filter = out
        self.refresh_tags()
        self.refresh_list()

    def add_tag_filter(self, tag: str) -> None:
        self.set_tag_filter(self.tag_filter + [tag])

    def remove_tag_filter(self, tag: str) -> None:
        k = tagmod.normalize(tag)
        self.set_tag_filter([t for t in self.tag_filter if tagmod.normalize(t) != k])

    def clear_tag_filter(self) -> None:
        self.set_tag_filter([])

    def _preview_link(self, url: QUrl) -> None:
        if url.scheme() == "tag":
            tag = urllib.parse.unquote(url.toString()[4:])
            self.add_tag_filter(tag)
            self.statusBar().showMessage(f"Filtering by #{tag}", 3000)
            if self.isVisible():  # also offer the tag entries view / rename
                from PySide6.QtGui import QCursor
                self.show_tag_menu(tag, QCursor.pos(), filter_item=False)
        elif url.scheme() in ("http", "https", "mailto", "file"):
            QDesktopServices.openUrl(url)

    # Config ------------------------------------------------------------------
    def change_journal_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose library folder", str(self.library.root))
        if folder:
            self.change_root_to(Path(folder))

    def change_root_to(self, target: Path) -> str:
        """Ask move/copy/switch and act. Returns "switch", "pending" or "cancel"."""
        decision = self.request_root_change(target)
        if decision == "switch":
            self.config.journal_root = str(target)
            self.config.save()
            self._apply_root()
        return decision

    def request_root_change(self, target: Path) -> str:
        """Used by Preferences and File → Change Journal Folder.
        "switch": caller switches now; "pending": a move/copy is running and the
        window switches when it finishes; "cancel": do nothing."""
        target = Path(target).expanduser()
        source = self.library.root
        if target.resolve() == source.resolve():
            return "switch"
        if self._transfer_job is not None:
            QMessageBox.information(self, "Busy", "A journal move/copy is already running.")
            return "cancel"
        self.save_current()
        choice = ask_relocation(self, source, target)
        if choice is None:
            return "cancel"
        if choice == "switch":
            return "switch"
        self.editor.setReadOnly(True)
        job = TransferJob(self, source, target, choice)
        job.done.connect(lambda report, t=target: self._transfer_finished(report, t))
        self._transfer_job = job
        job.start()
        return "pending"

    def _transfer_finished(self, report, target: Path) -> None:
        self._transfer_job = None
        self.editor.setReadOnly(False)
        if not report.canceled and not (report.errors and not report.transferred and not report.already_present):
            self.config.journal_root = str(target)
            self.config.save()
            self._apply_root()
            report_note = "\n\nNow using the new folder."
        else:
            report_note = "\n\nStill using the old folder."
        box = QMessageBox(QMessageBox.Icon.Information, "Library folder changed" if not report.canceled else "Transfer canceled",
                          report.summary() + report_note, parent=self)
        self._last_summary_box = box
        box.open()

    def reload_config(self) -> None:
        self.save_current()
        self.config.data = Config.load(self.config.path).data
        self.apply_settings()

    def open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            return
        self.save_current()
        self.plugins.discover()
        dlg = SettingsDialog(self.config, self.plugins, self.themes, self)
        dlg.root_change_handler = self.request_root_change
        dlg.limiter = self.limiter
        dlg.applied.connect(self.apply_settings)
        dlg.live_style_preview.connect(self.preview_live_style)
        dlg.rejected.connect(self.apply_appearance)
        self._settings_dialog = dlg
        dlg.open()  # window-modal, non-blocking

    def apply_settings(self) -> None:
        """Apply everything in self.config live (called after Preferences Apply/OK)."""
        if Path(self.config.journal_root).expanduser() != self.library.root:
            self.save_current()
            self._apply_root()
        self.autosave_timer.setInterval(int(self.config.data.get("autosave_ms", 1000)))
        self._rebuild_plugins_menu()
        self._apply_security_settings()
        self.update_streak()
        self.apply_appearance()
        if self.config.data.get("view_mode", "live") != self.view_mode:
            self.set_view_mode(self.config.data.get("view_mode", "live"), save=False)
        self.cleanup_tag_backups()
        self.statusBar().showMessage("Settings applied", 3000)

    def _apply_root(self) -> None:
        self._init_library()
        self.tag_filter = []
        self.refresh_journals()
        self.refresh_calendar()
        self.refresh_list()
        self.refresh_tags()
        self._dirty = False
        self.open_date(self.current_date, force=True)

    # Find & Replace / tables -----------------------------------------------------
    def _ensure_editable_view(self) -> None:
        if self.right_stack.currentWidget() is not self._editor_page:
            self.close_tag_entries()
        if self.view_mode == "reading":
            self.set_view_mode("live", save=False)

    def open_find(self, replace: bool = False) -> None:
        self._ensure_editable_view()
        self.find_bar.open_bar(replace)

    def find_next(self) -> None:
        self._ensure_editable_view()
        self.find_bar.find_next()

    def find_prev(self) -> None:
        self._ensure_editable_view()
        self.find_bar.find_prev()

    def format_table(self) -> None:
        if self.view_mode == "reading" or not self.editor.format_table_at_cursor():
            self.statusBar().showMessage("Put the cursor inside a Markdown table to format it.", 4000)

    # Appearance ----------------------------------------------------------------
    def _apply_editor_fonts(self, ap: dict) -> None:
        """Source-mode (monospace) font plus Live Preview typography. Formatting only."""
        family = ap.get("editor_font_family") or ""
        font = QFont(family) if family else QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(int(ap.get("editor_font_size", 12)))
        live_family = ap.get("live_font_family") or ""
        live_font = QFont(live_family) if live_family else QFont(QApplication.font())
        live_font.setPointSize(int(ap.get("live_font_size") or int(ap.get("editor_font_size", 12)) + 1))
        self.editor.set_live_style(ap.get("live_heading_family") or "", ap.get("live_code_family") or "",
                                   float(ap.get("live_line_spacing", 1.0) or 1.0),
                                   int(ap.get("live_max_width", 0) or 0), refresh=False)
        self.editor.set_fonts(font, live_font)

    def preview_live_style(self, style: dict) -> None:
        """Preferences → Appearance changes shown live (reverted if the dialog is canceled)."""
        self._apply_editor_fonts(dict(self.config.data["appearance"], **style))
        self.editor.update_bands()

    def apply_appearance(self) -> None:
        ap = self.config.data["appearance"]
        self.theme = theming.theme_for_config(self.themes, ap, theming.system_prefers_dark())
        app = QApplication.instance()
        if app is not None:
            theming.apply_to_app(app, self.theme)
        self.editor.theme = self.theme
        self._apply_editor_fonts(ap)
        self.editor.set_theme(self.theme)
        self.tag_view.set_theme(self.theme)
        self.find_bar.set_theme(self.theme)
        weekend = QTextCharFormat()
        weekend.setForeground(QBrush(QColor(self.theme.colors["weekend"])))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.calendar.setWeekdayTextFormat(day, weekend)
        header = QTextCharFormat()
        header.setForeground(QBrush(QColor(self.theme.colors["muted"])))
        self.calendar.setHeaderTextFormat(header)
        self._highlighted_refresh_all()
        for value, action in getattr(self, "mode_actions", {}).items():
            action.setChecked(value == ap.get("mode", "system"))
        self.update_preview()

    def _highlighted_refresh_all(self) -> None:
        self._highlighted = set()
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())  # clear all
        self.refresh_calendar()

    def _set_theme_mode(self, mode: str) -> None:
        self.config.data["appearance"]["mode"] = mode
        self.config.save()
        self.apply_appearance()

    def _on_system_scheme_changed(self, *_args) -> None:
        if self.config.data["appearance"].get("mode", "system") == "system":
            self.apply_appearance()

    def _open_user_themes(self) -> None:
        d = theming.user_themes_dir()
        d.mkdir(parents=True, exist_ok=True)
        self._open_path(d)

    def _reload_themes(self) -> None:
        self.themes.reload()
        self.apply_appearance()
        self.statusBar().showMessage(f"Loaded {len(self.themes.themes)} themes", 4000)

    # On This Day / streak ------------------------------------------------------
    def toggle_on_this_day(self) -> None:
        visible = self._otd_wrap.isHidden()
        self._otd_wrap.setVisible(visible)
        self.otd_action.setChecked(visible)
        self.config.data["show_on_this_day"] = visible
        self.config.save()
        if visible and not self.locked:
            self.on_this_day.update_for(self.journal, self.current_date)

    def update_streak(self) -> None:
        if self.locked or not self.config.data.get("show_streak", True):
            self.streak_label.hide()
            return
        info = self.streaks.compute(self.journal, self.journal.template(self.config.data["template"]))
        self.streak_label.setText(info.message())
        self.streak_label.setToolTip(f"Current streak: {info.current} day(s)\nLongest streak: {info.longest} day(s)\n"
                                     "Days count when you've written something beyond the template and plugin blocks.")
        self.streak_label.show()

    # Export ----------------------------------------------------------------------
    def _export(self, dates: list, default_name: str, one_per_page: bool = True, page_size: str = "Letter") -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export to PDF", str(Path.home() / default_name), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.export_to(dates, Path(path), one_per_page, page_size)

    def export_to(self, dates: list, path: Path, one_per_page: bool = True, page_size: str = "Letter",
                  title: str | None = None) -> int:
        self.save_current()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = export_pdf(self.journal, dates, path, one_per_page, page_size, title)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return 0
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Exported {count} entr{'y' if count == 1 else 'ies'} to {path}", 8000)
        return count

    def export_current_pdf(self) -> None:
        if not self.journal.exists(self.current_date) and not self._dirty:
            QMessageBox.information(self, "Nothing to export", "This day has no entry yet.")
            return
        self._export([self.current_date], f"daily-vibe-{self.current_date}.pdf")

    def export_range_pdf(self) -> None:
        known = [disp for disp, _n in self.tag_index.counts([self.journal]).values()]
        dlg = ExportRangeDialog(self.current_date - dt.timedelta(days=6), self.current_date, self, known)
        if self.tag_filter:
            dlg.tag_edit.setText(" ".join(self.tag_filter))
        if dlg.exec():
            start, end, per_page, size = dlg.values()
            dates = self.dates_for_export(start, end, dlg.tag_filter())
            if not dates:
                QMessageBox.information(self, "Nothing to export", "No entries in that date range"
                                        + (" with those tags." if dlg.tag_filter() else "."))
                return
            suffix = ("-" + "-".join(dlg.tag_filter())).replace("/", "_") if dlg.tag_filter() else ""
            self._export(dates, f"{self.journal.root.name}-{start}_to_{end}{suffix}.pdf", per_page, size)

    def dates_for_export(self, start: dt.date, end: dt.date, tag_filter: list[str] | None = None) -> list[dt.date]:
        dates = [d for d in self.journal.list_dates() if start <= d <= end]
        if tag_filter:
            dates = [d for d in dates if tagmod.matches(self.tag_index.tags_for(self.journal, d), tag_filter)]
        return dates

    def export_journal_pdf_dialog(self) -> None:
        dates = self.journal.list_dates()
        if not dates:
            QMessageBox.information(self, "Nothing to export", f'"{self.journal.name}" has no entries yet.')
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export journal to PDF",
                                              str(Path.home() / f"{self.journal.root.name}.pdf"), "PDF (*.pdf)")
        if path:
            path = path if path.lower().endswith(".pdf") else path + ".pdf"
            self.export_to(dates, Path(path), True, "Letter", title=self.journal.name)

    def export_journal_zip_dialog(self) -> None:
        stamp = dt.date.today().isoformat()
        path, _ = QFileDialog.getSaveFileName(self, "Export journal as zip",
                                              str(Path.home() / f"{self.journal.root.name}-{stamp}.zip"), "Zip (*.zip)")
        if path:
            include = False
            if (self.journal.root / tag_rename.BACKUP_DIR).is_dir():
                box = QMessageBox(QMessageBox.Icon.Question, "Export journal as zip",
                                  f"Export {self.journal.name} to {Path(path).name}?", parent=self)
                cb = QCheckBox("Include tag-rename backups (.dailyvibe/backups)")
                box.setCheckBox(cb)
                box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
                if box.exec() != QMessageBox.StandardButton.Ok:
                    return
                include = cb.isChecked()
            self.export_journal_zip(Path(path), include_backups=include)

    def export_journal_zip(self, path: Path, include_backups: bool = False) -> Path | None:
        self.save_current()
        try:
            out = self.library.export_zip(self.journal.root.name, path, include_backups=include_backups)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return None
        self.statusBar().showMessage(f"Exported {self.journal.name} to {out}", 8000)
        return out

    # Lock ------------------------------------------------------------------------
    def password_enabled(self) -> bool:
        return bool(self.config.data.get("security", {}).get("password_hash"))

    def _apply_security_settings(self) -> None:
        minutes = int(self.config.data.get("security", {}).get("auto_lock_minutes", 0) or 0)
        self.idle.set_minutes(minutes if self.password_enabled() and not self.locked else 0)
        self.lock_action.setEnabled(self.password_enabled() and not self.locked)

    def _on_idle(self) -> None:
        if self.password_enabled() and not self.locked:
            self.lock()

    def lock_now(self) -> None:
        if not self.password_enabled():
            QMessageBox.information(self, "No password set",
                                    "Set a password in Preferences → Security to use the app lock.")
            return
        self.lock()

    def lock(self) -> None:
        """Hide and unload all entry content. Plugin results arriving while locked
        are written straight to their files (never shown)."""
        if self._loaded_once:
            self.save_current()
        self.autosave_timer.stop()
        self.preview_timer.stop()
        self.locked = True
        self._loading = True
        self.editor.clear()
        self.editor.document().clearUndoRedoStacks()
        self._loading = False
        self._dirty = False
        self.preview.clear()
        self.right_stack.setCurrentWidget(self._editor_page)
        self.tag_view.browser.clear()
        self.tag_view.cards = []
        self.entry_list.clear()
        self.on_this_day.clear()
        self.search_box.clear()
        self.tag_list.clear()
        self.date_label.clear()
        self.streak_label.hide()
        self.statusBar().clearMessage()
        self._highlighted_refresh_all()
        self.stack.setCurrentWidget(self.lock_screen)
        self.menuBar().setEnabled(False)
        self.journal_combo.setEnabled(False)
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — Locked")
        self.idle.set_minutes(0)
        self.lock_screen.reset()

    def try_unlock(self, password: str) -> bool:
        if not self.limiter.can_try():
            self.lock_screen.show_error("", self.limiter)
            return False
        ok = security.verify_password(password, self.config.data.get("security", {}).get("password_hash", ""))
        self.limiter.record(ok)
        if not ok:
            left = self.limiter.free - self.limiter.failures
            msg = "Wrong password." + (f" {left} attempt(s) before a delay." if left > 0 else "")
            self.lock_screen.show_error(msg, self.limiter)
            return False
        self.unlock()
        return True

    def unlock(self) -> None:
        self.locked = False
        self.menuBar().setEnabled(True)
        self.journal_combo.setEnabled(True)
        self.stack.setCurrentIndex(0)
        self._highlighted_refresh_all()
        self.refresh_list()
        self.refresh_tags()
        self._apply_security_settings()
        self.open_date(self.current_date, force=True)
        if getattr(self, "_migrate_after_unlock", False):
            self._migrate_after_unlock = False
            QTimer.singleShot(200, self.maybe_migrate)

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_DISPLAY_NAME}",
                          f"<h3>{APP_DISPLAY_NAME}</h3><p>Version {__version__}</p>"
                          "<p>A day-by-day Markdown journal. Plain files, your folder, your vibe.</p>"
                          f"<p>Library folder:<br><code>{self.library.root}</code></p>"
                          f"<p>Current journal:<br><code>{self.journal.root}</code></p>")

    def _open_path(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event) -> None:
        if self._transfer_job is not None:
            QMessageBox.information(self, "Please wait", "A journal move/copy is still running.")
            event.ignore()
            return
        self.save_current()
        self.runner.shutdown(1500)
        if QApplication.instance() is not None:
            QApplication.instance().removeEventFilter(self.idle)
        super().closeEvent(event)

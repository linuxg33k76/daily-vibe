"""Preferences dialog. Edits a copy of the config; Apply/OK write it back."""
from __future__ import annotations

import copy
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QApplication, QRadioButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from daily_vibe import security
from daily_vibe.config import DEFAULT_TEMPLATE, Config
from daily_vibe.ui.plugin_settings import PluginsPage
from daily_vibe.plugin_manager import PluginManager
from daily_vibe.theming import HEX_RE, ThemeRegistry, system_prefers_dark, user_themes_dir
from daily_vibe.workers import TaskRunner

LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


SAMPLE_MD = """# Morning pages
Coffee first, then **three things** I'm grateful for — *slowly*, with `no phone`.

- A quiet walk
    - ~~Emails~~ later
- [x] Stretch

> Small steps, every day.

| Habit | Days |
|:------|-----:|
| Read  | 5 |
"""


class SettingsDialog(QDialog):
    applied = Signal()
    live_style_preview = Signal(dict)   # un-applied Live Preview typography (applied live)

    def __init__(self, config: Config, plugins: PluginManager, themes: ThemeRegistry, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences — The Daily Vibe")
        self.setMinimumSize(640, 560)
        self.config = config
        self.plugins = plugins
        self.themes = themes
        self.tasks = TaskRunner(self, max_threads=2)
        self._accent = ""
        self.root_change_handler = None  # callable(Path) -> "switch" | "pending" | "cancel"
        self.limiter = None  # shared security.AttemptLimiter

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._scrolled(self._appearance_tab()), "Appearance")
        self.tabs.addTab(self._images_tab(), "Images")
        self.tabs.addTab(self._plugins_tab(), "Plugins")
        self.tabs.addTab(self._security_tab(), "Security")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.buttons)
        self.load(config.data)

    # Tabs ---------------------------------------------------------------------
    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        row = QHBoxLayout()
        self.root_edit = QLineEdit()
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse_root)
        row.addWidget(self.root_edit, 1)
        row.addWidget(self.browse_btn)
        form.addRow("Library folder:", row)
        form.addRow("", _hint("The library holds all your journals (one sub-folder each). When you change "
                              "it you'll be asked whether to move or copy the whole library, or just switch "
                              "to the new folder as it is."))

        self.autosave_spin = QDoubleSpinBox()
        self.autosave_spin.setRange(0.2, 60.0)
        self.autosave_spin.setSingleStep(0.5)
        self.autosave_spin.setDecimals(1)
        self.autosave_spin.setSuffix(" s")
        form.addRow("Autosave after idle:", self.autosave_spin)
        self.streak_check = QCheckBox("Show journaling streak under the calendar")
        self.otd_check = QCheckBox("Show the On This Day panel")
        form.addRow("", self.streak_check)
        form.addRow("", self.otd_check)
        self.view_mode_combo = QComboBox()
        for value, label in (("live", "Live Preview"), ("split", "Source + Preview"),
                             ("source", "Source only"), ("reading", "Reading")):
            self.view_mode_combo.addItem(label, value)
        form.addRow("Editor view:", self.view_mode_combo)
        krow = QHBoxLayout()
        self.keep_renames_spin = QSpinBox()
        self.keep_renames_spin.setRange(0, 1000)
        self.keep_renames_spin.setSpecialValueText("all")
        self.keep_renames_spin.setSuffix(" renames")
        self.keep_days_spin = QSpinBox()
        self.keep_days_spin.setRange(0, 3650)
        self.keep_days_spin.setSpecialValueText("no age limit")
        self.keep_days_spin.setSuffix(" days")
        krow.addWidget(QLabel("last"))
        krow.addWidget(self.keep_renames_spin)
        krow.addWidget(QLabel("and at most"))
        krow.addWidget(self.keep_days_spin)
        krow.addStretch(1)
        form.addRow("Keep tag-rename backups:", krow)

        self.template_edit = QPlainTextEdit()
        self.template_edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.template_edit.setMinimumHeight(180)
        form.addRow("New entry template:", self.template_edit)
        trow = QHBoxLayout()
        trow.addWidget(_hint("Placeholders: {date} {weekday} {long_date} {plugins}"), 1)
        reset = QPushButton("Reset to default")
        reset.clicked.connect(lambda: self.template_edit.setPlainText(DEFAULT_TEMPLATE))
        trow.addWidget(reset)
        form.addRow("", trow)
        return w

    def _appearance_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.mode_combo = QComboBox()
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            self.mode_combo.addItem(label, value)
        form.addRow("Theme mode:", self.mode_combo)
        form.addRow("", _hint("System follows the OS dark-mode setting (currently: "
                              f"{'dark' if system_prefers_dark() else 'light / not reported'})."))
        self.light_combo = QComboBox()
        self.light_combo.addItems(self.themes.names("light"))
        self.dark_combo = QComboBox()
        self.dark_combo.addItems(self.themes.names("dark"))
        form.addRow("Light theme:", self.light_combo)
        form.addRow("Dark theme:", self.dark_combo)

        arow = QHBoxLayout()
        self.accent_btn = QPushButton()
        self.accent_btn.setMinimumWidth(140)
        self.accent_btn.clicked.connect(self._pick_accent)
        self.accent_reset = QPushButton("Use theme accent")
        self.accent_reset.clicked.connect(lambda: self._set_accent(""))
        arow.addWidget(self.accent_btn)
        arow.addWidget(self.accent_reset)
        arow.addStretch(1)
        form.addRow("Accent color:", arow)

        frow = QHBoxLayout()
        self.font_combo = QFontComboBox()
        self.font_combo.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        self.font_default = QCheckBox("System monospace")
        self.font_default.toggled.connect(lambda on: self.font_combo.setEnabled(not on))
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 48)
        self.font_size.setSuffix(" pt")
        frow.addWidget(self.font_default)
        frow.addWidget(self.font_combo, 1)
        frow.addWidget(self.font_size)
        form.addRow("Editor font:", frow)

        self.preview_size = QSpinBox()
        self.preview_size.setRange(8, 40)
        self.preview_size.setSuffix(" px")
        form.addRow("Preview font size:", self.preview_size)
        form.addRow("", _hint(f"Add your own themes as TOML files in {user_themes_dir()} "
                              "(copy one of the built-ins from daily_vibe/themes/)."))
        form.addRow(self._live_group())
        return w

    def _scrolled(self, page: QWidget) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(page)
        return area

    def _live_group(self) -> QGroupBox:
        """Live Preview typography: text / heading / code fonts, size, spacing, column width."""
        box = QGroupBox("Live Preview typography")
        self.live_group = box
        grid = QGridLayout(box)
        self.live_font_default = QCheckBox("System font")
        self.live_font_combo = QFontComboBox()
        self.live_font_default.toggled.connect(lambda on: self.live_font_combo.setEnabled(not on))
        self.live_size = QSpinBox()
        self.live_size.setRange(0, 40)
        self.live_size.setSuffix(" pt")
        self.live_size.setSpecialValueText("Auto")
        self.live_size.setToolTip("Auto = editor font size + 1")
        grid.addWidget(QLabel("Text font:"), 0, 0)
        grid.addWidget(self.live_font_default, 0, 1)
        grid.addWidget(self.live_font_combo, 0, 2)
        grid.addWidget(self.live_size, 0, 3)

        self.live_heading_same = QCheckBox("Same as text")
        self.live_heading_combo = QFontComboBox()
        self.live_heading_same.toggled.connect(lambda on: self.live_heading_combo.setEnabled(not on))
        grid.addWidget(QLabel("Heading font:"), 1, 0)
        grid.addWidget(self.live_heading_same, 1, 1)
        grid.addWidget(self.live_heading_combo, 1, 2, 1, 2)

        self.live_code_default = QCheckBox("Editor font")
        self.live_code_combo = QFontComboBox()
        self.live_code_combo.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        self.live_code_default.toggled.connect(lambda on: self.live_code_combo.setEnabled(not on))
        grid.addWidget(QLabel("Code font:"), 2, 0)
        grid.addWidget(self.live_code_default, 2, 1)
        grid.addWidget(self.live_code_combo, 2, 2, 1, 2)

        self.live_spacing = QDoubleSpinBox()
        self.live_spacing.setRange(1.0, 2.5)
        self.live_spacing.setSingleStep(0.05)
        self.live_spacing.setDecimals(2)
        self.live_spacing.setSuffix(" ×")
        self.live_width = QSpinBox()
        self.live_width.setRange(0, 3000)
        self.live_width.setSingleStep(20)
        self.live_width.setSuffix(" px")
        self.live_width.setSpecialValueText("Full width")
        self.live_width.setToolTip("Maximum text width; the column is centered in wider windows")
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Line spacing:"))
        srow.addWidget(self.live_spacing)
        srow.addSpacing(16)
        srow.addWidget(QLabel("Max text width:"))
        srow.addWidget(self.live_width)
        srow.addStretch(1)
        grid.addLayout(srow, 3, 0, 1, 4)

        from daily_vibe.ui.editor import MarkdownEditor
        from daily_vibe.highlighter import MarkdownHighlighter
        self.live_sample = MarkdownEditor()
        self.live_sample.setObjectName("liveSample")
        self.live_sample.setMinimumHeight(190)
        self.live_sample.setMaximumHeight(230)
        self._sample_theme = self._current_theme()
        self.live_sample.setup_highlighting(self._sample_theme, MarkdownHighlighter, live=True)
        self.live_sample.setPlainText(SAMPLE_MD)
        self.live_sample.setReadOnly(True)
        self.live_sample.moveCursor(QTextCursor.MoveOperation.End)   # cursor on the empty last line
        grid.addWidget(QLabel("Sample:"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.live_sample, 4, 1, 1, 3)
        grid.setColumnStretch(2, 1)

        for sig in (self.live_font_default.toggled, self.live_font_combo.currentFontChanged,
                    self.live_size.valueChanged, self.live_heading_same.toggled,
                    self.live_heading_combo.currentFontChanged, self.live_code_default.toggled,
                    self.live_code_combo.currentFontChanged, self.live_spacing.valueChanged,
                    self.live_width.valueChanged, self.font_size.valueChanged,
                    self.font_combo.currentFontChanged, self.font_default.toggled):
            sig.connect(lambda *_: self._live_changed())
        return box

    def _current_theme(self):
        from daily_vibe import theming
        return theming.theme_for_config(self.themes, self.config.data.get("appearance", {}),
                                        system_prefers_dark())

    def live_style(self) -> dict:
        """The Live Preview typography keys as currently shown in the dialog."""
        return dict(
            live_font_family="" if self.live_font_default.isChecked() else self.live_font_combo.currentFont().family(),
            live_font_size=self.live_size.value(),
            live_heading_family="" if self.live_heading_same.isChecked()
            else self.live_heading_combo.currentFont().family(),
            live_code_family="" if self.live_code_default.isChecked() else self.live_code_combo.currentFont().family(),
            live_line_spacing=round(self.live_spacing.value(), 2),
            live_max_width=self.live_width.value(),
        )

    def _live_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        style = self.live_style()
        mono = QFont(self.font_combo.currentFont()) if not self.font_default.isChecked() else \
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(self.font_size.value())
        live = QFont(style["live_font_family"]) if style["live_font_family"] else QFont(QApplication.font())
        live.setPointSize(style["live_font_size"] or self.font_size.value() + 1)
        sample = self.live_sample
        sample.set_live_style(style["live_heading_family"], style["live_code_family"],
                              style["live_line_spacing"], 0, refresh=False)
        sample.set_fonts(mono, live)
        self.live_style_preview.emit(dict(style, editor_font_family="" if self.font_default.isChecked()
                                          else self.font_combo.currentFont().family(),
                                          editor_font_size=self.font_size.value()))

    def _images_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        qrow = QHBoxLayout()
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_label = QLabel()
        self.quality_label.setMinimumWidth(30)
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        qrow.addWidget(self.quality_slider, 1)
        qrow.addWidget(self.quality_label)
        form.addRow("WebP quality:", qrow)
        self.maxdim_spin = QSpinBox()
        self.maxdim_spin.setRange(0, 10000)
        self.maxdim_spin.setSingleStep(64)
        self.maxdim_spin.setSuffix(" px")
        self.maxdim_spin.setSpecialValueText("No limit")
        form.addRow("Max dimension:", self.maxdim_spin)
        form.addRow("", _hint("Pasted and dropped images are converted to WebP and saved in the entry's "
                              "YYYY/MM/assets/ folder. The longest side is scaled down to the max dimension."))
        return w

    def _plugins_tab(self) -> QWidget:
        self.plugins_page = PluginsPage(self.plugins, self.tasks)
        return self.plugins_page

    def _security_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(_hint("<b>App lock.</b> This hides the app behind a password; it does <b>not</b> encrypt "
                          "your journal. Entries stay plain Markdown files that anyone with access to your "
                          "account or disk can read. There is no password recovery: if you forget it, quit "
                          "the app and delete <code>password_hash</code> under <code>[security]</code> in "
                          "config.toml."))
        self.sec_status = QLabel()
        v.addWidget(self.sec_status)
        box = QGroupBox("Password")
        form = QFormLayout(box)
        self.sec_current = QLineEdit()
        self.sec_new = QLineEdit()
        self.sec_confirm = QLineEdit()
        for e in (self.sec_current, self.sec_new, self.sec_confirm):
            e.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Current password:", self.sec_current)
        form.addRow("New password:", self.sec_new)
        form.addRow("Confirm new password:", self.sec_confirm)
        row = QHBoxLayout()
        self.sec_set_btn = QPushButton("Set password")
        self.sec_set_btn.clicked.connect(self._set_password)
        self.sec_remove_btn = QPushButton("Remove password")
        self.sec_remove_btn.clicked.connect(self._remove_password)
        row.addWidget(self.sec_set_btn)
        row.addWidget(self.sec_remove_btn)
        row.addStretch(1)
        form.addRow("", row)
        self.sec_message = _hint("")
        form.addRow("", self.sec_message)
        form.addRow("", _hint("Password changes take effect immediately (they don't wait for OK/Apply)."))
        v.addWidget(box)
        lbox = QGroupBox("Locking")
        lform = QFormLayout(lbox)
        self.autolock_spin = QSpinBox()
        self.autolock_spin.setRange(0, 24 * 60)
        self.autolock_spin.setSuffix(" min")
        self.autolock_spin.setSpecialValueText("Never")
        lform.addRow("Auto-lock after idle:", self.autolock_spin)
        lform.addRow("", _hint("Lock now any time with Entry → Lock Now (Ctrl+L)."))
        v.addWidget(lbox)
        v.addStretch(1)
        return w

    def _refresh_security_status(self) -> None:
        enabled = bool(self.config.data.get("security", {}).get("password_hash"))
        self.sec_status.setText("🔒 App lock is <b>on</b>." if enabled else "🔓 App lock is <b>off</b> (no password set).")
        self.sec_current.setEnabled(enabled)
        self.sec_remove_btn.setEnabled(enabled)
        self.sec_set_btn.setText("Change password" if enabled else "Set password")

    def _check_current(self) -> bool:
        stored = self.config.data.get("security", {}).get("password_hash", "")
        if not stored:
            return True
        if self.limiter is not None and not self.limiter.can_try():
            self.sec_message.setText(f"Too many failed attempts. Try again in {self.limiter.remaining():.0f} s.")
            return False
        ok = security.verify_password(self.sec_current.text(), stored)
        if self.limiter is not None:
            self.limiter.record(ok)
        if not ok:
            self.sec_message.setText("⚠ Current password is wrong.")
        return ok

    def _set_password(self) -> None:
        new, confirm = self.sec_new.text(), self.sec_confirm.text()
        if len(new) < 4:
            self.sec_message.setText("⚠ Use at least 4 characters.")
            return
        if new != confirm:
            self.sec_message.setText("⚠ The new passwords don't match.")
            return
        if not self._check_current():
            return
        self.config.data.setdefault("security", {})["password_hash"] = security.hash_password(new)
        self.config.save()
        for e in (self.sec_current, self.sec_new, self.sec_confirm):
            e.clear()
        self.sec_message.setText("✓ Password saved (only a salted scrypt hash is stored).")
        self._refresh_security_status()
        self.applied.emit()

    def _remove_password(self) -> None:
        if not self._check_current():
            return
        self.config.data.setdefault("security", {})["password_hash"] = ""
        self.config.save()
        self.sec_current.clear()
        self.sec_message.setText("✓ Password removed. The app lock is off.")
        self._refresh_security_status()
        self.applied.emit()

    # Load / collect -------------------------------------------------------------
    def load(self, data: dict) -> None:
        self.root_edit.setText(str(data.get("journal_root", "")))
        self.autosave_spin.setValue(int(data.get("autosave_ms", 1000)) / 1000)
        self.template_edit.setPlainText(data.get("template", DEFAULT_TEMPLATE))

        ap = data.get("appearance", {})
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(ap.get("mode", "system"))))
        self.light_combo.setCurrentText(ap.get("light_theme", "Default Light"))
        self.dark_combo.setCurrentText(ap.get("dark_theme", "Default Dark"))
        self._set_accent(ap.get("accent", ""))
        family = ap.get("editor_font_family", "")
        self.font_default.setChecked(not family)
        self.font_combo.setEnabled(bool(family))
        self.font_combo.setCurrentFont(QFont(family) if family else
                                       QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.font_size.setValue(int(ap.get("editor_font_size", 12)))
        self.preview_size.setValue(int(ap.get("preview_font_size", 14)))
        self._loading = True
        lf = ap.get("live_font_family", "") or ""
        self.live_font_default.setChecked(not lf)
        self.live_font_combo.setEnabled(bool(lf))
        self.live_font_combo.setCurrentFont(QFont(lf) if lf else QApplication.font())
        self.live_size.setValue(int(ap.get("live_font_size", 0) or 0))
        hf = ap.get("live_heading_family", "") or ""
        self.live_heading_same.setChecked(not hf)
        self.live_heading_combo.setEnabled(bool(hf))
        self.live_heading_combo.setCurrentFont(QFont(hf) if hf else QApplication.font())
        cf = ap.get("live_code_family", "") or ""
        self.live_code_default.setChecked(not cf)
        self.live_code_combo.setEnabled(bool(cf))
        self.live_code_combo.setCurrentFont(QFont(cf) if cf else
                                            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.live_spacing.setValue(float(ap.get("live_line_spacing", 1.0) or 1.0))
        self.live_width.setValue(int(ap.get("live_max_width", 0) or 0))
        self._loading = False
        self._live_changed()

        img = data.get("images", {})
        self.quality_slider.setValue(int(img.get("webp_quality", 80)))
        self.quality_label.setText(str(self.quality_slider.value()))
        self.maxdim_spin.setValue(int(img.get("max_dimension", 1920)))

        self.plugins_page.load(data)
        self.streak_check.setChecked(bool(data.get("show_streak", True)))
        self.otd_check.setChecked(bool(data.get("show_on_this_day", True)))
        i = self.view_mode_combo.findData(data.get("view_mode", "live"))
        self.view_mode_combo.setCurrentIndex(max(0, i))
        self.keep_renames_spin.setValue(int(data.get("tag_rename_keep", 20) or 0))
        self.keep_days_spin.setValue(int(data.get("tag_rename_keep_days", 0) or 0))
        self.autolock_spin.setValue(int(data.get("security", {}).get("auto_lock_minutes", 0) or 0))
        self._refresh_security_status()

    def collect(self, write_secrets: bool = False) -> dict:
        data = copy.deepcopy(self.config.data)
        data["journal_root"] = self.root_edit.text().strip() or "~/Journal"
        data["autosave_ms"] = int(round(self.autosave_spin.value() * 1000))
        data["template"] = self.template_edit.toPlainText()
        data["appearance"] = dict(
            data.get("appearance", {}),
            mode=self.mode_combo.currentData(),
            light_theme=self.light_combo.currentText(),
            dark_theme=self.dark_combo.currentText(),
            accent=self._accent,
            editor_font_family="" if self.font_default.isChecked() else self.font_combo.currentFont().family(),
            editor_font_size=self.font_size.value(),
            preview_font_size=self.preview_size.value(),
            **self.live_style(),
        )
        data["images"] = dict(data.get("images", {}), webp_quality=self.quality_slider.value(),
                              max_dimension=self.maxdim_spin.value())
        data["show_streak"] = self.streak_check.isChecked()
        data["show_on_this_day"] = self.otd_check.isChecked()
        data["view_mode"] = self.view_mode_combo.currentData()
        data["tag_rename_keep"] = self.keep_renames_spin.value()
        data["tag_rename_keep_days"] = self.keep_days_spin.value()
        sec = dict(data.get("security", {}))
        sec["auto_lock_minutes"] = self.autolock_spin.value()
        data["security"] = sec
        self.plugins_page.collect_into(data, write_secrets=write_secrets)
        return data

    def apply(self) -> bool:
        data = self.collect(write_secrets=True)
        old_root = Path(self.config.data["journal_root"]).expanduser()
        root = Path(data["journal_root"]).expanduser()
        if root != old_root and self.root_change_handler is not None:
            decision = self.root_change_handler(root)
            if decision == "cancel":
                self.tabs.setCurrentIndex(0)
                return False
            if decision == "pending":
                # a move/copy is running; the main window switches when it's done
                data["journal_root"] = self.config.data["journal_root"]
                root = old_root
        if not root.is_dir():
            answer = QMessageBox.question(
                self, "Create library folder?",
                f"The folder\n{root}\ndoes not exist. Create it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.tabs.setCurrentIndex(0)
                return False
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QMessageBox.warning(self, "Could not create folder", str(exc))
                return False
        # password hash may have changed via the Security tab meanwhile
        data.setdefault("security", {})["password_hash"] = self.config.data.get("security", {}).get("password_hash", "")
        self.config.data = data
        self.config.save()
        self.applied.emit()
        return True

    def _ok(self) -> None:
        if self.apply():
            self.accept()

    # Helpers --------------------------------------------------------------------
    def _browse_root(self) -> None:
        start = str(Path(self.root_edit.text()).expanduser()) if self.root_edit.text() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose library folder", start)
        if folder:
            self.root_edit.setText(folder)

    def _set_accent(self, color: str) -> None:
        self._accent = color if color and HEX_RE.match(color) else ""
        if self._accent:
            fg = "#000000" if QColor(self._accent).lightness() > 140 else "#ffffff"
            self.accent_btn.setText(self._accent)
            self.accent_btn.setStyleSheet(f"background:{self._accent}; color:{fg};")
        else:
            self.accent_btn.setText("Theme default…")
            self.accent_btn.setStyleSheet("")
        self.accent_reset.setEnabled(bool(self._accent))

    def _pick_accent(self) -> None:
        initial = QColor(self._accent) if self._accent else self.palette().highlight().color()
        color = QColorDialog.getColor(initial, self, "Accent color")
        if color.isValid():
            self._set_accent(color.name())

    def done(self, result: int) -> None:  # noqa: D401 - QDialog.done override
        self.tasks.pool.waitForDone(100)
        super().done(result)

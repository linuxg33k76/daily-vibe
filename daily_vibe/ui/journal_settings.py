"""Journal → Journal Settings…: edits <journal>/.dailyvibe.toml.

General (name / folder rename, color, icon, read-only info), Template
(per-journal override) and Plugins (use global settings, or per-journal
enable/disable + per-journal values for the plugins' schema settings).
OK / Apply / Cancel like Preferences. Writes go through
journal_meta.update_meta, which keeps unknown keys and (with tomlkit)
comments and formatting.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QRadioButton, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from daily_vibe.journal_meta import DELETE, load_meta, preserves_comments, update_meta
from daily_vibe.library import LibraryError, validate_name
from daily_vibe.ui.plugin_settings import PluginsPage

from daily_vibe.emoji_data import SUGGESTED as EMOJI  # noqa: E402  (quick picks when nothing is recent)

QUICK_PICKS = 12


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def journal_stats(journal) -> dict:
    dates = journal.list_dates()
    assets = journal.root / "assets"
    files = [p for p in assets.rglob("*") if p.is_file()] if assets.is_dir() else []
    return {
        "entries": len(dates),
        "images": len(files),
        "assets_bytes": sum(p.stat().st_size for p in files),
        "first": min(dates) if dates else None,
        "last": max(dates) if dates else None,
    }


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


class JournalSettingsDialog(QDialog):
    applied = Signal(object)  # the (possibly renamed) Journal

    def __init__(self, journal, config, plugins, tasks, parent=None):
        super().__init__(parent)
        self.journal = journal
        self.config = config
        self.plugins = plugins
        self.tasks = tasks
        # MainWindow.rename_journal: renames the folder safely, returns the new Journal
        self.rename_handler: Callable[[str], object] | None = None
        self._color = ""
        self.setWindowTitle(f"Journal Settings — {journal.name}")
        self.setMinimumWidth(640)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general(), "General")
        self.tabs.addTab(self._build_template(), "Template")
        self.tabs.addTab(self._build_plugins(), "Plugins")

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        self.message = QLabel()
        self.message.setObjectName("warning")
        self.message.setWordWrap(True)
        self.message.hide()
        v = QVBoxLayout(self)
        v.addWidget(self.tabs, 1)
        v.addWidget(self.message)
        v.addWidget(self.buttons)
        self.load()

    # General -------------------------------------------------------------------------
    def _build_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.name_edit = QLineEdit()
        form.addRow("Display name:", self.name_edit)
        self.rename_folder = QCheckBox("Also rename the folder")
        self.rename_folder.setToolTip("Renames the journal folder too (refused if a folder with that name exists)")
        form.addRow("", self.rename_folder)

        crow = QHBoxLayout()
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(90)
        self.color_btn.clicked.connect(self._pick_color)
        self.color_clear = QToolButton()
        self.color_clear.setText("Default")
        self.color_clear.clicked.connect(lambda: self.set_color(""))
        crow.addWidget(self.color_btn)
        crow.addWidget(self.color_clear)
        crow.addStretch(1)
        form.addRow("Color:", crow)

        icon_box = QVBoxLayout()
        irow = QHBoxLayout()
        self.icon_edit = QLineEdit()
        self.icon_edit.setMaxLength(8)
        self.icon_edit.setFixedWidth(90)
        self.icon_edit.setPlaceholderText("none")
        self.icon_edit.setToolTip("Any emoji or short text; pick one below or type/paste your own")
        clear_icon = QToolButton()
        clear_icon.setText("None")
        clear_icon.clicked.connect(lambda: self.icon_edit.setText(""))
        self.choose_emoji_btn = QPushButton("Choose Emoji…")
        self.choose_emoji_btn.setToolTip("Searchable picker with every Unicode emoji")
        self.choose_emoji_btn.clicked.connect(self.choose_emoji)
        irow.addWidget(self.icon_edit)
        irow.addWidget(clear_icon)
        irow.addWidget(self.choose_emoji_btn)
        irow.addStretch(1)
        icon_box.addLayout(irow)
        # quick picks: recently used emoji first, then suggestions
        grid = QHBoxLayout()
        grid.setSpacing(2)
        self.emoji_buttons = []
        recent = list((self.config.data.get("recent_emoji") or []) if self.config is not None else [])
        quick = list(dict.fromkeys(recent + EMOJI))[:QUICK_PICKS]
        for e in quick:
            b = QToolButton()
            b.setText(e)
            b.setAutoRaise(True)
            b.setFixedSize(34, 30)
            b.clicked.connect(lambda _c=False, e=e: self.icon_edit.setText(e))
            grid.addWidget(b)
            self.emoji_buttons.append(b)
        grid.addStretch(1)
        icon_box.addLayout(grid)
        form.addRow("Icon:", icon_box)

        info = QGroupBox("About this journal")
        iform = QFormLayout(info)
        prow = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.journal.root))))
        prow.addWidget(self.path_label, 1)
        prow.addWidget(open_btn)
        iform.addRow("Folder:", prow)
        self.entries_label = QLabel()
        self.images_label = QLabel()
        self.range_label = QLabel()
        iform.addRow("Entries:", self.entries_label)
        iform.addRow("Images:", self.images_label)
        iform.addRow("Date range:", self.range_label)
        form.addRow(info)
        note = ("Stored in .dailyvibe.toml inside the journal folder (travels with it). "
                + ("Comments and unknown keys in that file are preserved."
                   if preserves_comments() else "Unknown keys are preserved; install tomlkit to keep comments too."))
        form.addRow(_hint(note))
        return w

    def set_color(self, color: str) -> None:
        self._color = color
        if color:
            self.color_btn.setText(color)
            fg = "#000000" if QColor(color).lightness() > 140 else "#ffffff"
            self.color_btn.setStyleSheet(f"background-color: {color}; color: {fg};")
        else:
            self.color_btn.setText("theme accent")
            self.color_btn.setStyleSheet("")

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._color or "#89b4fa"), self, "Journal color")
        if c.isValid():
            self.set_color(c.name())

    # Template ------------------------------------------------------------------------
    def _build_template(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.custom_template = QCheckBox("Use a custom template for this journal")
        self.custom_template.toggled.connect(self._template_toggled)
        self.template_edit = QPlainTextEdit()
        self.template_edit.setTabChangesFocus(True)
        reset = QPushButton("Reset to global template")
        reset.clicked.connect(lambda: self.template_edit.setPlainText(self.config.data["template"]))
        self.template_reset = reset
        row = QHBoxLayout()
        row.addWidget(self.custom_template, 1)
        row.addWidget(reset)
        v.addLayout(row)
        v.addWidget(self.template_edit, 1)
        v.addWidget(_hint("Placeholders: {date}, {weekday}, {long_date}, {plugins} (where plugin blocks go). "
                          "Used for new entries in this journal. Unchecked = the template from Preferences."))
        return w

    def _template_toggled(self, on: bool) -> None:
        self.template_edit.setReadOnly(not on)
        self.template_edit.setEnabled(on)
        self.template_reset.setEnabled(on)
        if not on:
            self.template_edit.setPlainText(self.config.data["template"])

    # Plugins -------------------------------------------------------------------------
    def _build_plugins(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.plugins_global = QRadioButton("Use global plugin settings (Preferences → Plugins)")
        self.plugins_custom = QRadioButton("Customize plugins for this journal")
        group = QButtonGroup(w)
        group.addButton(self.plugins_global)
        group.addButton(self.plugins_custom)
        self.plugins_global.toggled.connect(lambda on: self.plugins_page.setEnabled(not on))
        v.addWidget(self.plugins_global)
        v.addWidget(self.plugins_custom)
        self.plugins_page = PluginsPage(self.plugins, self.tasks, journal_mode=True)
        v.addWidget(self.plugins_page, 1)
        return w

    def _plugin_data(self, meta: dict) -> dict:
        """Data dict for PluginsPage: this journal's effective plugin config."""
        data = {"plugins": {"enabled": self.journal.plugin_ids(self.config.enabled_plugins)}}
        for p in self.plugins.records:
            if p.status == "error":
                continue
            section = dict(self.config.plugin_config(p.id))
            section.update(self.journal.plugin_settings(p.id))
            data["plugins"][p.id] = section
        return data

    # Load / collect / apply ------------------------------------------------------------
    def load(self) -> None:
        j = self.journal
        meta = load_meta(j.root)
        self.name_edit.setText(meta.get("name") or j.root.name)
        self.rename_folder.setChecked(True)
        self.set_color(str(meta.get("color") or ""))
        self.icon_edit.setText(str(meta.get("icon") or ""))
        tpl = j.overrides.get("template")
        has_tpl = isinstance(tpl, str) and bool(tpl.strip())
        self.custom_template.setChecked(has_tpl)
        self.template_edit.setPlainText(tpl if has_tpl else self.config.data["template"])
        self._template_toggled(has_tpl)
        custom = isinstance(j.overrides.get("plugins"), list) or bool(j.overrides.get("plugin_settings"))
        (self.plugins_custom if custom else self.plugins_global).setChecked(True)
        self.plugins_page.load(self._plugin_data(meta))
        self.plugins_page.setEnabled(custom)
        self._load_info()

    def _load_info(self) -> None:
        st = journal_stats(self.journal)
        self.path_label.setText(str(self.journal.root))
        self.entries_label.setText(str(st["entries"]))
        self.images_label.setText(f"{st['images']} file(s), {human_size(st['assets_bytes'])}")
        self.range_label.setText(f"{st['first']:%b %-d, %Y} – {st['last']:%b %-d, %Y}" if st["first"] else "no entries yet")

    def _plugin_overrides(self) -> tuple[list[str], dict]:
        data = self.plugins_page.collect_into({"plugins": {"enabled": self.journal.plugin_ids(self.config.enabled_plugins)}},
                                              write_secrets=False)
        enabled = data["plugins"]["enabled"]
        settings: dict = {}
        for p in self.plugins.records:
            if p.id not in self.plugins_page.fields:
                continue
            global_values = self.plugins.settings_for(p)
            for key, (f, _w) in self.plugins_page.fields[p.id].items():
                if f.type == "secret":
                    continue
                value = data["plugins"].get(p.id, {}).get(key)
                if value != global_values.get(key):
                    settings.setdefault(p.id, {})[key] = value
        return enabled, settings

    def collect(self) -> dict:
        """Changes for update_meta (without the folder rename)."""
        name = self.name_edit.text().strip()
        overrides: dict = {}
        if self.custom_template.isChecked() and self.template_edit.toPlainText().strip():
            overrides["template"] = self.template_edit.toPlainText()
        else:
            overrides["template"] = DELETE
        if self.plugins_custom.isChecked():
            enabled, settings = self._plugin_overrides()
            overrides["plugins"] = enabled
        else:
            overrides["plugins"] = DELETE
        return {"name": name or DELETE, "color": self._color or DELETE,
                "icon": self.icon_edit.text().strip() or DELETE, "overrides": overrides}

    def emoji_dialog(self):
        from daily_vibe.ui.emoji_picker import EmojiPickerDialog
        dlg = EmojiPickerDialog(self.config, self, "Choose Journal Icon")
        dlg.picker.picked.connect(self.icon_edit.setText)
        self._emoji_dialog = dlg
        return dlg

    def choose_emoji(self) -> None:
        self.emoji_dialog().open()

    def apply(self) -> bool:
        self.message.hide()
        name = self.name_edit.text().strip()
        try:
            validate_name(name) if self.rename_folder.isChecked() else None
            if not name:
                raise LibraryError("The display name can't be empty.")
        except LibraryError as exc:
            self._error(str(exc))
            return False
        changes = self.collect()
        plugin_settings = self._plugin_overrides()[1] if self.plugins_custom.isChecked() else {}
        if self.rename_folder.isChecked() and name != self.journal.root.name:
            if self.rename_handler is None:
                self._error("Renaming the folder isn't available here.")
                return False
            try:
                self.journal = self.rename_handler(name)
            except (LibraryError, OSError) as exc:
                self._error(f"Folder not renamed: {exc}")
                return False
        try:
            update_meta(self.journal.root, changes)
            # replace (not merge) the per-journal plugin settings
            update_meta(self.journal.root, {"overrides": {"plugin_settings": DELETE}})
            if plugin_settings:
                update_meta(self.journal.root, {"overrides": {"plugin_settings": plugin_settings}})
        except OSError as exc:
            self._error(f"Could not save {self.journal.root / '.dailyvibe.toml'}: {exc}")
            return False
        self.setWindowTitle(f"Journal Settings — {self.journal.name}")
        self._load_info()
        self.applied.emit(self.journal)
        return True

    def _ok(self) -> None:
        if self.apply():
            self.accept()

    def _error(self, text: str) -> None:
        self.message.setText(text)
        self.message.show()
        self.tabs.setCurrentIndex(0)

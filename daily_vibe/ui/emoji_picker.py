"""Searchable emoji picker: search box, Recent + category tabs, names as tooltips.
Used for journal icons (Journal Settings) and Edit → Insert Emoji (Ctrl+.)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from daily_vibe import emoji_data


def _grid() -> QListWidget:
    w = QListWidget()
    w.setViewMode(QListView.ViewMode.IconMode)
    w.setResizeMode(QListView.ResizeMode.Adjust)
    w.setMovement(QListView.Movement.Static)
    w.setUniformItemSizes(True)
    w.setGridSize(QSize(40, 40))
    w.setSpacing(0)
    w.setWordWrap(False)
    f = QFont(w.font())
    f.setPointSize(18)
    w.setFont(f)
    return w


class EmojiPicker(QWidget):
    picked = Signal(str)

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        data = config.data if config is not None else {}
        self.max_version = float(data.get("emoji_max_version", emoji_data.DEFAULT_MAX_VERSION))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search emoji by name or keyword (e.g. coffee, happy, tree)…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._pick_first)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.recent_list = _grid()
        self.tabs.addTab(self.recent_list, "🕘 Recent")
        self.group_lists: dict[str, QListWidget] = {}
        groups = emoji_data.by_group(self.max_version)
        for group, label, icon in emoji_data.GROUPS:
            lst = _grid()
            for e in groups.get(group, []):
                lst.addItem(self._item(e.char, e.name))
            lst.itemActivated.connect(self._activated)
            lst.itemClicked.connect(self._activated)
            self.tabs.addTab(lst, f"{icon} {label}")
            self.tabs.setTabToolTip(self.tabs.count() - 1, group)
            self.group_lists[group] = lst
        self.recent_list.itemActivated.connect(self._activated)
        self.recent_list.itemClicked.connect(self._activated)

        self.results = _grid()
        self.results.itemActivated.connect(self._activated)
        self.results.itemClicked.connect(self._activated)
        self.results_label = QLabel()
        rp = QWidget()
        rl = QVBoxLayout(rp)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.results_label)
        rl.addWidget(self.results, 1)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        self.stack.addWidget(rp)
        self.name_label = QLabel(" ")
        self.name_label.setObjectName("hint")
        for lst in [self.recent_list, self.results, *self.group_lists.values()]:
            lst.setMouseTracking(True)
            lst.itemEntered.connect(lambda it: self.name_label.setText(f"{it.text()}  {it.toolTip()}"))
            lst.currentItemChanged.connect(
                lambda it, _p: it is not None and self.name_label.setText(f"{it.text()}  {it.toolTip()}"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.search)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.name_label)
        self.refresh_recent()
        self.tabs.setCurrentIndex(0 if self.recent() else 1)

    @staticmethod
    def _item(char: str, name: str) -> QListWidgetItem:
        it = QListWidgetItem(char)
        it.setToolTip(name)
        it.setData(Qt.ItemDataRole.UserRole, char)
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return it

    # Recent ------------------------------------------------------------------
    def recent(self) -> list[str]:
        if self.config is None:
            return []
        return list(self.config.data.get("recent_emoji", []) or [])

    def refresh_recent(self) -> None:
        self.recent_list.clear()
        recent = self.recent()
        chars = recent or emoji_data.SUGGESTED
        for c in chars:
            e = emoji_data.lookup(c)
            self.recent_list.addItem(self._item(c, e.name if e else ""))
        self.tabs.setTabText(0, "🕘 Recent" if recent else "⭐ Suggested")

    def record(self, char: str) -> None:
        if self.config is None:
            return
        self.config.data["recent_emoji"] = emoji_data.push_recent(self.recent(), char)
        self.config.save()

    # Search -----------------------------------------------------------------
    def _search_changed(self, text: str) -> None:
        if not text.strip():
            self.stack.setCurrentIndex(0)
            return
        found = emoji_data.search(text, self.max_version)
        self.results.clear()
        for e in found:
            self.results.addItem(self._item(e.char, e.name))
        self.results_label.setText(f"{len(found)} result{'s' if len(found) != 1 else ''} for “{text.strip()}”"
                                   if found else f"No emoji match “{text.strip()}”")
        if found:
            self.results.setCurrentRow(0)
        self.stack.setCurrentIndex(1)

    def result_chars(self) -> list[str]:
        return [self.results.item(i).text() for i in range(self.results.count())]

    def _pick_first(self) -> None:
        if self.stack.currentIndex() == 1 and self.results.count():
            item = self.results.currentItem() or self.results.item(0)
            self._activated(item)

    def _activated(self, item: QListWidgetItem) -> None:
        char = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self.pick(char)

    def pick(self, char: str) -> None:
        self.record(char)
        self.refresh_recent()
        self.picked.emit(char)


class EmojiPickerDialog(QDialog):
    def __init__(self, config=None, parent=None, title: str = "Choose Emoji"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 420)
        self.chosen: str | None = None
        self.picker = EmojiPicker(config, self)
        self.picker.picked.connect(self._picked)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.picker, 1)
        lay.addWidget(buttons)
        self.picker.search.setFocus()

    def _picked(self, char: str) -> None:
        self.chosen = char
        self.accept()

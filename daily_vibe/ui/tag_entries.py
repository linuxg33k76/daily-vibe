"""Tag entries view: a page that replaces the editor area (with a Back button)
and lists every entry carrying a tag as cards."""
from __future__ import annotations

import datetime as dt
import urllib.parse

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from daily_vibe import tag_view
from daily_vibe import tags as tagmod


class TagEntriesView(QWidget):
    back_requested = Signal()
    open_requested = Signal(str, object)      # journal folder name, date
    export_requested = Signal()               # window asks for a path and exports self.cards
    rename_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = None
        self.current_journal = None   # callable -> Journal
        self.tag_index = None
        self.theme = None
        self.tag = ""
        self.cards: list[tag_view.TagEntry] = []

        self.back_btn = QPushButton("← Back to entry")
        self.back_btn.clicked.connect(self.back_requested)
        self.title = QLabel()
        self.title.setObjectName("dateLabel")
        top = QHBoxLayout()
        top.addWidget(self.back_btn)
        top.addWidget(self.title, 1)

        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.setMinimumWidth(180)
        self.tag_combo.setToolTip("Tag to show (type or pick)")
        self.tag_combo.activated.connect(lambda _i: self.show_tag(self.tag_combo.currentText().lstrip("#")))
        self.tag_combo.lineEdit().returnPressed.connect(
            lambda: self.show_tag(self.tag_combo.currentText().lstrip("#")))
        self.nested = QCheckBox("Include nested tags")
        self.nested.setChecked(True)
        self.nested.toggled.connect(lambda _on: self.refresh())
        self.scope = QComboBox()
        self.scope.addItem("This journal", "journal")
        self.scope.addItem("All journals", "all")
        self.scope.currentIndexChanged.connect(lambda _i: self._scope_changed())
        self.sort = QComboBox()
        self.sort.addItem("Newest first", "new")
        self.sort.addItem("Oldest first", "old")
        self.sort.currentIndexChanged.connect(lambda _i: self.refresh())
        self.rename_btn = QPushButton("Rename Tag…")
        self.rename_btn.clicked.connect(lambda: self.rename_requested.emit(self.tag))
        self.export_btn = QPushButton("Export These to PDF…")
        self.export_btn.clicked.connect(self.export_requested)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Tag:"))
        bar.addWidget(self.tag_combo)
        bar.addWidget(self.nested)
        bar.addWidget(self.scope)
        bar.addWidget(self.sort)
        bar.addStretch(1)
        bar.addWidget(self.rename_btn)
        bar.addWidget(self.export_btn)

        self.count_label = QLabel()
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._link)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 6)
        lay.addLayout(top)
        lay.addLayout(bar)
        lay.addWidget(self.count_label)
        lay.addWidget(self.browser, 1)

    def setup(self, library, current_journal, tag_index, theme) -> None:
        self.library, self.current_journal, self.tag_index, self.theme = library, current_journal, tag_index, theme

    # State -----------------------------------------------------------------------
    def scope_value(self) -> str:
        return self.scope.currentData()

    def set_scope(self, scope: str) -> None:
        i = self.scope.findData(scope)
        if i >= 0 and i != self.scope.currentIndex():
            self.scope.setCurrentIndex(i)

    def set_newest_first(self, on: bool) -> None:
        self.sort.setCurrentIndex(0 if on else 1)

    def journals(self):
        if self.scope_value() == "all":
            return self.library.journals()
        return [self.current_journal()]

    def _scope_changed(self) -> None:
        self._fill_tags()
        self.refresh()

    def _fill_tags(self) -> None:
        if self.tag_index is None:
            return
        counts = self.tag_index.counts(self.journals())
        names = sorted((d for d, _n in counts.values()), key=str.lower)
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItems(["#" + n for n in names])
        self.tag_combo.setEditText("#" + self.tag if self.tag else "")
        self.tag_combo.blockSignals(False)

    def show_tag(self, tag: str, scope: str | None = None) -> None:
        self.tag = (tag or "").strip().lstrip("#")
        if scope:
            self.scope.blockSignals(True)
            self.set_scope(scope)
            self.scope.blockSignals(False)
        self._fill_tags()
        self.refresh()

    def refresh(self) -> None:
        if self.tag_index is None or self.theme is None:
            return
        where = "all journals" if self.scope_value() == "all" else self.current_journal().name
        if not self.tag or not tagmod.is_valid_tag(self.tag):
            self.cards = []
            self.title.setText("Tag browser")
            self.count_label.setText("Pick a tag.")
            self.browser.setHtml("")
            self.export_btn.setEnabled(False)
            self.rename_btn.setEnabled(False)
            return
        self.cards = tag_view.collect(self.journals(), self.tag_index, self.tag, self.nested.isChecked(),
                                      self.sort.currentData() == "new")
        self.title.setText(f"Entries tagged #{self.tag}")
        n = len(self.cards)
        self.count_label.setText(f"{n} entr{'y' if n == 1 else 'ies'} in {where}"
                                 + ("" if self.nested.isChecked() else " (exact tag only)"))
        bar = self.browser.verticalScrollBar().value()
        self.browser.setHtml(tag_view.cards_html(self.cards, self.tag, self.nested.isChecked(),
                                                 self.scope_value() == "all", self.theme))
        self.browser.verticalScrollBar().setValue(bar)
        self.export_btn.setEnabled(n > 0)
        self.rename_btn.setEnabled(True)

    def set_theme(self, theme) -> None:
        self.theme = theme
        if self.isVisible():
            self.refresh()

    def open_card(self, index: int) -> None:
        card = self.cards[index]
        self.open_requested.emit(card.journal.root.name, card.date)

    def _link(self, url: QUrl) -> None:
        s = url.toString()
        if url.scheme() == "entry":
            folder, _, iso = urllib.parse.unquote(s[6:]).rpartition("/")
            self.open_requested.emit(folder, dt.date.fromisoformat(iso))
        elif url.scheme() == "tag":
            self.show_tag(urllib.parse.unquote(s[4:]))

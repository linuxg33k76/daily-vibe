"""Small sidebar widgets: On This Day panel and streak label."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from daily_vibe.on_this_day import on_this_day


class OnThisDayPanel(QWidget):
    open_requested = Signal(object)  # dt.date

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        self.title = QLabel("On This Day")
        self.title.setObjectName("panelTitle")
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemActivated.connect(self._clicked)
        layout.addWidget(self.title)
        layout.addWidget(self.list, 1)

    def update_for(self, journal, date: dt.date) -> None:
        self.title.setText(f"On This Day — {date:%b} {date.day}")
        self.list.clear()
        memories = on_this_day(journal, date)
        if not memories:
            item = QListWidgetItem("Nothing from this day in earlier years yet.\nFuture you will thank you for today's entry.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            return
        for m in memories:
            text = f"{m.label}\n  {m.excerpt or '(no text yet)'}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, m.date)
            item.setToolTip(f"{m.date:%A, %B} {m.date.day}, {m.date:%Y}")
            self.list.addItem(item)

    def clear(self) -> None:
        self.list.clear()
        self.title.setText("On This Day")

    def _clicked(self, item: QListWidgetItem) -> None:
        date = item.data(Qt.ItemDataRole.UserRole)
        if date:
            self.open_requested.emit(date)

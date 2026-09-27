"""Journal → Tag Rename History…: list every tag rename (with its backup) and
undo any of them (newer renames touching the same entries must be undone first)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from daily_vibe import tag_rename


class TagRenameHistoryDialog(QDialog):
    def __init__(self, records_provider: Callable[[], list], undo_handler: Callable[[str], object],
                 retention_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tag Rename History")
        self.resize(820, 420)
        self.records_provider = records_provider
        self.undo_handler = undo_handler
        self.records: list[tag_rename.RenameRecord] = []
        self.last_result = None
        self.last_error = ""

        intro = QLabel("Every tag rename keeps a backup of the entries it changed. Undo restores entries that "
                       "haven't been edited since. A rename can only be undone after newer renames that "
                       "touched the same entries are undone (newest first).")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Date", "Rename", "Scope", "Journals", "Files", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.undo_btn = QPushButton("Undo Selected Rename")
        self.undo_btn.clicked.connect(self.undo_selected)
        self.folder_btn = QPushButton("Open Backup Folder")
        self.folder_btn.clicked.connect(self._open_folder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.undo_btn)
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        if retention_text:
            keep = QLabel(retention_text)
            keep.setObjectName("hint")
            row.addWidget(keep)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.detail)
        lay.addLayout(row)
        lay.addWidget(buttons)
        self.reload()

    def reload(self) -> None:
        self.records = self.records_provider()
        self.tree.clear()
        colors = {tag_rename.APPLIED: None, tag_rename.UNDONE: "#888888", tag_rename.PARTIAL: "#c77700"}
        for rec in self.records:
            when = rec.created.strftime("%Y-%m-%d %H:%M") if rec.created else rec.stamp
            scope = "All journals" if rec.scope == "all" else "This journal"
            if rec.nested:
                scope += " · nested"
            item = QTreeWidgetItem([when, rec.label, scope, ", ".join(rec.journals), str(len(rec.files)),
                                    rec.status])
            item.setData(0, Qt.ItemDataRole.UserRole, rec.stamp)
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if colors.get(rec.status):
                for col in range(6):
                    item.setForeground(col, QBrush(QColor(colors[rec.status])))
            self.tree.addTopLevelItem(item)
        for col in (0, 2, 3, 4, 5):
            self.tree.resizeColumnToContents(col)
        if self.records:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._update_buttons()

    def selected(self) -> tag_rename.RenameRecord | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        stamp = item.data(0, Qt.ItemDataRole.UserRole)
        return next((r for r in self.records if r.stamp == stamp), None)

    def select_stamp(self, stamp: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == stamp:
                self.tree.setCurrentItem(it)
                return

    def _update_buttons(self) -> None:
        rec = self.selected()
        self.folder_btn.setEnabled(rec is not None)
        if rec is None:
            self.undo_btn.setEnabled(False)
            self.detail.setText("No tag renames yet." if not self.records else "")
            return
        newer = tag_rename.blockers(self.records, rec.stamp)
        can = rec.status == tag_rename.APPLIED and not newer
        self.undo_btn.setEnabled(can)
        if rec.status != tag_rename.APPLIED:
            text = f"{rec.label} is {rec.status}."
            if rec.conflicts:
                text += " Not restored (edited after the rename): " + ", ".join(rec.conflicts[:5])
        elif newer:
            text = ("Undo first (newer renames of the same entries): "
                    + ", ".join(f"{r.label} ({r.created:%Y-%m-%d %H:%M})" if r.created else r.label for r in newer))
        else:
            text = f"Undo {rec.label}: restores {len(rec.files)} entr{'y' if len(rec.files) == 1 else 'ies'} " \
                   "unless they were edited since."
        self.detail.setText(text)

    def undo_selected(self, confirm: bool = True):
        rec = self.selected()
        if rec is None:
            return None
        if confirm and QMessageBox.question(
                self, "Undo tag rename", f"Undo {rec.label}?") != QMessageBox.StandardButton.Yes:
            return None
        try:
            self.last_result = self.undo_handler(rec.stamp)
            self.last_error = ""
        except tag_rename.TagRenameError as exc:
            self.last_error = str(exc)
            if confirm:
                QMessageBox.warning(self, "Can't undo", str(exc))
            return None
        self.reload()
        self.select_stamp(rec.stamp)
        if confirm and self.last_result is not None:
            QMessageBox.information(self, "Tag rename undone", self.last_result.summary())
        return self.last_result

    def _open_folder(self) -> None:
        rec = self.selected()
        if rec and rec.backups:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(rec.backups[0])))

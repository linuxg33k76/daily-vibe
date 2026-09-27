"""Rename Tag… dialog: old/new tag, scope, nested option, preview, apply."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from daily_vibe import tags as tagmod
from daily_vibe.tag_rename import RenamePlan, TagRenameError


class TagRenameDialog(QDialog):
    """`planner(old, new, nested, scope) -> RenamePlan`, `applier(plan) -> bool`."""

    def __init__(self, known_tags: list[str], planner: Callable, applier: Callable,
                 old: str = "", scope: str = "journal", journal_name: str = "", parent=None):
        super().__init__(parent)
        self.planner, self.applier = planner, applier
        self.plan: RenamePlan | None = None
        self.setWindowTitle("Rename Tag")
        self.setMinimumSize(760, 520)

        form = QFormLayout()
        self.old_combo = QComboBox()
        self.old_combo.setEditable(True)
        self.old_combo.addItems(sorted(known_tags, key=str.lower))
        self.old_combo.setCurrentText(old.lstrip("#"))
        self.new_edit = QLineEdit()
        self.new_edit.setPlaceholderText("new tag, e.g. fitness or health/walking")
        comp = QCompleter(sorted(known_tags, key=str.lower), self.new_edit)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.new_edit.setCompleter(comp)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem(f"This journal ({journal_name})" if journal_name else "This journal", "journal")
        self.scope_combo.addItem("All journals", "all")
        self.scope_combo.setCurrentIndex(1 if scope == "all" else 0)
        self.nested = QCheckBox("Include nested tags (#old/child → #new/child)")
        self.nested.setChecked(True)
        case = QLabel("Matching ignores case, like the tag panel (#Health = #health); the new tag is written as you type it.")
        case.setObjectName("hint")
        case.setWordWrap(True)
        form.addRow("Rename tag:", self.old_combo)
        form.addRow("To:", self.new_edit)
        form.addRow("In:", self.scope_combo)
        form.addRow("", self.nested)
        form.addRow("", case)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.merge_label = QLabel()
        self.merge_label.setObjectName("warning")
        self.merge_label.setWordWrap(True)
        self.merge_label.hide()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Journal", "Entry", "Changes", "Before → after (sample)"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 100)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 70)

        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_btn = self.buttons.addButton("Rename", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_btn.setEnabled(False)
        self.buttons.accepted.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        for sig in (self.old_combo.currentTextChanged, self.new_edit.textChanged):
            sig.connect(self._invalidate)
        self.scope_combo.currentIndexChanged.connect(self._invalidate)
        self.nested.toggled.connect(self._invalidate)

        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.preview_btn)
        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addLayout(row)
        v.addWidget(self.merge_label)
        v.addWidget(self.tree, 1)
        v.addWidget(self.buttons)
        self.new_edit.setFocus()

    def scope(self) -> str:
        return self.scope_combo.currentData()

    def _invalidate(self, *_a) -> None:
        self.plan = None
        self.apply_btn.setEnabled(False)
        self.tree.clear()
        self.merge_label.hide()
        new = self.new_edit.text().strip().lstrip("#")
        if new and not tagmod.is_valid_tag(new):
            self.status.setText(f"⚠ #{new} isn't a valid tag (start with a letter; letters, digits, - _ and / only; not a hex color).")
        else:
            self.status.setText("Press Preview to see the affected entries.")

    def preview(self) -> RenamePlan | None:
        self._invalidate()
        try:
            plan = self.planner(self.old_combo.currentText(), self.new_edit.text(), self.nested.isChecked(), self.scope())
        except TagRenameError as exc:
            self.status.setText(f"⚠ {exc}")
            return None
        self.plan = plan
        for c in plan.changes:
            sample = "   ".join(f"{b or '∅'}  →  {a or '(removed duplicate)'}" for b, a in c.samples[:2])
            item = QTreeWidgetItem([c.journal.name, c.date.isoformat(), str(c.count), sample])
            item.setToolTip(3, "\n".join(f"- {b}\n+ {a}" for b, a in c.samples))
            item.setData(0, Qt.ItemDataRole.UserRole, c)
            self.tree.addTopLevelItem(item)
        n = len(plan.changes)
        self.status.setText(f"#{plan.old} → #{plan.new}: {plan.total} change(s) in {n} entr{'y' if n == 1 else 'ies'}."
                            + ("" if n else " Nothing to rename."))
        if plan.merge_with:
            self.merge_label.setText(f"⚠ #{plan.merge_with} already exists: the two tags will be merged "
                                     "(duplicates in front matter lists are removed).")
            self.merge_label.show()
        self.apply_btn.setEnabled(n > 0)
        return plan

    def _apply(self) -> None:
        if self.plan is None:
            return
        if self.applier(self.plan):
            self.accept()

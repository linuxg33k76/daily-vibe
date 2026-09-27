"""Export dialog and journal relocation UI."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QProgressDialog, QRadioButton, QVBoxLayout,
)

from daily_vibe import relocate


class ExportRangeDialog(QDialog):
    def __init__(self, start: dt.date, end: dt.date, parent=None, known_tags: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export Date Range to PDF")
        form = QFormLayout()
        self.start = QDateEdit(QDate(start.year, start.month, start.day))
        self.end = QDateEdit(QDate(end.year, end.month, end.day))
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("yyyy-MM-dd")
        form.addRow("From:", self.start)
        form.addRow("To:", self.end)
        self.per_page = QRadioButton("One entry per page")
        self.continuous = QRadioButton("Continuous (entries separated by headings)")
        self.per_page.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.per_page)
        group.addButton(self.continuous)
        form.addRow("Layout:", self.per_page)
        form.addRow("", self.continuous)
        self.page_size = QComboBox()
        self.page_size.addItems(["Letter", "A4"])
        form.addRow("Paper:", self.page_size)
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("optional, e.g. work  or  work family (all must match)")
        if known_tags:
            from PySide6.QtWidgets import QCompleter
            comp = QCompleter(sorted(known_tags, key=str.lower), self.tag_edit)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.tag_edit.setCompleter(comp)
        form.addRow("Only tagged:", self.tag_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addWidget(buttons)

    def values(self) -> tuple[dt.date, dt.date, bool, str]:
        s, e = self.start.date().toPython(), self.end.date().toPython()
        if s > e:
            s, e = e, s
        return s, e, self.per_page.isChecked(), self.page_size.currentText()

    def tag_filter(self) -> list[str]:
        return [t.lstrip("#") for t in self.tag_edit.text().replace(",", " ").split() if t.lstrip("#")]


def ask_relocation(parent, source: Path, target: Path, single_journal: bool = False) -> str | None:
    """Returns "move", "copy", "switch" or None (cancel).
    single_journal: moving/copying one journal folder (no "Just switch")."""
    files, other = relocate.journal_files(source, include_all=single_journal)
    what = "journal" if single_journal else "library"
    if not files and not single_journal:
        return "switch"
    try:
        relocate.check_paths(source, target)
    except relocate.RelocateError as exc:
        if single_journal:
            QMessageBox.warning(parent, "Move journal", str(exc))
            return None
        box = QMessageBox(QMessageBox.Icon.Warning, "Change library folder",
                          f"{exc}\n\nEntries can't be moved or copied between nested folders. "
                          "You can still switch to the new folder as-is.", parent=parent)
        switch = box.addButton("Just switch", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return "switch" if box.clickedButton() is switch else None
    size = sum(f.stat().st_size for f in files)
    entries = sum(1 for f in files if f.suffix == ".md")
    title = "Move or copy journal" if single_journal else "Change library folder"
    box = QMessageBox(QMessageBox.Icon.Question, title, "", parent=parent)
    scope = ("This journal" if single_journal else
             "Your library (all journals)")
    box.setText(f"{scope} has {entries} entries and {len(files) - entries} other file(s) "
                f"({size / 1024:.0f} KB).\n\nFrom: {source}\nTo:   {target}\n\nWhat should happen to them?")
    box.setInformativeText("Existing files in the new folder are never overwritten; conflicts are skipped "
                           "and listed at the end. A move only deletes a source file after its copy is verified.")
    move = box.addButton("Move entries", QMessageBox.ButtonRole.AcceptRole)
    copy = box.addButton("Copy entries", QMessageBox.ButtonRole.AcceptRole)
    switch = None if single_journal else box.addButton("Just switch", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(move)
    parent._relocate_box = box  # for screenshots/tests
    box.exec()
    clicked = box.clickedButton()
    return {move: "move", copy: "copy"}.get(clicked) or ("switch" if switch is not None and clicked is switch else None)


class _TransferWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, source, target, mode, include_all=False):
        super().__init__()
        self.source, self.target, self.mode, self.include_all = source, target, mode, include_all
        self.cancel_requested = False

    def run(self):
        try:
            report = relocate.transfer(self.source, self.target, self.mode,
                                       progress=lambda i, n, p: self.progress.emit(i, n, p),
                                       canceled=lambda: self.cancel_requested,
                                       include_all=self.include_all)
        except Exception as exc:
            report = relocate.TransferReport(self.mode, Path(self.source), Path(self.target), errors=[str(exc)])
        self.finished.emit(report)


class TransferJob(QObject):
    """Runs relocate.transfer on a QThread with a progress dialog."""

    done = Signal(object)  # TransferReport

    def __init__(self, parent, source: Path, target: Path, mode: str, include_all: bool = False):
        super().__init__(parent)
        self.thread = QThread(self)
        self.worker = _TransferWorker(source, target, mode, include_all)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        verb = "Moving" if mode == "move" else "Copying"
        what = "journal" if include_all else "library"
        self.dialog = QProgressDialog(f"{verb} {what}…", "Cancel", 0, 0, parent)
        self.dialog.setWindowTitle(f"{verb} {what}")
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.canceled.connect(self._cancel)

    def start(self):
        self.dialog.show()
        self.thread.start()

    def _cancel(self):
        self.worker.cancel_requested = True

    def _progress(self, i, n, path):
        self.dialog.setMaximum(n)
        self.dialog.setValue(i)
        self.dialog.setLabelText(f"{i} / {n}\n{path}")

    def _finished(self, report):
        self.thread.quit()
        self.thread.wait()
        self.dialog.canceled.disconnect(self._cancel)
        self.dialog.reset()
        self.dialog.hide()
        self.done.emit(report)

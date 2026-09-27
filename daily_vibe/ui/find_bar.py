"""Slim Find / Replace bar under the editor (Ctrl+F, Ctrl+H, F3 / Shift+F3, Esc).

Matches are highlighted as editor extra selections ("n of m" counter); the
current match is selected in the editor, which in Live Preview also reveals the
raw Markdown of that line. Replace All is a single undo step."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

from daily_vibe import find_replace as fr

MAX_HIGHLIGHTS = 3000


class _FindEdit(QLineEdit):
    next_requested = Signal()
    prev_requested = Signal()
    escape = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.prev_requested.emit()
            else:
                self.next_requested.emit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(event)


class FindBar(QWidget):
    message = Signal(str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.setObjectName("findBar")
        self.editor = editor
        self.theme = None
        self.matches: list[tuple[int, int]] = []     # UTF-16 positions
        self._py_spans: list[tuple[int, int]] = []
        self.current = -1
        self.error = ""

        self.find_edit = _FindEdit()
        self.find_edit.setPlaceholderText("Find in this entry")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.textChanged.connect(lambda _t: self.refresh(keep_position=False))
        self.find_edit.next_requested.connect(self.find_next)
        self.find_edit.prev_requested.connect(self.find_prev)
        self.find_edit.escape.connect(self.close_bar)
        self.counter = QLabel()
        self.counter.setMinimumWidth(90)
        self.prev_btn = self._tool("▲", "Previous match (Shift+F3 / Shift+Enter)", self.find_prev)
        self.next_btn = self._tool("▼", "Next match (F3 / Enter)", self.find_next)
        self.case_btn = self._tool("Aa", "Match case", lambda: self.refresh(keep_position=True), checkable=True)
        self.word_btn = self._tool("W", "Whole word", lambda: self.refresh(keep_position=True), checkable=True)
        self.regex_btn = self._tool(".*", "Regular expression", lambda: self.refresh(keep_position=True),
                                    checkable=True)
        self.replace_toggle = self._tool("⇄", "Show replace (Ctrl+H)", self.toggle_replace, checkable=True)
        self.close_btn = self._tool("✕", "Close (Esc)", self.close_bar)
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.addWidget(self.replace_toggle)
        row1.addWidget(self.find_edit, 1)
        row1.addWidget(self.counter)
        for w in (self.prev_btn, self.next_btn, self.case_btn, self.word_btn, self.regex_btn, self.close_btn):
            row1.addWidget(w)

        self.replace_edit = _FindEdit()
        self.replace_edit.setPlaceholderText("Replace with (regex: \\1, \\g<name>)")
        self.replace_edit.next_requested.connect(self.replace_current)
        self.replace_edit.escape.connect(self.close_bar)
        self.replace_btn = QPushButton("Replace")
        self.replace_btn.clicked.connect(self.replace_current)
        self.replace_all_btn = QPushButton("Replace All")
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.replace_row = QWidget()
        row2 = QHBoxLayout(self.replace_row)
        row2.setContentsMargins(28, 0, 0, 0)
        row2.addWidget(self.replace_edit, 1)
        row2.addWidget(self.replace_btn)
        row2.addWidget(self.replace_all_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(3)
        lay.addLayout(row1)
        lay.addWidget(self.replace_row)
        self.replace_row.hide()
        self.hide()

        self._timer = QTimer(self, singleShot=True, interval=150, timeout=lambda: self.refresh(keep_position=True))
        editor.document().contentsChange.connect(self._doc_changed)
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), editor, self._editor_escape,
                              context=Qt.ShortcutContext.WidgetShortcut)
        self._esc.setEnabled(False)

    def _tool(self, text, tip, slot, checkable=False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.clicked.connect(lambda *_: slot())
        return b

    @property
    def is_open(self) -> bool:
        """Open even while the window itself isn't shown (isVisible() would be False)."""
        return not self.isHidden()

    # Options ----------------------------------------------------------------------
    def options(self) -> fr.FindOptions:
        return fr.FindOptions(self.case_btn.isChecked(), self.word_btn.isChecked(), self.regex_btn.isChecked())

    def set_theme(self, theme) -> None:
        self.theme = theme
        if self.is_open:
            self._highlight()

    # Open / close -------------------------------------------------------------------
    def open_bar(self, replace: bool = False) -> None:
        sel = self.editor.textCursor().selectedText()
        if sel and "\u2029" not in sel and len(sel) < 200:
            self.find_edit.blockSignals(True)
            self.find_edit.setText(sel)
            self.find_edit.blockSignals(False)
        self.replace_toggle.setChecked(replace)
        self.replace_row.setVisible(replace)
        self.show()
        self._esc.setEnabled(True)
        (self.replace_edit if replace and self.find_edit.text() else self.find_edit).setFocus()
        self.find_edit.selectAll()
        self.refresh(keep_position=False)

    def toggle_replace(self) -> None:
        self.replace_row.setVisible(self.replace_toggle.isChecked())

    def close_bar(self) -> None:
        self.hide()
        self._esc.setEnabled(False)
        self.matches, self._py_spans, self.current = [], [], -1
        self.editor.set_find_selections([])
        self.editor.setFocus()

    def _editor_escape(self) -> None:
        popup = self.editor.completer.popup()
        if popup.isVisible():
            popup.hide()
        else:
            self.close_bar()

    # Matching --------------------------------------------------------------------------
    def _doc_changed(self, _pos, removed, added) -> None:
        if self.is_open and (removed or added) and not self.editor.formatting:
            self._timer.start()

    def refresh(self, keep_position: bool = True) -> None:
        if not self.is_open:
            return
        text = self.editor.toPlainText()
        query = self.find_edit.text()
        self.error = ""
        try:
            spans = fr.find_all(text, query, self.options())
        except re.error as exc:
            spans, self.error = [], f"Invalid regex: {exc.msg}"
        u = fr.utf16_offsets(text)
        self._py_spans = spans
        self.matches = [(u(s), u(e)) for s, e in spans]
        if not self.matches:
            self.current = -1
        else:
            pos = self.editor.textCursor().selectionStart()
            if keep_position and 0 <= self.current < len(self.matches):
                pos = self.matches[self.current][0]
            self.current = next((i for i, (s, _e) in enumerate(self.matches) if s >= pos), 0)
        self._update_counter()
        self._highlight()

    def _update_counter(self) -> None:
        q = self.find_edit.text()
        if self.error:
            self.counter.setText("bad regex")
            self.counter.setToolTip(self.error)
            self.find_edit.setStyleSheet("QLineEdit { border: 1px solid #e05561; }")
        elif q and not self.matches:
            self.counter.setText("No results")
            self.counter.setToolTip("")
            self.find_edit.setStyleSheet("QLineEdit { border: 1px solid #e05561; }")
        else:
            self.counter.setText(f"{self.current + 1} of {len(self.matches)}" if self.matches else "")
            self.counter.setToolTip("")
            self.find_edit.setStyleSheet("")
        has = bool(self.matches)
        for b in (self.prev_btn, self.next_btn, self.replace_btn, self.replace_all_btn):
            b.setEnabled(has)

    def counter_text(self) -> str:
        return self.counter.text()

    def _highlight(self) -> None:
        accent = QColor(self.theme.colors["accent"]) if self.theme else QColor("#2f6fdb")
        other = QColor("#f5c542")
        other.setAlpha(110)
        cur_bg = QColor(accent)
        sels = []
        doc = self.editor.document()
        for i, (s, e) in enumerate(self.matches[:MAX_HIGHLIGHTS]):
            sel = QTextEdit.ExtraSelection()
            c = QTextCursor(doc)
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = c
            if i == self.current:
                sel.format.setBackground(cur_bg)
                sel.format.setForeground(QColor(self.theme.accent_text if self.theme else "#ffffff"))
            else:
                sel.format.setBackground(other)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, False)
            sels.append(sel)
        self.editor.set_find_selections(sels)

    def _select_current(self) -> None:
        if not (0 <= self.current < len(self.matches)):
            return
        s, e = self.matches[self.current]
        c = self.editor.textCursor()
        c.setPosition(s)
        c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(c)       # Live Preview reveals markers on this line
        self.editor.ensureCursorVisible()
        self._update_counter()
        self._highlight()

    def find_next(self) -> None:
        if not self.is_open:
            self.open_bar()
        if not self.matches:
            return
        c = self.editor.textCursor()
        if c.hasSelection() and 0 <= self.current < len(self.matches) and \
                (c.selectionStart(), c.selectionEnd()) == self.matches[self.current]:
            self.current = (self.current + 1) % len(self.matches)
        else:
            pos = c.selectionStart()
            self.current = next((i for i, (s, _e) in enumerate(self.matches) if s >= pos), 0)
        self._select_current()

    def find_prev(self) -> None:
        if not self.is_open:
            self.open_bar()
        if not self.matches:
            return
        c = self.editor.textCursor()
        if c.hasSelection() and 0 <= self.current < len(self.matches) and \
                (c.selectionStart(), c.selectionEnd()) == self.matches[self.current]:
            self.current = (self.current - 1) % len(self.matches)
        else:
            pos = c.selectionStart()
            before = [i for i, (s, _e) in enumerate(self.matches) if s < pos]
            self.current = before[-1] if before else len(self.matches) - 1
        self._select_current()

    # Replace ---------------------------------------------------------------------------
    def replace_current(self) -> None:
        if not self.matches:
            return
        c = self.editor.textCursor()
        if not (c.hasSelection() and 0 <= self.current < len(self.matches)
                and (c.selectionStart(), c.selectionEnd()) == self.matches[self.current]):
            self.find_next()     # first press selects the match, like most editors
            return
        text = self.editor.toPlainText()
        rep = fr.replacement_for(text, self._py_spans[self.current], self.find_edit.text(),
                                 self.replace_edit.text(), self.options())
        start = c.selectionStart()
        c.insertText(rep)                  # one undo step
        self.refresh(keep_position=False)
        if self.matches:
            after = start + len(rep.encode("utf-16-le")) // 2
            self.current = next((i for i, (s, _e) in enumerate(self.matches) if s >= after), 0)
            self._select_current()

    def replace_all(self) -> int:
        if not self.matches:
            return 0
        text = self.editor.toPlainText()
        opts, q, r = self.options(), self.find_edit.text(), self.replace_edit.text()
        reps = [fr.replacement_for(text, span, q, r, opts) for span in self._py_spans]
        c = QTextCursor(self.editor.document())
        c.beginEditBlock()                 # single undo step
        for (s, e), rep in reversed(list(zip(self.matches, reps))):
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(rep)
        c.endEditBlock()
        n = len(reps)
        self.refresh(keep_position=False)
        self.message.emit(f"Replaced {n} occurrence{'s' if n != 1 else ''}")
        return n

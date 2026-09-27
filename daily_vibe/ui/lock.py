"""Lock screen and idle watcher."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


class LockScreen(QWidget):
    unlock_requested = Signal(str)

    def __init__(self, icon_path: str | None = None, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        box = QWidget()
        box.setMaximumWidth(380)
        v = QVBoxLayout(box)
        if icon_path:
            icon = QLabel()
            icon.setPixmap(QPixmap(icon_path).scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(icon)
        title = QLabel("The Daily Vibe is locked")
        title.setObjectName("lockTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Password")
        self.password.returnPressed.connect(self._submit)
        v.addWidget(self.password)
        self.button = QPushButton("Unlock")
        self.button.setDefault(True)
        self.button.clicked.connect(self._submit)
        v.addWidget(self.button)
        self.message = QLabel("")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        v.addWidget(self.message)
        note = QLabel("App lock only: your entries are stored as plain, unencrypted Markdown files.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(note)
        row = QVBoxLayout()
        row.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(row)
        outer.addStretch(3)
        self._countdown = QTimer(self, interval=500)
        self._countdown.timeout.connect(self._tick)
        self._limiter = None

    def reset(self) -> None:
        self.password.clear()
        self.message.clear()
        self.password.setFocus()

    def _submit(self) -> None:
        if self.button.isEnabled():
            self.unlock_requested.emit(self.password.text())

    def show_error(self, text: str, limiter=None) -> None:
        self.password.clear()
        self.message.setText(text)
        self._limiter = limiter
        if limiter is not None and not limiter.can_try():
            self._tick()
            self._countdown.start()

    def _tick(self) -> None:
        if self._limiter is None or self._limiter.can_try():
            self._countdown.stop()
            self.button.setEnabled(True)
            self.password.setEnabled(True)
            if self._limiter is not None:
                self.message.setText("Wrong password. Try again.")
                self.password.setFocus()
            return
        self.button.setEnabled(False)
        self.password.setEnabled(False)
        self.message.setText(f"Too many failed attempts. Try again in {self._limiter.remaining():.0f} s.")


class IdleWatcher(QObject):
    """App-wide event filter: emits `idle` after `minutes` without input."""

    idle = Signal()
    INPUT_EVENTS = {QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                    QEvent.Type.Wheel, QEvent.Type.TouchBegin}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.idle)

    def set_minutes(self, minutes: int) -> None:
        if minutes and minutes > 0:
            self.timer.setInterval(int(minutes * 60_000))
            self.timer.start()
        else:
            self.timer.stop()

    def eventFilter(self, obj, event) -> bool:
        if event.type() in self.INPUT_EVENTS and self.timer.interval() > 0 and self.timer.isActive():
            self.timer.start()
        return False

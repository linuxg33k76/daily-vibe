from __future__ import annotations

import logging
import sys
from pathlib import Path

from daily_vibe import APP_DISPLAY_NAME, APP_ID


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from daily_vibe.config import Config
    from daily_vibe.ui.main_window import ICON_PATH, MainWindow

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_DISPLAY_NAME)       # macOS menu items ("About/Quit The Daily Vibe")
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_ID)
    app.setDesktopFileName(APP_ID)                 # Wayland app-id / .desktop matching
    if Path(ICON_PATH).exists():
        app.setWindowIcon(QIcon(ICON_PATH))
    window = MainWindow(Config.load())
    window.resize(1400, 850)
    window.show()
    window.open_today(create=False)
    from PySide6.QtCore import QTimer
    QTimer.singleShot(400, window.maybe_migrate)  # old single-journal layout? ask to upgrade
    return app.exec()

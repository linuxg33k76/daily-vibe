"""0.8.1 screenshot: Journal Settings showing the US-English "Color:" field.

    DISPLAY=:8 python scripts/shot_r8_10.py   ->  screenshots/r8_10_us_english_color.png
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "r8_10"
sys.path.insert(0, str(ROOT))
shutil.rmtree(DEMO, ignore_errors=True)
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

import datetime as dt  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from daily_vibe.config import Config  # noqa: E402
from daily_vibe.library import Library  # noqa: E402
from daily_vibe.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
lib = Library(DEMO / "Journal")
j = lib.create("Personal", color="#fab387", icon="🌿")
j.write(dt.date(2026, 9, 26), "# Saturday\n\nA calm day. #rest\n")
cfg = Config.load()
cfg.journal_root = DEMO / "Journal"
cfg.data["plugins"]["enabled"] = []
cfg.data["last_journal"] = "Personal"
cfg.save()
win = MainWindow(cfg)
win.resize(1100, 760)
win.show()
dlg = win.open_journal_settings()
dlg.show()
dlg.raise_()
for _ in range(30):
    app.processEvents()
out = ROOT / "screenshots" / "r8_10_us_english_color.png"
dlg.grab().save(str(out))
print("saved", out.relative_to(ROOT))
dlg.close()
win.close()
win.runner.shutdown(1000)

"""Round-7 demo: Live Preview tables / nested lists / inline images, find & replace,
Preferences typography, Reading view with table + strikethrough. Saves screenshots/r7_*.png."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "r7"
SHOTS = ROOT / "screenshots"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

import demo_round4 as r4  # noqa: E402

os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")   # demo_round4 sets its own on import
from PySide6.QtGui import QIcon, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daily_vibe.config import Config  # noqa: E402
from daily_vibe.images import convert_to_webp, markdown_link  # noqa: E402
from daily_vibe.storage import Journal  # noqa: E402
from daily_vibe.ui.main_window import ICON_PATH, MainWindow  # noqa: E402

ONLY = set(sys.argv[1:])


def log(m):
    print(m, flush=True)


def grab_screen(app, widgets, name):
    geo = widgets[0].frameGeometry()
    for w in widgets[1:]:
        geo = geo.united(w.frameGeometry())
    app.primaryScreen().grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height()).save(str(SHOTS / name))
    log(f"saved screenshots/{name}")


ENTRY = """# Training log — week 38

Solid week. The ~~rest day~~ easy spin on Wednesday helped a lot. #training

| Day       | Activity        | Distance | Feel   |
|:----------|:---------------:|---------:|:-------|
| Monday    | Easy run        | 6.2 km   | 🙂 good |
| Tuesday   | Intervals 6×800 | 8.0 km   | 💪 strong |
| Wednesday | Spin bike       | 20 km    | 😌 easy |
| Thursday  | Tempo           | 10.5 km  | 😅 hard |
| Saturday  | **Long run**    | 21.1 km  | 🎉 done! |

## Plan for next week

- Running
  - Tuesday: intervals
    - 8 × 400 m at 5k pace
    - [ ] Book the track
  - Saturday: long run 24 km
- Strength
    - Squats & lunges
        - 3 × 10 each
    - [x] Buy a kettlebell
- Recovery
\t- Foam roller
\t\t- [ ] Calves every evening

1. Sleep 8 hours
   1. Phone out of the bedroom
   2. Lights out by 22:30
2. Eat more greens

Shoes on the left INL1 and my watch INL2 after the run, both muddy.

The run felt great. Every run this week felt better than the last run, and the long run was the best run yet.
"""


def main() -> int:
    shutil.rmtree(DEMO, ignore_errors=True)
    if not ONLY:
        for old in SHOTS.glob("r7_*"):
            old.unlink()
    (DEMO / "config").mkdir(parents=True)
    r4.DEMO = DEMO
    today = dt.date.today()
    lib_root = DEMO / "Library"
    r4.build_library(lib_root, today)
    p = Journal(lib_root / "Personal")
    a, b = DEMO / "shoes.png", DEMO / "watch.png"
    r4.photo(a, palette=((200, 90, 60), (90, 160, 220)), sun=(255, 240, 200))
    r4.photo(b, palette=((60, 150, 110), (210, 200, 120)), sun=(240, 255, 250))
    wa = convert_to_webp(a, p.assets_dir(today), quality=80, max_dimension=800)
    wb = convert_to_webp(b, p.assets_dir(today), quality=80, max_dimension=800)
    text = ENTRY.replace("INL1", markdown_link(wa, p.entry_dir(today), "shoes")) \
                .replace("INL2", markdown_link(wb, p.entry_dir(today), "watch"))
    p.write(today, text)

    cfg = Config.load()
    cfg.journal_root = lib_root
    cfg.data["last_journal"] = "Personal"
    cfg.data["appearance"].update(mode="dark", dark_theme="Catppuccin Mocha", light_theme="Default Light")
    cfg.data["plugins"]["enabled"] = []
    cfg.save()

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow(cfg)
    win.resize(1270, 770)
    win.show()
    r4.pump(app, 500)
    entry_file = p.entry_path(today)
    original = entry_file.read_bytes()
    win.open_date(today, force=True)
    ed = win.editor

    def cursor_to(line_text, scroll_top=True):
        block = ed.document().begin()
        while block.isValid() and line_text not in block.text():
            block = block.next()
        c = QTextCursor(block)
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        ed.setTextCursor(c)
        ed.setFocus()
        return block

    def scroll_to_block(block):
        sb = ed.verticalScrollBar()
        sb.setValue(0)
        r4.pump(app, 50)
        # QPlainTextEdit scrolls by blocks
        sb.setValue(block.blockNumber())

    # 1) styled table, cursor elsewhere
    cursor_to("Solid week")
    ed.verticalScrollBar().setValue(0)
    r4.pump(app, 700)
    grab_screen(app, [win], "r7_01_live_table_dark.png")
    # 2) cursor inside the table -> raw source (monospace)
    cursor_to("Tuesday   |")
    ed.verticalScrollBar().setValue(0)
    r4.pump(app, 600)
    grab_screen(app, [win], "r7_02_live_table_cursor_raw.png")
    # 3) nested lists
    blk = cursor_to("Eat more greens")
    c = ed.textCursor()
    c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    ed.setTextCursor(c)
    cursor_to("The run felt great")
    scroll_to_block(ed.document().findBlockByNumber(11))
    r4.pump(app, 600)
    grab_screen(app, [win], "r7_03_nested_lists.png")
    # 4) inline images
    cursor_to("The run felt great")
    scroll_to_block(ed.document().findBlockByNumber(28))
    r4.pump(app, 700)
    grab_screen(app, [win], "r7_04_inline_images.png")
    after = entry_file.read_bytes()
    log(f"byte-identical after styling/cursor moves: {after == original} dirty={win._dirty}")

    # 5) find bar
    cursor_to("# Training log")
    win.open_find(False)
    win.find_bar.find_edit.setText("run")
    r4.pump(app, 200)
    win.find_bar.find_next()
    win.find_bar.find_next()
    win.find_bar.find_next()
    r4.pump(app, 500)
    log(f"find 'run': {win.find_bar.counter_text()}")
    grab_screen(app, [win], "r7_05_find_bar.png")
    # 6) replace bar (whole word, not yet replaced)
    win.open_find(True)
    win.find_bar.find_edit.setText("run")
    win.find_bar.word_btn.setChecked(True)
    win.find_bar.refresh()
    win.find_bar.replace_edit.setText("ride")
    win.find_bar.find_next()
    r4.pump(app, 500)
    log(f"replace bar (whole word): {win.find_bar.counter_text()}")
    grab_screen(app, [win], "r7_06_replace_bar.png")
    n = win.find_bar.replace_all()
    r4.pump(app, 200)
    log(f"replace all -> {n}; dirty={win._dirty}")
    ed.undo()
    r4.pump(app, 200)
    log(f"one undo restores: {ed.toPlainText() == text.rstrip(chr(10)) or ed.toPlainText() == text}")
    win.find_bar.close_bar()
    win.save_current()

    # 7) preferences font picker
    dlg_holder = []
    win.open_settings()
    dlg = win._settings_dialog
    dlg.resize(760, 730)
    dlg.move(win.frameGeometry().x() + 250, 0)
    dlg.tabs.setCurrentIndex(1)
    page = dlg.tabs.currentWidget()
    dlg.live_spacing.setValue(1.2)
    dlg.live_heading_same.setChecked(False)
    dlg.live_heading_combo.setCurrentFont(dlg.live_heading_combo.font().__class__("DejaVu Serif"))
    r4.pump(app, 300)
    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
    r4.pump(app, 500)
    grab_screen(app, [dlg], "r7_07_preferences_fonts.png")
    dlg.reject()
    r4.pump(app, 200)
    dlg_holder.append(dlg)

    # 8) reading view with table + strikethrough
    win.set_view_mode("reading")
    r4.pump(app, 600)
    grab_screen(app, [win], "r7_08_reading_table_strike.png")
    win.set_view_mode("live")
    cfg.data["appearance"]["mode"] = "light"
    win.apply_appearance()
    cursor_to("Solid week")
    ed.verticalScrollBar().setValue(0)
    r4.pump(app, 600)
    grab_screen(app, [win], "r7_09_live_table_light.png")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

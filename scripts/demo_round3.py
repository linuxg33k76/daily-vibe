"""Round-3 demo: builds demo data, drives the app on the real display and
saves screenshots (screenshots/r3_*.png) plus an exported PDF (demo/export/)."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
SHOTS = ROOT / "screenshots"
sys.path.insert(0, str(ROOT))
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from daily_vibe import security  # noqa: E402
from daily_vibe.config import Config  # noqa: E402
from daily_vibe.images import convert_to_webp, markdown_link  # noqa: E402
from daily_vibe.storage import Journal  # noqa: E402
from daily_vibe.ui.main_window import ICON_PATH, MainWindow  # noqa: E402


def pump(app, ms=300):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(app, cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.005)
    return cond()


def photo(path: Path) -> None:
    w, h = 2400, 1500
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(40 + 60 * t), int(90 + 80 * t), int(160 + 60 * t)))
    d.ellipse([w * 0.15, h * 0.18, w * 0.15 + 260, h * 0.18 + 260], fill=(255, 240, 200))
    d.polygon([(0, h), (0, h * 0.7), (w * 0.3, h * 0.45), (w * 0.55, h * 0.72), (w * 0.8, h * 0.5), (w, h * 0.68), (w, h)], fill=(30, 60, 50))
    img.save(path)


TODAY_TEXT = """

## Notes

Round three for **The Daily Vibe**: *On This Day*, a lock screen, streaks and PDF export.

- Finished the `relocate` module (move/copy with verification)
- Read 30 pages of [Four Thousand Weeks](https://example.com/book)
- [ ] Try the Gruvbox theme on the Omarchy box

> The best way out is always through.

```python
streak = compute_streak(days, today)
print(streak.message())
```
"""


def build_demo_data(j: Journal, today: dt.date) -> dt.date:
    past_same_day = {
        1: "# A year ago\n\nFirst week at the new job. Nervous but excited; lunch with the team at the taco place.\n",
        2: "# Two years back\n\nCamping at Lost Lake. Saw the Milky Way for the first time in years.\n",
        3: "# Three years\n\nStarted journaling again after a long break. Keeping it simple: one page a day.\n",
    }
    for years, text in past_same_day.items():
        try:
            j.write(today.replace(year=today.year - years), text)
        except ValueError:
            pass
    daily = [
        "Long run along the creek trail. Legs tired but good.",
        "Rainy day. Soup, a book and an early night.",
        "Tinkered with Hyprland config on the Omarchy box; finally fixed the waybar clock.",
        "Coffee with Sam. Talked about the cabin trip in October.",
        "Shipped the importer refactor. Need to write tests for the edge cases.",
    ]
    for i, line in enumerate(daily, 1):
        j.write(today - dt.timedelta(days=i), f"# {(today - dt.timedelta(days=i)):%A}\n\n{line}\n")
    j.write(today - dt.timedelta(days=7), "# Week ago\n\nFarmers market haul: peaches, sourdough, too many tomatoes.\n")
    month_ago = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=min(today.day, 28))
    j.write(month_ago, "# A month ago\n\nBooked the cabin for October. Can't wait.\n")
    # an entry with a photo (for the PDF)
    photo_day = today - dt.timedelta(days=2)
    src = DEMO / "lake.png"
    photo(src)
    img = convert_to_webp(src, j.assets_dir(photo_day), 80, 1920, stem="lake")
    j.write(photo_day, j.read(photo_day) + f"\n## Photo\n\nEvening at the lake:\n\n{markdown_link(img, j.entry_dir(photo_day), 'lake')}\n")
    return photo_day


def main() -> int:
    shutil.rmtree(DEMO, ignore_errors=True)
    SHOTS.mkdir(exist_ok=True)
    (DEMO / "config" / "plugins").mkdir(parents=True)
    shutil.copy(ROOT / "examples" / "plugin_template.py", DEMO / "config" / "plugins" / "quote_of_the_day.py")
    cfg = Config.load()
    cfg.journal_root = DEMO / "Journal"
    cfg.data["appearance"].update(mode="dark", dark_theme="Catppuccin Mocha", light_theme="Default Light")
    cfg.data["plugins"]["enabled"] = ["weather", "moon_phase", "quote_of_the_day"]
    cfg.save()
    j = Journal(cfg.journal_root)
    today = dt.date.today()
    photo_day = build_demo_data(j, today)

    app = QApplication(sys.argv)
    app.setApplicationName("The Daily Vibe")
    app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow(cfg)
    win.resize(1500, 920)
    win.show()
    pump(app)

    # 1) Today (template + background plugins), then write some Markdown
    win.today_btn.click()
    wait(app, lambda: not win.runner.is_busy())
    pump(app, 300)
    cur = win.editor.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    win.editor.setTextCursor(cur)
    win.editor.insertPlainText(TODAY_TEXT.strip("\n").replace("## Notes\n\n", "", 1) + "\n")
    pump(app, 1600)  # autosave -> streak updates
    # scroll so the plugin markers, headings, list, quote and code fence are all in view
    doc_text = win.editor.toPlainText()
    block_no = doc_text[: doc_text.index("<!-- plugin:moon_phase")].count("\n")
    win.editor.verticalScrollBar().setValue(block_no)
    win.preview.verticalScrollBar().setValue(win.preview.verticalScrollBar().maximum() // 2)
    pump(app, 300)
    win.grab().save(str(SHOTS / "r3_01_main_dark_on_this_day_highlighting.png"))
    print("title:", win.windowTitle())
    print("streak:", win.streak_label.text())
    print("on this day:", [win.on_this_day.list.item(i).text().split("\n")[0] for i in range(win.on_this_day.list.count())])

    # 2) Plugins tab (schema-rendered settings + list)
    win.open_settings()
    pump(app, 300)
    dlg = win._settings_dialog
    dlg.resize(780, 660)
    dlg.tabs.setCurrentIndex(3)
    page = dlg.plugins_page
    for i in range(page.tree.topLevelItemCount()):
        if page.tree.topLevelItem(i).text(0) == "Weather":
            page.tree.setCurrentItem(page.tree.topLevelItem(i))
    page.field("weather", "location").setText("Denver, CO")
    for f, w in page.fields["weather"].values():
        if f.check:
            page._run_check("weather", f)
    wait(app, lambda: "Checking" not in page.check_labels[("weather", "location")].text(), 15)
    pump(app, 200)
    print("weather look up:", page.check_labels[("weather", "location")].text())
    dlg.grab().save(str(SHOTS / "r3_04_settings_plugins_weather.png"))
    for i in range(page.tree.topLevelItemCount()):
        if page.tree.topLevelItem(i).text(0) == "Quote of the Day":
            page.tree.setCurrentItem(page.tree.topLevelItem(i))
    pump(app, 200)
    dlg.grab().save(str(SHOTS / "r3_04b_settings_plugins_user_plugin.png"))

    # 3) Security tab: set a password (via the UI fields)
    dlg.tabs.setCurrentIndex(4)
    dlg.sec_new.setText("vibes2026")
    dlg.sec_confirm.setText("vibes2026")
    dlg._set_password()
    dlg.autolock_spin.setValue(10)
    pump(app, 300)
    dlg.grab().save(str(SHOTS / "r3_03_settings_security.png"))
    dlg.reject()  # password already saved; auto-lock value discarded by Cancel
    pump(app, 200)

    # 4) Lock screen
    win.lock_now()
    pump(app, 300)
    win.lock_screen.try_msg = win.try_unlock("wrong guess")
    pump(app, 200)
    win.grab().save(str(SHOTS / "r3_02_lock_screen.png"))
    print("locked editor empty:", win.editor.toPlainText() == "", "timeline empty:", win.entry_list.count() == 0)
    assert win.try_unlock("vibes2026")
    pump(app, 300)

    # 5) PDF export (week incl. photo entry), rendered to PNG
    out_dir = DEMO / "export"
    out_dir.mkdir()
    pdf = out_dir / "daily-vibe-last-week.pdf"
    dates = [d for d in j.list_dates() if today - dt.timedelta(days=7) <= d <= today]
    n = win.export_to(dates, pdf, one_per_page=True, page_size="Letter")
    print("exported", n, "entries ->", pdf)
    idx = sorted(dates).index(photo_day) + 1
    subprocess.run(["pdftoppm", "-png", "-r", "80", "-f", str(idx), "-l", str(idx), str(pdf), str(out_dir / "page")], check=True)
    page_png = sorted(out_dir.glob("page*.png"))[0]
    shutil.copy(page_png, SHOTS / "r3_07_pdf_page_with_image.png")
    subprocess.run(["pdftoppm", "-png", "-r", "80", "-f", str(len(dates)), "-l", str(len(dates)), str(pdf), str(out_dir / "today")], check=True)
    shutil.copy(sorted(out_dir.glob("today*.png"))[0], SHOTS / "r3_07b_pdf_page_today.png")

    # 6) Move journal: dialog + summary (with a deliberate conflict)
    target = DEMO / "Journal-moved"
    Journal(target).write(today - dt.timedelta(days=5), "# Different file already here\n")
    (DEMO / "Journal" / "notes.txt").write_text("not part of the journal")

    def answer():
        box = getattr(win, "_relocate_box", None)
        if box is None or not box.isVisible():
            QTimer.singleShot(100, answer)
            return
        box.grab().save(str(SHOTS / "r3_05_move_journal_dialog.png"))
        for b in box.buttons():
            if b.text() == "Move entries":
                b.click()

    QTimer.singleShot(300, answer)
    print("change_root_to:", win.change_root_to(target))
    wait(app, lambda: win._transfer_job is None, 30)
    pump(app, 400)
    box = win._last_summary_box
    box.grab().save(str(SHOTS / "r3_06_move_journal_summary.png"))
    print("SUMMARY:\n" + box.text())
    box.close()

    # 7) light theme main window after the move
    win._set_theme_mode("light")
    win.open_date(today)
    pump(app, 600)
    win.grab().save(str(SHOTS / "r3_08_main_light_after_move.png"))
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

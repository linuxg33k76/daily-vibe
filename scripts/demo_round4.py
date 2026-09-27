"""Round-4 demo: a library with two journals (Personal, Work) with tags and
images; drives the app on the real display and saves screenshots
(screenshots/r4_*.png), a tree listing and a migration run on old-layout data."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "r4"
SHOTS = ROOT / "screenshots"
sys.path.insert(0, str(ROOT))
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QIcon, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daily_vibe.config import Config  # noqa: E402
from daily_vibe.images import convert_to_webp, markdown_link  # noqa: E402
from daily_vibe.library import Library  # noqa: E402
from daily_vibe.ui.main_window import ICON_PATH, MainWindow  # noqa: E402

LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def pump(app, ms=300):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(app, cond, timeout=25):
    end = time.time() + timeout
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.005)
    return cond()


def screen_shot(app, win, name, extra=None):
    """Grab the real screen region of the window (includes popups)."""
    geo = win.frameGeometry()
    if extra is not None:
        geo = geo.united(extra.frameGeometry())
    pix = app.primaryScreen().grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
    pix.save(str(SHOTS / name))
    log(f"saved screenshots/{name}")


def photo(path: Path, palette=((40, 90, 160), (100, 170, 220)), sun=(255, 240, 200)) -> None:
    w, h = 1800, 1100
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    (r1, g1, b1), (r2, g2, b2) = palette
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t)))
    d.ellipse([w * 0.15, h * 0.18, w * 0.15 + 200, h * 0.18 + 200], fill=sun)
    d.polygon([(0, h), (0, h * 0.7), (w * 0.3, h * 0.45), (w * 0.55, h * 0.72), (w * 0.8, h * 0.5), (w, h * 0.68), (w, h)],
              fill=(30, 60, 50))
    img.save(path)


def chart(path: Path) -> None:
    w, h = 1400, 800
    img = Image.new("RGB", (w, h), (250, 250, 252))
    d = ImageDraw.Draw(img)
    vals = [3, 5, 4, 7, 6, 9, 8]
    for i, v in enumerate(vals):
        x = 120 + i * 170
        d.rectangle([x, h - 80 - v * 70, x + 110, h - 80], fill=(137, 180, 250))
    d.line([(80, h - 80), (w - 60, h - 80)], fill=(60, 60, 70), width=4)
    img.save(path)


def add_image(j, day, src: Path, alt: str, caption: str):
    out = convert_to_webp(src, j.assets_dir(day), quality=80, max_dimension=1600)
    j.write(day, j.read(day) + f"\n{caption}\n\n{markdown_link(out, j.entry_dir(day), alt)}\n")


def build_library(root: Path, today: dt.date) -> None:
    lib = Library(root)
    p = lib.create("Personal", color="#a6e3a1", icon="🌿")
    w = lib.create("Work", color="#89b4fa", icon="💼")
    D = lambda n: today - dt.timedelta(days=n)  # noqa: E731
    p.write(D(1), "---\ntags: [family, weekend]\n---\n# Saturday with the kids\n\nPancakes, then the farmers market. "
                  "Picked up peaches and a ridiculous amount of #coffee beans.\n\nEvening walk with Sam #health/walking\n")
    p.write(D(2), "# Long run\n\n10 km along the creek path, felt strong. #health/running #goals\n\n"
                  "Finished *Four Thousand Weeks* #reading\n")
    p.write(D(3), "# Quiet day\n\nCalled mom. #family\n\nTried the new café on 5th — great flat white #coffee\n")
    p.write(D(5), "# Hike\n\nLost Lake trail with the family #family #outdoors\n")
    p.write(D(9), "# Reading night\n\nStarted *The Overstory* #reading #books/fiction\n")
    p.write(D(12), "# Garden\n\nTomatoes finally ripe #garden #outdoors\n")
    photo(DEMO / "lake.png")
    add_image(p, D(5), DEMO / "lake.png", "lost lake", "## Photo\n\nThe lake at noon:")
    photo(DEMO / "sunset.png", palette=((120, 60, 90), (250, 170, 90)), sun=(255, 220, 120))
    add_image(p, D(1), DEMO / "sunset.png", "sunset", "## Photo\n\nSunset from the porch:")

    w.write(D(1), "# Weekend on-call\n\nNo pages, quiet. #work #oncall\n")
    w.write(D(2), "# Sprint review\n\nDemoed the tag panel to the team #work #meeting #project/daily-vibe\n\n"
                  "Retro action: fewer meetings, more #deep-work. Grabbed #coffee with Priya after.\n")
    w.write(D(3), "---\ntags: [work, planning]\n---\n# Q4 planning\n\nDrafted the roadmap. #project/daily-vibe #meeting\n")
    w.write(D(4), "# Focus day\n\nThree hours of #deep-work on the migration code. #work\n")
    w.write(D(8), "# 1:1 with Alex\n\nCareer chat, growth plan. #work #meeting #people\n")
    chart(DEMO / "burndown.png")
    add_image(w, D(2), DEMO / "burndown.png", "burndown", "## Burndown\n\nSprint burndown:")


def build_old_layout(root: Path, today: dt.date) -> None:
    """Round-3 (single journal) layout: YYYY/MM/*.md with YYYY/MM/assets/*.webp."""
    for n, body in ((2, "# Old entry\n\nWritten with v0.3. #legacy\n"),
                    (40, "# Summer\n\nBeach day #family #legacy\n"),
                    (400, "# Last year\n\nFirst entry ever. #legacy\n")):
        d = today - dt.timedelta(days=n)
        folder = root / f"{d:%Y}" / f"{d:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        text = body
        if n == 40:
            (folder / "assets").mkdir(exist_ok=True)
            out = convert_to_webp(DEMO / "lake.png", folder / "assets", quality=75, max_dimension=1200)
            text += f"\n![beach](assets/{out.name})\n"
        (folder / f"{d:%Y-%m-%d}.md").write_text(text)


def main() -> int:
    shutil.rmtree(DEMO, ignore_errors=True)
    for old in SHOTS.glob("r4_*"):
        old.unlink()
    SHOTS.mkdir(exist_ok=True)
    (DEMO / "config").mkdir(parents=True)
    today = dt.date.today()
    lib_root = DEMO / "Library"
    build_library(lib_root, today)

    cfg = Config.load()
    cfg.journal_root = lib_root
    cfg.data["last_journal"] = "Personal"
    cfg.data["appearance"].update(mode="dark", dark_theme="Catppuccin Mocha", light_theme="Default Light")
    cfg.data["plugins"]["enabled"] = ["weather", "moon_phase"]
    cfg.data["plugins"]["weather"] = {"location": "Denver, CO", "units": "imperial"}
    cfg.save()

    app = QApplication(sys.argv)
    app.setApplicationName("The Daily Vibe")
    app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow(cfg)
    win.resize(1500, 940)
    win.move(40, 30)
    win.show()
    pump(app, 500)

    # Live weather: create today's entry in Personal (plugins run in background)
    win.today_btn.click()
    wait(app, lambda: not win.runner.is_busy(), 40)
    pump(app, 300)
    text = win.editor.toPlainText()
    wline = next((l for l in text.splitlines() if "Location:" in l), "")
    provider = "MET Norway" if "MET Norway" in wline else ("Open-Meteo" if "Open-Meteo" in wline else "none")
    log(f"LIVE WEATHER provider: {provider}")
    log("LIVE WEATHER block: " + " | ".join(l for l in text.split("<!-- plugin:weather -->")[1].split("<!-- /plugin:weather -->")[0].splitlines() if l.strip()))
    cur = win.editor.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    win.editor.setTextCursor(cur)
    win.editor.insertPlainText("Morning pages, then a long walk. #health/walking #family\n")
    pump(app, 1500)  # autosave, tags refresh

    # 1) journal picker open
    win.journal_combo.showPopup()
    pump(app, 600)
    screen_shot(app, win, "r4_01_journal_picker_open.png")
    win.journal_combo.hidePopup()
    pump(app, 200)

    # 2) tag panel with an active filter (family AND outdoors) + chips in preview
    win.add_tag_filter("family")
    pump(app, 200)
    fam_count = win.entry_list.count()
    win.add_tag_filter("outdoors")
    pump(app, 200)
    win.open_date(today - dt.timedelta(days=5), force=True)
    pump(app, 600)
    log(f"tag filter family -> {fam_count} entries; family+outdoors -> {win.entry_list.count()} entries")
    win.grab().save(str(SHOTS / "r4_02_tag_filter_active.png"))
    log("saved screenshots/r4_02_tag_filter_active.png")
    win.clear_tag_filter()

    # 3) tag autocomplete popup while typing "#he" in today's entry
    win.open_date(today, force=True)
    pump(app, 300)
    win.activateWindow()
    win.editor.setFocus()
    cur = win.editor.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    win.editor.setTextCursor(cur)
    win.editor.ensureCursorVisible()
    QTest.keyClicks(win.editor, "Lunch with Sam, then #he")
    pump(app, 600)
    popup = win.editor.completer.popup()
    log(f"autocomplete visible={popup.isVisible()} items={[win.editor.completer.completionModel().index(i, 0).data() for i in range(win.editor.completer.completionCount())]}")
    screen_shot(app, win, "r4_03_tag_autocomplete.png")
    QTest.keyClick(popup, Qt.Key.Key_Return)
    pump(app, 1500)

    # 4) all-journals search results (On This Day hidden to give the results room)
    win.toggle_on_this_day()
    win.scope_combo.setCurrentIndex(win.scope_combo.findData("all"))
    win.search_box.setText("coffee")
    win.refresh_list()
    pump(app, 400)
    work_rows = [i for i in range(win.entry_list.count()) if win.entry_list.item(i).text().startswith("[Work]")]
    if work_rows:
        win._list_item_opened(win.entry_list.item(work_rows[0]))   # opens the Work hit -> switches journal
        win.refresh_list()
        win.entry_list.setCurrentRow(work_rows[0])
    pump(app, 600)
    log("all-journals search 'coffee': " + " || ".join(win.entry_list.item(i).text().replace("\n", " ")
                                                         for i in range(win.entry_list.count())))
    win.grab().save(str(SHOTS / "r4_04_all_journals_search.png"))
    log("saved screenshots/r4_04_all_journals_search.png")
    win.search_box.clear()
    win.scope_combo.setCurrentIndex(0)
    win.toggle_on_this_day()
    pump(app, 300)

    # 4b) Work journal with tag:project filter and image (light theme)
    win.switch_journal("Work")
    win._set_theme_mode("light")
    win.add_tag_filter("project")
    win.open_date(today - dt.timedelta(days=2), force=True)
    pump(app, 700)
    win.grab().save(str(SHOTS / "r4_05_work_journal_light_nested_tag.png"))
    log("saved screenshots/r4_05_work_journal_light_nested_tag.png")
    win.clear_tag_filter()
    win._set_theme_mode("dark")

    # PDF of the whole Work journal and a tag-filtered range export
    (DEMO / "export").mkdir()
    n = win.export_to(win.journal.list_dates(), DEMO / "export" / "Work.pdf", title="Work")
    dates = win.dates_for_export(today - dt.timedelta(days=30), today, ["meeting"])
    n2 = win.export_to(dates, DEMO / "export" / "Work-meetings.pdf")
    z = win.export_journal_zip(DEMO / "export" / "Work.zip")
    log(f"exported Work.pdf ({n} entries), Work-meetings.pdf ({n2} entries tagged #meeting), {z.name}")
    win.close()
    pump(app, 300)

    # 5) migration of an old-layout copy
    old_root = DEMO / "OldLayout"
    build_old_layout(old_root, today)
    cfg2 = Config.load(DEMO / "config-migration" / "config.toml")
    cfg2.journal_root = old_root
    cfg2.data["plugins"]["enabled"] = []
    cfg2.save()
    win2 = MainWindow(cfg2)
    win2.resize(1300, 820)
    win2.move(60, 40)
    win2.show()
    pump(app, 500)
    win2.maybe_migrate()
    pump(app, 600)
    box = win2._migration_box
    screen_shot(app, win2, "r4_06_migration_dialog.png")
    go = next(b for b in box.buttons() if b.text() == "Migrate now")
    go.click()
    pump(app, 800)
    screen_shot(app, win2, "r4_07_migration_summary.png")
    log("MIGRATION SUMMARY:\n" + win2.last_migration.summary())
    win2._last_summary_box.accept()
    d40 = today - dt.timedelta(days=40)
    win2.open_date(d40, force=True)
    pump(app, 600)
    win2.grab().save(str(SHOTS / "r4_08_migrated_entry_image_renders.png"))
    log("saved screenshots/r4_08_migrated_entry_image_renders.png")
    win2.close()
    pump(app, 200)

    # tree listing
    listing = subprocess.run(["tree", "-a", "--noreport", "-I", "config*|*.png|export", str(DEMO)],
                             capture_output=True, text=True).stdout
    listing = listing.replace(str(DEMO), "demo/r4")
    (SHOTS / "r4_library_tree.txt").write_text(listing)
    log("saved screenshots/r4_library_tree.txt")
    (SHOTS / "r4_demo_log.txt").write_text("\n".join(LOG) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Round-5 demo: Journal Settings dialog, tag rename with preview, undo.
Saves screenshots/r5_*.png (real display) using the round-4 demo library builder."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "r5"
SHOTS = ROOT / "screenshots"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

import demo_round4 as r4  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daily_vibe.config import Config  # noqa: E402
from daily_vibe.storage import Journal  # noqa: E402
from daily_vibe.ui.main_window import ICON_PATH, MainWindow  # noqa: E402

LOG = []


def log(m):
    print(m, flush=True)
    LOG.append(m)


def grab_screen(app, widgets, name):
    geo = widgets[0].frameGeometry()
    for w in widgets[1:]:
        geo = geo.united(w.frameGeometry())
    app.primaryScreen().grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height()).save(str(SHOTS / name))
    log(f"saved screenshots/{name}")


def main() -> int:
    shutil.rmtree(DEMO, ignore_errors=True)
    for old in SHOTS.glob("r5_*"):
        old.unlink()
    (DEMO / "config").mkdir(parents=True)
    r4.DEMO = DEMO  # images are generated under demo/r5
    today = dt.date.today()
    lib_root = DEMO / "Library"
    r4.build_library(lib_root, today)
    p = Journal(lib_root / "Personal")
    # entries that already use #fitness, so the rename merges (incl. a front matter list)
    p.write(today - dt.timedelta(days=4), "---\ntags: [health, fitness, weekend]\n---\n# Gym\n\nLeg day. #fitness\n")
    p.write(today - dt.timedelta(days=6), "# Stretching\n\n20 min yoga #health/yoga, water `#health` in code.\n")

    cfg = Config.load()
    cfg.journal_root = lib_root
    cfg.data["last_journal"] = "Work"
    cfg.data["appearance"].update(mode="dark", dark_theme="Catppuccin Mocha")
    cfg.data["plugins"]["enabled"] = ["weather", "moon_phase"]
    cfg.data["plugins"]["weather"] = {"location": "Denver, CO", "units": "imperial"}
    cfg.save()

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow(cfg)
    win.resize(1500, 940)
    win.show()
    r4.pump(app, 500)

    # 1-3) Journal Settings for Work
    dlg = win.open_journal_settings()
    dlg.resize(760, 640)
    r4.pump(app, 300)
    dlg.set_color("#f9e2af")
    dlg.icon_edit.setText("🛠️")
    r4.pump(app, 300)
    grab_screen(app, [dlg], "r5_01_journal_settings_general.png")
    dlg.tabs.setCurrentIndex(1)
    dlg.custom_template.setChecked(True)
    dlg.template_edit.setPlainText("# {weekday}, {long_date} — Work log\n\n{plugins}\n\n## Top 3 today\n\n1. \n2. \n3. \n\n## Meetings\n\n## Notes\n")
    r4.pump(app, 300)
    grab_screen(app, [dlg], "r5_02_journal_settings_template.png")
    dlg.tabs.setCurrentIndex(2)
    dlg.plugins_custom.setChecked(True)
    dlg.plugins_page.set_enabled("moon_phase", False)
    dlg.plugins_page.field("weather", "location").setText("Seattle, WA")
    item = dlg.plugins_page.items["weather"]
    dlg.plugins_page.tree.setCurrentItem(item)
    r4.pump(app, 300)
    grab_screen(app, [dlg], "r5_03_journal_settings_plugins.png")
    dlg._ok()
    r4.pump(app, 300)
    log("Work .dailyvibe.toml after OK:\n" + (lib_root / "Work" / ".dailyvibe.toml").read_text())

    # 4) picker context menu (gear alternative)
    win._journal_context_menu(QPoint(20, 10))
    r4.pump(app, 400)
    grab_screen(app, [win], "r5_04_journal_picker_context_menu.png")
    win._journal_menu_popup.close()

    # 5) tag panel context menu -> Rename Tag…, then dialog with preview (all journals, merge)
    win.switch_journal("Personal")
    win.scope_combo.setCurrentIndex(win.scope_combo.findData("all"))
    r4.pump(app, 300)
    row = next(i for i in range(win.tag_list.count())
               if win.tag_list.item(i).data(Qt.ItemDataRole.UserRole).startswith("health"))
    rect = win.tag_list.visualItemRect(win.tag_list.item(row))
    win.tag_list.scrollToItem(win.tag_list.item(row))
    rect = win.tag_list.visualItemRect(win.tag_list.item(row))
    win._tag_context_menu(rect.center())
    r4.pump(app, 400)
    grab_screen(app, [win], "r5_05_tag_context_menu.png")
    win._tag_menu_popup.close()
    rd = win.rename_tag_dialog("health")
    rd.resize(900, 560)
    rd.new_edit.setText("fitness")
    plan = rd.preview()
    r4.pump(app, 400)
    grab_screen(app, [rd], "r5_06_tag_rename_preview.png")
    log(f"preview: {rd.status.text()} | merge: {rd.merge_label.text()}")
    for c in plan.changes:
        log(f"  {c.journal.name} {c.date}: {c.count} change(s) {c.samples[:2]}")
    rd.apply_btn.click()
    r4.pump(app, 500)
    log("RENAME SUMMARY:\n" + win._last_summary_box.text())
    grab_screen(app, [win], "r5_07_rename_summary.png")
    win._last_summary_box.accept()

    # 6) tag panel after rename, filtered by the merged tag
    win.add_tag_filter("fitness")
    win.open_date(today - dt.timedelta(days=4), force=True)
    r4.pump(app, 500)
    win.grab().save(str(SHOTS / "r5_08_tag_panel_after_rename.png"))
    log("saved screenshots/r5_08_tag_panel_after_rename.png")
    log("tags now: " + ", ".join(win.tag_list.item(i).text() for i in range(win.tag_list.count())))

    # 7) Undo action in the Journal menu, then the result
    win._update_undo_rename_action()
    mb = win.menuBar()
    act = win.journal_menu.menuAction()
    win.journal_menu.popup(mb.mapToGlobal(mb.actionGeometry(act).bottomLeft()))
    r4.pump(app, 300)
    win.journal_menu.setActiveAction(win.undo_rename_action)
    r4.pump(app, 400)
    grab_screen(app, [win], "r5_09_undo_action_menu.png")
    win.journal_menu.close()
    res = win.undo_last_tag_rename(confirm=False)
    r4.pump(app, 500)
    grab_screen(app, [win], "r5_10_undo_result.png")
    log("UNDO SUMMARY:\n" + win._last_summary_box.text())
    win._last_summary_box.accept()
    win.close()
    (SHOTS / "r5_demo_log.txt").write_text("\n".join(LOG) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

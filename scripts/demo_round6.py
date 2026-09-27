"""Round-6 demo: Live Preview (dark + light), emoji picker, tag rename history,
tag entries view. Saves screenshots/r6_*.png on the real display."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "r6"
SHOTS = ROOT / "screenshots"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DAILY_VIBE_CONFIG_DIR"] = str(DEMO / "config")

import demo_round4 as r4  # noqa: E402
from PySide6.QtGui import QIcon, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from daily_vibe.config import Config  # noqa: E402
from daily_vibe.images import convert_to_webp, markdown_link  # noqa: E402
from daily_vibe.storage import Journal  # noqa: E402
from daily_vibe.ui.main_window import ICON_PATH, MainWindow  # noqa: E402

LOG = []
ONLY = set(sys.argv[1:])


def log(m):
    print(m, flush=True)
    LOG.append(m)


def grab_screen(app, widgets, name):
    geo = widgets[0].frameGeometry()
    for w in widgets[1:]:
        geo = geo.united(w.frameGeometry())
    app.primaryScreen().grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height()).save(str(SHOTS / name))
    log(f"saved screenshots/{name}")


LIVE_ENTRY = """---
tags: [travel, family]
mood: great
---
# Saturday at Chautauqua

A **bright** and _breezy_ morning — hiked the Royal Arch trail with Sam. ~~Skipped~~ Did the last climb! #hiking #health/walking

## Highlights

- Wildflowers everywhere, `42` photos taken
- Lunch at the [Dining Hall](https://www.chautauqua.com) with a view
- [x] Pack the water bottles
- [ ] Print the best photo for mom

> The mountains are calling and I must go. — John Muir

IMAGE

---

### Notes for next time

1. Start before 8 am (parking!)
2. Bring the good camera #photography

```python
miles = 3.4
print(f"hiked {miles} mi")
```

<!-- plugin:moon_phase -->

## Moon

🌔 Waxing gibbous · 83% illuminated

<!-- /plugin:moon_phase -->
"""


def main() -> int:
    shutil.rmtree(DEMO, ignore_errors=True)
    if not ONLY:
        for old in SHOTS.glob("r6_*"):
            old.unlink()
    (DEMO / "config").mkdir(parents=True)
    r4.DEMO = DEMO
    today = dt.date.today()
    lib_root = DEMO / "Library"
    r4.build_library(lib_root, today)
    p = Journal(lib_root / "Personal")
    src = DEMO / "flatirons.png"
    r4.photo(src, palette=((230, 140, 80), (120, 170, 230)), sun=(255, 250, 220))
    webp = convert_to_webp(src, p.assets_dir(today), quality=80, max_dimension=1600)
    p.write(today, LIVE_ENTRY.replace("IMAGE", markdown_link(webp, p.entry_dir(today), "Flatirons from the trail")))

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
    log(f"view mode: {win.view_mode}")

    def cursor_to(line_text):
        doc = win.editor.document()
        block = doc.begin()
        while block.isValid() and line_text not in block.text():
            block = block.next()
        c = QTextCursor(block)
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        win.editor.setTextCursor(c)
        win.editor.setFocus()
        win.editor.centerCursor()

    # 1) Live Preview, dark theme, cursor on the first paragraph (raw markers there)
    cursor_to("breezy")
    win.editor.verticalScrollBar().setValue(0)
    r4.pump(app, 600)
    grab_screen(app, [win], "r6_01_live_preview_dark.png")
    win.editor.verticalScrollBar().setValue(win.editor.verticalScrollBar().maximum())
    r4.pump(app, 400)
    grab_screen(app, [win], "r6_02_live_preview_dark_bottom.png")

    # 2) Light theme, cursor on a list line
    cfg.data["appearance"]["mode"] = "light"
    win.apply_appearance()
    cursor_to("Dining Hall")
    win.editor.verticalScrollBar().setValue(0)
    r4.pump(app, 600)
    grab_screen(app, [win], "r6_03_live_preview_light.png")
    after = entry_file.read_bytes()
    log(f"file byte-identical after Live Preview styling/cursor moves/theme switch: {after == original} dirty={win._dirty}")

    # 3) other view modes (quick look)
    win.set_view_mode("split")
    r4.pump(app, 400)
    grab_screen(app, [win], "r6_04_view_source_preview.png")
    win.set_view_mode("reading")
    r4.pump(app, 400)
    grab_screen(app, [win], "r6_05_view_reading.png")
    win.set_view_mode("live")
    cfg.data["appearance"]["mode"] = "dark"
    win.apply_appearance()
    r4.pump(app, 300)

    # 4) emoji picker with a search (journal icon)
    dlg = win.open_journal_settings()
    r4.pump(app, 300)
    ep = dlg.emoji_dialog()
    ep.open()
    ep.move(dlg.frameGeometry().right() - 200, dlg.frameGeometry().top() + 60)
    r4.pump(app, 300)
    ep.picker.search.setText("mountain")
    r4.pump(app, 400)
    log(f"emoji search 'mountain': {''.join(ep.picker.result_chars()[:20])}")
    grab_screen(app, [dlg, ep], "r6_06_emoji_picker_search.png")
    ep.picker.pick("🏔️")
    r4.pump(app, 200)
    log(f"icon now {dlg.icon_edit.text()!r}; recent={cfg.data['recent_emoji']}")
    ep.picker.search.clear()
    dlg.reject()
    ins = win.insert_emoji_dialog()
    r4.pump(app, 300)
    ins.picker.search.setText("coffee")
    r4.pump(app, 300)
    grab_screen(app, [win, ins], "r6_07_insert_emoji_dialog.png")
    ins.reject()

    # 5) several tag renames -> history dialog
    pj = Journal(lib_root / "Personal")
    renames = [("coffee", "drinks/coffee", "journal"), ("health", "wellness", "all"),
               ("reading", "books", "journal"), ("drinks/coffee", "coffee", "journal")]
    import time
    for old, new, scope in renames:
        plan = win.plan_tag_rename(old, new, True, scope)
        win.apply_tag_rename(plan)
        win._last_summary_box.accept()
        time.sleep(0.01)
    log(f"applied {len(renames)} renames")
    # undo the 'books' one (not blocked) so the history shows mixed statuses
    recs = win.tag_rename_records()
    books = next(r for r in recs if r.new == "books")
    win.undo_tag_rename(books.stamp)
    hd = win.tag_history_dialog()
    hd.resize(880, 400)
    r4.pump(app, 300)
    first = next(r for r in hd.records if r.new == "drinks/coffee")
    hd.select_stamp(first.stamp)  # blocked by the newer rename back to #coffee
    r4.pump(app, 300)
    grab_screen(app, [hd], "r6_08_tag_rename_history.png")
    for r in hd.records:
        log(f"  history: {r.created:%H:%M:%S} {r.label} scope={r.scope} files={len(r.files)} status={r.status}")
    log("  selected detail: " + hd.detail.text())
    hd.reject()

    # 6) tag entries view
    win.show_tag_entries("coffee", "all")
    r4.pump(app, 500)
    grab_screen(app, [win], "r6_09_tag_entries_view.png")
    log(f"tag view #coffee (all journals): {len(win.tag_view.cards)} cards: " +
        ", ".join(f"{c.journal.name} {c.date}" for c in win.tag_view.cards))
    out = DEMO / "coffee.pdf"
    n = win.export_tag_entries(out)
    log(f"exported {n} entries to {out.name} ({out.stat().st_size} bytes)")
    # chip click menu in the live editor
    win.close_tag_entries()
    win.open_date(today, force=True)
    r4.pump(app, 300)
    doc = win.editor.document()
    block = doc.begin()
    while block.isValid() and "#photography" not in block.text():
        block = block.next()
    win.editor.verticalScrollBar().setValue(win.editor.verticalScrollBar().maximum())
    r4.pump(app, 300)
    c = QTextCursor(block)
    c.setPosition(block.position() + block.text().index("#photography") + 3)
    pos = win.editor.cursorRect(c).center()
    menu = win.show_tag_menu("photography", win.editor.viewport().mapToGlobal(pos))
    r4.pump(app, 400)
    grab_screen(app, [win], "r6_10_tag_chip_menu.png")
    menu.close()
    win.close()
    (SHOTS / "r6_demo_log.txt").write_text("\n".join(LOG) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

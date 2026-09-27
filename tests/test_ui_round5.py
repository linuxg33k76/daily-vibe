"""Round 5 UI: Journal Settings dialog, Rename Tag dialog + undo."""
import datetime as dt

from PySide6.QtCore import Qt

from conftest import wait_until
from daily_vibe.config import Config
from daily_vibe.journal_meta import load_meta
from daily_vibe.library import Library
from daily_vibe.storage import Journal

D = dt.date
FAST = ("ID = 'fast'\nNAME = 'Fast'\nTITLE = 'Fast'\n"
        "SETTINGS = [{'key': 'word', 'label': 'Word', 'type': 'string', 'default': 'hello'}]\n"
        "def render(date, context):\n    return 'fast ' + context['settings']['word']\n")


def _setup(tmp_path, enabled=("fast",)):
    from daily_vibe.ui.main_window import MainWindow
    root = tmp_path / "Lib"
    lib = Library(root)
    p, w = lib.create("Personal"), lib.create("Work")
    p.write(D(2026, 9, 20), "---\ntags: [health, fitness]\n---\n# p\nran #health/running today\n")
    p.write(D(2026, 9, 21), "# p2\nyoga #Health and `#health` code\n")
    w.write(D(2026, 9, 21), "# w\n#health benefits meeting #work\n")
    (root / "Personal" / "assets" / "2026" / "09").mkdir(parents=True)
    (root / "Personal" / "assets" / "2026" / "09" / "a.webp").write_bytes(b"x" * 2048)
    cfg = Config.load()
    cfg.journal_root = root
    cfg.data["plugins"]["enabled"] = list(enabled)
    cfg.save()
    pdir = tmp_path / "config" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "fast.py").write_text(FAST)
    return root, MainWindow(cfg)


def test_journal_settings_general_apply_live(qapp, tmp_path):
    root, win = _setup(tmp_path)
    (root / "Personal" / ".dailyvibe.toml").write_text('# keep me\nname = "Personal"\nextra = "x"\n')
    dlg = win.open_journal_settings()
    assert dlg.entries_label.text() == "2" and "1 file(s), 2.0 KB" == dlg.images_label.text()
    assert "Sep 20, 2026 – Sep 21, 2026" == dlg.range_label.text()
    dlg.set_color("#a6e3a1")
    dlg.emoji_buttons[2].click()                      # 🌿
    dlg.name_edit.setText("My Life")
    dlg.rename_folder.setChecked(False)               # display name only
    assert dlg.apply()
    meta = load_meta(root / "Personal")
    assert meta["name"] == "My Life" and meta["color"] == "#a6e3a1" and meta["icon"] == "🌿" and meta["extra"] == "x"
    assert "# keep me" in (root / "Personal" / ".dailyvibe.toml").read_text()
    assert "🌿  My Life" == win.journal_combo.itemText(win.journal_combo.currentIndex())
    # rename the folder too; conflict is refused
    dlg.name_edit.setText("Work")
    dlg.rename_folder.setChecked(True)
    assert not dlg.apply() and "already exists" in dlg.message.text()
    dlg.name_edit.setText("Life")
    assert dlg.apply()
    assert win.journal.root == root / "Life" and (root / "Life" / "2026/09/2026-09-20.md").exists()
    assert load_meta(root / "Life")["icon"] == "🌿" and dlg.path_label.text() == str(root / "Life")
    dlg.reject()
    win.close()


def test_journal_settings_template_and_plugins(qapp, tmp_path):
    root, win = _setup(tmp_path)
    win.switch_journal("Work")
    dlg = win.open_journal_settings()
    assert not dlg.custom_template.isChecked() and dlg.plugins_global.isChecked()
    dlg.custom_template.setChecked(True)
    dlg.template_edit.setPlainText("# Work {date}\n\n{plugins}\n\n## Tasks\n")
    dlg.plugins_custom.setChecked(True)
    dlg.plugins_page.field("fast", "word").setText("from work")
    assert dlg.apply()
    ov = load_meta(root / "Work")["overrides"]
    assert ov["template"].startswith("# Work {date}") and ov["plugins"] == ["fast"]
    assert ov["plugin_settings"] == {"fast": {"word": "from work"}}   # only values that differ
    # live: next new entry uses the journal template and the per-journal plugin setting
    day = D(2026, 9, 25)
    win.create_entry(day)
    assert wait_until(qapp, lambda: not win.runner.is_busy())
    win.save_current()  # the result landed in the open editor
    text = Journal(root / "Work").read(day)
    assert text.startswith("# Work 2026-09-25") and "## Tasks" in text and "fast from work" in text
    # Personal is unaffected
    win.switch_journal("Personal")
    win.create_entry(day)
    assert wait_until(qapp, lambda: not win.runner.is_busy())
    win.save_current()
    assert "fast hello" in Journal(root / "Personal").read(day)
    # back to global
    win.switch_journal("Work")
    dlg2 = win.open_journal_settings()
    assert dlg2.custom_template.isChecked() and dlg2.plugins_custom.isChecked()
    assert dlg2.plugins_page.field("fast", "word").text() == "from work"
    dlg2.custom_template.setChecked(False)
    dlg2.plugins_global.setChecked(True)
    dlg2._ok()
    assert "overrides" not in load_meta(root / "Work")
    win._dirty = False
    win.close()


def test_rename_tag_dialog_preview_apply_and_undo(qapp, tmp_path):
    root, win = _setup(tmp_path, enabled=())
    win.open_date(D(2026, 9, 21), force=True)
    cur = win.editor.textCursor()
    cur.setPosition(10)
    win.editor.setTextCursor(cur)
    win.add_tag_filter("health")
    dlg = win.rename_tag_dialog("health")
    assert dlg.old_combo.currentText() == "health"
    dlg.new_edit.setText("fff")
    assert "isn't a valid tag" in dlg.status.text()
    dlg.new_edit.setText("fitness")
    plan = dlg.preview()
    assert dlg.tree.topLevelItemCount() == 2 and plan.merge_with == "fitness" and dlg.merge_label.isVisibleTo(dlg)
    assert dlg.apply_btn.isEnabled()
    dlg.apply_btn.click()
    p = Journal(root / "Personal")
    assert p.read(D(2026, 9, 20)).startswith("---\ntags: [fitness]\n---")
    assert "#fitness/running" in p.read(D(2026, 9, 20))
    assert "yoga #fitness and `#health` code" in win.editor.toPlainText()     # open entry reloaded
    assert win.editor.textCursor().position() == 10 and not win._dirty
    assert "#health benefits" in Journal(root / "Work").read(D(2026, 9, 21))  # other journal untouched
    assert win.tag_filter == ["fitness"]                                       # filter swapped
    keys = {win.tag_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(win.tag_list.count())}
    assert "health" not in keys and "fitness/running" in keys
    assert "Renamed 3 tag occurrence(s) in 2 entries" in win._last_summary_box.text()
    win._update_undo_rename_action()
    assert win.undo_rename_action.isEnabled() and "#health → #fitness" in win.undo_rename_action.text()
    res = win.undo_last_tag_rename(confirm=False)
    assert len(res.restored) == 2
    assert "yoga #Health" in win.editor.toPlainText()
    assert p.read(D(2026, 9, 20)).startswith("---\ntags: [health, fitness]")
    win._update_undo_rename_action()
    assert not win.undo_rename_action.isEnabled()
    win.close()


def test_rename_tag_all_journals_and_menu_entries(qapp, tmp_path):
    root, win = _setup(tmp_path, enabled=())
    actions = [a.text() for a in win.journal_menu.actions()]
    assert "Journal Settings…" in actions and "Rename Tag…" in actions
    assert not any("Edit Journal Settings File" in a for a in actions)          # moved to Advanced
    adv = next(a.menu() for a in win.journal_menu.actions() if a.text() == "Advanced")
    assert any("Edit Journal Settings File" in a.text() for a in adv.actions())
    plan = win.plan_tag_rename("health", "wellbeing", True, "all")
    assert {c.journal.root.name for c in plan.changes} == {"Personal", "Work"}
    assert win.apply_tag_rename(plan)
    assert "#wellbeing benefits" in Journal(root / "Work").read(D(2026, 9, 21))
    assert len(win.last_tag_rename.backups) == 2
    win.close()

"""Round 4 UI: journal picker/switching, scoped search, tag panel filter,
autocomplete, preview tag links, journal menu actions, migration flow."""
import datetime as dt
import sys
import types

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest

from conftest import wait_until
from daily_vibe.config import Config
from daily_vibe.library import Library, LibraryError
from daily_vibe.storage import Journal

D = dt.date
FAST = "ID = 'fast'\nNAME = 'Fast'\nTITLE = 'Fast'\ndef render(date, context):\n    return 'fast ' + context['journal_name']\n"


def _lib(tmp_path):
    root = tmp_path / "Lib"
    lib = Library(root)
    p, w = lib.create("Personal"), lib.create("Work", color="#89b4fa", icon="💼")
    p.write(D(2026, 9, 20), "# p\ncoffee with mom #family #coffee\n")
    p.write(D(2026, 9, 21), "---\ntags: [family, trip]\n---\n# p2\nroad trip\n")
    w.write(D(2026, 9, 21), "# w\ncoffee with the team #work #coffee\n")
    return root


def _window(tmp_path, root, enabled=()):
    from daily_vibe.ui.main_window import MainWindow
    cfg = Config.load()
    cfg.journal_root = root
    cfg.data["plugins"]["enabled"] = list(enabled)
    cfg.save()
    pdir = tmp_path / "config" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "fast.py").write_text(FAST)
    return MainWindow(cfg)


def _items(lst):
    return [lst.item(i).text() for i in range(lst.count())]


def test_journal_picker_switch_and_remember(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    assert [win.journal_combo.itemData(i) for i in range(win.journal_combo.count())] == ["Personal", "Work"]
    assert "💼" in win.journal_combo.itemText(1)
    assert win.journal.name == "Personal" and win.entry_list.count() == 2
    win.journal_combo.setCurrentIndex(1)
    win._journal_combo_activated(1)
    assert win.journal.root == root / "Work" and win.entry_list.count() == 1
    assert D(2026, 9, 21) in win._highlighted and D(2026, 9, 20) not in win._highlighted  # calendar scoped
    assert "Work" in win.windowTitle()
    win.close()
    win2 = _window(tmp_path, root)          # last-opened journal remembered
    assert win2.journal.root.name == "Work"
    win2.close()


def test_scoped_search_and_open_hit_in_other_journal(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    win.search_box.setText("coffee")
    win.refresh_list()
    assert win.entry_list.count() == 1 and "Personal" in win.list_label.text()
    win.scope_combo.setCurrentIndex(win.scope_combo.findData("all"))
    win.search_box.setText("coffee")
    win.refresh_list()
    texts = _items(win.entry_list)
    assert len(texts) == 2 and texts[0].startswith("[Work]") and texts[1].startswith("[Personal]")
    win.search_box.setText("coffee tag:work")
    win.refresh_list()
    assert _items(win.entry_list)[0].startswith("[Work]") and win.entry_list.count() == 1
    win._list_item_opened(win.entry_list.item(0))
    assert win.journal.root.name == "Work" and win.current_date == D(2026, 9, 21)
    assert "coffee with the team" in win.editor.toPlainText()
    win.close()


def test_tag_panel_filter_and_clear(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    tags = {win.tag_list.item(i).data(Qt.ItemDataRole.UserRole): win.tag_list.item(i).text()
            for i in range(win.tag_list.count())}
    assert tags["family"] == "#family  (2)" and "work" not in tags        # journal-scoped counts
    fam = next(win.tag_list.item(i) for i in range(win.tag_list.count())
               if win.tag_list.item(i).data(Qt.ItemDataRole.UserRole) == "family")
    fam.setCheckState(Qt.CheckState.Checked)
    assert win.tag_filter == ["family"] and win.entry_list.count() == 2
    win.add_tag_filter("trip")                                              # AND
    assert win.entry_list.count() == 1 and "#trip" in win.list_label.text()
    assert win.clear_tags_btn.isVisibleTo(win)
    win.clear_tag_filter()
    assert win.tag_filter == [] and "Timeline" in win.list_label.text()
    # all-journals scope shows Work's tags too
    win.scope_combo.setCurrentIndex(win.scope_combo.findData("all"))
    keys = {win.tag_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(win.tag_list.count())}
    assert {"work", "coffee", "family"} <= keys
    win.add_tag_filter("coffee")
    assert sorted(t.split("]")[0] for t in _items(win.entry_list)) == ["[Personal", "[Work"]
    win.close()


def test_preview_tag_click_and_save_updates_tags(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    win.show()
    win.open_date(D(2026, 9, 20), force=True)
    win.preview.setVisible(True)
    win.update_preview()
    assert "coffee" in win.preview.toPlainText() and "#family" in win.preview.toPlainText()
    win._preview_link(QUrl("tag:coffee"))
    assert win.tag_filter == ["coffee"]
    win.clear_tag_filter()
    win.editor.setPlainText("# p\ncoffee with mom #family #coffee #brand-new\n")
    win.save_current()
    keys = {win.tag_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(win.tag_list.count())}
    assert "brand-new" in keys
    assert "brand-new" in win.editor.tag_model.stringList()
    win.close()


def test_tag_autocomplete_popup(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    win.show()
    win.open_date(D(2026, 9, 22), force=True)
    win.editor.setFocus()
    QTest.keyClicks(win.editor, "Today #fa")
    popup = win.editor.completer.popup()
    assert wait_until(qapp, popup.isVisible, 2)
    assert win.editor.completer.currentCompletion() == "family"
    QTest.keyClick(popup, Qt.Key.Key_Return)
    assert win.editor.toPlainText() == "Today #family "
    QTest.keyClicks(win.editor, "## heading")
    assert not popup.isVisible()
    win._dirty = False
    win.close()


def test_new_rename_remove_delete_journal(qapp, tmp_path, monkeypatch):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    win.create_journal("Dreams")
    assert win.journal.root == root / "Dreams" and win.journal_combo.count() == 3
    with pytest.raises(LibraryError):
        win.create_journal("Work")
    win.journal.write(D(2026, 9, 1), "# dream #flying\n")
    with pytest.raises(LibraryError):
        win.rename_journal("Work")                         # conflict refused
    win.rename_journal("Night Dreams")
    assert win.journal.root == root / "Night Dreams" and (root / "Night Dreams/2026/09/2026-09-01.md").exists()
    assert win.config.data["last_journal"] == "Night Dreams"
    win.remove_journal_from_library("Night Dreams")
    assert (root / "Night Dreams").exists() and win.journal.root.name != "Night Dreams"
    assert "Night Dreams" not in [win.journal_combo.itemData(i) for i in range(win.journal_combo.count())]
    win.restore_journal("Night Dreams")
    assert win.journal.root.name == "Night Dreams"
    trashed = []
    fake = types.ModuleType("send2trash")
    fake.send2trash = lambda p: trashed.append(p)
    monkeypatch.setitem(sys.modules, "send2trash", fake)
    with pytest.raises(LibraryError):
        win.delete_journal("Night Dreams", "night dreams")  # must type the exact name
    assert trashed == []
    win.delete_journal("Night Dreams", "Night Dreams")
    assert trashed == [str(root / "Night Dreams")] and win.journal.root.name != "Night Dreams"
    win.close()


def test_export_journal_zip_pdf_and_tag_filtered_range(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root)
    out = win.export_journal_zip(tmp_path / "personal.zip")
    assert out.exists()
    assert win.export_to(win.journal.list_dates(), tmp_path / "all.pdf", title=win.journal.name) == 2
    assert (tmp_path / "all.pdf").read_bytes()[:4] == b"%PDF"
    assert win.dates_for_export(D(2026, 9, 1), D(2026, 9, 30), ["trip"]) == [D(2026, 9, 21)]
    assert sorted(win.dates_for_export(D(2026, 9, 1), D(2026, 9, 30))) == [D(2026, 9, 20), D(2026, 9, 21)]
    win.close()


def test_plugins_use_journal_and_finish_after_switch(qapp, tmp_path):
    root = _lib(tmp_path)
    win = _window(tmp_path, root, enabled=["fast"])
    day = D(2026, 9, 25)
    win.create_entry(day)                                  # Personal
    win.switch_journal("Work")                             # switch while the plugin may still run
    assert wait_until(qapp, lambda: not win.runner.is_busy())
    assert "fast Personal" in Journal(root / "Personal").read(day)   # applied to the right journal's file
    assert not Journal(root / "Work").exists(day)
    win.close()


def test_migration_dialog_and_run(qapp, tmp_path):
    root = tmp_path / "Old"
    (root / "2026" / "09" / "assets").mkdir(parents=True)
    (root / "2026" / "09" / "assets" / "a.webp").write_bytes(b"RIFF")
    (root / "2026" / "09" / "2026-09-20.md").write_text("# d #old\n![a](assets/a.webp)\n")
    win = _window(tmp_path, root)
    assert win.maybe_migrate()
    box = win._migration_box
    assert "old layout" in box.text() and "Personal" in box.text()
    box.done(0)       # "Not now"
    box.close()
    report = win.run_migration()
    assert report and len(report.entries) == 1
    assert win.journal.root == root / "Personal" and win.entry_list.count() == 1
    assert "Migrated" in win._last_summary_box.text() and "Backup" in win._last_summary_box.text()
    assert "../../assets/2026/09/a.webp" in Journal(root / "Personal").read(D(2026, 9, 20))
    assert not win.maybe_migrate()                         # idempotent: nothing left to ask about
    win.close()

"""Round 6 UI: Live Preview editor, view modes, emoji picker, tag rename
history dialog, tag entries view."""
import datetime as dt
import time

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QTextCursor

from conftest import wait_until
from daily_vibe.config import Config
from daily_vibe.library import Library
from daily_vibe.live_highlighter import block_data

import pytest

D = dt.date


@pytest.fixture(autouse=True)
def _cleanup_windows(qapp):
    """Close windows between tests: every MainWindow installs an app-wide event
    filter (removed on close), and dozens of leftover windows make later tests slow."""
    yield
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if w.isVisible() or hasattr(w, "idle"):
            w.close()     # MainWindow.closeEvent removes its app event filter
    qapp.processEvents()
    while _CREATED:
        win = _CREATED.pop()
        try:
            win.runner.shutdown(1000)
            win.deleteLater()
        except RuntimeError:
            pass
    from PySide6.QtCore import QEvent
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


_CREATED = []
TODAY = D(2026, 9, 21)
ENTRY = ("---\ntags: [a]\n---\n# Title\n\nSome **bold** and _it_ text #coffee here\n"
         "- item\n- [ ] task\n- [x] done\n> quote\n\n---\n\n```py\ncode\n```\n"
         "[site](https://example.com)\n")


def _window(tmp_path, text=ENTRY, mode=None):
    from daily_vibe.ui.main_window import MainWindow
    root = tmp_path / "Lib"
    lib = Library(root)
    p, w = lib.create("Personal"), lib.create("Work")
    p.write(TODAY, text)
    p.write(D(2026, 9, 20), "# Earlier\n\nflat white #coffee #drinks\n")
    w.write(D(2026, 9, 19), "# Work\n\nmeeting fuel #coffee/decaf\n")
    cfg = Config.load()
    cfg.journal_root = root
    cfg.data["plugins"]["enabled"] = []
    cfg.data["last_journal"] = "Personal"
    if mode:
        cfg.data["view_mode"] = mode
    cfg.save()
    win = MainWindow(cfg)
    _CREATED.append(win)
    win.resize(1100, 800)
    win.open_date(TODAY, force=True)
    return root, p, w, cfg, win


def _block(editor, needle):
    b = editor.document().begin()
    while b.isValid() and needle not in b.text():
        b = b.next()
    return b


def _fmt_at(block, col):
    from PySide6.QtGui import QTextCharFormat
    ranges = block.layout().formats()
    for r in ranges:
        if r.start <= col < r.start + r.length:
            return QTextCharFormat(r.format)
    return None


def _put_cursor(editor, block, col=0):
    c = QTextCursor(block)
    c.setPosition(block.position() + col)
    editor.setTextCursor(c)


def test_live_preview_is_default_and_hides_markers_off_cursor_line(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    ed = win.editor
    assert win.view_mode == "live" and win.preview.isHidden()
    assert ed.live and win.view_actions["live"].isChecked()
    _put_cursor(ed, _block(ed, "# Title"))
    bold = _block(ed, "**bold**")
    col = bold.text().index("**")
    f = _fmt_at(bold, col)
    assert f is not None and f.foreground().color().alpha() == 0 and f.fontPointSize() <= 1.01  # hidden
    inner = _fmt_at(bold, col + 2)
    assert inner.fontWeight() >= 700
    # moving the cursor onto the line shows the markers (muted, normal size)
    _put_cursor(ed, bold, 3)
    f = _fmt_at(bold, col)
    assert f.foreground().color().alpha() == 255 and f.fontPointSize() < 1  # not tiny any more
    assert bold.blockNumber() in ed.active_blocks
    # the previous cursor line got re-highlighted (heading marker hidden again)
    title = _block(ed, "# Title")
    assert _fmt_at(title, 0).foreground().color().alpha() == 0
    # heading sizes scale with level
    assert _fmt_at(title, 3).fontPointSize() > ed.font().pointSizeF() * 1.5
    # link URL hidden, link text styled
    link = _block(ed, "[site]")
    assert _fmt_at(link, 1).fontUnderline()
    assert _fmt_at(link, link.text().index("https")).foreground().color().alpha() == 0
    # overlays recorded for painting
    assert block_data(_block(ed, "- item")).bullets == [0]
    assert block_data(_block(ed, "[ ] task")).tasks == [(2, False)]
    assert block_data(_block(ed, "[x] done")).tasks == [(2, True)]
    assert block_data(_block(ed, "#coffee")).tags[0][2] == "coffee"
    assert block_data(_block(ed, "> quote")).kind == "quote"
    assert block_data(_block(ed, "code")).kind == "code"
    assert block_data(ed.document().findBlockByNumber(1)).kind == "front"
    hr = ed.document().findBlockByNumber(11)
    assert hr.text() == "---" and block_data(hr).hr
    # the document text is never touched, nothing is dirty
    assert ed.toPlainText() == ENTRY and not win._dirty
    win.show()
    qapp.processEvents()
    win.editor.viewport().repaint()  # paintEvent overlays run without errors
    win.close()


def test_selection_activates_all_selected_lines(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    ed = win.editor
    a, b = _block(ed, "# Title"), _block(ed, "- item")
    c = QTextCursor(a)
    c.setPosition(b.position() + 2, QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(c)
    assert ed.active_blocks == set(range(a.blockNumber(), b.blockNumber() + 1))
    assert _fmt_at(_block(ed, "**bold**"), _block(ed, "**bold**").text().index("**")).foreground().color().alpha() == 255


def test_rehighlight_does_not_mark_dirty_or_autosave(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    ed = win.editor
    before = p.entry_path(TODAY).read_bytes()
    for needle in ("# Title", "**bold**", "- item", "> quote", "code", "[site]"):
        _put_cursor(ed, _block(ed, needle))
    for mode in ("split", "source", "reading", "live"):
        win.set_view_mode(mode)
    cfg.data["appearance"]["mode"] = "dark"
    win.apply_appearance()
    assert not win._dirty and not win.autosave_timer.isActive()
    win.open_date(D(2026, 9, 20))        # switching entries saves only if dirty
    assert p.entry_path(TODAY).read_bytes() == before
    win.open_date(TODAY)
    ed.textCursor().insertText("x")      # a real edit still marks dirty
    assert win._dirty


def test_checkbox_click_toggles_and_is_undoable(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    win.show()
    ed = win.editor
    _put_cursor(ed, _block(ed, "# Title"))
    qapp.processEvents()
    task = _block(ed, "[ ] task")
    off, checked = block_data(task).tasks[0]
    hit_pos = ed._task_box(task, off).center().toPoint()
    hit = ed.task_at(hit_pos)
    assert hit is not None and hit[1] == off and hit[2] is False
    ed.toggle_task(*hit)
    assert "- [x] task" in ed.toPlainText() and win._dirty
    ed.undo()
    assert "- [ ] task" in ed.toPlainText()
    # a real mouse click on the painted box toggles too
    from PySide6.QtTest import QTest
    _put_cursor(ed, _block(ed, "# Title"))   # undo left the cursor on the task line (raw there)
    qapp.processEvents()
    task = _block(ed, "[ ] task")
    QTest.mouseClick(ed.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     ed._task_box(task, off).center().toPoint())
    assert "- [x] task" in ed.toPlainText()
    win.close()


def test_inline_image_line_is_tall_when_inactive_and_raw_when_active(qapp, tmp_path):
    from PIL import Image
    text = "# Pic\n\n![alt](../../assets/2026/09/pic.webp)\n\nafter\n"
    root, p, w, cfg, win = _window(tmp_path, text)
    img_dir = p.assets_dir(TODAY)
    img_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 400), (200, 80, 40)).save(img_dir / "pic.webp", "WEBP")
    win.open_date(D(2026, 9, 20))
    win.open_date(TODAY)
    win.show()
    qapp.processEvents()
    ed = win.editor
    _put_cursor(ed, _block(ed, "# Pic"))
    blk = _block(ed, "![alt]")
    data = block_data(blk)
    assert data.image and data.image_size is not None
    w_px, h_px = data.image_size
    assert w_px <= ed.image_max_width() and h_px <= 360
    height = ed.blockBoundingRect(blk).height()
    assert height >= h_px - 4
    _put_cursor(ed, blk, 2)   # editing the line shows the raw link
    assert block_data(blk).image is None
    assert ed.blockBoundingRect(blk).height() < 60
    ed.viewport().repaint()
    win.close()


def test_long_entry_performance(qapp, tmp_path):
    body = "\n".join(
        f"## Section {i}\n\nSome **bold** _it_ `code` [l](https://x.y) #tag{i % 7} text\n- item\n- [ ] task\n> q"
        for i in range(350))
    root, p, w, cfg, win = _window(tmp_path, body)
    ed = win.editor
    assert ed.document().blockCount() >= 2000
    t = time.perf_counter()
    win.open_date(D(2026, 9, 20))
    win.open_date(TODAY)
    load = time.perf_counter() - t
    t = time.perf_counter()
    for i in range(0, 2000, 20):
        _put_cursor(ed, ed.document().findBlockByNumber(i))
    moves = time.perf_counter() - t
    assert load < 3.0, load
    assert moves / 100 < 0.02, moves   # per cursor move


def test_view_modes_menu_and_persistence(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    win.show()
    win.view_actions["split"].trigger()
    assert win.view_mode == "split" and not win.editor.live
    assert win.preview.isVisible() and win.editor.isVisible()
    assert Config.load().data["view_mode"] == "split"
    win.set_view_mode("reading")
    assert win.preview.isVisible() and not win.editor.isVisible()
    assert "Title" in win.preview.toPlainText()
    win.set_view_mode("source")
    assert win.editor.isVisible() and not win.preview.isVisible() and not win.editor.live
    win.toggle_preview()      # Ctrl+P from source -> split
    assert win.view_mode == "split"
    win.set_view_mode("live")
    assert win.editor.live and not win.preview.isVisible()
    win.close()
    from daily_vibe.ui.main_window import MainWindow
    cfg2 = Config.load()
    cfg2.data["view_mode"] = "reading"
    win2 = MainWindow(cfg2)
    assert win2.view_mode == "reading" and win2.view_actions["reading"].isChecked()
    win2.close()


def test_live_editor_keeps_autocomplete_paste_and_undo(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    win.show()
    ed = win.editor
    ed.moveCursor(QTextCursor.MoveOperation.End)
    from PySide6.QtTest import QTest
    QTest.keyClicks(ed, "new #cof")
    assert wait_until(qapp, lambda: ed.completer.popup().isVisible(), 3)
    assert ed.completer.currentCompletion().lower().startswith("cof")
    ed.completer.popup().hide()
    ed.undo()
    assert win._dirty
    # theme switch applies live to the live highlighter
    cfg.data["appearance"]["mode"] = "light"
    win.apply_appearance()
    assert ed.live_hl.theme is win.theme
    win.close()


def test_tag_menu_actions_and_context_menu(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    win.show()
    ed = win.editor
    blk = _block(ed, "#coffee")
    c = QTextCursor(blk)
    c.setPosition(blk.position() + blk.text().index("#coffee") + 2)
    pos = ed.cursorRect(c).center()
    assert ed.token_at(pos) == ("tag", "coffee")
    menu = ed.build_context_menu(pos)
    texts = [a.text() for a in menu.actions()]
    assert "Show All Entries Tagged #coffee" in texts and "Rename Tag #coffee…" in texts
    next(a for a in menu.actions() if a.text().startswith("Show All")).trigger()
    assert win.right_stack.currentWidget() is win.tag_view and win.tag_view.tag == "coffee"
    win.close_tag_entries()
    link = _block(ed, "[site]")
    c = QTextCursor(link)
    c.setPosition(link.position() + 2)
    assert ed.token_at(ed.cursorRect(c).center()) == ("link", "https://example.com")
    m = win.show_tag_menu("coffee", QPoint(10, 10))
    assert [a.text() for a in m.actions()][0] == "Show All Entries Tagged #coffee"
    m.close()
    win.close()


def test_tag_entries_view_scope_sort_open_export(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    view = win.show_tag_entries("coffee", "journal")
    assert win.right_stack.currentWidget() is view
    assert [c.date for c in view.cards] == [TODAY, D(2026, 9, 20)]
    view.set_scope("all")
    assert [(c.journal.name, c.date.day) for c in view.cards] == [("Personal", 21), ("Personal", 20), ("Work", 19)]
    html = view.browser.toHtml()
    assert "Work" in html and "decaf" in html
    view.nested.setChecked(False)
    assert len(view.cards) == 2
    view.nested.setChecked(True)
    view.set_newest_first(False)
    assert view.cards[0].date == D(2026, 9, 19)
    # open a card in the other journal -> switches journal and shows the editor
    idx = next(i for i, c in enumerate(view.cards) if c.journal.name == "Work")
    view.open_card(idx)
    assert win.right_stack.currentWidget() is win._editor_page
    assert win.journal.name == "Work" and win.current_date == D(2026, 9, 19)
    view = win.show_tag_entries("coffee", "all")
    out = tmp_path / "c.pdf"
    assert win.export_tag_entries(out) == 3 and out.exists()
    # tag panel double-click opens the view; Back returns to the entry
    win.close_tag_entries()
    item = win.tag_list.item(0)
    win.tag_list.itemDoubleClicked.emit(item)
    assert win.right_stack.currentWidget() is win.tag_view
    view.back_btn.click()
    assert win.right_stack.currentWidget() is win._editor_page
    # tag browser without a tag picks one
    v = win.show_tag_entries(None)
    assert v.tag
    win.close()


def test_emoji_picker_search_pick_recent_and_journal_icon(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    dlg = win.open_journal_settings()
    ep = dlg.emoji_dialog()
    ep.picker.search.setText("mountain")
    chars = ep.picker.result_chars()
    assert "🏔️" in chars and ep.picker.stack.currentIndex() == 1
    ep.picker.search.setText("")
    assert ep.picker.stack.currentIndex() == 0
    ep.picker.pick("🏔️")
    assert dlg.icon_edit.text() == "🏔️"
    assert Config.load().data["recent_emoji"][0] == "🏔️"
    assert ep.picker.recent_list.item(0).text() == "🏔️"
    assert ep.picker.tabs.count() == 10     # Recent + 9 categories
    dlg.reject()
    # recently used emoji become the first quick pick next time
    dlg2 = win.open_journal_settings()
    assert dlg2.emoji_buttons[0].text() == "🏔️"
    dlg2.reject()
    # Insert Emoji (Ctrl+.) inserts at the cursor
    ins = win.insert_emoji_dialog()
    ins.picker.search.setText("hot beverage")
    ins.picker._pick_first()
    assert "☕" in win.editor.toPlainText()
    assert any(a.shortcut().toString() == "Ctrl+." for a in win.findChildren(type(win.preview_action)))
    win.close()


def test_tag_rename_history_dialog_undo_rules(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    win.apply_tag_rename(win.plan_tag_rename("coffee", "brew", True, "all"))
    win._last_summary_box.accept()
    time.sleep(0.01)
    win.apply_tag_rename(win.plan_tag_rename("brew", "tea", True, "journal"))
    win._last_summary_box.accept()
    dlg = win.tag_history_dialog()
    assert dlg.tree.topLevelItemCount() == 2
    assert dlg.tree.topLevelItem(0).text(1) == "#brew → #tea"
    assert dlg.tree.topLevelItem(1).text(2).startswith("All journals")
    old = dlg.records[1]
    dlg.select_stamp(old.stamp)
    assert not dlg.undo_btn.isEnabled() and "Undo first" in dlg.detail.text()
    assert dlg.undo_selected(confirm=False) is None and "Undo those first" in dlg.last_error
    dlg.select_stamp(dlg.records[0].stamp)
    assert dlg.undo_btn.isEnabled()
    res = dlg.undo_selected(confirm=False)
    assert res is not None and not res.conflicts
    assert dlg.tree.topLevelItem(0).text(5) == "undone"
    dlg.select_stamp(old.stamp)
    assert dlg.undo_btn.isEnabled()
    dlg.undo_selected(confirm=False)
    assert "#coffee" in p.read(TODAY) and "#coffee/decaf" in w.read(D(2026, 9, 19))
    assert win.editor.toPlainText() == p.read(TODAY)   # open entry reloaded
    dlg.reject()
    win.close()


def test_retention_setting_cleans_up_after_rename(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    cfg.data["tag_rename_keep"] = 2
    names = ["coffee", "c1", "c2", "c3", "c4"]
    for a, b in zip(names, names[1:]):
        win.apply_tag_rename(win.plan_tag_rename(a, b, True, "journal"))
        win._last_summary_box.accept()
        time.sleep(0.01)
    recs = win.tag_rename_records()
    assert [r.label for r in recs] == ["#c3 → #c4", "#c2 → #c3"]
    # zip export: backups excluded by default
    out = win.export_journal_zip(tmp_path / "p.zip")
    import zipfile
    assert not any("backups" in n for n in zipfile.ZipFile(out).namelist())
    out = win.export_journal_zip(tmp_path / "p2.zip", include_backups=True)
    assert any("backups" in n for n in zipfile.ZipFile(out).namelist())
    win.close()


def test_preferences_expose_view_mode_and_retention(qapp, tmp_path):
    root, p, w, cfg, win = _window(tmp_path)
    win.open_settings()
    dlg = win._settings_dialog
    dlg.view_mode_combo.setCurrentIndex(dlg.view_mode_combo.findData("split"))
    dlg.keep_renames_spin.setValue(5)
    dlg.keep_days_spin.setValue(30)
    data = dlg.collect()
    assert data["view_mode"] == "split" and data["tag_rename_keep"] == 5 and data["tag_rename_keep_days"] == 30
    dlg.reject()
    win.close()

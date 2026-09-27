"""Round 7 UI: Live Preview tables / nested lists / inline images, find & replace
bar, Live Preview typography preferences."""
import datetime as dt

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from daily_vibe.config import Config
from daily_vibe.library import Library
from daily_vibe.live_highlighter import block_data

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
TABLE_ENTRY = """# Log

Intro paragraph with a run.

| Day | Activity | km |
|:----|:--------:|---:|
| Mon | Easy run | 6.2 |
| Tue | **Intervals** | 8 |
| Wed | Spin | 20 |

After the table.
"""

LISTS = """# Lists

- two
  - two-a
    - two-b
      - [ ] two-task

- four
    - four-a
        - four-b

- tab
\t- tab-a
\t\t- tab-b

1. one
   1. one-a
"""


def _window(tmp_path, text=TABLE_ENTRY, mode=None):
    from daily_vibe.ui.main_window import MainWindow
    root = tmp_path / "Lib"
    lib = Library(root)
    p = lib.create("Personal")
    p.write(TODAY, text)
    p.write(D(2026, 9, 20), "# Earlier\n\nother day\n")
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
    return root, p, cfg, win


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



def test_live_table_renders_as_grid_and_raw_when_cursor_inside(qapp, tmp_path):
    root, p, cfg, win = _window(tmp_path)
    win.show()
    qapp.processEvents()
    ed = win.editor
    before = p.entry_path(TODAY).read_bytes()
    _put_cursor(ed, _block(ed, "After the table"))
    head, delim = _block(ed, "| Day"), _block(ed, "|:----")
    rows = [_block(ed, "| Mon"), _block(ed, "| Tue"), _block(ed, "| Wed")]
    assert block_data(head).table == "head" and block_data(delim).table == "delim"
    assert all(block_data(r).table == "body" for r in rows)
    assert block_data(rows[1]).cells == ["Tue", "Intervals", "8"]
    assert block_data(rows[1]).cell_styles[1] == {"bold"}
    # pipes are hidden, the delimiter row collapses, rows get a fixed comfortable height
    assert _fmt_at(rows[0], 0).foreground().color().alpha() == 0
    assert ed.blockBoundingRect(delim).height() < ed.blockBoundingRect(rows[0]).height() / 2
    assert ed.blockBoundingRect(rows[0]).height() >= ed.fontMetrics().height() + 8
    lay = ed.table_layout(rows[0])
    assert lay["ncols"] == 3 and lay["aligns"] == ["left", "center", "right"]
    assert lay["kinds"][rows[1].blockNumber()] == ("body", 1)      # second body row -> stripe
    w_day, w_act = lay["widths"][0], lay["widths"][1]
    assert w_act > w_day                                           # content-based widths
    ed.viewport().repaint()
    # cursor anywhere inside the table -> the whole table shows raw Markdown in monospace
    _put_cursor(ed, rows[1], 3)
    table_nums = {b.blockNumber() for b in (head, delim, *rows)}
    assert table_nums <= ed.active_blocks
    f = _fmt_at(head, 2)
    assert f.foreground().color().alpha() == 255
    assert ed.mono_font.family() in (f.fontFamilies() or [f.fontFamily()])
    assert ed.blockBoundingRect(delim).height() >= 14                # raw delimiter row is a normal line
    ed.viewport().repaint()
    _put_cursor(ed, _block(ed, "Intro"))
    assert not (table_nums & ed.active_blocks)
    assert not win._dirty and p.entry_path(TODAY).read_bytes() == before


def test_format_table_action_is_one_undo_step(qapp, tmp_path):
    text = "# T\n\n| a | long header |\n|---|:-:|\n| wide cell here | x |\n\nend\n"
    root, p, cfg, win = _window(tmp_path, text)
    ed = win.editor
    _put_cursor(ed, _block(ed, "wide cell"), 4)
    win.format_table()
    lines = ed.toPlainText().split("\n")[2:5]
    assert len({len(x) for x in lines}) == 1 and lines[1].startswith("| ---")
    assert ed.textCursor().block().text().startswith("| wide cell here")
    assert win._dirty
    ed.undo()
    assert ed.toPlainText() == text
    _put_cursor(ed, _block(ed, "end"))
    assert ed.format_table_at_cursor() is False


def test_nested_lists_levels_guides_and_equal_indent_for_2_4_tab(qapp, tmp_path):
    root, p, cfg, win = _window(tmp_path, LISTS)
    win.show()
    qapp.processEvents()
    ed = win.editor
    _put_cursor(ed, _block(ed, "# Lists"))
    levels = {n: block_data(_block(ed, n)).bullet_levels for n in
              ("- two", "two-a", "two-b", "- four", "four-a", "four-b", "- tab", "tab-a", "tab-b")}
    assert levels["- two"] == levels["- four"] == levels["- tab"] == [0]
    assert levels["two-a"] == levels["four-a"] == levels["tab-a"] == [1]
    assert levels["two-b"] == levels["four-b"] == levels["tab-b"] == [2]
    task = block_data(_block(ed, "two-task"))
    assert task.tasks and len(task.guides) == 3
    assert len(block_data(_block(ed, "four-b")).guides) == 2
    assert len(block_data(_block(ed, "one-a")).guides) == 1

    def text_x(needle):
        b = _block(ed, needle)
        c = QTextCursor(b)
        c.setPosition(b.position() + b.text().index(needle.split()[-1]))
        return ed.cursorRect(c).left()
    # one nesting level has the same visual width whatever the source indentation
    xs1 = [text_x(n) for n in ("two-a", "four-a", "tab-a")]
    xs2 = [text_x(n) for n in ("two-b", "four-b", "tab-b")]
    assert max(xs1) - min(xs1) <= 3 and max(xs2) - min(xs2) <= 3
    assert xs2[0] - xs1[0] >= 15
    ed.viewport().repaint()
    assert not win._dirty


def test_inline_images_inside_a_text_line(qapp, tmp_path):
    from PIL import Image
    text = "# Pics\n\nShoes ![a](../../assets/2026/09/a.webp) and ![b](../../assets/2026/09/b.webp) muddy.\n\nend\n"
    root, p, cfg, win = _window(tmp_path, text)
    d = p.assets_dir(TODAY)
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 400), (200, 80, 40)).save(d / "a.webp", "WEBP")
    Image.new("RGB", (300, 600), (20, 180, 40)).save(d / "b.webp", "WEBP")
    win.open_date(D(2026, 9, 20))
    win.open_date(TODAY)
    win.show()
    qapp.processEvents()
    ed = win.editor
    _put_cursor(ed, _block(ed, "end"))
    blk = _block(ed, "Shoes")
    data = block_data(blk)
    assert data.image is None and len(data.inline_images) == 2
    mw, mh = ed.inline_image_limits()
    for _off, _target, (w, h) in data.inline_images:
        assert w <= mw and h <= mh
    assert data.inline_images[1][2][1] == mh                        # tall image capped at max height
    assert ed.blockBoundingRect(blk).height() >= mh
    ed.viewport().repaint()
    _put_cursor(ed, blk, 2)
    assert block_data(blk).inline_images == []
    assert ed.blockBoundingRect(blk).height() < 60
    assert not win._dirty


def test_find_bar_counter_navigation_options_and_escape(qapp, tmp_path):
    root, p, cfg, win = _window(tmp_path)
    win.show()
    qapp.processEvents()
    ed, bar = win.editor, win.find_bar
    before = p.entry_path(TODAY).read_bytes()
    _put_cursor(ed, _block(ed, "# Log"))
    win.open_find(False)
    assert bar.is_open and bar.replace_row.isHidden()
    bar.find_edit.setText("run")
    assert bar.counter_text() == "1 of 2" and len(ed._find_sels) == 2
    bar.find_next()
    first = (ed.textCursor().selectionStart(), ed.textCursor().selectionEnd())
    assert ed.textCursor().selectedText().lower() == "run"
    bar.find_next()
    assert bar.counter_text() == "2 of 2"
    # the current match's line is revealed (active) in Live Preview: raw table source
    assert _block(ed, "| Mon").blockNumber() in ed.active_blocks
    bar.find_next()                                  # wraps around
    assert (ed.textCursor().selectionStart(), ed.textCursor().selectionEnd()) == first
    bar.find_prev()
    assert bar.counter_text() == "2 of 2"
    # Enter / Shift+Enter in the field
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Return)
    assert bar.counter_text() == "1 of 2"
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert bar.counter_text() == "2 of 2"
    # options
    bar.find_edit.setText("RUN")
    bar.case_btn.setChecked(True)
    bar.refresh()
    assert bar.counter_text() == "No results"
    bar.case_btn.setChecked(False)
    bar.find_edit.setText("a")
    bar.word_btn.setChecked(True)
    bar.refresh()
    assert bar.counter_text().endswith("of 1")          # only the standalone "a"
    bar.word_btn.setChecked(False)
    bar.regex_btn.setChecked(True)
    bar.find_edit.setText(r"\d+")
    assert bar.counter_text().endswith("of 4")          # 6, 2, 8, 20
    bar.find_edit.setText("(")
    assert bar.counter_text() == "bad regex" and bar.error
    # Esc closes and clears highlights; nothing was modified
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Escape)
    assert not bar.is_open and ed._find_sels == []
    assert not win._dirty and p.entry_path(TODAY).read_bytes() == before


def test_replace_and_replace_all_single_undo(qapp, tmp_path):
    root, p, cfg, win = _window(tmp_path)
    ed, bar = win.editor, win.find_bar
    original = ed.toPlainText()
    _put_cursor(ed, _block(ed, "# Log"))
    win.open_find(True)
    assert not bar.replace_row.isHidden()
    bar.find_edit.setText("run")
    bar.replace_edit.setText("ride")
    bar.replace_current()                # first press selects the match
    assert ed.textCursor().selectedText() == "run" and ed.toPlainText() == original
    bar.replace_current()
    assert ed.toPlainText().count("ride") == 1 and bar.counter_text() == "1 of 1"
    assert win._dirty
    ed.undo()
    assert ed.toPlainText() == original
    # regex replace all with groups, one undo step
    bar.regex_btn.setChecked(True)
    bar.find_edit.setText(r"\| (\w{3}) \|")
    bar.replace_edit.setText(r"| \1day |")
    assert bar.replace_all() == 4
    assert "| Monday |" in ed.toPlainText() and "| Wedday |" in ed.toPlainText()
    ed.undo()
    assert ed.toPlainText() == original


def test_find_from_reading_mode_switches_to_live_and_global_search_kept(qapp, tmp_path):
    root, p, cfg, win = _window(tmp_path, mode="reading")
    assert win.view_mode == "reading"
    win.open_find(False)
    assert win.view_mode == "live" and win.find_bar.is_open
    win.set_view_mode("reading")
    assert not win.find_bar.is_open
    win.set_view_mode("source")
    win.open_find(False)
    win.find_bar.find_edit.setText("table")
    assert win.find_bar.counter_text() == "1 of 1" and not win.editor.live
    shortcuts = {a.shortcut().toString(): a.text() for a in win.findChildren(type(win.view_actions["live"]))
                 if not a.shortcut().isEmpty()}
    assert shortcuts["Ctrl+F"] == "Find…" and shortcuts["Ctrl+H"] == "Replace…"
    assert shortcuts["F3"] == "Find Next" and shortcuts["Shift+F3"] == "Find Previous"
    assert shortcuts["Ctrl+Shift+F"] == "Find in Entries" and shortcuts["Ctrl+Alt+T"] == "Format Table"
    assert win.search_box is not None


def test_live_typography_preferences_apply_live_and_revert_on_cancel(qapp, tmp_path):
    root, p, cfg, win = _window(tmp_path)
    win.show()
    ed = win.editor
    before = p.entry_path(TODAY).read_bytes()
    para = _block(ed, "After the table")
    _put_cursor(ed, _block(ed, "# Log"))
    h0 = ed.blockBoundingRect(para).height()
    win.open_settings()
    dlg = win._settings_dialog
    dlg.live_size.setValue(16)
    dlg.live_spacing.setValue(1.6)
    dlg.live_heading_same.setChecked(False)
    dlg.live_heading_combo.setCurrentFont(dlg.live_heading_combo.currentFont())
    dlg.live_width.setValue(500)
    # applied live to the main editor and the sample
    assert ed.live_font.pointSize() == 16 and ed.line_spacing == pytest.approx(1.6)
    assert ed.heading_family == dlg.live_heading_combo.currentFont().family()
    assert dlg.live_sample.line_spacing == pytest.approx(1.6)
    assert ed.blockBoundingRect(para).height() > h0 * 1.3
    assert ed.viewportMargins().left() > 0                 # centered reading column
    data = dlg.collect()["appearance"]
    assert data["live_font_size"] == 16 and data["live_line_spacing"] == 1.6 and data["live_max_width"] == 500
    assert data["live_code_family"] == "" and data["live_heading_family"]
    dlg.reject()
    qapp.processEvents()
    assert ed.line_spacing == 1.0 and ed.viewportMargins().left() == 0   # canceled -> reverted
    assert cfg.data["appearance"]["live_font_size"] == 0
    # Apply persists
    win.open_settings()
    dlg = win._settings_dialog
    dlg.live_spacing.setValue(1.3)
    dlg.apply()
    assert Config.load().data["appearance"]["live_line_spacing"] == 1.3
    dlg.reject()
    qapp.processEvents()
    assert ed.line_spacing == pytest.approx(1.3)
    win.set_view_mode("source")
    assert ed.viewportMargins().left() == 0 and ed.font().fixedPitch() or ed.font() == ed.mono_font
    assert not win._dirty and p.entry_path(TODAY).read_bytes() == before

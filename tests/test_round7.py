"""Round 7 (pure logic): GFM table parsing/formatting, strikethrough + tables in the
rendered preview/PDF, list normalization for rendering, find & replace."""
import re

import pytest

from daily_vibe import find_replace as fr
from daily_vibe import md_ext, md_tables
from daily_vibe.render import markdown_to_html

TABLE = ["| Day | Activity | km |", "|:---|:---:|---:|", "| Mon | Easy run | 6.2 |", "| Saturday | **Long** | 21.1 |"]


def test_split_row_handles_escapes_code_spans_and_outer_pipes():
    assert md_tables.split_row("| a | b |") == ["a", "b"]
    assert md_tables.split_row("a | b") == ["a", "b"]
    assert md_tables.split_row(r"| a \| b | `x|y` |") == [r"a \| b", "`x|y`"]
    assert md_tables.split_row("no pipes here") is None
    assert md_tables.is_delimiter("|:---|:---:|---:|") and not md_tables.is_delimiter("| a | b |")
    assert md_tables.alignments("| --- |:---|:---:| ---: |") == ["", "left", "center", "right"]
    assert md_tables.is_header(TABLE[0], TABLE[1])
    assert not md_tables.is_header(TABLE[0], "| --- |")          # column count must match
    assert not md_tables.is_header("plain text", TABLE[1])


def test_find_table_and_format_table_aligns_and_is_idempotent():
    lines = ["intro", "", *TABLE, "", "after"]
    assert md_tables.find_table(lines, 4) == (2, 5)
    assert md_tables.find_table(lines, 2) == (2, 5)
    assert md_tables.find_table(lines, 0) is None
    out = md_tables.format_table(TABLE)
    assert len({len(line) for line in out}) == 1                  # all rows the same width
    assert out[1] == "| :------- | :------: | ---: |"
    assert out[2] == "| Mon      | Easy run |  6.2 |"
    assert out[3] == "| Saturday | **Long** | 21.1 |"
    assert md_tables.format_table(out) == out                      # idempotent
    # wide characters count double so emoji/CJK columns still line up in monospace
    assert md_tables.display_width("😀") == 2 and md_tables.display_width("日本") == 4


def test_cell_text_and_whole_cell_styles():
    assert md_tables.cell_text("**Long** run [x](http://a)") == "Long run x"
    assert md_tables.cell_style("**Long run**") == {"bold"}
    assert md_tables.cell_style("~~old~~") == {"strike"}
    assert md_tables.cell_style("`code`") == {"code"}
    assert md_tables.cell_style("_it_") == {"italic"}
    assert md_tables.cell_style("**~~both~~**") == {"bold", "strike"}
    assert md_tables.cell_style("**partial** bold") == frozenset()


def test_rendered_preview_has_strikethrough_and_styled_tables():
    html = markdown_to_html("A ~~gone~~ word, `~~code~~` stays.\n\n" + "\n".join(TABLE + ["| Sun | rest | 0 |"]) + "\n",
                            stripe_bg="#123456")
    assert "<del>gone</del>" in html and "<code>~~code~~</code>" in html
    assert '<table class="md" cellspacing="0" cellpadding="6">' in html
    assert '<th align="center">' in html and '<td align="right">6.2</td>' in html
    rows = re.findall(r"<tr>.*?</tr>", html.split("<tbody>")[1], flags=re.S)
    assert "#123456" not in rows[0] and 'bgcolor="#123456"' in rows[1] and "#123456" not in rows[2]
    # the rich-text engine really strikes the <del> text out
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml("<p>a <del>b</del></p>")
    it = doc.begin().begin()
    fmts = []
    while not it.atEnd():
        fmts.append((it.fragment().text(), it.fragment().charFormat().fontStrikeOut()))
        it += 1
    assert ("b", True) in fmts


def test_pdf_html_has_strikethrough_and_table(tmp_path):
    import datetime as dt
    from daily_vibe.export import _ImageResolver, entry_html
    from daily_vibe.storage import Journal
    j = Journal(tmp_path / "J")
    day = dt.date(2026, 9, 1)
    j.write(day, "# T\n\n~~no~~ yes\n\n" + "\n".join(TABLE) + "\n")
    res = _ImageResolver()
    try:
        html = entry_html(j, day, res, page_break=False)
    finally:
        res.cleanup()
    assert "<del>no</del>" in html and "<table" in html and 'align="right"' in html


def test_normalize_lists_for_rendering():
    text = "- a\n  - b\n    - [ ] c\n\t- d\n- [x] e\n\n1. x\n   1. y\n\n```\n  - raw\n```\n* * *\n    code block\n"
    out = md_ext.normalize_lists(text).split("\n")
    assert out[:5] == ["- a", "    - b", "        - ☐ c", "        - d", "- ☑ e"]
    assert out[7] == "    1. y"
    assert "  - raw" in out and "* * *" in out and "    code block" in out
    html = markdown_to_html("- a\n  - b\n")
    assert html.count("<ul>") == 2                                  # 2-space nesting renders nested


def test_find_all_options():
    text = "Run, run! Running is fun. rerun RUN"
    o = fr.FindOptions
    assert len(fr.find_all(text, "run", o())) == 5
    assert fr.find_all(text, "run", o(case=True)) == [(5, 8), (28, 31)]
    assert fr.find_all(text, "run", o(word=True)) == [(0, 3), (5, 8), (32, 35)]
    assert fr.find_all(text, r"r\w+g", o(regex=True)) == [(10, 17)]
    assert fr.find_all(text, "", o()) == []
    assert fr.find_all("a.b axb", ".", o()) == [(1, 2)]            # literal unless regex
    assert fr.find_all("aaa", "x*", o(regex=True)) == []           # empty matches skipped
    with pytest.raises(re.error):
        fr.find_all(text, "(", o(regex=True))


def test_replace_all_and_regex_groups():
    o = fr.FindOptions
    assert fr.replace_all("cat Cat cat", "cat", "dog", o()) == ("dog dog dog", 3)
    assert fr.replace_all("cat Cat", "cat", "dog", o(case=True)) == ("dog Cat", 1)
    new, n = fr.replace_all("2026-09-26 and 2025-01-02", r"(\d{4})-(\d\d)-(\d\d)", r"\3/\2/\1", o(regex=True))
    assert (new, n) == ("26/09/2026 and 02/01/2025", 2)
    assert fr.replace_all(r"a\1", "a", r"\1", o()) == (r"\1\1", 1)     # literal replacement
    assert fr.replacement_for("x2y", (1, 2), r"(?P<d>\d)", r"<\g<d>>", o(regex=True)) == "<2>"


def test_utf16_offsets():
    u = fr.utf16_offsets("abc")
    assert u(2) == 2
    text = "😀 run"
    u = fr.utf16_offsets(text)
    s, e = fr.find_all(text, "run", fr.FindOptions())[0]
    assert (u(s), u(e)) == (3, 6)

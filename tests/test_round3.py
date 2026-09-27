import datetime as dt
import re
import subprocess
import shutil

import pytest
from PIL import Image

from conftest import wait_until
from daily_vibe import config as config_mod
from daily_vibe import relocate, security
from daily_vibe.config import Config
from daily_vibe.on_this_day import on_this_day, one_month_before
from daily_vibe.storage import Journal
from daily_vibe.streak import compute_streak, has_real_content, StreakTracker

TEMPLATE = config_mod.DEFAULT_TEMPLATE
D = dt.date


# --- config rename / migration -------------------------------------------------------
def test_env_var_and_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("DAILY_VIBE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("JOURNAL_APP_CONFIG_DIR", str(tmp_path / "old"))
    assert config_mod.config_dir() == tmp_path / "old"
    monkeypatch.setenv("DAILY_VIBE_CONFIG_DIR", str(tmp_path / "new"))
    assert config_mod.config_dir() == tmp_path / "new"


def test_migrate_legacy_config_copies_once(tmp_path):
    old, new = tmp_path / "journal-app", tmp_path / "daily-vibe"
    (old / "plugins").mkdir(parents=True)
    (old / "config.toml").write_text('journal_root = "~/MyJournal"\n')
    (old / "plugins" / "p.py").write_text("x = 1")
    assert config_mod.migrate_legacy_config(new, old)
    assert (new / "config.toml").read_text() == (old / "config.toml").read_text()
    assert (new / "plugins" / "p.py").exists() and (old / "config.toml").exists()  # old kept
    assert Config.load(new / "config.toml").data["journal_root"] == "~/MyJournal"
    assert not config_mod.migrate_legacy_config(new, old)  # second run: no-op


# --- On This Day ------------------------------------------------------------------------
def _j(tmp_path, dates, text="# Day\n\nwrote {d}\n"):
    j = Journal(tmp_path / "J")
    for d in dates:
        j.write(d, text.format(d=d))
    return j


def test_on_this_day_years_month_week(tmp_path):
    j = _j(tmp_path, [D(2025, 9, 26), D(2023, 9, 26), D(2024, 9, 25), D(2026, 8, 26), D(2026, 9, 19)])
    mems = on_this_day(j, D(2026, 9, 26))
    assert [(m.kind, m.date) for m in mems] == [
        ("year", D(2025, 9, 26)), ("year", D(2023, 9, 26)), ("month", D(2026, 8, 26)), ("week", D(2026, 9, 19))]
    assert mems[0].label.startswith("2025 · 1 year ago") and "wrote 2025-09-26" in mems[0].excerpt
    assert mems[1].label.startswith("2023 · 3 years ago")


def test_on_this_day_feb29(tmp_path):
    j = _j(tmp_path, [D(2024, 2, 29), D(2023, 2, 28), D(2020, 2, 29), D(2024, 2, 28)])
    leap = on_this_day(j, D(2028, 2, 29), include_recent=False)
    assert [m.date for m in leap] == [D(2024, 2, 29), D(2023, 2, 28), D(2020, 2, 29)]
    assert "(Feb 28)" in leap[1].label
    # Feb 28 in a non-leap year also shows Feb 29 from leap years
    feb28 = on_this_day(j, D(2025, 2, 28), include_recent=False)
    assert [m.date for m in feb28] == [D(2024, 2, 28), D(2024, 2, 29), D(2023, 2, 28), D(2020, 2, 29)]
    # Feb 28 in a leap year: only real Feb 28s
    assert [m.date for m in on_this_day(j, D(2028, 2, 28), include_recent=False)] == [D(2024, 2, 28), D(2023, 2, 28)]
    assert one_month_before(D(2026, 3, 31)) == D(2026, 2, 28)
    assert one_month_before(D(2026, 1, 15)) == D(2025, 12, 15)


# --- Streaks -----------------------------------------------------------------------------
def test_streak_math():
    today = D(2026, 9, 26)
    days = {D(2026, 9, 26), D(2026, 9, 25), D(2026, 9, 24), D(2026, 9, 20), D(2026, 9, 19)}
    s = compute_streak(days, today)
    assert (s.current, s.longest, s.today_done) == (3, 3, True)
    assert "3-day streak — keep it going" in s.message()
    # today not written yet: streak still alive through yesterday
    s = compute_streak(days - {today}, today)
    assert (s.current, s.today_done) == (2, False) and "write today to make it 3" in s.message()
    # gap of one full day breaks it
    s = compute_streak({D(2026, 9, 24), D(2026, 9, 23)}, today)
    assert s.current == 0 and s.longest == 2 and "No streak" in s.message()
    long_run = {D(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)}
    s = compute_streak(long_run | {today}, today)
    assert (s.current, s.longest) == (1, 10)
    assert compute_streak(set(), today).message().startswith("🌱 Write")


def test_template_only_entries_not_counted(tmp_path):
    from daily_vibe import markers
    from daily_vibe.storage import render_template
    d = D(2026, 9, 26)
    blocks = markers.wrap_block("weather", "## Weather\n\n☀️ sunny") + "\n\n" + markers.wrap_block("moon_phase", "## Moon\n\n🌕")
    untouched = render_template(TEMPLATE, d, blocks)
    assert not has_real_content(untouched, TEMPLATE, d)
    assert not has_real_content("", TEMPLATE, d)
    assert has_real_content(untouched + "Went for a walk.\n", TEMPLATE, d)
    j = Journal(tmp_path / "J")
    j.write(d, untouched)
    j.write(d - dt.timedelta(days=1), "# Friday\n\nreal words\n")
    tracker = StreakTracker()
    info = tracker.compute(j, TEMPLATE, today=d)
    assert (info.current, info.today_done) == (1, False)
    j.write(d, untouched + "Now I wrote something.\n")
    info = tracker.compute(j, TEMPLATE, today=d)
    assert (info.current, info.today_done) == (2, True)


# --- Security ---------------------------------------------------------------------------
def test_password_hash_and_verify():
    h = security.hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct horse" not in h
    assert security.verify_password("correct horse", h)
    assert not security.verify_password("wrong", h)
    assert h != security.hash_password("correct horse")  # salted
    assert not security.verify_password("x", "garbage")
    with pytest.raises(ValueError):
        security.hash_password("")


def test_attempt_limiter_backoff():
    now = [100.0]
    lim = security.AttemptLimiter(free=3, base=2, cap=10, clock=lambda: now[0])
    for _ in range(2):
        lim.record(False)
        assert lim.can_try()
    lim.record(False)  # 3rd failure -> 2 s
    assert not lim.can_try() and lim.remaining() == pytest.approx(2)
    now[0] += 2.1
    lim.record(False)  # 4th -> 4 s
    assert lim.remaining() == pytest.approx(4)
    now[0] += 100
    for _ in range(5):
        lim.record(False)
    assert lim.remaining() == pytest.approx(10)  # capped
    now[0] += 11
    lim.record(True)
    assert lim.can_try() and lim.failures == 0


# --- Relocation -------------------------------------------------------------------------
def _make_journal(root):
    j = Journal(root)
    j.write(D(2025, 9, 26), "# old year\n")
    j.write(D(2026, 9, 26), "# today\n\n![x](../../assets/2026/09/a.webp)\n")
    (j.assets_dir(D(2026, 9, 26))).mkdir(parents=True, exist_ok=True)
    (j.assets_dir(D(2026, 9, 26)) / "a.webp").write_bytes(b"RIFFxxxxWEBP" * 10)
    return j


def test_copy_preserves_structure_and_keeps_source(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _make_journal(src)
    (src / "README.txt").write_text("not journal content")
    r = relocate.transfer(src, dst, "copy")
    assert sorted(r.transferred) == ["2025/09/2025-09-26.md", "2026/09/2026-09-26.md", "assets/2026/09/a.webp"]
    assert (dst / "assets/2026/09/a.webp").read_bytes() == (src / "assets/2026/09/a.webp").read_bytes()
    assert (src / "2026/09/2026-09-26.md").exists()
    assert r.left_behind == ["README.txt"] and not r.conflicts and not r.errors
    assert not list(dst.rglob("*.dvpart"))


def test_move_removes_source_when_empty(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _make_journal(src)
    r = relocate.transfer(src, dst, "move")
    assert len(r.transferred) == 3 and r.source_removed and not src.exists()
    assert Journal(dst).list_dates() == [D(2026, 9, 26), D(2025, 9, 26)]
    assert "Moved 3 file(s)" in r.summary()


def test_conflicts_never_overwrite(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _make_journal(src)
    Journal(dst).write(D(2026, 9, 26), "# different content already there\n")
    Journal(dst).write(D(2025, 9, 26), "# old year\n")  # identical
    progress = []
    r = relocate.transfer(src, dst, "move", progress=lambda i, n, p: progress.append((i, n)))
    assert r.conflicts == ["2026/09/2026-09-26.md"]
    assert r.already_present == ["2025/09/2025-09-26.md"]
    assert Journal(dst).read(D(2026, 9, 26)) == "# different content already there\n"
    assert (src / "2026/09/2026-09-26.md").exists()       # conflicting source kept
    assert not (src / "2025/09/2025-09-26.md").exists()   # identical copy verified -> source removed
    assert src.exists() and not r.source_removed          # old folder kept (not empty)
    assert progress[-1] == (3, 3)
    assert "conflict" in r.summary()


def test_nested_paths_refused(tmp_path):
    src = tmp_path / "J"
    _make_journal(src)
    for target in (src / "inner", tmp_path, src):
        with pytest.raises(relocate.RelocateError):
            relocate.transfer(src, target, "copy")
    assert Journal(src).list_dates()


def test_cancel_stops_transfer(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _make_journal(src)
    calls = []
    r = relocate.transfer(src, dst, "copy", progress=lambda *a: calls.append(a), canceled=lambda: len(calls) >= 1)
    assert r.canceled and len(r.transferred) == 1


# --- Export -----------------------------------------------------------------------------
def test_pdf_export_with_webp_image(qapp, tmp_path):
    from daily_vibe.export import export_pdf, strip_markers
    from daily_vibe.images import convert_to_webp, markdown_link
    j = Journal(tmp_path / "J")
    d = D(2026, 9, 26)
    img = convert_to_webp(Image.new("RGB", (800, 400), (30, 120, 200)), j.assets_dir(d), stem="blue")
    j.write(d, "# Export me\n\n<!-- plugin:moon_phase -->\n\n## Moon\n\nFull moon\n\n<!-- /plugin:moon_phase -->\n\n"
               f"Some **bold** text.\n\n{markdown_link(img, j.entry_dir(d), 'blue')}\n")
    j.write(d - dt.timedelta(days=1), "# Second entry\n\nhello\n")
    assert "plugin:" not in strip_markers(j.read(d))
    out = tmp_path / "out.pdf"
    n = export_pdf(j, [d, d - dt.timedelta(days=1), d + dt.timedelta(days=1)], out, one_per_page=True)
    assert n == 2 and out.stat().st_size > 1000
    if shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True).stdout
        assert "Export me" in text and "Full moon" in text and "plugin:" not in text
        info = subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True).stdout
        assert re.search(r"Pages:\s+2\b", info)
    if shutil.which("pdfimages"):
        imgs = subprocess.run(["pdfimages", "-list", str(out)], capture_output=True, text=True).stdout
        assert any(l.split()[3:5] == ["800", "400"] for l in imgs.splitlines()[2:])


# --- Highlighter ------------------------------------------------------------------------
def test_highlighter_formats(qapp):
    from PySide6.QtGui import QTextCharFormat, QTextDocument
    from daily_vibe.highlighter import IN_FENCE, MarkdownHighlighter
    from daily_vibe.theming import ThemeRegistry
    theme = ThemeRegistry().get("Catppuccin Mocha")
    doc = QTextDocument()
    doc.setPlainText("# Heading\nsome **bold** and `code`\n```\nx = 1\n```\n<!-- plugin:weather -->\n- item")
    hl = MarkdownHighlighter(doc, theme)

    def fmt_at(block_no, col):
        block = doc.findBlockByNumber(block_no)
        ranges = block.layout().formats()
        for r in ranges:
            if r.start <= col < r.start + r.length:
                return QTextCharFormat(r.format)
        return None

    assert fmt_at(0, 2).foreground().color().name() == theme.hl("heading")
    assert fmt_at(1, 7).fontWeight() >= 700
    assert fmt_at(1, 20).foreground().color().name() == theme.hl("code")
    assert doc.findBlockByNumber(3).userState() == IN_FENCE
    assert fmt_at(3, 0).foreground().color().name() == theme.hl("code")
    assert fmt_at(5, 3).foreground().color().name() == theme.hl("marker")
    assert fmt_at(6, 0).foreground().color().name() == theme.hl("list")
    other = ThemeRegistry().get("Default Light")
    hl.set_theme(other)
    assert fmt_at(0, 2).foreground().color().name() == other.hl("heading")

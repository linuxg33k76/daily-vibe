"""Round 6 (non-widget logic): emoji data/search/recent, tag-rename history +
undo rule + retention, zip export without backups, tag entries data, PDF of pairs."""
import datetime as dt
import json
import zipfile

import pytest

from daily_vibe import emoji_data, tag_rename, tag_view
from daily_vibe import tags as tagmod
from daily_vibe.library import Library

D = dt.date


# Emoji ------------------------------------------------------------------------------
def test_emoji_data_is_complete_and_grouped():
    data = emoji_data.load(99.0)
    assert len(data) > 1800
    groups = {e.group for e in data}
    assert {g for g, _l, _i in emoji_data.GROUPS} <= groups
    assert "Component" not in groups
    chars = {e.char for e in data}
    assert "😀" in chars and "🌿" in chars and "🇺🇸" in chars
    assert not any("\U0001F3FB" <= c <= "\U0001F3FF" for e in data for c in e.char)  # no skin tones
    grin = next(e for e in data if e.char == "😀")
    assert grin.name == "grinning face" and "smile" in grin.keywords
    # version filter hides newer emoji
    assert len(emoji_data.load(12.0)) < len(data)
    assert all(e.version <= 12.0 for e in emoji_data.load(12.0))


def test_emoji_search_by_name_and_keyword():
    names = [e.name for e in emoji_data.search("coffee")]
    assert "hot beverage" in names  # keyword match (CLDR: coffee)
    top = emoji_data.search("red heart")[0]
    assert top.char.startswith("❤")
    assert emoji_data.search("grinning face")[0].char == "😀"   # exact name ranks first
    assert all("tree" in (e.name + " ".join(e.keywords)) for e in emoji_data.search("tree"))
    assert emoji_data.search("") == [] and emoji_data.search("zzqqxx") == []


def test_push_recent_dedupes_and_caps():
    r = []
    for c in "abcab":
        r = emoji_data.push_recent(r, c, max_len=3)
    assert r == ["b", "a", "c"]


# Tag rename history -------------------------------------------------------------------
def _lib(tmp_path):
    lib = Library(tmp_path / "Lib")
    p, w = lib.create("Personal"), lib.create("Work")
    p.write(D(2026, 9, 1), "a #coffee and #run\n")
    p.write(D(2026, 9, 2), "b #coffee\n")
    p.write(D(2026, 9, 3), "c #run only\n")
    w.write(D(2026, 9, 1), "w #coffee\n")
    return lib, p, w


def _rename(journals, old, new, stamp, scope="journal"):
    plan = tag_rename.plan_rename(journals, old, new, True, scope)
    return tag_rename.apply_rename(plan, stamp=stamp)


def test_history_lists_renames_with_scope_and_status(tmp_path):
    lib, p, w = _lib(tmp_path)
    _rename([p, w], "coffee", "brew", "20260901-100000-000000", scope="all")
    _rename([p], "run", "running", "20260901-110000-000000")
    recs = tag_rename.history([p, w])
    assert [r.label for r in recs] == ["#run → #running", "#coffee → #brew"]
    brew = recs[1]
    assert brew.scope == "all" and brew.status == tag_rename.APPLIED
    assert len(brew.files) == 3 and brew.journals == ["Personal", "Work"]
    manifest = json.loads((brew.backups[0] / "manifest.json").read_text())
    assert manifest["scope"] == "all"


def test_undo_older_rename_blocked_by_newer_on_same_files(tmp_path):
    lib, p, w = _lib(tmp_path)
    _rename([p], "coffee", "brew", "20260901-100000-000000")
    _rename([p], "brew", "tea", "20260901-110000-000000")      # touches the same files
    _rename([p], "run", "running", "20260901-120000-000000")   # touches 09-01 too (shared file)
    recs = tag_rename.history([p])
    blockers = tag_rename.blockers(recs, "20260901-100000-000000")
    assert {b.label for b in blockers} == {"#brew → #tea", "#run → #running"}
    with pytest.raises(tag_rename.TagRenameError, match="Undo those first"):
        tag_rename.undo_record([p], "20260901-100000-000000")
    # undo newest first -> everything restores cleanly
    tag_rename.undo_record([p], "20260901-120000-000000")
    tag_rename.undo_record([p], "20260901-110000-000000")
    res = tag_rename.undo_record([p], "20260901-100000-000000")
    assert not res.conflicts
    assert p.read(D(2026, 9, 1)) == "a #coffee and #run\n" and p.read(D(2026, 9, 2)) == "b #coffee\n"
    assert all(r.status == tag_rename.UNDONE for r in tag_rename.history([p]))
    with pytest.raises(tag_rename.TagRenameError, match="already undone"):
        tag_rename.undo_record([p], "20260901-100000-000000")


def test_undo_non_overlapping_older_rename_and_partial_status(tmp_path):
    lib, p, w = _lib(tmp_path)
    _rename([p], "coffee", "brew", "20260901-100000-000000")    # 09-01, 09-02
    _rename([w], "coffee", "tea", "20260901-110000-000000")     # Work only: no overlap
    assert tag_rename.blockers(tag_rename.history([p, w]), "20260901-100000-000000") == []
    p.write(D(2026, 9, 2), "b #brew edited later\n")           # user edit -> conflict
    res = tag_rename.undo_record([p, w], "20260901-100000-000000")
    assert len(res.restored) == 1 and len(res.conflicts) == 1
    rec = next(r for r in tag_rename.history([p, w]) if r.stamp == "20260901-100000-000000")
    assert rec.status == tag_rename.PARTIAL
    assert p.read(D(2026, 9, 2)) == "b #brew edited later\n"   # never overwritten


def test_cleanup_keeps_last_n_and_by_age(tmp_path):
    lib, p, w = _lib(tmp_path)
    tags = ["coffee", "c1", "c2", "c3", "c4"]
    for i in range(4):
        _rename([p], tags[i], tags[i + 1], f"2026090{i + 1}-100000-000000")
    assert len(tag_rename.history([p])) == 4
    removed = tag_rename.cleanup([p], keep=2)
    assert len(removed) == 2
    assert [r.stamp[:8] for r in tag_rename.history([p])] == ["20260904", "20260903"]
    # created timestamps are "now" at apply time; age out using a far-future clock
    removed = tag_rename.cleanup([p], keep=0, days=30, now=dt.datetime.now() + dt.timedelta(days=31))
    assert len(removed) == 2 and tag_rename.history([p]) == []


def test_zip_export_excludes_backups_unless_asked(tmp_path):
    lib, p, w = _lib(tmp_path)
    _rename([p], "coffee", "brew", "20260901-100000-000000")
    out = lib.export_zip("Personal", tmp_path / "p.zip")
    names = zipfile.ZipFile(out).namelist()
    assert "Personal/2026/09/2026-09-01.md" in names
    assert not any(".dailyvibe/backups" in n for n in names)
    out2 = lib.export_zip("Personal", tmp_path / "p2.zip", include_backups=True)
    assert any(".dailyvibe/backups/tag-rename-" in n for n in zipfile.ZipFile(out2).namelist())


# Tag entries view data ---------------------------------------------------------------------
def test_tag_view_collect_cards_nested_sort_scope(tmp_path):
    lib = Library(tmp_path / "Lib")
    p, w = lib.create("Personal"), lib.create("Work")
    p.write(D(2026, 9, 1), "# Morning run\n\nEasy 5k #health/running with **Sam** #goals\n")
    p.write(D(2026, 9, 3), "---\ntags: [health]\n---\nJust a note about sleep.\n")
    w.write(D(2026, 9, 2), "Standup then walk #health\n")
    idx = tagmod.TagIndex()
    cards = tag_view.collect([p, w], idx, "health", nested=True, newest_first=True)
    assert [(c.journal.name, c.date.day) for c in cards] == [("Personal", 3), ("Work", 2), ("Personal", 1)]
    run = cards[2]
    assert run.title == "Morning run"
    assert '<b class="hit">#health/running</b>' in run.excerpt_html and "Sam" in run.excerpt_html
    assert "**" not in run.excerpt_html
    assert run.tags == ["health/running", "goals"]
    exact = tag_view.collect([p, w], idx, "health", nested=False, newest_first=False)
    assert [c.date.day for c in exact] == [2, 3]
    only_p = tag_view.collect([p], idx, "health")
    assert {c.journal.name for c in only_p} == {"Personal"}


def test_export_pdf_pairs_multi_journal(qapp, tmp_path):
    from daily_vibe.export import export_pdf_pairs
    lib = Library(tmp_path / "Lib")
    p, w = lib.create("Personal"), lib.create("Work")
    p.write(D(2026, 9, 1), "# P\n#x one\n")
    w.write(D(2026, 9, 2), "# W\n#x two\n")
    out = tmp_path / "x.pdf"
    n = export_pdf_pairs([(w, D(2026, 9, 2)), (p, D(2026, 9, 1)), (p, D(2026, 1, 1))], out, title="#x")
    assert n == 2 and out.read_bytes()[:4] == b"%PDF"
    with pytest.raises(ValueError):
        export_pdf_pairs([(p, D(2020, 1, 1))], tmp_path / "none.pdf")

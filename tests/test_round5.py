"""Round 5: tag rename (parser-based rewrite, merge, safety, undo), journal meta edits."""
import datetime as dt
import json
import os

import pytest

from daily_vibe import tag_rename as tr
from daily_vibe.journal_meta import DELETE, load_meta, update_meta
from daily_vibe.storage import Journal
from daily_vibe.tags import extract_tags, is_valid_tag

D = dt.date
R = tr.rename_in_text


def test_inline_rename_respects_parser_exclusions():
    text = ("# Health log\n"                                  # heading marker isn't a tag
            "Did #health stuff, #Health again, #healthy and #health-food stay.\n"
            "`#health` in code, http://x.com/#health, [l](http://y/#health) and <!-- #health -->\n"
            "```\n#health in a fence\n```\n"
            "C#health a#health &#health\n"
            "<!-- plugin:weather -->\n#health generated\n<!-- /plugin:weather -->\n")
    out, n = R(text, "health", "fitness")
    assert n == 2
    assert "Did #fitness stuff, #fitness again, #healthy and #health-food stay." in out
    assert out.replace("Did #fitness stuff, #fitness again", "Did #health stuff, #Health again") == text


def test_partial_matches_and_nested_option():
    text = "#run #running #run/5k #run-club #runner/x #Run/Trail\n"
    assert R(text, "run", "jog", nested=False) == ("#jog #running #run/5k #run-club #runner/x #Run/Trail\n", 1)
    assert R(text, "run", "jog") == ("#jog #running #jog/5k #run-club #runner/x #jog/Trail\n", 3)
    # renaming a nested tag only
    assert R("#run/5k #run/10k #run\n", "run/5k", "race/5k") == ("#race/5k #run/10k #run\n", 1)


def test_front_matter_forms():
    inline = "---\ntags: [health, 'work', \"#health/yoga\"]\ntitle: x\n---\nbody\n"
    assert R(inline, "health", "fitness") == (
        "---\ntags: [fitness, 'work', \"#fitness/yoga\"]\ntitle: x\n---\nbody\n", 2)
    bare = "---\ntags: health, work\n---\n"
    assert R(bare, "health", "fitness") == ("---\ntags: fitness, work\n---\n", 1)
    spaced = "---\ntags: health work\n---\n"
    assert R(spaced, "health", "fitness") == ("---\ntags: fitness work\n---\n", 1)
    dash = "---\ntitle: t\ntags:\n  - health\n  - 'work'\nmood: ok\n---\n#health\n"
    assert R(dash, "Health", "fitness") == ("---\ntitle: t\ntags:\n  - fitness\n  - 'work'\nmood: ok\n---\n#fitness\n", 2)
    # a "tags:" line outside front matter is just text
    assert R("tags: [health]\n", "health", "x")[1] == 0


def test_merge_dedupes_front_matter_only():
    assert R("---\ntags: [fitness, health]\n---\n", "health", "fitness") == ("---\ntags: [fitness]\n---\n", 1)
    assert R("---\ntags: [health, b, fitness]\n---\n", "health", "fitness") == ("---\ntags: [fitness, b]\n---\n", 1)
    assert R("---\ntags: health, fitness\n---\n", "health", "fitness") == ("---\ntags: fitness\n---\n", 1)
    assert R("---\ntags:\n  - fitness\n  - health\n---\n", "health", "fitness") == ("---\ntags:\n  - fitness\n---\n", 1)
    # body duplicates are kept (they're prose)
    assert R("#health and #fitness\n", "health", "fitness") == ("#fitness and #fitness\n", 1)
    # pre-existing unrelated duplicates aren't touched
    assert R("---\ntags: [a, a, health]\n---\n", "health", "x") == ("---\ntags: [a, a, x]\n---\n", 1)


def test_line_endings_and_bytes_preserved():
    text = "---\r\ntags: [health]\r\n---\r\n# T\r\n\r\nline #health\r\ntrailing  \r\n\tindent #other\r\n"
    out, n = R(text, "health", "fitness")
    assert n == 2 and out == text.replace("health", "fitness")
    assert out.count("\r\n") == text.count("\r\n")
    assert extract_tags(out) == ["fitness", "other"]


def test_hex_colors_and_validation():
    assert R("color #fff and #abc123 #fad\n", "fad", "x") == ("color #fff and #abc123 #x\n", 1)
    assert is_valid_tag("fitness/yoga") and is_valid_tag("#deep-work")
    for bad in ("", "2026", "fff", "a1b2c3", "has space", "a/", "bad!", "-x"):
        assert not is_valid_tag(bad), bad
    with pytest.raises(tr.TagRenameError):
        tr.validate("health", "fff")
    with pytest.raises(tr.TagRenameError):
        tr.validate("health", "health")


def _journal(tmp_path):
    j = Journal(tmp_path / "Personal")
    j.write(D(2026, 9, 1), "---\ntags: [health, fitness]\n---\n# a\n#health/running today\n")
    j.write(D(2026, 9, 2), "# b\nnothing here #healthy\n")
    j.entry_path(D(2026, 9, 3)).write_bytes(b"# c\r\nCRLF #Health\r\n")
    return j


def test_plan_apply_backup_and_undo(tmp_path):
    j = _journal(tmp_path)
    before = {d: j.entry_path(d).read_bytes() for d in j.list_dates()}
    plan = tr.plan_rename([j], "health", "fitness")
    assert [c.date for c in plan.changes] == [D(2026, 9, 1), D(2026, 9, 3)]
    assert plan.merge_with == "fitness" and plan.total == 3
    assert plan.changes[0].samples and "#fitness/running" in plan.changes[0].samples[-1][1]
    res = tr.apply_rename(plan, stamp="t1")
    assert len(res.changed) == 2 and res.occurrences == 3
    assert j.entry_path(D(2026, 9, 3)).read_bytes() == b"# c\r\nCRLF #fitness\r\n"
    assert j.entry_path(D(2026, 9, 2)).read_bytes() == before[D(2026, 9, 2)]
    backup = j.root / ".dailyvibe/backups/tag-rename-t1"
    assert (backup / "2026/09/2026-09-01.md").read_bytes() == before[D(2026, 9, 1)]
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["old"] == "health" and set(manifest["files"]) == {"2026/09/2026-09-01.md", "2026/09/2026-09-03.md"}
    assert j.list_dates() == [D(2026, 9, 3), D(2026, 9, 2), D(2026, 9, 1)]   # backups aren't entries
    assert not list(j.root.rglob("*.dvtmp"))
    # undo
    assert tr.find_last_rename([j]) == [backup] and tr.describe([backup]) == "#health → #fitness"
    u = tr.undo([backup])
    assert len(u.restored) == 2 and not u.conflicts
    assert {d: j.entry_path(d).read_bytes() for d in j.list_dates()} == before
    assert tr.find_last_rename([j]) == []                                      # already undone


def test_undo_skips_files_edited_since(tmp_path):
    j = _journal(tmp_path)
    tr.apply_rename(tr.plan_rename([j], "health", "fitness"), stamp="t2")
    j.write(D(2026, 9, 3), "# c\nedited after the rename #fitness\n")
    u = tr.undo(tr.find_last_rename([j]))
    assert len(u.restored) == 1 and u.conflicts == ["Personal/2026/09/2026-09-03.md"]
    assert "edited after the rename" in j.read(D(2026, 9, 3))
    assert "Not restored" in u.summary()


def test_apply_skips_file_changed_after_preview(tmp_path):
    j = _journal(tmp_path)
    plan = tr.plan_rename([j], "health", "fitness")
    j.write(D(2026, 9, 1), "# a\nchanged meanwhile #health\n")
    res = tr.apply_rename(plan, stamp="t3")
    assert "changed meanwhile #health" in j.read(D(2026, 9, 1))
    assert any("changed since the preview" in s for s in res.skipped) and len(res.changed) == 1


def test_atomic_write_replaces_whole_file(tmp_path, monkeypatch):
    p = tmp_path / "x.md"
    p.write_bytes(b"old")
    tr.atomic_write_bytes(p, b"new content")
    assert p.read_bytes() == b"new content" and not list(tmp_path.glob("*.dvtmp"))
    # if the replace step fails, the original file is untouched
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        tr.atomic_write_bytes(p, b"half")
    assert p.read_bytes() == b"new content"


def test_plan_across_journals_and_nested_into_self(tmp_path):
    a, b = Journal(tmp_path / "A"), Journal(tmp_path / "B")
    a.write(D(2026, 9, 1), "#work\n")
    b.write(D(2026, 9, 1), "#Work/meetings\n")
    plan = tr.plan_rename([a, b], "work", "job")
    assert {c.journal.root.name for c in plan.changes} == {"A", "B"} and not plan.merge_with
    res = tr.apply_rename(plan, stamp="t4")
    assert len(res.backups) == 2 and b.read(D(2026, 9, 1)) == "#job/meetings\n"
    assert len(tr.find_last_rename([a, b])) == 2
    with pytest.raises(tr.TagRenameError):
        tr.plan_rename([a], "job", "job/sub")


# journal meta -----------------------------------------------------------------------------
def test_update_meta_preserves_comments_and_unknown_keys(tmp_path):
    (tmp_path / ".dailyvibe.toml").write_text(
        '# my own notes\nname = "Work"  # display\nmy_key = 42\n\n[overrides]\nplugins = ["weather"]\n')
    update_meta(tmp_path, {"color": "#112233", "overrides": {"template": "# {date}\n\n## Tasks\n", "plugins": DELETE,
                                                            "plugin_settings": {"weather": {"location": "Seattle, WA"}}}})
    text = (tmp_path / ".dailyvibe.toml").read_text()
    assert "# my own notes" in text and "# display" in text and "my_key = 42" in text
    assert '"""' in text  # multi-line template stays readable
    meta = load_meta(tmp_path)
    assert meta["overrides"] == {"template": "# {date}\n\n## Tasks\n",
                                 "plugin_settings": {"weather": {"location": "Seattle, WA"}}}
    update_meta(tmp_path, {"overrides": {"template": DELETE, "plugin_settings": DELETE}, "color": DELETE})
    assert load_meta(tmp_path) == {"name": "Work", "my_key": 42}


def test_per_journal_plugin_settings(tmp_path):
    from daily_vibe.config import Config
    from daily_vibe.plugin_manager import PluginManager
    cfg = Config.load()
    cfg.data["plugins"]["weather"] = {"location": "Denver, CO", "units": "imperial"}
    pm = PluginManager(cfg)
    work = Journal(tmp_path / "Work")
    update_meta(work.root, {"overrides": {"plugin_settings": {"weather": {"location": "Seattle, WA"}}}})
    w = pm.plugins["weather"]
    assert pm.settings_for(w)["location"] == "Denver, CO"
    assert pm.settings_for(w, work)["location"] == "Seattle, WA"
    assert pm.settings_for(w, work)["units"] == "imperial"
    ctx = pm._context(D(2026, 9, 1), w, work)
    assert ctx["settings"]["location"] == "Seattle, WA" and ctx["journal_name"] == "Work"

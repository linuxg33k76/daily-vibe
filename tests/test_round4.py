"""Round 4: tags, library, journal metadata, migration, MET Norway fallback."""
import datetime as dt
import json
import sys
import types
import zipfile

import pytest

from daily_vibe import migrate, tags
from daily_vibe.journal_meta import load_meta, save_meta
from daily_vibe.library import Library, LibraryError
from daily_vibe.storage import Journal

D = dt.date


# Tags -------------------------------------------------------------------------------
def test_inline_tag_rules():
    text = (
        "# Heading is not a tag\n"
        "## Tags inside heading text count: #not? yes, and #inheading\n"
        "Met with #team about #Health/Running and #deep-work.\n"
        "Colors #fff #000 #1e1e2e #0af are hex; #cafe #bad are words.\n"
        "Code `#nocode` and C# and a#b and &#123; and https://x.com/page#frag\n"
        "[link](http://example.com/#anchor) #2026goals\n"
        "```\n#infence\n```\n"
        "<!-- plugin:weather -->\n#generated\n<!-- /plugin:weather -->\n"
    )
    found = [t for _s, _e, t in tags.find_inline_tags(text)]
    assert found == ["not", "inheading", "team", "Health/Running", "deep-work", "cafe", "bad"]
    # offsets point into the original text
    for s, e, t in tags.find_inline_tags(text):
        assert text[s:e] == "#" + t


def test_front_matter_forms_and_normalization():
    a = "---\ntags: [Travel, 'family']\nmood: good\n---\n# Day\n#travel again\n"
    b = "---\ntags: travel, family\n---\nbody\n"
    c = "---\ntitle: x\ntags:\n  - travel\n  - \"family\"\n---\nbody #extra\n"
    assert tags.front_matter_tags(a) == ["Travel", "family"]
    assert tags.front_matter_tags(b) == ["travel", "family"]
    assert tags.front_matter_tags(c) == ["travel", "family"]
    assert tags.extract_tags(a) == ["Travel", "family"]           # #travel de-duplicated
    assert tags.extract_tags(c) == ["travel", "family", "extra"]
    assert tags.front_matter_tags("no front matter\n---\ntags: [x]\n---\n") == []
    assert tags.matches(["work/meetings", "Health"], ["work", "health"])   # nested + case
    assert not tags.matches(["work"], ["work", "health"])                   # AND semantics


def test_tag_index_cache_and_counts(tmp_path, monkeypatch):
    j = Journal(tmp_path / "Personal")
    j.write(D(2026, 9, 1), "# a\n#work #Focus\n")
    j.write(D(2026, 9, 2), "---\ntags: [work]\n---\n# b\n")
    idx = tags.TagIndex()
    counts = idx.counts([j])
    assert counts["work"] == ("work", 2) and counts["focus"] == ("Focus", 1)
    calls = []
    real = tags.extract_tags
    monkeypatch.setattr(tags, "extract_tags", lambda t: calls.append(1) or real(t))
    idx.counts([j])
    assert calls == []                              # served from the (mtime, size) cache
    j.write(D(2026, 9, 1), "# a\n#work #focus #new-tag here\n")
    assert "new-tag" in idx.counts([j]) and len(calls) == 1
    assert [d for _j, d in idx.entries_with([j], ["work", "focus"])] == [D(2026, 9, 1)]


def test_search_tag_terms(tmp_path):
    j = Journal(tmp_path / "Personal")
    j.write(D(2026, 9, 1), "# a\nstandup notes #work\n")
    j.write(D(2026, 9, 2), "# b\nstandup at home #family\n")
    idx = tags.TagIndex()
    assert [h.date for h in j.search("tag:work", tag_index=idx)] == [D(2026, 9, 1)]
    assert [h.date for h in j.search("standup tag:family")] == [D(2026, 9, 2)]
    assert j.search("standup tag:nope") == []
    assert {h.journal for h in j.search("standup")} == {"Personal"}


def test_render_chips_and_hidden_front_matter(tmp_path):
    from daily_vibe.render import markdown_to_html
    html = markdown_to_html("---\ntags: [trip]\n---\n# Day\nwith #friends and `#code`\n", tmp_path)
    assert 'href="tag:trip"' in html and 'href="tag:friends"' in html
    assert "tags: [trip]" not in html and "#code" in html and 'href="tag:code"' not in html


# Library / metadata -------------------------------------------------------------------
def test_library_create_rename_conflicts(tmp_path):
    lib = Library(tmp_path)
    personal = lib.ensure_default()
    assert personal.root == tmp_path / "Personal" and lib.names() == ["Personal"]
    work = lib.create("Work", color="#89b4fa", icon="💼")
    assert load_meta(work.root) == {"name": "Work", "color": "#89b4fa", "icon": "💼"}
    with pytest.raises(LibraryError):
        lib.create("Work")
    for bad in ("", ".hidden", "a/b", "2026", "assets"):
        with pytest.raises(LibraryError):
            lib.create(bad)
    work.write(D(2026, 9, 1), "# w\n")
    with pytest.raises(LibraryError):
        lib.rename("Work", "Personal")                     # refuse on conflict
    renamed = lib.rename("Work", "Job")
    assert renamed.read(D(2026, 9, 1)) == "# w\n" and not (tmp_path / "Work").exists()
    assert renamed.name == "Job" and load_meta(renamed.root)["icon"] == "💼"
    (tmp_path / "2019").mkdir()                            # old-layout year folder is not a journal
    (tmp_path / ".migration-backup-x").mkdir()
    assert lib.names() == ["Job", "Personal"]
    assert Library(tmp_path, hidden=["Job"]).names() == ["Personal"]


def test_library_trash_and_zip(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib")
    j = lib.create("Work")
    j.write(D(2026, 9, 1), "# w\n![x](../../assets/2026/09/x.webp)\n")
    (j.assets_dir(D(2026, 9, 1))).mkdir(parents=True)
    (j.assets_dir(D(2026, 9, 1)) / "x.webp").write_bytes(b"RIFF")
    out = lib.export_zip("Work", tmp_path / "work.zip")
    names = zipfile.ZipFile(out).namelist()
    assert "Work/2026/09/2026-09-01.md" in names and "Work/assets/2026/09/x.webp" in names
    assert "Work/.dailyvibe.toml" in names
    # without send2trash: refuse (never hard-delete)
    monkeypatch.setitem(sys.modules, "send2trash", None)
    with pytest.raises(LibraryError, match="send2trash"):
        lib.trash("Work")
    assert j.root.exists()
    trashed = []
    fake = types.ModuleType("send2trash")
    fake.send2trash = lambda p: trashed.append(p)
    monkeypatch.setitem(sys.modules, "send2trash", fake)
    lib.trash("Work")
    assert trashed == [str(j.root)]


def test_library_search_across_journals(tmp_path):
    lib = Library(tmp_path)
    lib.create("Personal").write(D(2026, 9, 1), "# p\ncoffee with mom #family\n")
    lib.create("Work").write(D(2026, 9, 2), "# w\ncoffee with the team #work\n")
    hits = lib.search("coffee")
    assert [(h.journal, h.date) for h in hits] == [("Work", D(2026, 9, 2)), ("Personal", D(2026, 9, 1))]
    assert [h.journal for h in lib.search("coffee tag:family")] == ["Personal"]


def test_journal_meta_overrides(tmp_path):
    from daily_vibe.config import Config
    from daily_vibe.plugin_manager import PluginManager
    j = Journal(tmp_path / "Work")
    save_meta(j.root, {"name": "Work Log", "overrides": {"template": "# {date}\n\n## Tasks\n", "plugins": ["moon_phase"]}})
    assert j.name == "Work Log"
    assert j.template("default") == "# {date}\n\n## Tasks\n"
    assert Journal(tmp_path / "Other").template("default") == "default"
    cfg = Config.load()
    cfg.data["plugins"]["enabled"] = ["weather", "moon_phase"]
    pm = PluginManager(cfg)
    assert [p.id for p in pm.enabled(j)] == ["moon_phase"]
    assert [p.id for p in pm.enabled(Journal(tmp_path / "Other"))] == ["weather", "moon_phase"]
    assert "# The Daily Vibe journal settings" in (j.root / ".dailyvibe.toml").read_text()


def test_images_link_is_relative_to_journal_assets(tmp_path):
    from daily_vibe.images import markdown_link
    j = Journal(tmp_path / "Personal")
    d = D(2026, 9, 26)
    link = markdown_link(j.assets_dir(d) / "x.webp", j.entry_dir(d), "x")
    assert link == "![x](../../assets/2026/09/x.webp)"


# Migration ------------------------------------------------------------------------------
def _old_layout(root):
    (root / "2026" / "09" / "assets").mkdir(parents=True)
    (root / "2026" / "09" / "assets" / "cat.webp").write_bytes(b"RIFFcat")
    (root / "2026" / "09" / "2026-09-20.md").write_text(
        "# Day\n\n![cat](assets/cat.webp)\n<img src=\"./assets/cat.webp\">\n![web](https://x.com/assets/a.png)\n")
    (root / "2025" / "12").mkdir(parents=True)
    (root / "2025" / "12" / "2025-12-25.md").write_text("# Xmas #family\n")
    (root / "notes.txt").write_text("keep me")


def test_migration_moves_rewrites_backs_up_and_is_idempotent(tmp_path):
    root = tmp_path / "Journal"
    _old_layout(root)
    assert migrate.detect(root) and migrate.plan_counts(root) == (2, 1)
    report = migrate.migrate(root)
    p = root / "Personal"
    text = (p / "2026/09/2026-09-20.md").read_text()
    assert "](../../assets/2026/09/cat.webp)" in text and 'src="../../assets/2026/09/cat.webp"' in text
    assert "https://x.com/assets/a.png" in text                      # external links untouched
    assert (p / "assets/2026/09/cat.webp").read_bytes() == b"RIFFcat"
    assert (p / "2025/12/2025-12-25.md").exists()
    assert not (root / "2026").exists() and not (root / "2025").exists()
    assert (root / "notes.txt").exists()                              # loose files left alone
    assert report.backup and (report.backup / "2026/09/assets/cat.webp").read_bytes() == b"RIFFcat"
    assert (report.backup / "2026/09/2026-09-20.md").read_text().count("](assets/cat.webp)") == 1
    assert len(report.entries) == 2 and len(report.assets) == 1 and report.links_rewritten == 2
    assert "2 entries, 1 images" in report.summary()
    assert load_meta(p)["name"] == "Personal"
    # rendered image resolves from the new location
    assert Journal(p).entry_dir(D(2026, 9, 20)).joinpath("../../assets/2026/09/cat.webp").resolve().is_file()
    # idempotent
    assert not migrate.detect(root)
    again = migrate.migrate(root)
    assert again.entries == [] and again.backup is None
    assert Library(root).names() == ["Personal"]


def test_migration_never_overwrites(tmp_path):
    root = tmp_path / "Journal"
    _old_layout(root)
    existing = root / "Personal" / "2025" / "12" / "2025-12-25.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("# a different, newer entry\n")
    report = migrate.migrate(root)
    assert existing.read_text() == "# a different, newer entry\n"
    assert report.conflicts == ["2025/12/2025-12-25.md"]
    assert (root / "2025/12/2025-12-25.md").exists()                 # source kept
    assert migrate.detect(root)                                      # still something to resolve
    assert "conflict" in report.summary()


def test_relocate_library_moves_journals_but_not_backups(tmp_path):
    from daily_vibe import relocate
    src, dst = tmp_path / "lib", tmp_path / "new"
    Library(src).create("Work").write(D(2026, 9, 1), "# w\n")
    (src / ".migration-backup-1").mkdir()
    (src / ".migration-backup-1" / "x.md").write_text("old")
    r = relocate.transfer(src, dst, "move")
    assert (dst / "Work/2026/09/2026-09-01.md").exists() and (dst / "Work/.dailyvibe.toml").exists()
    assert (src / ".migration-backup-1" / "x.md").exists()
    # single journal copy includes everything inside it
    r2 = relocate.transfer(dst / "Work", tmp_path / "elsewhere" / "Work", "copy", include_all=True)
    assert ".dailyvibe.toml" in r2.transferred


# Weather: MET Norway fallback ------------------------------------------------------------
def _met_payload(day: dt.date):
    series = []
    for h in range(0, 24):
        t = dt.datetime(day.year, day.month, day.day, h, tzinfo=dt.timezone.utc) + dt.timedelta(hours=6)
        series.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_temperature": 10.0 + h / 2, "wind_speed": 2.0 + h / 10,
                                    "wind_from_direction": 90.0, "relative_humidity": 40.0}},
            "next_1_hours": {"summary": {"symbol_code": "rain" if h == 12 else "partlycloudy_day"},
                             "details": {"precipitation_amount": 0.5 if h in (11, 12) else 0.0}},
            "next_6_hours": {"summary": {"symbol_code": "lightrain"}, "details": {"precipitation_amount": 9.0}}}})
    return {"properties": {"timeseries": series}}


def test_met_parse_units_and_symbols():
    from daily_vibe.plugins import weather
    day = D(2026, 9, 28)
    loc = {"lat": 39.74, "lon": -104.99, "name": "Denver", "source": "config", "tz": "America/Denver"}
    res = weather.parse_met(_met_payload(day), day, loc, "metric", now=dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc))
    d = res["data"]["daily"]
    assert res["provider"] == "MET Norway" and "current" not in res["data"]
    assert d["temperature_2m_min"][0] == pytest.approx(10.0) and d["temperature_2m_max"][0] == pytest.approx(21.5)
    assert d["precipitation_sum"][0] == pytest.approx(1.0)            # hourly amounts, no 6h double count
    imp = weather.parse_met(_met_payload(day), day, loc, "imperial", now=dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc))
    assert imp["data"]["daily"]["temperature_2m_min"][0] == pytest.approx(50.0)
    text = weather.format_weather(day, loc, imp)
    assert "MET Norway" in text and "°F" in text
    assert weather.met_symbol("heavyrainandthunder") == ("Thunderstorm", "⛈️")
    assert weather.met_symbol("partlycloudy_night")[0] == "Partly cloudy"
    assert "@" not in weather.MET_USER_AGENT and weather.MET_USER_AGENT.startswith("DailyVibe/0.")
    assert weather.met_user_agent("me@example.org").endswith("; me@example.org)")


def test_weather_falls_back_to_met_on_429(monkeypatch):
    from daily_vibe.plugins import weather
    weather._cache.clear()
    today = dt.date.today()
    calls = []

    def fake_get_json(url, params=None, user_agent=None):
        calls.append((url.split("/")[2], user_agent))
        if "open-meteo" in url:
            raise OSError("HTTP Error 429: Too Many Requests")
        return _met_for_today(today)

    def _met_for_today(day):
        p = _met_payload(day)
        start = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
        for i, item in enumerate(p["properties"]["timeseries"]):
            item["time"] = (start + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return p

    monkeypatch.setattr(weather, "_get_json", fake_get_json)
    loc = {"lat": 39.7392, "lon": -104.9903, "name": "Denver", "source": "config", "tz": "America/Denver"}
    res = weather.fetch_weather(today, loc, "imperial")
    assert res["provider"] == "MET Norway" and "limit" in res["fallback_reason"]
    met_calls = [c for c in calls if c[0] == "api.met.no"]
    assert met_calls and met_calls[0][1].startswith("DailyVibe/0.")
    text = weather.format_weather(today, loc, res)
    assert "· MET Norway" in text and "backup" in text
    # past dates can't use MET: the Open-Meteo error is raised
    weather._cache.clear()
    with pytest.raises(Exception, match="429|limit"):
        weather.fetch_weather(today - dt.timedelta(days=30), loc, "imperial")
    weather._cache.clear()

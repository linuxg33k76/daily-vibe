import datetime as dt

from daily_vibe.config import Config
from daily_vibe.storage import Journal, render_template


def test_entry_path_layout(tmp_path):
    j = Journal(tmp_path)
    d = dt.date(2026, 3, 7)
    assert j.entry_path(d) == tmp_path / "2026" / "03" / "2026-03-07.md"
    # round 4: journal-level assets folder
    assert j.assets_dir(d) == tmp_path / "assets" / "2026" / "03"


def test_write_read_list_search(tmp_path):
    j = Journal(tmp_path)
    j.write(dt.date(2026, 1, 2), "# Jan\n\nWent hiking with Sam")
    j.write(dt.date(2026, 2, 3), "# Feb\n\nCoffee and code")
    (tmp_path / "2026" / "02" / "notes.md").write_text("not an entry")
    assert j.list_dates() == [dt.date(2026, 2, 3), dt.date(2026, 1, 2)]
    assert j.read(dt.date(2026, 1, 2)).endswith("Sam")
    assert j.read(dt.date(1999, 1, 1)) == ""
    hits = j.search("HIKING sam")
    assert [h.date for h in hits] == [dt.date(2026, 1, 2)]
    assert "hiking" in hits[0].snippet
    assert j.search("nothing-matches") == []
    assert j.summary(dt.date(2026, 2, 3)) == "Coffee and code"


def test_template_rendering():
    text = render_template("# {weekday}, {long_date}\n\n{plugins}\n\n## Notes\n", dt.date(2026, 9, 26), "BLOCK")
    assert text.startswith("# Saturday, September 26, 2026")
    assert "BLOCK" in text and "{plugins}" not in text


def test_config_roundtrip(tmp_path):
    cfg = Config.load(tmp_path / "c.toml")
    assert cfg.data["images"]["webp_quality"] == 80
    cfg.set_plugin_enabled("weather", False)
    cfg.journal_root = tmp_path / "J"
    cfg.save()
    again = Config.load(tmp_path / "c.toml")
    assert "weather" not in again.enabled_plugins
    assert again.journal_root == tmp_path / "J"
    assert again.data["template"] == cfg.data["template"]


def test_new_config_fields_roundtrip(tmp_path):
    cfg = Config.load(tmp_path / "c.toml")
    assert cfg.data["appearance"]["mode"] == "system"
    cfg.data["appearance"].update(mode="dark", dark_theme="Gruvbox Dark", accent="#ff8800",
                                  editor_font_family="JetBrains Mono", editor_font_size=14, preview_font_size=16)
    cfg.data["plugins"]["weather"].update(location="Denver, CO", location_label="Denver, Colorado", units="metric")
    cfg.data["autosave_ms"] = 2500
    cfg.save()
    again = Config.load(tmp_path / "c.toml")
    assert again.data["appearance"] == cfg.data["appearance"]
    assert again.data["plugins"]["weather"]["location"] == "Denver, CO"
    assert again.data["plugins"]["weather"]["units"] == "metric"
    assert again.data["autosave_ms"] == 2500


def test_old_config_gets_new_defaults(tmp_path):
    p = tmp_path / "old.toml"
    p.write_text('journal_root = "~/J"\n[images]\nwebp_quality = 70\n')
    cfg = Config.load(p)
    assert cfg.data["images"] == {"webp_quality": 70, "max_dimension": 1920}
    assert cfg.data["appearance"]["preview_font_size"] == 14

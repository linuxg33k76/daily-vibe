import datetime as dt
import textwrap

from daily_vibe import markers
from daily_vibe.config import Config
from daily_vibe.plugin_manager import PluginManager
from daily_vibe.storage import Journal


def test_replace_existing_block_only():
    text = "# Day\n\n" + markers.wrap_block("weather", "old") + "\n\n" + markers.wrap_block("moon", "m") + "\n\n## Notes\nhi\n"
    out = markers.replace_block(text, "weather", "## Weather\n\nnew")
    assert "old" not in out and "new" in out
    assert out.count("<!-- plugin:weather -->") == 1
    assert markers.wrap_block("moon", "m") in out
    assert out.endswith("## Notes\nhi\n")


def test_insert_missing_block_after_last_block_or_heading():
    text = "# Day\n\n" + markers.wrap_block("a", "A") + "\n\n## Notes\n"
    out = markers.replace_block(text, "b", "B")
    assert out.index("plugin:a") < out.index("plugin:b") < out.index("## Notes")
    out2 = markers.replace_block("# Day\n\nbody\n", "a", "A")
    assert out2.startswith("# Day\n\n<!-- plugin:a -->")
    assert out2.rstrip().endswith("body")


def test_failing_plugin_isolated(tmp_path):
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    (pdir / "good.py").write_text("NAME='good'\nTITLE='Good'\ndef render(date, context):\n    return 'fine ' + date.isoformat()\n")
    (pdir / "bad.py").write_text("NAME='bad'\ndef render(date, context):\n    raise RuntimeError('boom')\n")
    (pdir / "broken.py").write_text("this is not python(")
    cfg = Config({"plugins": {"enabled": ["bad", "good"]}}, tmp_path / "c.toml")
    pm = PluginManager(cfg, extra_dirs=[pdir])
    assert any("broken.py" in k for k in pm.load_errors)
    j = Journal(tmp_path / "J")
    results = pm.run_all(dt.date(2026, 9, 26), j)
    assert [r.ok for r in results] == [False, True]
    assert "boom" in results[0].markdown and "failed" in results[0].markdown
    text, _ = pm.refresh_text("# Day\n\n## Notes\n", dt.date(2026, 9, 26), j)
    assert "## Good\n\nfine 2026-09-26" in text
    # refreshing again replaces rather than duplicates
    text2, _ = pm.refresh_text(text, dt.date(2026, 9, 26), j)
    assert text2.count("<!-- plugin:good -->") == 1


def test_builtin_plugins_discovered(tmp_path):
    pm = PluginManager(Config({}, tmp_path / "c.toml"))
    assert {"weather", "moon_phase", "ai_highlights", "x_bookmarks"} <= set(pm.plugins)

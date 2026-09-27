import datetime as dt
import time

from conftest import wait_until
from daily_vibe import markers
from daily_vibe.config import Config
from daily_vibe.plugin_manager import PluginManager
from daily_vibe.storage import Journal
from daily_vibe.workers import PluginRunner

SLOW = """
import time
NAME = 'slow'
TITLE = 'Slow'
calls = []
def render(date, context):
    calls.append(1)
    n = len(calls)
    if n == 1:
        time.sleep(0.6)   # first request is slow and becomes stale
        return 'slow result 1'
    return 'slow result %d' % n
"""
FAST = "NAME = 'fast'\nTITLE = 'Fast'\ndef render(date, context):\n    return 'fast for ' + date.isoformat()\n"


def _setup(tmp_path):
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    (pdir / "slow.py").write_text(SLOW)
    (pdir / "fast.py").write_text(FAST)
    cfg = Config({"plugins": {"enabled": ["slow", "fast"]}}, tmp_path / "c.toml")
    return PluginManager(cfg, extra_dirs=[pdir]), Journal(tmp_path / "J")


def test_results_go_to_the_right_date(qapp, tmp_path):
    pm, journal = _setup(tmp_path)
    open_date, other = dt.date(2026, 9, 26), dt.date(2026, 9, 20)
    journal.write(other, "# Other day\n\nmy text\n")
    handled = []

    runner = PluginRunner(pm, lambda: journal)
    runner.open_entry_handler = lambda d, n, md: d == open_date and (handled.append((d, n, md)) or True)
    runner.run(other)
    runner.run(open_date)
    assert runner.is_busy()
    assert wait_until(qapp, lambda: not runner.is_busy())

    text = journal.read(other)
    assert "fast for 2026-09-20" in text and "slow result" in text and "my text" in text
    assert sorted(markers.find_blocks(text)) == ["fast", "slow"]
    # the open date was never written to disk; the editor handler got its results
    assert not journal.exists(open_date)
    assert {(d, n) for d, n, _ in handled} == {(open_date, "slow"), (open_date, "fast")}
    assert all("2026-09-20" not in md for _d, _n, md in handled)


def test_slow_plugin_does_not_block_fast_and_stale_result_dropped(qapp, tmp_path):
    pm, journal = _setup(tmp_path)
    day = dt.date(2026, 9, 21)
    journal.write(day, "# Day\n")
    runner = PluginRunner(pm, lambda: journal)
    arrivals = []
    runner.result_applied.connect(lambda d, r: arrivals.append((r.name, r.markdown, time.time())))

    runner.run(day)                      # slow #1 sleeps 0.6 s
    assert wait_until(qapp, lambda: any(a[0] == "fast" for a in arrivals), 2)
    assert not any(a[0] == "slow" for a in arrivals)  # fast arrived while slow still running
    runner.run(day, [pm.plugins["slow"]])  # newer refresh supersedes slow #1
    assert wait_until(qapp, lambda: not runner.is_busy(), 5)
    runner.wait(2000)
    qapp.processEvents()

    text = journal.read(day)
    assert "slow result 2" in text and "slow result 1" not in text
    assert text.count("<!-- plugin:slow -->") == 1
    assert [a[1] for a in arrivals if a[0] == "slow"] == ["## Slow\n\nslow result 2"]


def test_main_window_keeps_unsaved_edits(qapp, tmp_path, monkeypatch):
    from daily_vibe.ui.main_window import MainWindow

    cfg = Config.load()
    cfg.journal_root = tmp_path / "J"
    cfg.data["plugins"]["enabled"] = ["fast"]
    pdir = tmp_path / "config" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "fast.py").write_text(FAST)
    win = MainWindow(cfg)
    win.autosave_timer.setInterval(60_000)  # keep edits unsaved during the test
    day = dt.date(2026, 9, 22)
    win.open_date(day, force=True)
    win.editor.setPlainText("# Day\n\nunsaved words\n")
    win.refresh_plugin_blocks()
    assert wait_until(qapp, lambda: not win.runner.is_busy())
    text = win.editor.toPlainText()
    assert "unsaved words" in text and "fast for 2026-09-22" in text
    # user switches day while a refresh for the old day is pending
    win.runner.run(day)
    win.open_date(dt.date(2026, 9, 23))   # saves day 22 first
    assert wait_until(qapp, lambda: not win.runner.is_busy())
    assert "unsaved words" in win.journal.read(day)
    assert win.journal.read(day).count("<!-- plugin:fast -->") == 1
    assert "fast" not in win.editor.toPlainText()
    win._dirty = False
    win.close()


def test_settings_dialog_apply(qapp, tmp_path, monkeypatch):
    from daily_vibe.theming import ThemeRegistry
    from daily_vibe.ui.settings_dialog import SettingsDialog

    cfg = Config.load()
    cfg.journal_root = tmp_path
    pm = PluginManager(cfg)
    dlg = SettingsDialog(cfg, pm, ThemeRegistry())
    applied = []
    dlg.applied.connect(lambda: applied.append(1))
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("dark"))
    dlg.dark_combo.setCurrentText("Nord")
    dlg.quality_slider.setValue(65)
    page = dlg.plugins_page
    page.set_enabled("weather", False)
    page.field("weather", "location").setText("39.7392,-104.9903")
    page.field("weather", "units").setCurrentText("metric")
    page.field("moon_phase", "hemisphere").setCurrentText("southern")
    dlg.autosave_spin.setValue(2.5)
    assert cfg.data["images"]["webp_quality"] == 80  # nothing applied yet
    assert dlg.apply()
    assert applied == [1]
    saved = Config.load(cfg.path).data
    assert saved["appearance"]["mode"] == "dark" and saved["appearance"]["dark_theme"] == "Nord"
    assert saved["images"]["webp_quality"] == 65
    assert saved["plugins"]["enabled"] == ["moon_phase"]
    assert saved["plugins"]["weather"]["location"] == "39.7392,-104.9903"
    assert saved["plugins"]["weather"]["units"] == "metric"
    assert saved["plugins"]["moon_phase"]["hemisphere"] == "southern"
    assert saved["autosave_ms"] == 2500
    # Cancel after editing leaves config untouched
    dlg2 = SettingsDialog(cfg, pm, ThemeRegistry())
    dlg2.quality_slider.setValue(20)
    dlg2.reject()
    assert Config.load(cfg.path).data["images"]["webp_quality"] == 65

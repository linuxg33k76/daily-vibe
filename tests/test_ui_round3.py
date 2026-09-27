import datetime as dt

from conftest import wait_until
from daily_vibe import security
from daily_vibe.config import Config

FAST = "ID = 'fast'\nNAME = 'Fast'\nTITLE = 'Fast'\ndef render(date, context):\n    return 'fast for ' + date.isoformat()\n"


def _window(tmp_path, password=None, enabled=()):
    from daily_vibe.ui.main_window import MainWindow
    cfg = Config.load()
    cfg.journal_root = tmp_path / "J"
    cfg.data["plugins"]["enabled"] = list(enabled)
    if password:
        cfg.data["security"]["password_hash"] = security.hash_password(password)
    cfg.save()
    pdir = tmp_path / "config" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "fast.py").write_text(FAST)
    return MainWindow(cfg)


def test_title_on_this_day_and_streak(qapp, tmp_path):
    from daily_vibe.storage import Journal
    today = dt.date.today()
    j = Journal(tmp_path / "J" / "Personal")
    j.write(today.replace(year=today.year - 1) if not (today.month == 2 and today.day == 29) else today - dt.timedelta(days=366),
            "# Last year\n\nA memory from last year\n")
    j.write(today - dt.timedelta(days=1), "# Yesterday\n\nwrote stuff\n")
    win = _window(tmp_path)
    win.open_date(today, force=True)
    assert win.windowTitle().startswith("The Daily Vibe")
    items = [win.on_this_day.list.item(i).text() for i in range(win.on_this_day.list.count())]
    assert any("1 year ago" in t and "memory" in t for t in items)
    assert "write today to make it 2" in win.streak_label.text()
    win.editor.setPlainText("# Today\n\nI wrote today\n")
    win.save_current()
    assert "2-day streak — keep it going" in win.streak_label.text()
    win.config.data["show_streak"] = False
    win.update_streak()
    assert win.streak_label.isHidden()
    win.close()


def test_lock_hides_content_and_unlock(qapp, tmp_path):
    from daily_vibe.storage import Journal
    today = dt.date.today()
    Journal(tmp_path / "J" / "Personal").write(today, "# Secret\n\nvery private words\n")
    win = _window(tmp_path, password="hunter22", enabled=["fast"])
    win.open_today(create=False)
    # locked at startup: no content loaded anywhere
    assert win.locked and win.stack.currentWidget() is win.lock_screen
    assert win.editor.toPlainText() == "" and win.entry_list.count() == 0 and win.on_this_day.list.count() == 0
    assert not win.menuBar().isEnabled() and win.streak_label.isHidden()
    assert not win.try_unlock("wrong")
    assert win.locked
    assert win.try_unlock("hunter22")
    assert not win.locked and "very private words" in win.editor.toPlainText()
    # unsaved edit, then lock: saved first, then everything cleared
    win.editor.setPlainText(win.editor.toPlainText() + "more\n")
    win.lock()
    assert "more" in Journal(tmp_path / "J" / "Personal").read(today)
    assert win.editor.toPlainText() == "" and win.preview.toPlainText() == ""
    # plugin results arriving while locked go to the file, not the (hidden) editor
    win.runner.run(today)
    assert wait_until(qapp, lambda: not win.runner.is_busy())
    assert win.editor.toPlainText() == ""
    assert "fast for" in Journal(tmp_path / "J" / "Personal").read(today)
    assert win.try_unlock("hunter22") and "fast for" in win.editor.toPlainText()
    win.close()


def test_limiter_blocks_rapid_guessing(qapp, tmp_path):
    win = _window(tmp_path, password="pw1234")
    for _ in range(3):
        assert not win.try_unlock("nope")
    assert not win.limiter.can_try()
    assert not win.try_unlock("pw1234")  # even the right password waits out the delay
    assert win.locked
    win.limiter.locked_until = 0
    assert win.try_unlock("pw1234")
    win.close()


def test_relocation_flow_move(qapp, tmp_path, monkeypatch):
    from daily_vibe.storage import Journal
    import daily_vibe.ui.main_window as mw
    today = dt.date.today()
    Journal(tmp_path / "J" / "Personal").write(today, "# moving day\n")
    win = _window(tmp_path)
    win.open_date(today, force=True)
    win.editor.setPlainText("# moving day\n\nunsaved line\n")
    monkeypatch.setattr(mw, "ask_relocation", lambda parent, s, t: "move")
    target = tmp_path / "NewJ"
    assert win.change_root_to(target) == "pending"
    assert wait_until(qapp, lambda: win._transfer_job is None, 10)
    # round 4: the whole library moves; the same journal stays open
    assert win.library.root == target and win.config.journal_root == target
    assert win.journal.root == target / "Personal"
    assert "unsaved line" in Journal(target / "Personal").read(today)       # saved before moving
    assert not (tmp_path / "J").exists()                       # empty source removed
    assert "Moved 1 file" in win._last_summary_box.text()
    win.close()


def test_security_tab_set_change_remove(qapp, tmp_path):
    from daily_vibe.plugin_manager import PluginManager
    from daily_vibe.theming import ThemeRegistry
    from daily_vibe.ui.settings_dialog import SettingsDialog
    cfg = Config.load()
    cfg.journal_root = tmp_path
    dlg = SettingsDialog(cfg, PluginManager(cfg), ThemeRegistry())
    dlg.limiter = security.AttemptLimiter()
    dlg.sec_new.setText("abcd1234"); dlg.sec_confirm.setText("mismatch")
    dlg._set_password()
    assert not cfg.data["security"]["password_hash"] and "match" in dlg.sec_message.text()
    dlg.sec_confirm.setText("abcd1234")
    dlg._set_password()
    stored = Config.load(cfg.path).data["security"]["password_hash"]
    assert security.verify_password("abcd1234", stored) and "abcd1234" not in cfg.path.read_text()
    # change requires the current password
    dlg.sec_current.setText("wrong"); dlg.sec_new.setText("newpass1"); dlg.sec_confirm.setText("newpass1")
    dlg._set_password()
    assert security.verify_password("abcd1234", Config.load(cfg.path).data["security"]["password_hash"])
    dlg.sec_current.setText("abcd1234"); dlg.sec_new.setText("newpass1"); dlg.sec_confirm.setText("newpass1")
    dlg._set_password()
    assert security.verify_password("newpass1", Config.load(cfg.path).data["security"]["password_hash"])
    # auto-lock via Apply, and Apply keeps the hash
    dlg.autolock_spin.setValue(5)
    assert dlg.apply()
    saved = Config.load(cfg.path).data["security"]
    assert saved["auto_lock_minutes"] == 5 and security.verify_password("newpass1", saved["password_hash"])
    dlg.sec_current.setText("newpass1")
    dlg._remove_password()
    assert Config.load(cfg.path).data["security"]["password_hash"] == ""

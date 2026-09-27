import datetime as dt
import importlib.util
import shutil
import types
from pathlib import Path

from daily_vibe.config import Config
from daily_vibe.plugin_manager import PluginManager, SettingField
from daily_vibe.storage import Journal

ROOT = Path(__file__).resolve().parent.parent

FULL = '''
ID = "demo"
NAME = "Demo Plugin"
TITLE = "Demo"
VERSION = "2.1.0"
DESCRIPTION = "demo desc"
AUTHOR = "Ben"
API_VERSION = 1
SETTINGS = [
    {"key": "greeting", "label": "Greeting", "type": "string", "default": "hi"},
    {"key": "times", "label": "Times", "type": "int", "default": 2, "min": 1, "max": 5},
    {"key": "loud", "label": "Loud", "type": "bool", "default": False},
    {"key": "mode", "label": "Mode", "type": "choice", "choices": ["a", "b"], "default": "a"},
    {"key": "token", "label": "Token", "type": "secret"},
]
loaded = []
created = []
def on_load(context):
    loaded.append(context["settings"]["greeting"])
def on_entry_created(date, context):
    created.append(date)
def render(date, context):
    s = context["settings"]
    out = " ".join([s["greeting"]] * s["times"])
    return (out.upper() if s["loud"] else out) + " " + s["mode"] + " token=" + s["token"]
'''


def _pm(tmp_path, files: dict, enabled, extra_cfg=None, **kw):
    d = tmp_path / "plugins"
    d.mkdir(exist_ok=True)
    for name, src in files.items():
        (d / name).write_text(src)
    cfg = Config({"plugins": {"enabled": enabled, **(extra_cfg or {})}}, tmp_path / "c.toml")
    return PluginManager(cfg, extra_dirs=[d], **kw), Journal(tmp_path / "J")


def test_metadata_settings_and_hooks(tmp_path):
    pm, j = _pm(tmp_path, {"demo.py": FULL}, ["demo"],
                {"demo": {"greeting": "yo", "times": "3", "loud": True, "mode": "zzz", "token": "abc"}})
    p = pm.plugins["demo"]
    assert (p.display_name, p.version, p.author, p.source, p.title) == ("Demo Plugin", "2.1.0", "Ben", "user", "Demo")
    assert [f.type for f in p.settings] == ["string", "int", "bool", "choice", "secret"]
    assert p.module.loaded == ["yo"]  # on_load got merged settings
    s = pm.settings_for(p)
    assert s == {"greeting": "yo", "times": 3, "loud": True, "mode": "a", "token": "abc"}  # coerced, bad choice -> default
    r = pm.run_plugin(p, dt.date(2026, 9, 26), j)
    assert r.ok and r.markdown == "## Demo\n\nYO YO YO a token=abc"
    assert pm.run_entry_created_hooks(dt.date(2026, 9, 26), j) == []
    assert p.module.created == [dt.date(2026, 9, 26)]


def test_legacy_plugin_and_errors(tmp_path):
    files = {
        "legacy.py": "NAME = 'legacy'\nTITLE = 'Old'\ndef render(date, context):\n    return context['config'].get('x', 'none')\n",
        "badschema.py": "ID='bs'\nSETTINGS=[{'key':'k','type':'color'}]\ndef render(d, c):\n    return ''\n",
        "future.py": "ID='fut'\nAPI_VERSION=99\ndef render(d, c):\n    return ''\n",
        "warn.py": "ID='warn'\ndef on_load(c):\n    raise RuntimeError('nope')\ndef render(d, c):\n    return 'still works'\n",
    }
    pm, j = _pm(tmp_path, files, ["legacy", "warn"], {"legacy": {"x": "42"}})
    assert pm.plugins["legacy"].display_name == "Old" and pm.plugins["legacy"].id == "legacy"
    assert pm.run_plugin(pm.plugins["legacy"], dt.date(2026, 1, 1), j).markdown.endswith("42")
    errors = {r.id: r for r in pm.records if r.status == "error"}
    assert "unknown type" in errors["badschema"].error and "API_VERSION 99" in errors["future"].error
    assert "bs" not in pm.plugins and "fut" not in pm.plugins
    assert pm.plugins["warn"].status == "warning" and "nope" in pm.plugins["warn"].error
    assert pm.run_plugin(pm.plugins["warn"], dt.date(2026, 1, 1), j).ok


def test_entry_point_plugins(tmp_path, monkeypatch):
    mod = types.ModuleType("pkg_plugin")
    exec("ID='pkg'\nNAME='Packaged'\nVERSION='0.9'\ndef render(d, c):\n    return 'from package'\n", mod.__dict__)

    class EP:
        name = "pkg"
        def load(self):
            return mod

    class BadEP:
        name = "broken_pkg"
        def load(self):
            raise ImportError("missing dependency")

    import daily_vibe.plugin_manager as pmod
    monkeypatch.setattr(pmod, "_entry_points", lambda: [EP(), BadEP()])
    pm, j = _pm(tmp_path, {}, ["pkg"])
    assert pm.plugins["pkg"].source == "package" and pm.plugins["pkg"].version == "0.9"
    assert any(r.id == "broken_pkg" and r.status == "error" for r in pm.records)
    assert pm.run_all(dt.date(2026, 1, 1), j)[0].markdown.endswith("from package")


def test_user_plugin_overrides_builtin(tmp_path):
    pm, _ = _pm(tmp_path, {"mymoon.py": "ID='moon_phase'\nNAME='My Moon'\ndef render(d, c):\n    return 'custom'\n"}, [])
    assert pm.plugins["moon_phase"].display_name == "My Moon" and pm.plugins["moon_phase"].source == "user"


def test_secret_falls_back_to_config_without_keyring(tmp_path, monkeypatch):
    from daily_vibe import secrets
    monkeypatch.setattr(secrets, "_keyring", lambda: None)
    section = {}
    assert secrets.set_secret("demo", "token", "s3cret", section) == "config"
    assert section == {"token": "s3cret"} and secrets.get_secret("demo", "token", section) == "s3cret"


def test_example_template_plugin_works_offline(tmp_path):
    pm, j = _pm(tmp_path, {}, ["quote_of_the_day"])
    shutil.copy(ROOT / "examples" / "plugin_template.py", tmp_path / "plugins" / "quote.py")
    pm.discover()
    p = pm.plugins["quote_of_the_day"]
    assert p.status == "ok" and {f.key for f in p.settings} == {"style", "show_author", "custom_file"}
    r1 = pm.run_plugin(p, dt.date(2026, 9, 26), j)
    r2 = pm.run_plugin(p, dt.date(2026, 9, 26), j)
    assert r1.ok and r1.markdown == r2.markdown and r1.markdown.startswith("## Quote\n\n> ")
    pm.config.data["plugins"]["quote_of_the_day"] = {"style": "italic", "show_author": False}
    assert pm.run_plugin(p, dt.date(2026, 9, 26), j).markdown.startswith("## Quote\n\n*")


def test_builtin_schemas():
    from daily_vibe.plugins import moon_phase, weather  # type: ignore
    keys = {d["key"] for d in weather.SETTINGS}
    assert keys == {"units", "location", "contact"} and callable(weather.SETTINGS[1]["check"])
    assert SettingField.from_dict(moon_phase.SETTINGS[1]).choices == ["northern", "southern"]
    assert "🌘" in moon_phase.render(dt.date(2024, 1, 14), {"settings": {"hemisphere": "southern"}})

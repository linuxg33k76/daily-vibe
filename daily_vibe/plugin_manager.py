"""Plugin discovery, metadata, settings and execution (plugin API v1).

A plugin is a Python module (a single file, or an installed package exposed via
the ``daily_vibe.plugins`` entry-point group) defining:

    ID = "quote"                 # unique, stable identifier (config + markers)
    NAME = "Quote of the Day"    # display name
    VERSION = "1.0.0"
    DESCRIPTION = "..."
    AUTHOR = "You"
    API_VERSION = 1
    TITLE = "Quote"              # optional: block header "## Quote"; None = no header
    SETTINGS = [                 # optional: rendered automatically in Preferences
        {"key": "style", "label": "Style", "type": "choice",
         "choices": ["blockquote", "italic"], "default": "blockquote", "help": "..."},
    ]
    def render(date, context) -> str: ...            # required
    def on_load(context) -> None: ...                # optional, after discovery
    def on_entry_created(date, context) -> None: ... # optional, after a new entry is written

Legacy plugins (only NAME + render) still work: NAME is then used as the ID.
See docs/PLUGINS.md for the full reference.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from daily_vibe import markers, secrets
from daily_vibe.config import Config, config_dir, user_plugins_dir

log = logging.getLogger(__name__)
BUILTIN_DIR = Path(__file__).parent / "plugins"
ENTRY_POINT_GROUP = "daily_vibe.plugins"
API_VERSION = 1
SUPPORTED_API_VERSIONS = {1}
FIELD_TYPES = {"string", "int", "float", "bool", "choice", "secret", "action"}
VALUE_TYPES = FIELD_TYPES - {"action"}  # "action" rows hold buttons, no value
SOURCE_ORDER = ("builtin", "package", "user")


@dataclass
class SettingField:
    key: str
    label: str
    type: str = "string"
    default: Any = None
    help: str = ""
    choices: list = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    # Optional: callable(value) -> str, shown as a button (e.g. "Look up")
    check: Callable[[Any], str] | None = None
    check_label: str = "Check"
    # type "action": buttons [{"label", "run": callable(values: dict) -> str}] and an
    # optional status callable(values) -> str shown next to them (e.g. "Connected as …")
    actions: list = field(default_factory=list)
    status: Callable[[dict], str] | None = None

    @property
    def has_value(self) -> bool:
        return self.type != "action"

    @classmethod
    def from_dict(cls, d: dict) -> "SettingField":
        if "key" not in d:
            raise ValueError("setting field needs a 'key'")
        ftype = d.get("type", "string")
        if ftype not in FIELD_TYPES:
            raise ValueError(f"setting {d['key']!r}: unknown type {ftype!r}")
        if ftype == "choice" and not d.get("choices"):
            raise ValueError(f"setting {d['key']!r}: choice needs 'choices'")
        actions = list(d.get("actions") or [])
        if ftype == "action":
            if not actions or not all(isinstance(a, dict) and a.get("label") and callable(a.get("run"))
                                      for a in actions):
                raise ValueError(f"setting {d['key']!r}: action needs 'actions' [{{'label', 'run'}}]")
        default = d.get("default")
        if default is None:
            default = {"string": "", "secret": "", "int": 0, "float": 0.0, "bool": False,
                       "choice": (d.get("choices") or [""])[0], "action": ""}[ftype]
        return cls(key=d["key"], label=d.get("label", d["key"]), type=ftype, default=default,
                   help=d.get("help", ""), choices=list(d.get("choices", [])), min=d.get("min"),
                   max=d.get("max"), check=d.get("check"), check_label=d.get("check_label", "Check"),
                   actions=actions, status=d.get("status"))

    def coerce(self, value):
        try:
            if self.type == "int":
                return int(value)
            if self.type == "float":
                return float(value)
            if self.type == "bool":
                return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            if self.type == "choice":
                return value if value in self.choices else self.default
            return "" if value is None else str(value)
        except (TypeError, ValueError):
            return self.default


@dataclass
class Plugin:
    id: str
    display_name: str
    title: str | None
    description: str
    module: ModuleType | None
    path: Path | None
    source: str  # "builtin" | "user" | "package"
    version: str = "0"
    author: str = ""
    api_version: int = API_VERSION
    settings: list[SettingField] = field(default_factory=list)
    status: str = "ok"  # "ok" | "error" | "warning"
    error: str | None = None
    implemented: bool = True

    @property
    def name(self) -> str:  # legacy alias used throughout: the plugin id
        return self.id

    @property
    def builtin(self) -> bool:
        return self.source == "builtin"


@dataclass
class PluginResult:
    name: str
    markdown: str  # full block body including header
    ok: bool
    error: str | None = None


def _load_file_module(path: Path, prefix: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{prefix}_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _entry_points():
    from importlib.metadata import entry_points
    return list(entry_points(group=ENTRY_POINT_GROUP))


def plugin_from_module(module, path: Path | None, source: str) -> Plugin:
    pid = getattr(module, "ID", None) or getattr(module, "NAME", None)
    if not pid or not isinstance(pid, str):
        raise ValueError("plugin must define ID (or legacy NAME)")
    if not callable(getattr(module, "render", None)):
        raise ValueError("plugin must define render(date, context)")
    api = int(getattr(module, "API_VERSION", API_VERSION))
    if api not in SUPPORTED_API_VERSIONS:
        raise ValueError(f"plugin API_VERSION {api} not supported (app supports {sorted(SUPPORTED_API_VERSIONS)})")
    has_id = hasattr(module, "ID")
    display = getattr(module, "NAME", pid) if has_id else getattr(module, "TITLE", None) or pid
    display = display or pid.replace("_", " ").title()
    title = getattr(module, "TITLE", display)
    doc_line = (module.__doc__ or "").strip().split("\n")[0]
    fields = [SettingField.from_dict(d) for d in getattr(module, "SETTINGS", []) or []]
    return Plugin(
        id=pid, display_name=display, title=title,
        description=getattr(module, "DESCRIPTION", "") or doc_line,
        module=module, path=path, source=source,
        version=str(getattr(module, "VERSION", "0")), author=str(getattr(module, "AUTHOR", "")),
        api_version=api, settings=fields,
        implemented=getattr(module, "IMPLEMENTED", True) is not False,
    )


class PluginManager:
    def __init__(self, config: Config, extra_dirs: list[Path] | None = None, use_entry_points: bool = True):
        self.config = config
        self.extra_dirs = list(extra_dirs or [])
        self.use_entry_points = use_entry_points
        self.plugins: dict[str, Plugin] = {}   # loaded, usable plugins by id
        self.records: list[Plugin] = []        # everything found, including failures
        self.load_errors: dict[str, str] = {}  # location -> error
        self.discover()

    # Discovery ------------------------------------------------------------------
    def _file_candidates(self):
        dirs = [(BUILTIN_DIR, "builtin"), (user_plugins_dir(), "user")] + [(d, "user") for d in self.extra_dirs]
        for directory, source in dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.py")):
                if not path.name.startswith("_"):
                    yield source, path

    def discover(self) -> None:
        self.plugins.clear()
        self.records.clear()
        self.load_errors.clear()
        found: list[Plugin] = []

        for source, path in self._file_candidates():
            try:
                module = _load_file_module(path, f"dvplugin_{source}")
                found.append(plugin_from_module(module, path, source))
            except Exception as exc:  # a broken plugin must not break discovery
                self._record_failure(str(path), path.stem, "user" if source != "builtin" else source, path, exc)

        if self.use_entry_points:
            try:
                eps = _entry_points()
            except Exception as exc:
                eps = []
                log.warning("Entry point discovery failed: %s", exc)
            for ep in eps:
                try:
                    module = ep.load()
                    mpath = getattr(module, "__file__", None)
                    found.append(plugin_from_module(module, Path(mpath) if mpath else None, "package"))
                except Exception as exc:
                    self._record_failure(f"entry point {ep.name}", ep.name, "package", None, exc)

        # later sources override earlier ones: builtin < package < user
        found.sort(key=lambda p: SOURCE_ORDER.index(p.source))
        for plugin in found:
            self.plugins[plugin.id] = plugin
        self.records.extend(self.plugins.values())
        self.records.sort(key=lambda p: (p.status != "ok", p.display_name.lower()))
        for plugin in list(self.plugins.values()):
            self._call_on_load(plugin)

    def _record_failure(self, location: str, pid: str, source: str, path, exc: Exception) -> None:
        log.warning("Failed to load plugin %s: %s", location, exc)
        msg = f"{type(exc).__name__}: {exc}"
        self.load_errors[location] = msg
        self.records.append(Plugin(id=pid, display_name=pid, title=None, description="(failed to load)",
                                   module=None, path=path, source=source, status="error", error=msg))

    def _call_on_load(self, plugin: Plugin) -> None:
        hook = getattr(plugin.module, "on_load", None)
        if not callable(hook):
            return
        try:
            hook(self.base_context(plugin))
        except Exception as exc:
            plugin.status, plugin.error = "warning", f"on_load failed: {type(exc).__name__}: {exc}"
            log.warning("on_load of %s failed: %s", plugin.id, exc)

    # Settings -------------------------------------------------------------------
    def settings_for(self, plugin: Plugin, journal=None) -> dict:
        """Setting values: global [plugins.<id>] merged with the journal's
        [overrides.plugin_settings.<id>] (non-secret fields only)."""
        raw = dict(self.config.plugin_config(plugin.id))
        if journal is not None and hasattr(journal, "plugin_settings"):
            secret_keys = {f.key for f in plugin.settings if f.type == "secret"}
            raw.update({k: v for k, v in journal.plugin_settings(plugin.id).items() if k not in secret_keys})
        values = {}
        for f in plugin.settings:
            if not f.has_value:
                continue
            if f.type == "secret":
                values[f.key] = secrets.get_secret(plugin.id, f.key, raw)
            else:
                values[f.key] = f.coerce(raw.get(f.key, f.default))
        return values

    def base_context(self, plugin: Plugin, journal=None) -> dict:
        return {
            "plugin_id": plugin.id,
            "settings": self.settings_for(plugin, journal),
            "config": self.config.plugin_config(plugin.id),  # raw section (legacy)
            "app_config": self.config.data,
            "config_dir": config_dir(),
            "api_version": API_VERSION,
        }

    def _context(self, date: dt.date, plugin: Plugin, journal) -> dict:
        ctx = self.base_context(plugin, journal)
        ctx.update({
            "journal_root": journal.root,
            "journal_name": getattr(journal, "name", journal.root.name),
            "entry_path": journal.entry_path(date),
            "is_today": date == dt.date.today(),
        })
        return ctx

    # Running --------------------------------------------------------------------
    def enabled(self, journal=None) -> list[Plugin]:
        """Enabled plugins; a journal's [overrides] plugins list wins if present."""
        ids = self.config.enabled_plugins
        if journal is not None and hasattr(journal, "plugin_ids"):
            ids = journal.plugin_ids(ids)
        return [self.plugins[n] for n in ids if n in self.plugins]

    def run_plugin(self, plugin: Plugin, date: dt.date, journal) -> PluginResult:
        header = f"## {plugin.title}\n\n" if plugin.title else ""
        try:
            body = plugin.module.render(date, self._context(date, plugin, journal))
            if not isinstance(body, str):
                raise TypeError(f"render() returned {type(body).__name__}, expected str")
            return PluginResult(plugin.id, header + body.strip(), True)
        except Exception as exc:
            log.warning("Plugin %s failed:\n%s", plugin.id, traceback.format_exc())
            note = f"> ⚠️ Plugin `{plugin.id}` failed: {type(exc).__name__}: {exc}"
            return PluginResult(plugin.id, header + note, False, str(exc))

    def run_entry_created_hooks(self, date: dt.date, journal) -> list[str]:
        """Call on_entry_created on enabled plugins; returns error strings."""
        errors = []
        for plugin in self.enabled(journal):
            hook = getattr(plugin.module, "on_entry_created", None)
            if callable(hook):
                try:
                    hook(date, self._context(date, plugin, journal))
                except Exception as exc:
                    errors.append(f"{plugin.id}: {exc}")
                    log.warning("on_entry_created of %s failed: %s", plugin.id, exc)
        return errors

    def run_all(self, date: dt.date, journal) -> list[PluginResult]:
        return [self.run_plugin(p, date, journal) for p in self.enabled(journal)]

    def blocks_markdown(self, results: list[PluginResult]) -> str:
        return "\n\n".join(markers.wrap_block(r.name, r.markdown) for r in results)

    def refresh_text(self, text: str, date: dt.date, journal) -> tuple[str, list[PluginResult]]:
        """Re-run enabled plugins and replace/insert their blocks in `text`."""
        results = self.run_all(date, journal)
        for r in results:
            text = markers.replace_block(text, r.name, r.markdown)
        return text, results

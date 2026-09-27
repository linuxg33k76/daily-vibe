# Writing plugins for The Daily Vibe

A plugin adds a Markdown block to a day's entry: weather, the moon, a quote, a
word count… Plugins are plain Python modules. The quickest start is to copy
[`examples/plugin_template.py`](../examples/plugin_template.py) (a working,
offline "Quote of the Day").

## Where plugins come from

| Source | Location | Shown as |
|---|---|---|
| Built-in | `daily_vibe/plugins/*.py` | built-in |
| User | `<config dir>/plugins/*.py` (Linux: `~/.config/daily-vibe/plugins`, macOS: `~/Library/Application Support/daily-vibe/plugins`) | user |
| Installed package | any pip-installed distribution exposing the entry-point group `daily_vibe.plugins` | package |

If two plugins share an `ID`, the later source wins: built-in < package < user.
So you can override a built-in by dropping a file with the same `ID` in your
user folder. Files starting with `_` are ignored.

Preferences → Plugins shows every plugin found (name, version, source, status,
errors), the path of the user folder, **Open plugins folder** and
**Reload plugins** buttons, and a settings form for the selected plugin.

## The module API (API_VERSION = 1)

```python
ID = "quote_of_the_day"     # required*: unique, stable id (config key + block markers)
NAME = "Quote of the Day"   # display name
TITLE = "Quote"             # header of the inserted block ("## Quote"); None = no header
VERSION = "1.0.0"
DESCRIPTION = "One line shown in Preferences."
AUTHOR = "You"
API_VERSION = 1             # the app refuses plugins with an unsupported API version

SETTINGS = [...]            # optional, see below

def render(date, context) -> str: ...            # required
def on_load(context) -> None: ...                # optional
def on_entry_created(date, context) -> None: ... # optional
```

\* Legacy plugins that only define `NAME` + `render` keep working: `NAME` is then used as the ID.

### `render(date, context) -> str`

Return the Markdown *body* of your block for `date` (a `datetime.date`). The app
adds the `## TITLE` header and wraps it in markers:

```markdown
<!-- plugin:quote_of_the_day -->

## Quote

> Well begun is half done. — Aristotle

<!-- /plugin:quote_of_the_day -->
```

When the user runs **Refresh Plugin Blocks** only the text between your markers
is replaced. In the Live Preview editor (0.6+) the marker lines are hidden unless the
cursor is on them, and the block is drawn as a subtle band labeled with your plugin
id. Your Markdown (headings, lists, tags, images on their own line) is styled like
any other text. Rules:

* `render` runs on a **background thread**: don't touch Qt widgets; network calls are fine
  (use timeouts).
* Raise an exception to signal failure: the block shows `⚠️ Plugin … failed: <message>`
  and the other plugins are unaffected.
* Be deterministic for a given date if you can (the template's quote picks by date hash),
  so refreshing doesn't churn the text.

### `context`

| key | value |
|---|---|
| `settings` | dict of your `SETTINGS` values (defaults applied, types coerced, secrets resolved) |
| `config` | your raw `[plugins.<ID>]` table from config.toml |
| `app_config` | the whole config dict (read-only by convention) |
| `plugin_id` | your ID |
| `journal_root` | `pathlib.Path` of the journal folder the entry belongs to (`<library>/<Journal>`; since 0.4) |
| `journal_name` | display name of that journal (since 0.4) |
| `entry_path` | `Path` of the entry being rendered |
| `is_today` | `True` if `date` is today |
| `config_dir` | the app's config dir (e.g. for caches) |
| `api_version` | the app's plugin API version |

`on_load(context)` gets the same dict without the entry-specific keys.

### Hooks

* `on_load(context)` – called after discovery / **Reload plugins**. Raising marks the
  plugin with a *warning* (shown in Preferences) but it stays usable.
* `on_entry_created(date, context)` – called in the background after a new entry file
  was written from the template (enabled plugins only). Return value is ignored.

### Journals (0.4)

A journal's `.dailyvibe.toml` can override which plugins run for it
(`[overrides] plugins = ["weather"]`); otherwise the app-wide enabled list is used.
Since 0.5 a journal can also override your non-secret `SETTINGS` values
(`[overrides.plugin_settings.<ID>]`, edited in Journal → Journal Settings → Plugins);
`context["settings"]` already contains the merged values for the entry's journal.
Results are always written to the journal the run was started for, even if the
user switches journals while your plugin is still running. Images/assets for an
entry live in `<journal_root>/assets/YYYY/MM/` and are linked relatively from the
entry (`../../assets/YYYY/MM/x.webp`).

## Settings schema

`SETTINGS` is a list of dicts. Preferences renders a form automatically and saves
values under `[plugins.<ID>]` in config.toml.

| key | meaning |
|---|---|
| `key` | required; name in `context["settings"]` |
| `label` | form label |
| `type` | `string`, `int`, `float`, `bool`, `choice`, `secret`, `action` (0.8, buttons only, see below) |
| `default` | default value (type-appropriate default if omitted) |
| `help` | help text under the field |
| `choices` | list, required for `choice` |
| `min`, `max` | bounds for `int` / `float` |
| `check` | optional callable `check(value) -> str`, rendered as a button (runs in the background, result shown under the field). The weather plugin's **Look up** uses this. |
| `check_label` | button text (default "Check") |

```python
SETTINGS = [
    {"key": "style", "label": "Style", "type": "choice",
     "choices": ["blockquote", "italic", "plain"], "default": "blockquote"},
    {"key": "show_author", "label": "Show author", "type": "bool", "default": True},
    {"key": "max_items", "label": "Max items", "type": "int", "default": 5, "min": 1, "max": 20},
    {"key": "api_key", "label": "API key", "type": "secret", "help": "From your account page."},
]
```

### Action rows (0.8)

A field with `"type": "action"` stores no value. It renders a row of buttons, with a
status line under them. The X Bookmarks plugin uses one for **Connect X account** /
**Disconnect**:

```python
def account_status(values: dict) -> str: ...     # shown under the buttons (called on the GUI thread: keep it fast)
def connect(values: dict) -> str: ...            # runs in the background; return text is shown

SETTINGS = [
    {"key": "client_id", "label": "Client ID", "type": "string"},
    {"key": "account", "label": "Account", "type": "action", "status": account_status,
     "actions": [{"label": "Connect", "run": connect},
                 {"label": "Disconnect", "run": disconnect}]},
]
```

Each `run(values)` receives the form's **current, unsaved** values (secrets included),
runs on a background thread, and returns a message. Raising shows `⚠ <message>`.
After a run, the status line is refreshed. Action rows don't appear in
`context["settings"]` and are hidden in per-journal settings.

**Secrets** are stored in the OS keyring when the optional `keyring` package is
installed and has a working backend (`pip install 'daily-vibe[secrets]'`).
Otherwise they are stored in config.toml in plain text, and Preferences shows a
warning next to the field.

## Publishing a plugin as a package

```toml
# pyproject.toml of your plugin package
[project]
name = "daily-vibe-quotes"
version = "1.0.0"

[project.entry-points."daily_vibe.plugins"]
quotes = "daily_vibe_quotes.plugin"   # module that defines ID, render, ...
```

After `pip install daily-vibe-quotes` into the same environment, the plugin shows up
with source "package". The entry point may point at a module or at any object with
the same attributes.

## Testing a plugin

```python
import datetime as dt
from pathlib import Path
from daily_vibe.config import Config
from daily_vibe.plugin_manager import PluginManager
from daily_vibe.storage import Journal

pm = PluginManager(Config({"plugins": {"enabled": ["quote_of_the_day"]}}), extra_dirs=[Path("my_plugins")])
plugin = pm.plugins["quote_of_the_day"]
print(pm.run_plugin(plugin, dt.date.today(), Journal("/tmp/j")).markdown)
```

See `tests/test_plugin_api.py` for more examples.

"""Configuration: a TOML file in the platform user config dir.

Linux:  ~/.config/daily-vibe/config.toml
macOS:  ~/Library/Application Support/daily-vibe/config.toml

Set DAILY_VIBE_CONFIG_DIR to override the directory (JOURNAL_APP_CONFIG_DIR,
from when the app was called "journal-app", is still honored as a fallback).
On first run an existing journal-app config dir is copied (never deleted).
"""
from __future__ import annotations

import copy
import logging
import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_dir

APP_NAME = "daily-vibe"
LEGACY_APP_NAME = "journal-app"
ENV_VAR = "DAILY_VIBE_CONFIG_DIR"
LEGACY_ENV_VAR = "JOURNAL_APP_CONFIG_DIR"
log = logging.getLogger(__name__)

DEFAULT_TEMPLATE = """# {weekday}, {long_date}

{plugins}

## Notes

"""

DEFAULTS: dict[str, Any] = {
    # Library root: every sub-folder is a journal (<root>/<Journal>/YYYY/MM/...).
    "journal_root": "~/Journal",
    "last_journal": "",          # folder name of the last-opened journal
    "hidden_journals": [],       # "Remove from library" (folders stay on disk)
    "search_scope": "journal",   # "journal" | "all"
    "migration_declined": "",    # library root for which the user chose "Don't ask again"
    "autosave_ms": 1000,
    "show_preview": True,        # Source + Preview: preview pane shown (legacy toggle)
    # "live" (Live Preview editor) | "split" (Source + Preview) | "source" | "reading"
    "view_mode": "live",
    "recent_emoji": [],          # emoji picker "Recent" tab, newest first
    "emoji_max_version": 15.1,   # hide emoji newer than this (older fonts show boxes)
    # Tag-rename backups: keep the last N renames (0 = no limit) and/or drop
    # those older than N days (0 = no age limit). Cleaned up automatically.
    "tag_rename_keep": 20,
    "tag_rename_keep_days": 0,
    "template": DEFAULT_TEMPLATE,
    "show_on_this_day": True,
    "show_streak": True,
    "images": {"webp_quality": 80, "max_dimension": 1920},
    "security": {
        # App lock only - entries on disk stay plain Markdown.
        "password_hash": "",  # "scrypt$n$r$p$salt$hash"; delete to remove the lock
        "auto_lock_minutes": 0,  # 0 = never
    },
    "appearance": {
        "mode": "system",  # "light" | "dark" | "system"
        "light_theme": "Default Light",
        "dark_theme": "Catppuccin Mocha",
        "accent": "",  # "#rrggbb" overrides the theme accent; "" = theme default
        "editor_font_family": "",  # "" = system monospace
        "editor_font_size": 12,
        "live_font_family": "",    # Live Preview text font; "" = system UI font
        "live_font_size": 0,       # pt; 0 = editor font size + 1
        "live_line_spacing": 1.0,  # 1.0 - 2.5
        "live_heading_family": "",  # "" = same as the text font
        "live_code_family": "",    # "" = the editor (monospace) font
        "live_max_width": 0,       # px; >0 = centered reading column of at most this width
        "preview_font_size": 14,
    },
    "plugins": {
        # Order here is the order blocks are inserted into a new entry.
        "enabled": ["weather", "moon_phase"],
        "weather": {
            # "" = auto-detect from IP. Otherwise a city name ("Denver, CO")
            # geocoded with Open-Meteo, or "lat,lon" ("39.74,-104.99").
            "location": "",
            "units": "imperial",  # or "metric"
        },
    },
}


def config_dir() -> Path:
    for var in (ENV_VAR, LEGACY_ENV_VAR):
        override = os.environ.get(var)
        if override:
            return Path(override).expanduser()
    new = Path(user_config_dir(APP_NAME))
    migrate_legacy_config(new, Path(user_config_dir(LEGACY_APP_NAME)))
    return new


def migrate_legacy_config(new_dir: Path, old_dir: Path) -> bool:
    """Copy the old journal-app config dir to the new location once.
    Returns True if a copy happened. The old dir is left untouched."""
    if new_dir.exists() or not old_dir.is_dir():
        return False
    try:
        shutil.copytree(old_dir, new_dir)
        (new_dir / "MIGRATED_FROM.txt").write_text(f"Copied from {old_dir}\n", encoding="utf-8")
        log.info("Migrated config from %s to %s", old_dir, new_dir)
        return True
    except OSError as exc:
        log.warning("Config migration from %s failed: %s", old_dir, exc)
        return False


def config_path() -> Path:
    return config_dir() / "config.toml"


def user_plugins_dir() -> Path:
    return config_dir() / "plugins"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Thin wrapper around a nested dict with load/save."""

    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.path = path or config_path()
        self.data = _deep_merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        data: dict = {}
        if path.exists():
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        cfg = cls(data, path)
        if not path.exists():
            cfg.save()  # write defaults so the user has something to edit
        return cfg

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as fh:
            tomli_w.dump(self.data, fh, multiline_strings=True)

    # Convenience accessors -------------------------------------------------
    @property
    def journal_root(self) -> Path:
        return Path(self.data["journal_root"]).expanduser()

    @journal_root.setter
    def journal_root(self, value: Path | str) -> None:
        self.data["journal_root"] = str(value)

    @property
    def enabled_plugins(self) -> list[str]:
        return list(self.data["plugins"].get("enabled", []))

    def set_plugin_enabled(self, name: str, enabled: bool) -> None:
        current = self.enabled_plugins
        if enabled and name not in current:
            current.append(name)
        elif not enabled and name in current:
            current.remove(name)
        self.data["plugins"]["enabled"] = current

    def plugin_config(self, name: str) -> dict:
        value = self.data["plugins"].get(name, {})
        return value if isinstance(value, dict) else {}

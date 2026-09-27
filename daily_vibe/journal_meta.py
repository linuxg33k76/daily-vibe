"""Per-journal metadata: <journal>/.dailyvibe.toml (travels with the folder).

    name = "Work"            # display name (defaults to the folder name)
    color = "#89b4fa"        # shown in the journal picker
    icon = "💼"              # optional emoji
    [overrides]              # optional; omitted keys use the app settings
    template = "..."         # new-entry template for this journal
    plugins = ["weather"]    # enabled plugins for this journal (order = block order)
    [overrides.plugin_settings.weather]   # per-journal plugin settings (non-secret)
    location = "Seattle, WA"

Edits made by the app go through tomlkit when it's installed, so comments,
key order and keys the app doesn't know about are preserved. Without tomlkit
the file is rewritten with tomli-w (unknown keys kept, comments lost).

Legacy keys: files written by hand (or following pre-0.8.1 docs) may use British
spellings (see LEGACY_KEYS). They are read as a fallback when the US key is
absent, and renamed to the US key the next time the app saves the file.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w

try:  # optional: comment/format-preserving edits
    import tomlkit  # type: ignore
except ImportError:  # pragma: no cover
    tomlkit = None

META_FILE = ".dailyvibe.toml"
HEADER = "# The Daily Vibe journal settings (optional; safe to edit)\n"
DELETE = object()  # sentinel for update_meta: remove the key
LEGACY_KEYS = {"colour": "color"}  # legacy-spelling  (old key -> current key)


def meta_path(journal_dir: Path) -> Path:
    return Path(journal_dir) / META_FILE


def load_meta(journal_dir: Path) -> dict:
    path = meta_path(journal_dir)
    if not path.is_file():
        return {}
    try:
        with open(path, "rb") as fh:
            return normalize_keys(tomllib.load(fh))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def normalize_keys(meta: dict) -> dict:
    """Map legacy top-level keys to their current names (the current key wins
    if both are present); the legacy keys are dropped from the result."""
    for old, new in LEGACY_KEYS.items():
        if old in meta:
            value = meta.pop(old)
            meta.setdefault(new, value)
    return meta


def _migrate_legacy_keys(doc) -> None:
    """In-place rename of legacy keys in a tomlkit document (keeps comments)."""
    for old, new in LEGACY_KEYS.items():
        if old in doc:
            if new not in doc:
                doc[new] = doc[old]
            del doc[old]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_meta(journal_dir: Path, meta: dict) -> None:
    """Write a fresh file (used when creating journals)."""
    clean = {k: v for k, v in normalize_keys(dict(meta)).items() if v not in (None, "", {}, [])}
    if isinstance(clean.get("overrides"), dict):
        clean["overrides"] = {k: v for k, v in clean["overrides"].items() if v is not None}
        if not clean["overrides"]:
            clean.pop("overrides")
    _atomic_write(meta_path(journal_dir), HEADER.encode() + tomli_w.dumps(clean, multiline_strings=True).encode())


def _apply_changes(doc, changes: dict, kit: bool = False) -> None:
    """Recursively apply `changes` to a dict-like doc. DELETE removes a key;
    dict values are merged into sub-tables (created if missing); empty tables
    left behind by deletions are removed."""
    for key, value in changes.items():
        if value is DELETE:
            if key in doc:
                del doc[key]
        elif isinstance(value, dict):
            if key not in doc or not hasattr(doc[key], "keys"):
                doc[key] = tomlkit.table() if kit else {}
            _apply_changes(doc[key], value, kit)
            if len(doc[key]) == 0:
                del doc[key]
        else:
            if isinstance(value, str) and "\n" in value and kit:
                value = tomlkit.string(value, multiline=True)
            doc[key] = value


def update_meta(journal_dir: Path, changes: dict) -> dict:
    """Merge `changes` into the journal's .dailyvibe.toml, preserving unknown keys
    (and, with tomlkit, comments and formatting). Returns the new metadata."""
    path = meta_path(journal_dir)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if tomlkit is not None:
        try:
            doc = tomlkit.parse(text) if text else tomlkit.document()
        except Exception:
            doc = None
        if doc is not None:
            if not text:
                doc.add(tomlkit.comment(HEADER[2:].strip()))
            _migrate_legacy_keys(doc)
            _apply_changes(doc, changes, kit=True)
            out = tomlkit.dumps(doc)
            while "\n\n\n" in out:
                out = out.replace("\n\n\n", "\n\n")
            _atomic_write(path, (out.rstrip("\n") + "\n").encode("utf-8"))
            return load_meta(journal_dir)
    meta = load_meta(journal_dir)
    _apply_changes(meta, changes)
    _atomic_write(path, HEADER.encode() + tomli_w.dumps(meta, multiline_strings=True).encode())
    return load_meta(journal_dir)


def preserves_comments() -> bool:
    return tomlkit is not None

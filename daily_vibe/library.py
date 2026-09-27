"""A library is a folder of journals: <root>/<Journal Name>/...

Any non-hidden sub-folder of the root is a journal, except 4-digit year folders
(old single-journal layout, see migrate.py) and backup folders.
"""
from __future__ import annotations

import re
import os
import shutil
from pathlib import Path

from daily_vibe.journal_meta import save_meta, update_meta
from daily_vibe.storage import Journal, SearchHit

YEAR_RE = re.compile(r"^\d{4}$")
INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
DEFAULT_JOURNAL = "Personal"


class LibraryError(ValueError):
    pass


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise LibraryError("Journal name can't be empty.")
    if name.startswith(".") or INVALID_NAME_RE.search(name) or name in (".", ".."):
        raise LibraryError('Journal names can\'t start with "." or contain / \\ : * ? " < > |')
    if YEAR_RE.match(name):
        raise LibraryError("A journal can't be named like a year (e.g. 2026).")
    if name.lower() == "assets":
        raise LibraryError('"assets" is reserved.')
    return name


class Library:
    def __init__(self, root: Path | str, hidden: list[str] | None = None):
        self.root = Path(root).expanduser()
        self.hidden = set(hidden or [])

    def journal_dirs(self, include_hidden: bool = False) -> list[Path]:
        if not self.root.is_dir():
            return []
        dirs = [p for p in self.root.iterdir()
                if p.is_dir() and not p.name.startswith(".") and not YEAR_RE.match(p.name)]
        if not include_hidden:
            dirs = [p for p in dirs if p.name not in self.hidden]
        return sorted(dirs, key=lambda p: p.name.lower())

    def journals(self, include_hidden: bool = False) -> list[Journal]:
        return [Journal(p) for p in self.journal_dirs(include_hidden)]

    def names(self) -> list[str]:
        return [p.name for p in self.journal_dirs()]

    def get(self, folder_name: str) -> Journal:
        return Journal(self.root / folder_name)

    def ensure_default(self) -> Journal:
        journals = self.journals()
        if journals:
            return journals[0]
        return self.create(DEFAULT_JOURNAL)

    def create(self, name: str, color: str = "", icon: str = "") -> Journal:
        name = validate_name(name)
        path = self.root / name
        if path.exists():
            raise LibraryError(f'A journal or folder named "{name}" already exists.')
        path.mkdir(parents=True)
        save_meta(path, {"name": name, "color": color, "icon": icon})
        return Journal(path)

    def rename(self, folder_name: str, new_name: str) -> Journal:
        new_name = validate_name(new_name)
        src, dst = self.root / folder_name, self.root / new_name
        if not src.is_dir():
            raise LibraryError(f'Journal "{folder_name}" not found.')
        if dst.exists() and src.resolve() != dst.resolve():
            # case-only renames on case-insensitive filesystems resolve to the same dir
            raise LibraryError(f'A journal or folder named "{new_name}" already exists.')
        if folder_name != new_name:
            tmp = self.root / f".rename-{new_name}"
            src.rename(tmp)  # two-step rename works for case-only changes too
            tmp.rename(dst)
        update_meta(dst, {"name": new_name})  # keeps other keys and comments
        return Journal(dst)

    def trash(self, folder_name: str) -> str:
        """Move a journal folder to the system trash via send2trash.
        Raises LibraryError if send2trash isn't installed (we never hard-delete)."""
        path = self.root / folder_name
        try:
            from send2trash import send2trash  # type: ignore
        except ImportError as exc:
            raise LibraryError(
                "Deleting needs the 'send2trash' package so the journal goes to the trash instead of "
                f"being erased (pip install send2trash). Or delete the folder yourself:\n{path}") from exc
        send2trash(str(path))
        return str(path)

    def export_zip(self, folder_name: str, out_path: Path, include_backups: bool = False) -> Path:
        """Zip <root>/<folder_name> (paths inside start with the folder name).
        Tag-rename backups (.dailyvibe/backups) are left out unless asked for."""
        import zipfile
        src = self.root / folder_name
        out = Path(out_path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        backups = src / ".dailyvibe" / "backups"
        tmp = out.with_name(out.name + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(src.rglob("*")):
                if not include_backups and (path == backups or backups in path.parents):
                    continue
                arc = path.relative_to(self.root).as_posix()
                if path.is_dir():
                    zf.write(path, arc + "/")
                elif path.is_file():
                    zf.write(path, arc)
        os.replace(tmp, out)
        return out

    def search(self, query: str, journals: list[Journal] | None = None, tag_index=None, limit: int = 300) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for journal in (journals if journals is not None else self.journals()):
            hits.extend(journal.search(query, limit=limit, tag_index=tag_index))
        hits.sort(key=lambda h: h.date, reverse=True)
        return hits[:limit]

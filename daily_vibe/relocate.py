"""Move or copy a journal to a new folder.

What is transferred (library move): every non-hidden folder in the old root,
i.e. all journals (<Journal>/YYYY/MM/..., <Journal>/assets/..., including their
.dailyvibe.toml) and, for not-yet-migrated libraries, old-layout year folders.
Loose files and hidden items (e.g. .migration-backup-*) at the top of the old
root are left where they are (and reported). With include_all=True (single
journal copy/move) everything inside the folder is transferred.

Conflicts: the target file is never overwritten. If a file with the same
relative path already exists in the target:
  * identical content (same SHA-256) -> counted as "already present"; on move
    the source copy is then removed (it is verifiably safe),
  * different content -> skipped and reported; the source file is kept.
We skip rather than rename because a renamed daily file (e.g. 2026-09-26 (1).md)
would no longer be recognized as that day's entry.

Every copied file is verified (size + SHA-256) before a move deletes the source.
Empty folders left behind by a move are removed; the old root itself is kept
if anything remains in it.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

YEAR_RE = re.compile(r"^\d{4}$")


class RelocateError(ValueError):
    pass


@dataclass
class TransferReport:
    mode: str
    source: Path
    target: Path
    transferred: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    left_behind: list[str] = field(default_factory=list)  # non-journal files not touched
    source_removed: bool = False
    canceled: bool = False

    # Backward-compatible alias for the pre-0.8.1 (British) attribute name.
    @property
    def cancelled(self) -> bool:  # legacy-spelling
        return self.canceled

    @cancelled.setter  # legacy-spelling
    def cancelled(self, value: bool) -> None:  # legacy-spelling
        self.canceled = value

    def summary(self) -> str:
        verb = "Moved" if self.mode == "move" else "Copied"
        lines = [f"{verb} {len(self.transferred)} file(s) from\n  {self.source}\nto\n  {self.target}"]
        if self.already_present:
            lines.append(f"{len(self.already_present)} already present in the target (identical).")
        if self.conflicts:
            lines.append(f"{len(self.conflicts)} conflict(s) skipped (target has a different file with the same name; "
                         "source kept):\n  " + "\n  ".join(self.conflicts[:10]) + ("\n  …" if len(self.conflicts) > 10 else ""))
        if self.errors:
            lines.append(f"{len(self.errors)} error(s):\n  " + "\n  ".join(self.errors[:10]))
        if self.left_behind:
            lines.append(f"{len(self.left_behind)} non-journal item(s) left in the old folder.")
        if self.mode == "move":
            lines.append("Old folder removed (it was empty)." if self.source_removed
                         else "Old folder kept (it still contains files).")
        if self.canceled:
            lines.append("Canceled before finishing.")
        return "\n\n".join(lines)


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def check_paths(source: Path, target: Path) -> None:
    src, dst = Path(source).expanduser().resolve(), Path(target).expanduser().resolve()
    if src == dst:
        raise RelocateError("The new folder is the same as the current one.")
    if dst.is_relative_to(src):
        raise RelocateError("The new folder is inside the current journal folder.")
    if src.is_relative_to(dst):
        raise RelocateError("The current journal folder is inside the new folder.")


def journal_files(source: Path, include_all: bool = False) -> tuple[list[Path], list[Path]]:
    """(files to transfer, top-level items left behind)."""
    files, other = [], []
    if not source.is_dir():
        return files, other
    for child in sorted(source.iterdir()):
        if include_all and child.is_file():
            files.append(child)
        elif child.is_dir() and (include_all or not child.name.startswith(".")):
            files.extend(p for p in sorted(child.rglob("*"))
                         if p.is_file() and not p.name.endswith((".md.tmp", ".dvpart")))
        else:
            other.append(child)
    return files, other


def transfer(source: Path, target: Path, mode: str = "move",
             progress: Callable[[int, int, str], None] | None = None,
             canceled: Callable[[], bool] | None = None, include_all: bool = False,
             cancelled: Callable[[], bool] | None = None) -> TransferReport:  # legacy-spelling
    """Copy or move journal files. `canceled` is polled between files (the
    pre-0.8.1 British-spelled keyword is still accepted as a deprecated alias)."""
    if canceled is None:
        canceled = cancelled  # legacy-spelling
    if mode not in ("move", "copy"):
        raise ValueError("mode must be 'move' or 'copy'")
    source, target = Path(source).expanduser().resolve(), Path(target).expanduser().resolve()
    check_paths(source, target)
    report = TransferReport(mode, source, target)
    files, other = journal_files(source, include_all)
    report.left_behind = [p.name for p in other]
    target.mkdir(parents=True, exist_ok=True)

    for i, src in enumerate(files, 1):
        if canceled and canceled():
            report.canceled = True
            break
        rel = src.relative_to(source)
        dst = target / rel
        if progress:
            progress(i, len(files), str(rel))
        try:
            if dst.exists():
                if dst.stat().st_size == src.stat().st_size and sha256(dst) == sha256(src):
                    report.already_present.append(str(rel))
                    if mode == "move":
                        src.unlink()
                else:
                    report.conflicts.append(str(rel))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".dvpart")
            shutil.copy2(src, tmp)
            if tmp.stat().st_size != src.stat().st_size or sha256(tmp) != sha256(src):
                tmp.unlink(missing_ok=True)
                raise OSError("verification failed after copy")
            if dst.exists():  # appeared meanwhile: never overwrite
                tmp.unlink()
                report.conflicts.append(str(rel))
                continue
            os.replace(tmp, dst)
            report.transferred.append(str(rel))
            if mode == "move":
                src.unlink()
        except OSError as exc:
            report.errors.append(f"{rel}: {exc}")

    if mode == "move":
        for d in sorted((p for p in source.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()  # only succeeds when empty
            except OSError:
                pass
        try:
            source.rmdir()
            report.source_removed = True
        except OSError:
            report.source_removed = False
    return report

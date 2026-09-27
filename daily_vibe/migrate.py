"""One-time migration from the single-journal layout (<=0.3) to a library.

Old:  <root>/YYYY/MM/YYYY-MM-DD.md        and  <root>/YYYY/MM/assets/x.webp
New:  <root>/Personal/YYYY/MM/YYYY-MM-DD.md and <root>/Personal/assets/YYYY/MM/x.webp
      (links rewritten: assets/x.webp -> ../../assets/YYYY/MM/x.webp)

Safety:
  * a full backup copy of the old year folders is made first in
    <root>/.migration-backup-<timestamp>/ and verified (SHA-256 per file);
  * nothing is overwritten: if the target exists with different content the
    item is skipped and reported (the source stays where it was);
  * every written file is verified before its source is deleted;
  * idempotent: once nothing old-layout remains, detect() returns False.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from daily_vibe.journal_meta import load_meta, meta_path, save_meta
from daily_vibe.relocate import sha256

YEAR_RE = re.compile(r"^\d{4}$")
MONTH_RE = re.compile(r"^\d{2}$")
ENTRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


@dataclass
class MigrationReport:
    journal: str
    backup: Path | None = None
    entries: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)
    links_rewritten: int = 0
    already_present: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f'Migrated into the journal "{self.journal}":',
                 f"  {len(self.entries)} entries, {len(self.assets)} images/assets, {len(self.other)} other file(s)",
                 f"  {self.links_rewritten} image link(s) rewritten to the new assets folder"]
        if self.backup:
            lines.append(f"Backup of the old layout:\n  {self.backup}")
        if self.already_present:
            lines.append(f"{len(self.already_present)} item(s) were already migrated (identical).")
        if self.conflicts:
            lines.append(f"{len(self.conflicts)} conflict(s) skipped (a different file already exists; the old file was left in place):\n  "
                         + "\n  ".join(self.conflicts[:10]))
        if self.errors:
            lines.append(f"{len(self.errors)} error(s):\n  " + "\n  ".join(self.errors[:10]))
        return "\n\n".join(lines)


def old_year_dirs(root: Path) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and YEAR_RE.match(p.name) and any(f.is_file() for f in p.rglob("*")))


def detect(root: Path) -> bool:
    return bool(old_year_dirs(root))


def plan_counts(root: Path) -> tuple[int, int]:
    entries = assets = 0
    for y in old_year_dirs(root):
        for p in y.rglob("*"):
            if p.is_file():
                if p.parent.name == "assets":
                    assets += 1
                elif p.suffix == ".md":
                    entries += 1
    return entries, assets


_LINK_RE = re.compile(r"(!?\[[^\]]*\]\()(\s*)(\./)?assets/([^)\s]+)")
_IMG_TAG_RE = re.compile(r'(<img[^>]*?src=["\'])(\./)?assets/([^"\']+)')


def rewrite_links(text: str, year: str, month: str) -> tuple[str, int]:
    new_prefix = f"../../assets/{year}/{month}/"
    count = 0

    def md(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}{m.group(2)}{new_prefix}{m.group(4)}"

    def tag(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}{new_prefix}{m.group(3)}"

    text = _LINK_RE.sub(md, text)
    text = _IMG_TAG_RE.sub(tag, text)
    return text, count


def _backup(root: Path, years: list[Path], report: MigrationReport) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / f".migration-backup-{stamp}"
    n = 1
    while backup.exists():
        n += 1
        backup = root / f".migration-backup-{stamp}-{n}"
    backup.mkdir()
    for y in years:
        shutil.copytree(y, backup / y.name, copy_function=shutil.copy2)
    for y in years:  # verify the backup before touching anything
        for src in y.rglob("*"):
            if src.is_file():
                dst = backup / src.relative_to(root)
                if not dst.is_file() or sha256(dst) != sha256(src):
                    raise OSError(f"backup verification failed for {src}")
    return backup


def _place(content: bytes, dst: Path, rel: str, report: MigrationReport, bucket: list) -> bool:
    """Write bytes to dst without overwriting; verify. True if the source can be removed."""
    if dst.exists():
        if dst.read_bytes() == content:
            report.already_present.append(rel)
            return True
        report.conflicts.append(rel)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".dvpart")
    tmp.write_bytes(content)
    if tmp.read_bytes() != content:
        tmp.unlink(missing_ok=True)
        raise OSError("verification failed")
    if dst.exists():
        tmp.unlink()
        report.conflicts.append(rel)
        return False
    os.replace(tmp, dst)
    bucket.append(rel)
    return True


def migrate(root: Path, journal_name: str = "Personal", backup: bool = True) -> MigrationReport:
    root = Path(root).expanduser()
    report = MigrationReport(journal_name)
    years = old_year_dirs(root)
    if not years:
        return report
    target = root / journal_name
    target.mkdir(parents=True, exist_ok=True)
    if not meta_path(target).exists():
        save_meta(target, {"name": journal_name})
    if backup:
        report.backup = _backup(root, years, report)

    for year_dir in years:
        for src in sorted(p for p in year_dir.rglob("*") if p.is_file()):
            rel_old = src.relative_to(root).as_posix()
            parts = src.relative_to(year_dir).parts
            try:
                if len(parts) >= 3 and MONTH_RE.match(parts[0]) and parts[1] == "assets":
                    rel_new = Path("assets", year_dir.name, parts[0], *parts[2:])
                    ok = _place(src.read_bytes(), target / rel_new, rel_old, report, report.assets)
                elif len(parts) == 2 and MONTH_RE.match(parts[0]) and ENTRY_RE.match(parts[1]):
                    text = src.read_text(encoding="utf-8")
                    new_text, n = rewrite_links(text, year_dir.name, parts[0])
                    ok = _place(new_text.encode("utf-8"), target / year_dir.name / parts[0] / parts[1],
                                rel_old, report, report.entries)
                    if ok and rel_old in report.entries:
                        report.links_rewritten += n
                else:
                    ok = _place(src.read_bytes(), target / src.relative_to(root), rel_old, report, report.other)
                if ok:
                    src.unlink()
            except OSError as exc:
                report.errors.append(f"{rel_old}: {exc}")
        for d in sorted((p for p in year_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            year_dir.rmdir()
        except OSError:
            pass
    return report

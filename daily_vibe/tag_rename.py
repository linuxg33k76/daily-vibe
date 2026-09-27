"""Rename a tag across entries (optionally including nested child tags).

Only real tags are rewritten, using the same rules as the tag parser
(tags.find_inline_tags / front matter). Everything else in a file is kept
byte-for-byte: files are read and written as bytes (UTF-8), so line endings
and formatting survive. `#run` never touches `#running`; `#run/5k` changes
only with nested=True.

Safety: before writing, the original bytes of every changed file are copied
to <journal>/.dailyvibe/backups/tag-rename-<stamp>/<YYYY/MM/file.md> together
with a manifest.json (SHA-256 of the old and new content). Files are written
atomically (temp file + os.replace), and a file that changed since the preview
was built is skipped. `undo()` restores the backup of a rename, but only for
files that still have exactly the content the rename wrote; others are
reported and left alone.

History (Journal → Tag Rename History…): every rename keeps its backup dir
and manifest, so any rename can be undone later, with one rule: a rename
can't be undone while a *newer, still-applied* rename touched any of the same
files — undo the newer one(s) first (``blockers()``). Undoing in reverse
order always restores cleanly; the per-file SHA check still protects files you
edited afterwards (they are skipped and reported, status "partially undone").
Retention: ``cleanup()`` keeps the last N renames and/or those newer than N days.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from daily_vibe import tags as tagmod

BACKUP_DIR = Path(".dailyvibe") / "backups"
PREFIX = "tag-rename-"

Mapper = Callable[[str], "str | None"]


class TagRenameError(ValueError):
    pass


def make_mapper(old: str, new: str, nested: bool = True) -> Mapper:
    """tag core (no '#') -> new core, or None if the tag isn't affected."""
    old_n = tagmod.normalize(old)
    new = new.strip().lstrip("#")

    def mapper(tag: str) -> str | None:
        n = tagmod.normalize(tag)
        if n == old_n:
            return new
        if nested and n.startswith(old_n + "/"):
            return new + tag[len(old_n):]
        return None
    return mapper


# Rewriting --------------------------------------------------------------------------
def _rewrite_inline(text: str, mapper: Mapper) -> tuple[str, int]:
    count = 0
    for start, end, tag in reversed(tagmod.find_inline_tags(text)):
        new = mapper(tag)
        if new is not None and new != tag:
            text = text[:start] + "#" + new + text[end:]
            count += 1
    return text, count


_ITEM_RE = re.compile(r"^(\s*)(['\"]?)(#?)(.*?)\2(\s*)$", re.S)


def _split_item(raw: str):
    """'  "#tag" ' -> (lead, quote, hash, core, trail)."""
    m = _ITEM_RE.match(raw)
    return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)


class _Dedupe:
    """Drops an item whose key was already seen, if either copy was renamed."""

    def __init__(self):
        self.seen: dict[str, bool] = {}  # key -> renamed?

    def keep(self, core: str, renamed: bool) -> bool:
        key = tagmod.normalize(core)
        if key in self.seen and (renamed or self.seen[key]):
            return False
        self.seen[key] = self.seen.get(key, False) or renamed
        return True


def _rewrite_list_value(value: str, mapper: Mapper, dd: _Dedupe) -> tuple[str, int]:
    """Inline list '[a, "b"]' or bare 'a, b' / 'a b' value."""
    count = 0
    if value.lstrip().startswith("["):
        lb, rb = value.index("["), value.rindex("]") if "]" in value else len(value)
        inner, head, tail = value[lb + 1:rb], value[:lb + 1], value[rb:]
        raws = inner.split(",")
        out: list[list[str]] = []
        for raw in raws:
            lead, q, h, core, trail = _split_item(raw)
            if not core.strip():
                out.append([raw])
                continue
            new = mapper(core)
            renamed = new is not None and new != core
            if not dd.keep(new if new is not None else core, renamed):
                count += renamed
                if not out:  # dropping the first item: next item inherits its lead
                    raws_lead = lead
                    out.append(["__DROP_FIRST__", raws_lead])
                continue
            if renamed:
                count += 1
                core = new
            if out and out[-1][0] == "__DROP_FIRST__":
                lead = out.pop()[1]
            out.append([f"{lead}{q}{h}{core}{q}{trail}"])
        items = [o[0] for o in out if o[0] != "__DROP_FIRST__"]
        return head + ",".join(items) + tail, count
    parts = re.split(r"([,\s]+)", value)
    out_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out_parts.append(part)
            continue
        if not part:
            out_parts.append(part)
            continue
        q = part[0] if part[0] in "'\"" and part.endswith(part[0]) and len(part) > 1 else ""
        body = part[1:-1] if q else part
        h = "#" if body.startswith("#") else ""
        core = body[len(h):]
        new = mapper(core)
        renamed = new is not None and new != core
        if not dd.keep(new if new is not None else core, renamed):
            count += renamed
            if out_parts:
                out_parts.pop()  # the separator before it
            else:
                parts[i + 1:i + 2] = [""] if i + 1 < len(parts) else []
            continue
        if renamed:
            count += 1
            core = new
        out_parts.append(f"{q}{h}{core}{q}")
    return "".join(out_parts), count


_KEY_RE = re.compile(r"^(\s*(?:tags|tag|keywords)\s*:[ \t]*)(.*?)([ \t]*)$", re.I)
_DASH_RE = re.compile(r"^(\s*-\s*)(.+?)([ \t]*)$")


def _rewrite_front_matter(text: str, mapper: Mapper) -> tuple[str, int]:
    m = tagmod.FRONT_RE.match(text)
    if not m:
        return text, 0
    fm_start, fm_end = m.start(1), m.end(1)
    fm = text[fm_start:fm_end]
    lines = fm.splitlines(keepends=True)
    out, count, i = [], 0, 0
    dd = _Dedupe()
    while i < len(lines):
        line = lines[i]
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        km = _KEY_RE.match(body)
        if not km:
            out.append(line)
            i += 1
            continue
        if km.group(2).strip():
            new_value, n = _rewrite_list_value(km.group(2), mapper, dd)
            out.append(km.group(1) + new_value + km.group(3) + eol)
            count += n
            i += 1
            continue
        out.append(line)
        i += 1
        while i < len(lines):  # YAML dash list
            item_line = lines[i]
            ib = item_line.rstrip("\r\n")
            dm = _DASH_RE.match(ib)
            if not dm:
                break
            lead, q, h, core, trail = _split_item(dm.group(2))
            new = mapper(core)
            renamed = new is not None and new != core
            if dd.keep(new if new is not None else core, renamed):
                if renamed:
                    count += 1
                    item_line = f"{dm.group(1)}{lead}{q}{h}{new}{q}{trail}{dm.group(3)}{item_line[len(ib):]}"
                out.append(item_line)
            else:
                count += renamed
            i += 1
        # (other keys continue in the outer loop)
    new_fm = "".join(out)
    if not fm.endswith("\n") and new_fm.endswith("\n"):  # dropped the last (EOL-less) line
        new_fm = new_fm[:-2] if new_fm.endswith("\r\n") else new_fm[:-1]
    if count == 0 and new_fm == fm:
        return text, 0
    return text[:fm_start] + new_fm + text[fm_end:], count


def rename_in_text(text: str, old: str, new: str, nested: bool = True) -> tuple[str, int]:
    """Returns (new text, number of tag occurrences renamed)."""
    mapper = make_mapper(old, new, nested)
    text, n1 = _rewrite_inline(text, mapper)          # body first (front matter is masked there)
    text, n2 = _rewrite_front_matter(text, mapper)    # offsets at the top are unaffected
    return text, n1 + n2


# Plan / apply / undo --------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file in the same folder + os.replace (atomic on POSIX/NTFS)."""
    tmp = path.with_name(f".{path.name}.dvtmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass
class FileChange:
    journal: object
    date: dt.date
    path: Path
    original: bytes
    new_text: str
    count: int
    samples: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RenamePlan:
    old: str
    new: str
    nested: bool
    changes: list[FileChange]
    merge_with: str = ""            # existing tag the rename merges into ("" = none)
    skipped: list[str] = field(default_factory=list)  # unreadable files
    scope: str = "journal"          # "journal" | "all" (recorded in the manifest)

    @property
    def total(self) -> int:
        return sum(c.count for c in self.changes)


def _samples(before: str, after: str, limit: int = 3) -> list[tuple[str, str]]:
    b, a = before.splitlines(), after.splitlines()
    out = []
    if len(a) == len(b):
        for x, y in zip(b, a):
            if x != y:
                out.append((x.strip(), y.strip()))
                if len(out) >= limit:
                    break
    else:  # a front matter item line was removed (merge)
        import difflib
        for line in difflib.unified_diff(b, a, lineterm="", n=0):
            if line.startswith(("-", "+")) and not line.startswith(("---", "+++")):
                out.append((line[1:].strip(), "") if line[0] == "-" else ("", line[1:].strip()))
                if len(out) >= limit:
                    break
    return out


def validate(old: str, new: str) -> tuple[str, str]:
    old, new = old.strip().lstrip("#"), new.strip().lstrip("#")
    if not old:
        raise TagRenameError("Choose the tag to rename.")
    if not tagmod.is_valid_tag(new):
        raise TagRenameError(f'"#{new}" isn\'t a valid tag: start with a letter, then letters, digits, '
                             "- or _, with / for nesting (and not a hex color like #fff).")
    if new == old:
        raise TagRenameError("The new tag is the same as the old one.")
    return old, new


def plan_rename(journals, old: str, new: str, nested: bool = True, scope: str = "journal") -> RenamePlan:
    old, new = validate(old, new)
    old_n, new_n = tagmod.normalize(old), tagmod.normalize(new)
    if nested and new_n.startswith(old_n + "/"):
        raise TagRenameError("Can't rename a tag into one of its own children with nested renaming on.")
    changes, skipped, merge_with = [], [], ""
    for journal in journals:
        for date in sorted(journal.list_dates()):
            path = journal.entry_path(date)
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                skipped.append(f"{path}: {exc}")
                continue
            if not merge_with and new_n != old_n:
                for t in tagmod.extract_tags(text):
                    if tagmod.normalize(t) == new_n:
                        merge_with = t
                        break
            new_text, count = rename_in_text(text, old, new, nested)
            if count and new_text != text:
                changes.append(FileChange(journal, date, path, raw, new_text, count, _samples(text, new_text)))
    return RenamePlan(old, new, nested, changes, merge_with, skipped, scope)


@dataclass
class RenameResult:
    stamp: str
    backups: list[Path] = field(default_factory=list)
    changed: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # changed since preview / errors
    occurrences: int = 0

    def summary(self) -> str:
        s = f"Renamed {self.occurrences} tag occurrence(s) in {len(self.changed)} entr{'y' if len(self.changed) == 1 else 'ies'}."
        if self.skipped:
            s += "\n\nSkipped:\n  " + "\n  ".join(self.skipped[:10])
        if self.backups:
            s += "\n\nBackups (for Undo Last Tag Rename):\n  " + "\n  ".join(str(b) for b in self.backups)
        return s


def apply_rename(plan: RenamePlan, stamp: str | None = None) -> RenameResult:
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    result = RenameResult(stamp)
    by_journal: dict[Path, list[FileChange]] = {}
    for c in plan.changes:
        by_journal.setdefault(c.journal.root, []).append(c)
    for root, changes in by_journal.items():
        backup = root / BACKUP_DIR / f"{PREFIX}{stamp}"
        manifest = {"old": plan.old, "new": plan.new, "nested": plan.nested, "scope": plan.scope,
                    "journals": sorted({c.journal.name for c in plan.changes}),
                    "created": dt.datetime.now().isoformat(timespec="seconds"), "undone": False, "files": {}}
        for c in changes:
            rel = c.path.relative_to(root).as_posix()
            try:
                if c.path.read_bytes() != c.original:
                    result.skipped.append(f"{rel}: changed since the preview; not modified")
                    continue
                new_bytes = c.new_text.encode("utf-8")
                dst = backup / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(c.original)
                if dst.read_bytes() != c.original:
                    raise OSError("backup verification failed")
                manifest["files"][rel] = {"sha_old": sha256_bytes(c.original), "sha_new": sha256_bytes(new_bytes)}
                atomic_write_bytes(c.path, new_bytes)
                result.changed.append(c.path)
                result.occurrences += c.count
            except OSError as exc:
                result.skipped.append(f"{rel}: {exc}")
        if manifest["files"]:
            (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            result.backups.append(backup)
    return result


def find_last_rename(journals) -> list[Path]:
    """Backup dirs of the most recent not-yet-undone rename (one per journal)."""
    candidates: dict[str, list[Path]] = {}
    for journal in journals:
        base = journal.root / BACKUP_DIR
        if not base.is_dir():
            continue
        for d in base.glob(f"{PREFIX}*"):
            mf = d / "manifest.json"
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not data.get("undone"):
                candidates.setdefault(d.name[len(PREFIX):], []).append(d)
    if not candidates:
        return []
    return candidates[max(candidates)]


def describe(backups: list[Path]) -> str:
    try:
        data = json.loads((backups[0] / "manifest.json").read_text(encoding="utf-8"))
        return f"#{data['old']} → #{data['new']}"
    except (OSError, ValueError, KeyError, IndexError):
        return ""


@dataclass
class UndoResult:
    restored: list[Path] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"Restored {len(self.restored)} entr{'y' if len(self.restored) == 1 else 'ies'}."
        if self.conflicts:
            s += ("\n\nNot restored (edited after the rename; the backup copy is still in the backups folder):\n  "
                  + "\n  ".join(self.conflicts[:10]))
        return s


def undo(backups: list[Path]) -> UndoResult:
    result = UndoResult()
    for backup in backups:
        mf = backup / "manifest.json"
        data = json.loads(mf.read_text(encoding="utf-8"))
        root = backup.parent.parent.parent  # <journal>/.dailyvibe/backups/<dir>
        for rel, info in data.get("files", {}).items():
            path, saved = root / rel, backup / rel
            try:
                current = path.read_bytes() if path.exists() else None
                if current is None or sha256_bytes(current) != info["sha_new"]:
                    result.conflicts.append(f"{root.name}/{rel}")
                    continue
                atomic_write_bytes(path, saved.read_bytes())
                result.restored.append(path)
            except OSError as exc:
                result.conflicts.append(f"{root.name}/{rel}: {exc}")
        data["undone"] = True
        data["undo_conflicts"] = result.conflicts
        mf.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return result


# History ----------------------------------------------------------------------------
APPLIED, UNDONE, PARTIAL = "applied", "undone", "partially undone"


@dataclass
class RenameRecord:
    stamp: str
    backups: list[Path]
    old: str
    new: str
    nested: bool
    scope: str
    created: dt.datetime | None
    files: set[tuple[str, str]]          # (journal root, relative path)
    journals: list[str]
    status: str
    conflicts: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"#{self.old} → #{self.new}"


def _stamp_time(stamp: str) -> dt.datetime | None:
    for fmt in ("%Y%m%d-%H%M%S-%f", "%Y%m%d-%H%M%S"):
        try:
            return dt.datetime.strptime(stamp, fmt)
        except ValueError:
            continue
    return None


def history(journals) -> list[RenameRecord]:
    """All renames with a backup in these journals, newest first."""
    groups: dict[str, list[tuple[Path, dict]]] = {}
    for journal in journals:
        base = journal.root / BACKUP_DIR
        if not base.is_dir():
            continue
        for d in base.glob(f"{PREFIX}*"):
            try:
                data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            groups.setdefault(d.name[len(PREFIX):], []).append((d, data))
    out = []
    for stamp, items in groups.items():
        first = items[0][1]
        files, conflicts, undone = set(), [], []
        for d, data in items:
            root = str(d.parent.parent.parent)
            files |= {(root, rel) for rel in data.get("files", {})}
            conflicts += data.get("undo_conflicts", []) if data.get("undone") else []
            undone.append(bool(data.get("undone")))
        if all(undone):
            status = PARTIAL if conflicts else UNDONE
        elif any(undone):
            status = PARTIAL
        else:
            status = APPLIED
        created = None
        try:
            created = dt.datetime.fromisoformat(first.get("created", ""))
        except (TypeError, ValueError):
            created = _stamp_time(stamp)
        journals_ = sorted({n for _d, data in items for n in data.get("journals", [])}
                           or {d.parent.parent.parent.name for d, _data in items})
        out.append(RenameRecord(stamp, [d for d, _ in items], first.get("old", "?"), first.get("new", "?"),
                                bool(first.get("nested", True)), first.get("scope", "journal"), created,
                                files, journals_, status, conflicts))
    out.sort(key=lambda r: r.stamp, reverse=True)
    return out


def blockers(records: list[RenameRecord], stamp: str) -> list[RenameRecord]:
    """Newer, still-applied renames that touched a file of rename `stamp`."""
    target = next((r for r in records if r.stamp == stamp), None)
    if target is None:
        return []
    return [r for r in records if r.stamp > stamp and r.status == APPLIED and r.files & target.files]


def undo_record(journals, stamp: str) -> UndoResult:
    """Undo one rename from the history (see the module docstring for the rule)."""
    records = history(journals)
    target = next((r for r in records if r.stamp == stamp), None)
    if target is None:
        raise TagRenameError("That rename's backup no longer exists.")
    if target.status != APPLIED:
        raise TagRenameError(f"{target.label} was already undone.")
    newer = blockers(records, stamp)
    if newer:
        raise TagRenameError(
            f"{target.label} can't be undone yet: newer rename(s) changed the same entries: "
            + ", ".join(r.label for r in newer) + ". Undo those first (newest first).")
    return undo([b for b in target.backups if not _is_undone(b)])


def _is_undone(backup: Path) -> bool:
    try:
        return bool(json.loads((backup / "manifest.json").read_text(encoding="utf-8")).get("undone"))
    except (OSError, ValueError):
        return True


def cleanup(journals, keep: int = 20, days: int = 0, now: dt.datetime | None = None) -> list[RenameRecord]:
    """Delete backups beyond the newest `keep` renames (0 = no count limit) and
    older than `days` days (0 = no age limit). Returns the removed records."""
    now = now or dt.datetime.now()
    removed = []
    for i, rec in enumerate(history(journals)):
        too_many = keep > 0 and i >= keep
        too_old = days > 0 and rec.created is not None and (now - rec.created) > dt.timedelta(days=days)
        if too_many or too_old:
            for d in rec.backups:
                shutil.rmtree(d, ignore_errors=True)
            removed.append(rec)
    return removed

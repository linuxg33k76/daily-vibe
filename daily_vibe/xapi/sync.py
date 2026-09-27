"""Per-journal bookmark tracking.

The bookmarks endpoint has no "bookmarked at" timestamp, so the plugin polls
and diffs: every post ID it has seen is stored in
``<journal>/.dailyvibe/x_bookmarks.json`` (per connected X account). Posts
not seen before are "new today" and are stored under today's date together with
their rendered data, so refreshing the block (or rendering a past day) never
re-fetches or loses anything.

Assumption: the API lists bookmarks newest-bookmark-first (observed behavior,
not formally documented). A sync walks pages until a page contains an already
seen ID, or ``max_pages`` is reached. If it stops before reaching a seen ID the
state is flagged (``gap_ids``) and the next sync looks past those posts.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from daily_vibe.images import convert_to_webp
from daily_vibe.xapi.client import RateLimited, XApiError, XClient
from daily_vibe.xapi.render import normalize_page

log = logging.getLogger(__name__)
STATE_DIR = ".dailyvibe"
STATE_FILE = "x_bookmarks.json"
FIRST_ASK, FIRST_NONE, FIRST_LAST_N = "Ask first", "Import none", "Import last N"
FIRST_RUN_CHOICES = [FIRST_ASK, FIRST_NONE, FIRST_LAST_N]
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def state_path(journal_root: Path) -> Path:
    return Path(journal_root) / STATE_DIR / STATE_FILE


def _lock_for(journal_root: Path) -> threading.Lock:
    key = str(Path(journal_root).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class SeenState:
    """JSON state: {"version": 1, "accounts": {user_id: {...}}}. Per account:
    initialized, seen (list of IDs), days {YYYY-MM-DD: [records]}, gap_ids, last_sync."""

    def __init__(self, journal_root: Path):
        self.path = state_path(journal_root)
        self.data = {"version": 1, "accounts": {}}
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.setdefault("accounts", {})
            except Exception as exc:
                # keep a copy of the unreadable file instead of silently losing it
                bad = self.path.with_suffix(".corrupt.json")
                log.warning("unreadable %s (%s); moved to %s", self.path, exc, bad)
                os.replace(self.path, bad)

    def account(self, user_id: str) -> dict:
        acc = self.data["accounts"].setdefault(str(user_id), {})
        acc.setdefault("initialized", False)
        acc.setdefault("seen", [])
        acc.setdefault("days", {})
        acc.setdefault("gap_ids", [])
        return acc

    def records_for(self, user_id: str | None, date: dt.date) -> list[dict]:
        accounts = self.data["accounts"]
        ids = [str(user_id)] if user_id else list(accounts)
        out = []
        for uid in ids:
            out += accounts.get(uid, {}).get("days", {}).get(date.isoformat(), [])
        return out

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


@dataclass
class SyncResult:
    added: list[dict] = field(default_factory=list)   # new records stored for the date
    records: list[dict] = field(default_factory=list)  # everything stored for the date
    first_run_pending: bool = False
    first_run_done: str = ""        # "", FIRST_NONE or FIRST_LAST_N
    baseline: int = 0               # IDs marked seen without importing
    notes: list[str] = field(default_factory=list)  # non-fatal warnings for the block


def _download_media(client: XClient, rec: dict, assets_dir: Path, entry_dir: Path) -> None:
    for i, m in enumerate(rec.get("media") or [], 1):
        if m.get("local") or not m.get("url"):
            continue
        stem = f"x-{rec['id']}-{i}"
        out = assets_dir / f"{stem}.webp"
        try:
            if not out.exists():
                url = m["url"]
                if "pbs.twimg.com/media/" in url and "name=" not in url:
                    url += ("&" if "?" in url else "?") + "name=large"
                convert_to_webp(client.download(url), assets_dir, quality=80, max_dimension=1600, stem=stem)
            m["local"] = Path(os.path.relpath(out, entry_dir)).as_posix()
        except Exception as exc:  # keep the remote link, note nothing fatal
            log.warning("media %s of post %s not saved: %s", i, rec["id"], exc)


def sync(client: XClient, journal_root: Path, date: dt.date, *, first_run: str = FIRST_ASK,
         first_run_count: int = 10, page_size: int = 20, max_pages: int = 3,
         download_media: bool = True, entry_dir: Path | None = None,
         assets_dir: Path | None = None) -> SyncResult:
    """Fetch bookmarks, store the new ones under `date` and return what's stored for it."""
    tok = client.token()  # raises NotConnected
    with _lock_for(journal_root):
        state = SeenState(journal_root)
        acc = state.account(tok.user_id)
        res = SyncResult()
        seen = set(acc["seen"])

        if not acc["initialized"]:
            if first_run not in (FIRST_NONE, FIRST_LAST_N):
                res.first_run_pending = True
                res.records = list(acc["days"].get(date.isoformat(), []))
                return res
            want = max(1, int(first_run_count)) if first_run == FIRST_LAST_N else 0
            size = max(int(page_size), min(100, want))
            fetched: list[dict] = []
            for page in client.bookmark_pages(tok.user_id, size, max_pages=max(1, -(-want // size)) if want else 1):
                fetched += normalize_page(page)
                if len(fetched) >= want:
                    break
            new = [r for r in fetched[:want] if r["id"] not in seen]
            seen.update(r["id"] for r in fetched)
            res.baseline = len(fetched) - len(new)
            res.first_run_done = first_run
            acc["initialized"] = True
            new.reverse()  # oldest first, like a diary
        else:
            stop_ids = seen - set(acc.get("gap_ids") or [])
            new, reached = [], False
            last_page = None
            try:
                for page in client.bookmark_pages(tok.user_id, page_size, max_pages):
                    last_page = page
                    for rec in normalize_page(page):
                        if rec["id"] in stop_ids:
                            reached = True
                        elif rec["id"] not in seen:
                            new.append(rec)
                    if reached:
                        break
                    if client.rate_exhausted() and (page.get("meta") or {}).get("next_token"):
                        res.notes.append("X rate limit reached while paging; the rest will be checked next time.")
                        break
            except RateLimited as exc:
                if not new:
                    raise
                res.notes.append(str(exc))
            exhausted = last_page is not None and not (last_page.get("meta") or {}).get("next_token")
            if reached or exhausted or not stop_ids:
                acc["gap_ids"] = []
            else:
                acc["gap_ids"] = sorted(set(acc.get("gap_ids") or []) | {r["id"] for r in new})
                if new:
                    res.notes.append(f"Stopped after {max_pages} page(s) without reaching bookmarks seen before; "
                                     "older new ones will be picked up next time (or raise “Max pages”).")
            seen.update(r["id"] for r in new)
            new.reverse()

        if new and download_media and assets_dir is not None and entry_dir is not None:
            for rec in new:
                _download_media(client, rec, assets_dir, entry_dir)
        day = acc["days"].setdefault(date.isoformat(), [])
        known_today = {r["id"] for r in day}
        res.added = [r for r in new if r["id"] not in known_today]
        day.extend(res.added)
        if not day:
            acc["days"].pop(date.isoformat(), None)
        acc["seen"] = sorted(seen)
        acc["last_sync"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        state.save()
        res.records = list(day)
        return res


def stored_records(journal_root: Path, date: dt.date, user_id: str | None = None) -> list[dict]:
    return SeenState(journal_root).records_for(user_id, date)


__all__ = ["sync", "stored_records", "SeenState", "SyncResult", "FIRST_RUN_CHOICES",
           "FIRST_ASK", "FIRST_NONE", "FIRST_LAST_N", "XApiError"]

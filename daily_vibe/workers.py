"""Background execution: plugins and small one-off tasks on a QThreadPool.

Each plugin for each date runs as its own QRunnable, so a slow plugin never
holds up the others. Results are delivered back on the GUI thread through a
queued signal and applied either to the open editor (via `open_entry_handler`,
which preserves unsaved edits) or directly to that date's file on disk.

Concurrency: every (date, plugin) request gets a generation number. If a newer
request for the same pair is started before an older one finishes, the older
result is discarded when it arrives, so the latest refresh always wins and
blocks are never duplicated.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from daily_vibe import markers
from daily_vibe.plugin_manager import Plugin, PluginManager, PluginResult

log = logging.getLogger(__name__)

# (date, plugin name, block markdown) -> True if the open editor handled it
OpenEntryHandler = Callable[[dt.date, str, str], bool]


class _Emitter(QObject):
    done = Signal(object)  # payload is a tuple, delivered on the GUI thread


class _FnRunnable(QRunnable):
    def __init__(self, fn: Callable[[], object], emitter: _Emitter, tag: object):
        super().__init__()
        self.fn, self.emitter, self.tag = fn, emitter, tag
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            value, error = self.fn(), None
        except Exception as exc:  # delivered to the GUI thread, never raised here
            value, error = None, exc
        self.emitter.done.emit((self.tag, value, error))


class TaskRunner(QObject):
    """Run a plain callable off the GUI thread; callbacks run on the GUI thread."""

    def __init__(self, parent=None, max_threads: int = 4):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._emitter = _Emitter(self)
        self._emitter.done.connect(self._on_done)
        self._callbacks: dict[int, tuple[Callable, Callable | None]] = {}
        self._next = 0

    def submit(self, fn: Callable[[], object], on_done: Callable[[object], None],
               on_error: Callable[[Exception], None] | None = None) -> None:
        self._next += 1
        self._callbacks[self._next] = (on_done, on_error)
        self.pool.start(_FnRunnable(fn, self._emitter, self._next))

    def _on_done(self, payload) -> None:
        tag, value, error = payload
        on_done, on_error = self._callbacks.pop(tag, (None, None))
        if error is not None:
            if on_error:
                on_error(error)
            else:
                log.warning("Background task failed: %s", error)
        elif on_done:
            on_done(value)


class PluginRunner(QObject):
    result_applied = Signal(object, object)  # (date, PluginResult)
    pending_changed = Signal(list)           # [(date, plugin title), ...]

    def __init__(self, manager: PluginManager, journal_provider: Callable[[], object], parent=None,
                 max_threads: int = 6):
        super().__init__(parent)
        self.manager = manager
        self.journal_provider = journal_provider  # returns the current Journal
        self.open_entry_handler: OpenEntryHandler | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._emitter = _Emitter(self)
        self._emitter.done.connect(self._on_done)
        self._generation: dict[tuple[dt.date, str], int] = {}
        self._pending: dict[tuple[dt.date, str], str] = {}  # -> title
        self._counter = 0

    # Public API -------------------------------------------------------------
    def pending(self) -> list[tuple[dt.date, str]]:
        return [(d, title) for (_root, d, _n), title in self._pending.items()]

    def is_busy(self) -> bool:
        return bool(self._pending)

    def run(self, date: dt.date, plugins: list[Plugin] | None = None, journal=None) -> None:
        """Start every plugin for `date` in parallel (default: the journal's enabled ones)."""
        journal = journal or self.journal_provider()
        for plugin in (self.manager.enabled(journal) if plugins is None else plugins):
            self._counter += 1
            key = (journal.root, date, plugin.id)
            self._generation[key] = self._counter
            self._pending[key] = plugin.title or plugin.display_name
            tag = (key, self._counter, journal)
            fn = (lambda p=plugin, j=journal: self.manager.run_plugin(p, date, j))
            self.pool.start(_FnRunnable(fn, self._emitter, tag))
        self.pending_changed.emit(self.pending())

    def wait(self, msecs: int = -1) -> bool:
        return self.pool.waitForDone(msecs)

    def shutdown(self, msecs: int = 2000) -> None:
        self.pool.clear()  # drop queued (not yet started) jobs
        self._pending.clear()
        self.pool.waitForDone(msecs)

    # Internals ----------------------------------------------------------------
    def _on_done(self, payload) -> None:
        (key, generation, origin), result, error = payload
        _root, date, name = key
        if self._generation.get(key) != generation:
            return  # superseded by a newer refresh of the same plugin/date
        self._pending.pop(key, None)
        self._generation.pop(key, None)
        if error is not None:  # run_plugin already catches plugin errors; be safe anyway
            result = PluginResult(name, f"> ⚠️ Plugin `{name}` failed: {error}", False, str(error))
        journal = self.journal_provider()
        if journal.root == origin.root:
            self._apply(journal, date, result)
            self.result_applied.emit(date, result)
        elif origin.root.is_dir():
            # The user switched to another journal meanwhile: write the block
            # straight into the original journal's file (not the open editor).
            self._apply(origin, date, result, use_editor=False)
        else:
            log.info("Journal folder %s is gone (moved?); dropping %s result for %s", origin.root, name, date)
        self.pending_changed.emit(self.pending())

    def _apply(self, journal, date: dt.date, result: PluginResult, use_editor: bool = True) -> None:
        if use_editor and self.open_entry_handler and self.open_entry_handler(date, result.name, result.markdown):
            return
        if not journal.exists(date):
            log.info("Entry %s no longer exists; dropping %s result", date, result.name)
            return
        text = journal.read(date)
        journal.write(date, markers.replace_block(text, result.name, result.markdown))

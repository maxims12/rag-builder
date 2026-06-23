"""Local file watcher: auto-reindex docs/ on change (Phase 7).

A :class:`FileWatcher` wraps a ``watchdog`` observer over the configured
``docs_folder`` and triggers an *incremental* re-index whenever a relevant file
is created, modified, moved, or deleted. It reuses :func:`app.rag.pipeline.ingest`
end-to-end — there is **no** duplicate load/chunk/embed/store logic here.

Toggle & cadence come straight from ``SourcesConfig`` (CONTRACT.md ``sources``):
  - ``watch_mode``      — master on/off switch for the observer.
  - ``polling_interval``— debounce window (seconds): rapid bursts of filesystem
    events are coalesced into a single re-index pass so an editor save that emits
    several events doesn't kick off several overlapping ingests.
  - ``file_types``      — only events for these extensions schedule a re-index.

Lifecycle: the FastAPI lifespan calls :func:`start_watcher` on startup (when
``watch_mode`` is on) and :func:`stop_watcher` on shutdown. :func:`reload_watcher`
re-reads config and restarts/stops the observer after a config change.

The ``watchdog`` import is **lazy/optional**: if the library is not installed the
watcher logs a warning and no-ops, so ``import app.main`` (and the whole app)
still works without it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Awaitable, Callable, Optional, Set

from app.config_schemas import RAGConfigData, SourcesConfig

logger = logging.getLogger("app.rag.watcher")


def _has_watchdog() -> bool:
    try:
        import watchdog  # noqa: F401

        return True
    except Exception:
        return False


# Default async re-index action: run the local ingest pipeline for one user.
# Imported lazily inside the function to avoid import cycles / heavy deps at
# module import time.
async def _default_reindex(user_id: int) -> None:
    """Run an incremental local ingest for ``user_id`` via the shared pipeline."""
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import IndexJob, RAGConfig
    from app.rag.loader import LocalFileLoader
    from app.rag.pipeline import ingest

    with Session(engine) as session:
        cfg_row = session.exec(
            select(RAGConfig).where(RAGConfig.user_id == user_id)
        ).first()
        config = RAGConfigData(**cfg_row.data) if cfg_row else RAGConfigData()

        job = IndexJob(user_id=user_id, source_type="local", status="pending")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    loader = LocalFileLoader(config.sources)
    logger.info("Watcher: starting incremental re-index job %s", job_id)
    await ingest(job_id=job_id, config=config, loader=loader, source_type="local")


class Debouncer:
    """Coalesce a burst of triggers into a single deferred async callback.

    Each :meth:`trigger` (re)starts a timer for ``interval`` seconds; the callback
    only fires once the filesystem has been quiet for the full window. This is the
    unit we unit-test in isolation (no real observer, no sleeps in the test — the
    test injects a fake timer / drives the fire directly).
    """

    def __init__(
        self,
        interval: float,
        callback: Callable[[], Awaitable[None]],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.interval = max(0.0, float(interval))
        self._callback = callback
        self._loop = loop
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self.trigger_count = 0
        self.fire_count = 0

    def trigger(self) -> None:
        """Register an event; (re)arm the debounce timer."""
        with self._lock:
            self.trigger_count += 1
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.interval, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
            self.fire_count += 1
        self._dispatch()

    def _dispatch(self) -> None:
        """Schedule the async callback on the bound loop (thread-safe)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("Debouncer has no live event loop; dropping re-index trigger")
            return

        def _run() -> None:
            asyncio.ensure_future(self._safe_callback())

        loop.call_soon_threadsafe(_run)

    async def _safe_callback(self) -> None:
        try:
            await self._callback()
        except Exception:  # never let a re-index failure kill the watcher thread
            logger.exception("Watcher re-index callback failed")

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def _relevant(path: str, file_types: Set[str]) -> bool:
    """True if a path's extension is one of the configured ``file_types``."""
    if not file_types:
        return True
    _root, ext = os.path.splitext(path)
    return ext.lower() in file_types


class FileWatcher:
    """Owns one watchdog observer for a user's ``docs_folder``.

    Delegates the actual ingest to ``reindex`` (defaults to :func:`_default_reindex`)
    so tests can inject a mock and assert the incremental ingest path is reached.
    """

    def __init__(
        self,
        user_id: int,
        config: SourcesConfig,
        reindex: Optional[Callable[[int], Awaitable[None]]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.user_id = user_id
        self.config = config
        self._reindex = reindex or _default_reindex
        self._loop = loop
        self._observer = None
        self._file_types = {ft.lower() for ft in config.file_types}
        self._debouncer = Debouncer(
            interval=config.polling_interval,
            callback=self._on_debounced,
            loop=loop,
        )

    async def _on_debounced(self) -> None:
        await self._reindex(self.user_id)

    # ── event handling (called by watchdog from its own thread) ─────────
    def handle_event(self, event_src_path: str, is_directory: bool) -> bool:
        """Process one filesystem event; return True if it scheduled a re-index."""
        if is_directory:
            return False
        if not _relevant(event_src_path, self._file_types):
            logger.debug("Watcher ignoring irrelevant path: %s", event_src_path)
            return False
        logger.debug("Watcher scheduling re-index for change: %s", event_src_path)
        self._debouncer.trigger()
        return True

    def _build_handler(self):
        from watchdog.events import FileSystemEventHandler

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:  # noqa: ANN001
                if event.event_type not in ("created", "modified", "moved", "deleted"):
                    return
                watcher.handle_event(event.src_path, event.is_directory)

        return _Handler()

    def start(self) -> bool:
        """Start the observer. Returns False (no-op) if watchdog isn't installed."""
        if not _has_watchdog():
            logger.warning(
                "watch_mode enabled but 'watchdog' is not installed; "
                "file watching is disabled (no-op)."
            )
            return False

        folder = self.config.docs_folder
        if not os.path.isdir(folder):
            logger.warning(
                "Watcher: docs folder '%s' does not exist; creating it.", folder
            )
            os.makedirs(folder, exist_ok=True)

        from watchdog.observers import Observer

        self._observer = Observer()
        self._observer.schedule(
            self._build_handler(), folder, recursive=self.config.recursive
        )
        self._observer.start()
        logger.info(
            "Watcher started on '%s' (recursive=%s, debounce=%ss)",
            folder,
            self.config.recursive,
            self.config.polling_interval,
        )
        return True

    def stop(self) -> None:
        self._debouncer.cancel()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Error stopping file watcher")
            finally:
                self._observer = None
            logger.info("Watcher stopped.")


# ── Module-level lifecycle (used by the FastAPI lifespan) ─────────────

_watcher: Optional[FileWatcher] = None


def _load_sources_config(user_id: int) -> SourcesConfig:
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import RAGConfig

    with Session(engine) as session:
        cfg_row = session.exec(
            select(RAGConfig).where(RAGConfig.user_id == user_id)
        ).first()
        if cfg_row:
            return RAGConfigData(**cfg_row.data).sources
    return SourcesConfig()


def start_watcher(
    user_id: int,
    config: Optional[SourcesConfig] = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> Optional[FileWatcher]:
    """Start (or restart) the global file watcher for ``user_id``.

    No-ops cleanly when ``watch_mode`` is off or watchdog is unavailable.
    """
    global _watcher
    stop_watcher()

    cfg = config or _load_sources_config(user_id)
    if not cfg.watch_mode:
        logger.info("Watcher not started: watch_mode is disabled.")
        return None

    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - started outside a loop
            loop = None

    watcher = FileWatcher(user_id=user_id, config=cfg, loop=loop)
    if watcher.start():
        _watcher = watcher
        return watcher
    return None


def stop_watcher() -> None:
    """Stop and clear the global file watcher if running."""
    global _watcher
    if _watcher is not None:
        _watcher.stop()
        _watcher = None


def reload_watcher(
    user_id: int, loop: Optional[asyncio.AbstractEventLoop] = None
) -> Optional[FileWatcher]:
    """Re-read config and restart/stop the watcher (call after a config change)."""
    return start_watcher(user_id, loop=loop)


def get_watcher() -> Optional[FileWatcher]:
    return _watcher

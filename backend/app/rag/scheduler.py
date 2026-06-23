"""Web-source re-crawl scheduler (Phase 7).

An ``APScheduler`` ``AsyncIOScheduler`` runs a periodic job that re-crawls the
configured web sources and re-ingests only the pages whose content changed. It
reuses :func:`app.rag.pipeline.ingest` + :class:`app.rag.web_loader.WebSourceLoader`
end-to-end — there is **no** duplicate crawl/chunk/embed/store logic here.

Toggle & cadence come from ``WebSourcesConfig`` (CONTRACT.md ``web_sources``):
  - ``auto_refresh``           — master on/off switch for the scheduler.
  - ``refresh_interval_hours`` — interval between re-crawls (APScheduler trigger).

Incrementality: before each re-crawl we collect the ``content_hash`` values
already stored in the vector store and pass them as ``known_hashes`` to the
``WebSourceLoader``. Pages whose extracted text is unchanged share a hash and are
dropped from the loader's output, so only changed/new pages get re-embedded.

The ``apscheduler`` import is **lazy/optional**: if the library is not installed
the scheduler logs a warning and no-ops, so ``import app.main`` (and the whole
app) still works without it.
"""

from __future__ import annotations

import logging
from typing import Optional, Set

from app.config_schemas import RAGConfigData, WebSourcesConfig

logger = logging.getLogger("app.rag.scheduler")

# Stable id for the single re-crawl job so re-scheduling replaces it cleanly.
JOB_ID = "web_recrawl"


def _has_apscheduler() -> bool:
    try:
        import apscheduler  # noqa: F401

        return True
    except Exception:
        return False


async def collect_known_hashes(config: RAGConfigData) -> Set[str]:
    """Best-effort: gather ``content_hash`` values already in the vector store.

    Returned hashes are passed to the loader as ``known_hashes`` so unchanged
    pages are skipped. Any failure (store down, backend without bulk listing)
    degrades to an empty set — incrementality is then a no-op (everything is
    re-embedded), which is correct, just less efficient.
    """
    from app.rag.store import get_vector_store

    store = get_vector_store(config.vectorstore)
    try:
        await store.connect()
    except Exception as exc:  # pragma: no cover - store/backend not available
        logger.warning("Could not connect to vector store for known_hashes: %s", exc)
        return set()

    hashes: Set[str] = set()
    # Chroma exposes a bulk .get(); other backends may not. Stay behind a guard
    # so no backend-specific failure leaks out.
    try:
        collection = getattr(store, "_collection", None)
        if collection is not None and hasattr(collection, "get"):
            data = collection.get(include=["metadatas"])
            for meta in data.get("metadatas") or []:
                if meta and meta.get("content_hash"):
                    hashes.add(meta["content_hash"])
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("known_hashes collection failed (non-fatal): %s", exc)

    logger.info("Scheduler collected %d known content hash(es)", len(hashes))
    return hashes


async def recrawl_web_sources(user_id: int) -> Optional[int]:
    """Run one incremental web re-crawl for ``user_id`` via the shared pipeline.

    Returns the IndexJob id created (or ``None`` if web sources are unconfigured).
    """
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import IndexJob, RAGConfig
    from app.rag.pipeline import ingest
    from app.rag.web_loader import WebSourceLoader

    with Session(engine) as session:
        cfg_row = session.exec(
            select(RAGConfig).where(RAGConfig.user_id == user_id)
        ).first()
        config = RAGConfigData(**cfg_row.data) if cfg_row else RAGConfigData()

    web_cfg = config.web_sources
    if not web_cfg.web_urls and not web_cfg.sitemap_url:
        logger.info("Scheduler: no web sources configured; skipping re-crawl.")
        return None

    known = await collect_known_hashes(config)

    with Session(engine) as session:
        job = IndexJob(user_id=user_id, source_type="web", status="pending")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    loader = WebSourceLoader(web_cfg, known_hashes=known)
    logger.info(
        "Scheduler: starting incremental re-crawl job %s (%d known hashes)",
        job_id,
        len(known),
    )
    await ingest(job_id=job_id, config=config, loader=loader, source_type="web")
    return job_id


class WebScheduler:
    """Owns the AsyncIOScheduler and the single periodic re-crawl job."""

    def __init__(self, user_id: int, config: WebSourcesConfig) -> None:
        self.user_id = user_id
        self.config = config
        self._scheduler = None

    def start(self) -> bool:
        """Start the scheduler + register the re-crawl job. False if unavailable."""
        if not _has_apscheduler():
            logger.warning(
                "auto_refresh enabled but 'apscheduler' is not installed; "
                "scheduled re-crawl is disabled (no-op)."
            )
            return False

        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._scheduler = AsyncIOScheduler()
        hours = max(1, int(self.config.refresh_interval_hours))
        self._scheduler.add_job(
            recrawl_web_sources,
            trigger="interval",
            hours=hours,
            args=[self.user_id],
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info("Scheduler started: web re-crawl every %dh", hours)
        return True

    def stop(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Error shutting down scheduler")
            finally:
                self._scheduler = None
            logger.info("Scheduler stopped.")

    @property
    def scheduler(self):  # exposed for inspection/tests
        return self._scheduler


# ── Module-level lifecycle (used by the FastAPI lifespan) ─────────────

_scheduler: Optional[WebScheduler] = None


def _load_web_config(user_id: int) -> WebSourcesConfig:
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import RAGConfig

    with Session(engine) as session:
        cfg_row = session.exec(
            select(RAGConfig).where(RAGConfig.user_id == user_id)
        ).first()
        if cfg_row:
            return RAGConfigData(**cfg_row.data).web_sources
    return WebSourcesConfig()


def start_scheduler(
    user_id: int, config: Optional[WebSourcesConfig] = None
) -> Optional[WebScheduler]:
    """Start (or restart) the global web re-crawl scheduler for ``user_id``.

    No-ops cleanly when ``auto_refresh`` is off or apscheduler is unavailable.
    """
    global _scheduler
    stop_scheduler()

    cfg = config or _load_web_config(user_id)
    if not cfg.auto_refresh:
        logger.info("Scheduler not started: auto_refresh is disabled.")
        return None

    sched = WebScheduler(user_id=user_id, config=cfg)
    if sched.start():
        _scheduler = sched
        return sched
    return None


def stop_scheduler() -> None:
    """Stop and clear the global scheduler if running."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None


def reload_scheduler(user_id: int) -> Optional[WebScheduler]:
    """Re-read config and restart/stop the scheduler (call after a config change)."""
    return start_scheduler(user_id)


def get_scheduler() -> Optional[WebScheduler]:
    return _scheduler

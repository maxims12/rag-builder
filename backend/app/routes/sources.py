"""Local folder source routes (CONTRACT.md §2 Ingestion & Pipeline).

  - ``POST /sources/ingest``: trigger an async ingestion job over the configured
    local ``docs/`` folder. Returns 202 with the created IndexJob summary; the
    actual load → chunk → embed → store work runs in a background task.

Source configuration itself is read/written through ``/settings/config/sources``
(Phase 3); this route only owns the local ingestion trigger.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.auth.deps import get_current_user
from app.config_schemas import RAGConfigData
from app.db import get_session
from app.models import IndexJob, RAGConfig, User
from app.rag.loader import LocalFileLoader
from app.rag.pipeline import ingest

logger = logging.getLogger("app.routes.sources")

router = APIRouter(prefix="/sources", tags=["sources"])


def _load_config(session: Session, user: User) -> RAGConfigData:
    """Load the user's full config (defaults if none persisted yet)."""
    cfg = session.exec(
        select(RAGConfig).where(RAGConfig.user_id == user.id)
    ).first()
    if cfg is None:
        return RAGConfigData()
    return RAGConfigData(**cfg.data)


def _run_ingest_job(job_id: int, config: RAGConfigData) -> None:
    """Background entrypoint: run the async pipeline for a local source job."""
    loader = LocalFileLoader(config.sources)
    asyncio.run(ingest(job_id=job_id, config=config, loader=loader, source_type="local"))


def _job_summary(job: IndexJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "source_type": job.source_type,
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
    }


@router.post("/ingest")
def ingest_local_sources(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Create a pending local IndexJob and run ingestion in the background."""
    config = _load_config(session, user)

    job = IndexJob(user_id=user.id, source_type="local", status="pending")
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(_run_ingest_job, job.id, config)
    logger.info("Queued local ingestion job %s for user %s", job.id, user.id)

    return JSONResponse(status_code=202, content=_job_summary(job))

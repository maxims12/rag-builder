"""Pipeline job read endpoints (CONTRACT.md §2 Ingestion & Pipeline).

  - ``GET /pipeline/jobs``: paginated list of recent IndexJobs.
  - ``GET /pipeline/jobs/{job_id}``: a single IndexJob's real-time stats.

The ``GET /pipeline/status`` SSE stream from the contract is deferred to the
retrieval/overview phases (Phase 6/9); the polled jobs endpoints here are what
Phase 4 ingestion verification relies on.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.auth.deps import get_current_user
from app.db import get_session
from app.errors import APIError
from app.models import IndexJob, User

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _job_view(job: IndexJob) -> dict[str, Any]:
    """Serialize an IndexJob to the contract's job shape."""
    return {
        "id": job.id,
        "source_type": job.source_type,
        "status": job.status,
        "files_processed": job.files_processed,
        "pages_fetched": job.pages_fetched,
        "chunks_created": job.chunks_created,
        "error_message": job.error_message,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/jobs")
def list_jobs(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return recent ingestion jobs for the current user (newest first)."""
    total = session.exec(
        select(func.count()).select_from(IndexJob).where(IndexJob.user_id == user.id)
    ).one()
    jobs = session.exec(
        select(IndexJob)
        .where(IndexJob.user_id == user.id)
        .order_by(IndexJob.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {"total": int(total), "jobs": [_job_view(j) for j in jobs]}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a single ingestion job's details and real-time statistics."""
    job = session.get(IndexJob, job_id)
    if job is None or job.user_id != user.id:
        raise APIError(404, f"Job {job_id} not found", "NOT_FOUND")
    return _job_view(job)

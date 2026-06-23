"""Web source routes (CONTRACT.md §2 Ingestion & Pipeline).

  - ``POST /web-sources/test``: preview main-content extraction for a single URL
    without saving anything to the vector store. Lets the UI validate selectors,
    JS rendering, and content density before committing a crawl.
  - ``POST /web-sources/ingest``: trigger an async ingestion job over the
    configured web sources (single URLs / crawl / sitemap). Returns 202 with the
    created IndexJob summary; load → chunk → embed → store runs in the background.

Web source configuration itself is read/written through
``/settings/config/web_sources`` (Phase 3); this route owns the web ingestion
trigger and the test-extraction preview. All mode/provider logic lives behind
:class:`~app.rag.web_loader.WebSourceLoader` — no crawl specifics leak here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.auth.deps import get_current_user
from app.config_schemas import RAGConfigData
from app.db import get_session
from app.models import IndexJob, RAGConfig, User
from app.rag.pipeline import ingest
from app.rag.web_loader import WebSourceLoader, extract_url

logger = logging.getLogger("app.routes.web_sources")

router = APIRouter(prefix="/web-sources", tags=["web-sources"])


# ── Request/response models ───────────────────────────────────────────


class WebTestRequest(BaseModel):
    url: str
    render_js: bool = False
    strip_selectors: List[str] = Field(default_factory=list)


class WebTestResponse(BaseModel):
    url: str
    title: Optional[str] = None
    clean_text: str
    raw_html_length: int
    extracted_text_length: int
    content_hash: str
    fetched_at: str


# ── Helpers ───────────────────────────────────────────────────────────


def _load_config(session: Session, user: User) -> RAGConfigData:
    """Load the user's full config (defaults if none persisted yet)."""
    cfg = session.exec(
        select(RAGConfig).where(RAGConfig.user_id == user.id)
    ).first()
    if cfg is None:
        return RAGConfigData()
    return RAGConfigData(**cfg.data)


def _run_ingest_job(job_id: int, config: RAGConfigData) -> None:
    """Background entrypoint: run the async pipeline for a web source job."""
    loader = WebSourceLoader(config.web_sources)
    asyncio.run(ingest(job_id=job_id, config=config, loader=loader, source_type="web"))


def _job_summary(job: IndexJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "source_type": job.source_type,
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
    }


# ── Routes ────────────────────────────────────────────────────────────


@router.post("/test", response_model=WebTestResponse)
async def test_extraction(
    payload: WebTestRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> WebTestResponse:
    """Preview cleaned content + metadata for one URL (no vector store writes).

    Uses the configured request timeout from the user's web_sources config so the
    preview matches what an actual ingest would do.
    """
    config = _load_config(session, user)
    timeout = float(config.web_sources.request_timeout_s)
    result = await extract_url(
        payload.url,
        render_js=payload.render_js,
        strip_selectors=payload.strip_selectors,
        timeout=timeout,
    )
    return WebTestResponse(
        url=result.url,
        title=result.title,
        clean_text=result.clean_text,
        raw_html_length=result.raw_html_length,
        extracted_text_length=result.extracted_text_length,
        content_hash=result.content_hash,
        fetched_at=result.fetched_at,
    )


@router.post("/ingest")
def ingest_web_sources(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Create a pending web IndexJob and run web ingestion in the background."""
    config = _load_config(session, user)

    job = IndexJob(user_id=user.id, source_type="web", status="pending")
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(_run_ingest_job, job.id, config)
    logger.info("Queued web ingestion job %s for user %s", job.id, user.id)

    return JSONResponse(status_code=202, content=_job_summary(job))

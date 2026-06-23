"""Ingestion pipeline: load → chunk → embed → store, tracking IndexJob progress.

``ingest()`` ties the stages together for one IndexJob. It accepts any
``DocumentLoader`` (local files in Phase 4, web sources in Phase 5) because they
all emit the shared :class:`~app.rag.loader.ParsedDocument` shape, so the
chunk → embed → store path is reused unchanged.

Progress (files/pages processed, chunks created, status, errors) is written to
the ``IndexJob`` row referenced by ``job_id`` so the pipeline status endpoints
can report real-time stats.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Protocol

from sqlmodel import Session

from app.config_schemas import RAGConfigData
from app.db import engine
from app.models import IndexJob, utcnow
from app.rag.chunker import Chunker
from app.rag.embedder import get_embedding_provider
from app.rag.loader import LocalFileLoader, ParsedDocument
from app.rag.store import get_vector_store

logger = logging.getLogger("app.rag.pipeline")


class DocumentLoader(Protocol):
    async def load(self) -> List[ParsedDocument]: ...


def _update_job(job_id: int, **fields) -> None:
    """Persist progress fields onto the IndexJob row in its own session."""
    with Session(engine) as session:
        job = session.get(IndexJob, job_id)
        if job is None:
            logger.warning("IndexJob %s not found while updating progress", job_id)
            return
        for key, value in fields.items():
            setattr(job, key, value)
        session.add(job)
        session.commit()


async def ingest(
    job_id: int,
    config: RAGConfigData,
    loader: Optional[DocumentLoader] = None,
    source_type: str = "local",
) -> IndexJob:
    """Run a full ingestion for one IndexJob.

    Args:
        job_id: the IndexJob row to update with progress/result.
        config: the full RAG configuration (sections drive each stage).
        loader: the document loader; defaults to a ``LocalFileLoader`` built
            from ``config.sources`` when ``source_type == "local"``.
        source_type: "local" or "web" (controls which progress counter advances).
    """
    if loader is None:
        loader = LocalFileLoader(config.sources)

    _update_job(job_id, status="running", started_at=utcnow())

    try:
        documents = await loader.load()
        unit_count = len(documents)
        if source_type == "web":
            _update_job(job_id, pages_fetched=unit_count)
        else:
            _update_job(job_id, files_processed=unit_count)

        chunker = Chunker(config.chunking)
        chunks = chunker.chunk_documents(documents)
        logger.info(
            "Pipeline job %s: %d document(s) -> %d chunk(s)",
            job_id,
            unit_count,
            len(chunks),
        )

        if not chunks:
            _update_job(
                job_id,
                status="done",
                chunks_created=0,
                finished_at=utcnow(),
            )
            with Session(engine) as session:
                return session.get(IndexJob, job_id)

        embedder = get_embedding_provider(config.embedding, config.credentials)
        store = get_vector_store(config.vectorstore)
        await store.connect()

        texts = [c.text for c in chunks]
        vectors = await embedder.embed_documents(texts)

        ids = [c.metadata["chunk_id"] for c in chunks]
        metadatas = [c.metadata for c in chunks]
        await store.upsert(ids=ids, vectors=vectors, metadatas=metadatas, contents=texts)

        _update_job(
            job_id,
            status="done",
            chunks_created=len(chunks),
            finished_at=utcnow(),
        )
        logger.info("Pipeline job %s done: %d chunks stored", job_id, len(chunks))

    except Exception as exc:  # surface failure on the job, don't crash worker
        logger.exception("Pipeline job %s failed", job_id)
        _update_job(
            job_id,
            status="error",
            error_message=str(exc),
            finished_at=utcnow(),
        )

    with Session(engine) as session:
        return session.get(IndexJob, job_id)

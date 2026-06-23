"""Playground query route (CONTRACT.md §2 Playground Endpoints).

``POST /playground/query`` runs the full RAG pipeline:

    embed query (optionally LLM-expanded into variants) → retrieve chunks
    (similarity / mmr / hybrid, optional rerank + contextual compression)
    → build grounded prompt → generate answer (optionally streamed)

Two response modes, selected by the request ``stream`` flag:
  - ``stream=false``: a single JSON payload ``{answer, sources[]}``.
  - ``stream=true``: a ``text/event-stream`` with ``source`` (emitted before
    generation), ``token`` (per LLM chunk), ``error`` (on failure), and ``done``
    (final ``{answer, sources[]}``) events.

All retrieval / generation goes through the provider interfaces (retriever,
generator). Citations include ``source_url`` for web chunks and the file path for
local chunks — derived in the retriever from chunk metadata.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, List

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth.deps import get_current_user
from app.config_schemas import RAGConfigData
from app.db import get_session
from app.errors import APIError
from app.models import RAGConfig, User
from app.rag.generator import Generator
from app.rag.retriever import RetrievedChunk, get_retriever

logger = logging.getLogger("app.routes.playground")

router = APIRouter(prefix="/playground", tags=["playground"])


class QueryRequest(BaseModel):
    query: str
    stream: bool = True


def _load_config(session: Session, user: User) -> RAGConfigData:
    """Load the user's full config (defaults if none persisted yet)."""
    cfg = session.exec(select(RAGConfig).where(RAGConfig.user_id == user.id)).first()
    if cfg is None:
        return RAGConfigData()
    return RAGConfigData(**cfg.data)


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _retrieve(config: RAGConfigData, query: str) -> List[RetrievedChunk]:
    retriever = get_retriever(
        config.retrieval,
        config.embedding,
        config.vectorstore,
        config.credentials,
        llm=config.llm,  # used by multi-query expansion / contextual compression
    )
    return await retriever.retrieve(query)


@router.post("/query")
async def query(
    payload: QueryRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Query the RAG pipeline (streaming SSE or single JSON payload)."""
    if not payload.query or not payload.query.strip():
        raise APIError(422, "Query must not be empty", "VALIDATION_ERROR")

    config = _load_config(session, user)
    generator = Generator(config.llm, config.credentials)

    if not payload.stream:
        chunks = await _retrieve(config, payload.query)
        try:
            answer = await generator.generate(payload.query, chunks)
        except APIError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise APIError(502, f"Generation failed: {exc}", "PROVIDER_ERROR") from exc
        return JSONResponse(
            content={
                "answer": answer,
                "sources": [c.as_source() for c in chunks],
            }
        )

    async def event_stream() -> AsyncIterator[str]:
        # Retrieve first so the UI can render source cards before tokens arrive.
        try:
            chunks = await _retrieve(config, payload.query)
        except Exception as exc:
            logger.exception("Retrieval failed for playground query")
            yield _sse("error", {"message": f"Retrieval failed: {exc}"})
            return

        sources = [c.as_source() for c in chunks]
        yield _sse("source", {"sources": sources})

        answer_parts: List[str] = []
        try:
            async for token in generator.generate_stream(payload.query, chunks):
                answer_parts.append(token)
                yield _sse("token", {"token": token})
        except APIError as exc:
            yield _sse("error", {"message": exc.detail})
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Generation failed for playground query")
            yield _sse("error", {"message": str(exc)})
            return

        yield _sse("done", {"answer": "".join(answer_parts), "sources": sources})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

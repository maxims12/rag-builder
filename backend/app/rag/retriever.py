"""Retrieval: embed the query, search the vector store, optionally diversify /
rerank / compress, and return contract-shaped source chunks (CONTRACT.md §2
Playground, §4 Vector Store / Embedding interfaces).

Search types (per ``RetrievalConfig.search_type``):
  - ``similarity``: plain nearest-neighbour search by query embedding.
  - ``mmr``: Maximal Marginal Relevance re-selection over a larger candidate
    pool, trading relevance vs. diversity by ``mmr_diversity``.
  - ``hybrid``: blend the dense similarity score with a lexical score weighted
    by ``hybrid_alpha`` (semantic vs. lexical). The lexical side is chosen by
    ``hybrid_method``: ``token_overlap`` re-scores the dense candidate pool,
    while ``bm25`` builds an Okapi BM25 index over the whole collection so
    keyword-only matches the dense search missed can still surface.

Optional stages, each degrading gracefully when its dependency is unavailable:
  - ``multi_query``: the configured LLM rewrites the question into up to
    ``multi_query_count`` variants; results are unioned by chunk id for recall.
  - ``reranking``: a cross-encoder (``reranker_model``) reorders candidates;
    if the model can't be loaded the first-stage order is kept.
  - ``contextual_compression``: the configured LLM extracts only the
    query-relevant parts of each final chunk (chunks with nothing relevant are
    dropped), mirroring LangChain's ``LLMChainExtractor``.

All vector-store / embedding / LLM access goes through the provider interfaces —
no backend- or provider-specific calls leak out of this module. The route layer
consumes :class:`RetrievedChunk` (a flat, JSON-ready source shape).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.config_schemas import (
    CredentialsConfig,
    EmbeddingConfig,
    LLMConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from app.rag.embedder import get_embedding_provider
from app.rag.store import VectorStoreHit, get_vector_store

logger = logging.getLogger("app.rag.retriever")

# How many candidates to pull before MMR/hybrid/rerank narrow down to top_k.
_CANDIDATE_MULTIPLIER = 4
_MIN_CANDIDATE_POOL = 20

# Cap on how much of the collection the BM25 index will load per query.
_BM25_MAX_CHUNKS = 20_000

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_MULTI_QUERY_PROMPT = (
    "You rewrite search queries for a document retrieval system. Generate "
    "{count} alternative phrasings of the user question below, each "
    "approaching it from a different angle, to improve recall from a vector "
    "database. Return one query per line, with no numbering, bullets, or "
    "commentary.\n\n"
    "Question: {query}"
)

_COMPRESSION_PROMPT = (
    "Extract the parts of the context below that are relevant to answering "
    "the question. Quote the context verbatim; do not add commentary or "
    "rephrase. If nothing in the context is relevant, return exactly "
    "NO_OUTPUT.\n\n"
    "Question: {query}\n\n"
    "Context:\n{content}"
)


class RetrievedChunk(BaseModel):
    """One retrieved source chunk, flattened to the contract source shape.

    Maps to the ``sources[]`` entries in ``POST /playground/query``:
      ``source_type``, ``source_path_or_url``, ``title``, ``snippet``, ``score``.
    The raw ``content`` and ``metadata`` are kept for prompt synthesis.
    """

    source_type: str
    source_path_or_url: str
    title: Optional[str] = None
    snippet: str
    score: float
    content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # Vector-store chunk id — internal identity for merge/blend stages, never
    # part of the contract source shape.
    chunk_id: str = ""

    def as_source(self) -> Dict[str, Any]:
        """The contract ``source`` object (no raw content / metadata)."""
        return {
            "source_type": self.source_type,
            "source_path_or_url": self.source_path_or_url,
            "title": self.title,
            "snippet": self.snippet,
            "score": round(self.score, 6),
        }


def _snippet(text: str, limit: int = 300) -> str:
    """A short, single-line preview of a chunk for source cards."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "..."


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _hit_to_chunk(hit: VectorStoreHit) -> RetrievedChunk:
    """Map a vector-store hit to a contract-shaped retrieved chunk.

    Local chunks carry ``source`` (file path); web chunks carry ``source_url``.
    """
    meta = hit.get("metadata") or {}
    content = hit.get("content") or ""
    source_url = meta.get("source_url")
    if source_url:
        source_type = "web"
        source_path_or_url = source_url
    else:
        source_type = "local"
        source_path_or_url = meta.get("source") or meta.get("file_name") or "unknown"
    return RetrievedChunk(
        source_type=source_type,
        source_path_or_url=str(source_path_or_url),
        title=meta.get("title") or meta.get("file_name"),
        snippet=_snippet(content),
        score=float(hit.get("score", 0.0)),
        content=content,
        metadata=meta,
        chunk_id=str(hit.get("id") or ""),
    )


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _normalize_scores(scores: Dict[str, float]) -> Dict[str, float]:
    """Min-max normalize a score map to [0, 1]; equal scores all map to 1."""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class _BM25Index:
    """Minimal Okapi BM25 over tokenized chunk texts (no extra dependency)."""

    def __init__(
        self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75
    ) -> None:
        self._k1 = k1
        self._b = b
        self._tf = [Counter(d) for d in docs]
        self._lens = [len(d) for d in docs]
        n = len(docs)
        self._avg_len = (sum(self._lens) / n) if n else 0.0
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._idf = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query_tokens: List[str]) -> List[float]:
        """BM25 score of the query against every indexed document."""
        scores = [0.0] * len(self._tf)
        for i, tf in enumerate(self._tf):
            if not self._lens[i]:
                continue
            norm = self._k1 * (
                1.0 - self._b + self._b * self._lens[i] / (self._avg_len or 1.0)
            )
            total = 0.0
            for term in query_tokens:
                freq = tf.get(term)
                if not freq:
                    continue
                total += (
                    self._idf.get(term, 0.0)
                    * (freq * (self._k1 + 1.0))
                    / (freq + norm)
                )
            scores[i] = total
        return scores


class Retriever:
    """Embeds the query and retrieves source chunks per the retrieval config."""

    def __init__(
        self,
        retrieval: RetrievalConfig,
        embedding: EmbeddingConfig,
        vectorstore: VectorStoreConfig,
        credentials: Optional[CredentialsConfig] = None,
        llm: Optional[LLMConfig] = None,
    ) -> None:
        self.retrieval = retrieval
        self.embedding = embedding
        self.vectorstore = vectorstore
        self.credentials = credentials or CredentialsConfig()
        self.llm = llm
        self._embedder = get_embedding_provider(embedding, self.credentials)
        self._store = get_vector_store(vectorstore)
        self._llm_provider = None

    def _get_llm(self):
        """Lazily build the LLM provider used by multi-query / compression.

        Imported at call time: generator.py imports this module, so a
        module-level import here would be circular.
        """
        if self.llm is None:
            return None
        if self._llm_provider is None:
            from app.rag.generator import get_llm_provider

            self._llm_provider = get_llm_provider(self.llm, self.credentials)
        return self._llm_provider

    async def retrieve(
        self, query: str, filters: Optional[Dict[str, Any]] = None
    ) -> List[RetrievedChunk]:
        """Return up to ``top_k`` source chunks ranked for ``query``."""
        top_k = max(1, self.retrieval.top_k)

        await self._store.connect()

        queries = [query]
        if self.retrieval.multi_query:
            queries += await self._generate_query_variants(query)

        # Pull a larger candidate pool when a later stage narrows it down.
        needs_pool = (
            self.retrieval.search_type in ("mmr", "hybrid")
            or self.retrieval.reranking
            or len(queries) > 1
        )
        pool_size = (
            max(_MIN_CANDIDATE_POOL, top_k * _CANDIDATE_MULTIPLIER)
            if needs_pool
            else top_k
        )

        # Search once per query and union hits by chunk id (keep the best score).
        query_vector: List[float] = []
        merged: Dict[str, VectorStoreHit] = {}
        anon = 0
        for i, q in enumerate(queries):
            vector = await self._embedder.embed_query(q)
            if i == 0:
                query_vector = vector
            hits = await self._store.search(vector, top_k=pool_size, filters=filters)
            for hit in hits:
                hit_id = str(hit.get("id") or "")
                if not hit_id:
                    # No id -> can't dedupe; keep the hit under a unique key.
                    anon += 1
                    hit_id = f"__anon_{anon}"
                previous = merged.get(hit_id)
                if previous is None or float(hit.get("score", 0.0)) > float(
                    previous.get("score", 0.0)
                ):
                    merged[hit_id] = hit
        pooled = sorted(
            merged.values(), key=lambda h: float(h.get("score", 0.0)), reverse=True
        )
        candidates = [_hit_to_chunk(h) for h in pooled]

        # Score-threshold filter (lower bound on first-stage similarity score).
        threshold = self.retrieval.score_threshold
        if threshold and threshold > 0:
            filtered = [c for c in candidates if c.score >= threshold]
            # Keep at least one result if everything got filtered out.
            candidates = filtered or candidates[:1]

        if not candidates:
            return []

        search_type = self.retrieval.search_type
        if search_type == "mmr":
            candidates = await self._apply_mmr(query_vector, candidates, top_k)
        elif search_type == "hybrid":
            if self.retrieval.hybrid_method == "bm25":
                candidates = await self._apply_hybrid_bm25(query, candidates, filters)
            else:
                candidates = self._apply_hybrid(query, candidates)

        if self.retrieval.reranking:
            candidates = await self._apply_rerank(query, candidates)

        final = candidates[:top_k]
        if self.retrieval.contextual_compression:
            final = await self._apply_compression(query, final)
        return final

    # ── Multi-query ───────────────────────────────────────────────────
    async def _generate_query_variants(self, query: str) -> List[str]:
        """LLM-generated rephrasings of the query; [] when the LLM is unavailable."""
        provider = self._get_llm()
        if provider is None:
            return []
        count = max(1, min(8, self.retrieval.multi_query_count))
        prompt = _MULTI_QUERY_PROMPT.format(count=count, query=query)
        try:
            text = await provider.generate(prompt, temperature=0.3, max_tokens=256)
        except Exception as exc:
            logger.warning(
                "Multi-query: LLM unavailable (%s); using the original query only",
                exc,
            )
            return []

        variants: List[str] = []
        seen = {query.strip().lower()}
        for line in text.splitlines():
            # Strip bullets/numbering in case the model adds them anyway.
            cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
            key = cleaned.lower()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            variants.append(cleaned)
            if len(variants) >= count:
                break
        return variants

    # ── MMR ───────────────────────────────────────────────────────────
    async def _apply_mmr(
        self,
        query_vector: List[float],
        candidates: List[RetrievedChunk],
        top_k: int,
    ) -> List[RetrievedChunk]:
        """Maximal Marginal Relevance re-selection over the candidate pool."""
        texts = [c.content for c in candidates]
        try:
            vectors = await self._embedder.embed_documents(texts)
        except Exception:  # pragma: no cover - embedding failure falls back
            logger.warning("MMR: failed to embed candidates; using similarity order")
            return candidates

        lambda_mult = max(0.0, min(1.0, 1.0 - self.retrieval.mmr_diversity))
        relevance = [_cosine(query_vector, v) for v in vectors]

        selected: List[int] = []
        remaining = set(range(len(candidates)))
        while remaining and len(selected) < top_k:
            best_idx = None
            best_score = -math.inf
            for idx in remaining:
                if not selected:
                    diversity_penalty = 0.0
                else:
                    diversity_penalty = max(
                        _cosine(vectors[idx], vectors[s]) for s in selected
                    )
                mmr_score = (
                    lambda_mult * relevance[idx]
                    - (1.0 - lambda_mult) * diversity_penalty
                )
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx
            if best_idx is None:
                break
            selected.append(best_idx)
            remaining.discard(best_idx)

        return [candidates[i] for i in selected]

    # ── Hybrid ────────────────────────────────────────────────────────
    def _apply_hybrid(
        self, query: str, candidates: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        """Blend dense similarity with lexical token-overlap score."""
        alpha = max(0.0, min(1.0, self.retrieval.hybrid_alpha))
        query_tokens = set(_tokenize(query))

        dense_scores = [c.score for c in candidates]
        d_min, d_max = min(dense_scores), max(dense_scores)
        d_range = (d_max - d_min) or 1.0

        lexical_scores: List[float] = []
        for c in candidates:
            doc_tokens = set(_tokenize(c.content))
            if query_tokens and doc_tokens:
                overlap = len(query_tokens & doc_tokens) / len(query_tokens)
            else:
                overlap = 0.0
            lexical_scores.append(overlap)

        blended: List[RetrievedChunk] = []
        for c, dense, lexical in zip(candidates, dense_scores, lexical_scores):
            norm_dense = (dense - d_min) / d_range
            combined = alpha * norm_dense + (1.0 - alpha) * lexical
            updated = c.model_copy(update={"score": combined})
            blended.append(updated)

        blended.sort(key=lambda x: x.score, reverse=True)
        return blended

    async def _apply_hybrid_bm25(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        filters: Optional[Dict[str, Any]],
    ) -> List[RetrievedChunk]:
        """Blend dense scores with corpus-wide BM25 (weighted by hybrid_alpha).

        Unlike token-overlap hybrid, BM25 ranks the *whole* collection, so
        keyword matches the dense search missed are pulled into the pool. Falls
        back to token overlap if the corpus can't be fetched.
        """
        try:
            corpus = await self._store.fetch_all(
                filters=filters, limit=_BM25_MAX_CHUNKS
            )
        except Exception as exc:
            logger.warning(
                "BM25: could not fetch collection (%s); using token overlap", exc
            )
            return self._apply_hybrid(query, candidates)
        if not corpus:
            return self._apply_hybrid(query, candidates)

        query_tokens = _tokenize(query)
        index = _BM25Index([_tokenize(h.get("content") or "") for h in corpus])
        scores = index.score(query_tokens)

        # Keep the top positively-scored lexical hits (same pool sizing as dense).
        pool_size = max(
            _MIN_CANDIDATE_POOL, max(1, self.retrieval.top_k) * _CANDIDATE_MULTIPLIER
        )
        ranked = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)
        lexical_scores: Dict[str, float] = {}
        lexical_hits: Dict[str, VectorStoreHit] = {}
        for i in ranked[:pool_size]:
            if scores[i] <= 0.0:
                break
            hit_id = str(corpus[i].get("id") or "")
            lexical_scores[hit_id] = scores[i]
            lexical_hits[hit_id] = corpus[i]

        alpha = max(0.0, min(1.0, self.retrieval.hybrid_alpha))
        dense_norm = _normalize_scores({c.chunk_id: c.score for c in candidates})
        lexical_norm = _normalize_scores(lexical_scores)

        by_id: Dict[str, RetrievedChunk] = {c.chunk_id: c for c in candidates}
        for hit_id, hit in lexical_hits.items():
            if hit_id not in by_id:
                by_id[hit_id] = _hit_to_chunk(hit)

        blended = [
            chunk.model_copy(
                update={
                    "score": alpha * dense_norm.get(hit_id, 0.0)
                    + (1.0 - alpha) * lexical_norm.get(hit_id, 0.0)
                }
            )
            for hit_id, chunk in by_id.items()
        ]
        blended.sort(key=lambda x: x.score, reverse=True)
        return blended

    # ── Contextual compression ────────────────────────────────────────
    async def _apply_compression(
        self, query: str, chunks: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        """LLM-extract only the query-relevant parts of each final chunk.

        Chunks the LLM marks as irrelevant (NO_OUTPUT) are dropped; on any LLM
        failure the original chunk is kept untouched.
        """
        provider = self._get_llm()
        if provider is None or not chunks:
            return chunks

        async def extract(chunk: RetrievedChunk) -> str:
            prompt = _COMPRESSION_PROMPT.format(
                query=query, content=chunk.content or chunk.snippet
            )
            return await provider.generate(prompt, temperature=0.0, max_tokens=1024)

        results = await asyncio.gather(
            *(extract(c) for c in chunks), return_exceptions=True
        )
        compressed: List[RetrievedChunk] = []
        for chunk, result in zip(chunks, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Compression: LLM call failed (%s); keeping the full chunk",
                    result,
                )
                compressed.append(chunk)
                continue
            text = (result or "").strip()
            if not text or "NO_OUTPUT" in text:
                continue
            compressed.append(
                chunk.model_copy(update={"content": text, "snippet": _snippet(text)})
            )
        return compressed

    # ── Rerank ────────────────────────────────────────────────────────
    async def _apply_rerank(
        self, query: str, candidates: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        """Cross-encoder reranking; falls back to input order on any failure."""
        try:
            scores = await asyncio.to_thread(
                self._rerank_scores, query, [c.content for c in candidates]
            )
        except Exception as exc:  # pragma: no cover - optional dependency path
            logger.warning(
                "Reranker '%s' unavailable (%s); keeping first-stage order",
                self.retrieval.reranker_model,
                exc,
            )
            return candidates

        reranked = [
            c.model_copy(update={"score": float(s)})
            for c, s in zip(candidates, scores)
        ]
        reranked.sort(key=lambda x: x.score, reverse=True)
        return reranked

    def _rerank_scores(self, query: str, docs: List[str]) -> List[float]:
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(self.retrieval.reranker_model)
        pairs = [[query, d] for d in docs]
        return [float(s) for s in model.predict(pairs)]


def get_retriever(
    retrieval: RetrievalConfig,
    embedding: EmbeddingConfig,
    vectorstore: VectorStoreConfig,
    credentials: Optional[CredentialsConfig] = None,
    llm: Optional[LLMConfig] = None,
) -> Retriever:
    """Factory mirroring the other provider factories for symmetry."""
    return Retriever(retrieval, embedding, vectorstore, credentials, llm)

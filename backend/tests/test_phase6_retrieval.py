"""Phase 6 smoke tests: retrieval (similarity / mmr / hybrid), prompt synthesis,
generator provider switching, and the /playground/query route (JSON + SSE).

Everything external is faked — no embedding model downloads, no vector DB, and no
LLM API calls. We verify:
  - the retriever maps store hits to contract-shaped sources (web -> source_url,
    local -> file path) and honours top_k / score_threshold;
  - hybrid and mmr search types reorder/limit results without error;
  - build_context_prompt embeds numbered sources + the question;
  - the LLM provider factory selects the right provider per config;
  - POST /playground/query returns {answer, sources[]} for stream=false and a
    well-formed SSE stream (source -> token... -> done) for stream=true;
  - sources cite a web URL for web chunks and a file path for local chunks.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config_schemas import (
    CredentialsConfig,
    EmbeddingConfig,
    LLMConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from app.rag import retriever as retriever_mod
from app.rag.generator import (
    AnthropicLLM,
    Generator,
    OllamaLLM,
    OpenAICompatibleLLM,
    build_context_prompt,
    get_llm_provider,
)
from app.rag.retriever import RetrievedChunk, Retriever

# ── Fakes ──────────────────────────────────────────────────────────────


class _FakeEmbedder:
    """Deterministic tiny embeddings: vector keyed on token presence."""

    _VOCAB = ["timeout", "widget", "config", "server", "intro"]

    def _vec(self, text: str) -> list[float]:
        low = text.lower()
        return [1.0 if word in low else 0.0 for word in self._VOCAB] + [0.1]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    @property
    def dimensions(self) -> int:
        return len(self._VOCAB) + 1


class _FakeStore:
    """Returns a fixed candidate pool; ignores the query vector."""

    def __init__(self, hits):
        self._hits = hits

    async def connect(self):
        return None

    async def search(self, query_vector, top_k, filters=None):
        return self._hits[:top_k]


class _QueryAwareStore:
    """Returns different hits depending on which vocab flag the query set."""

    async def connect(self):
        return None

    async def search(self, query_vector, top_k, filters=None):
        if query_vector[0]:  # "timeout"
            return [_WEB_HIT]
        if query_vector[1]:  # "widget"
            return [_LOCAL_HIT]
        return []


class _CorpusStore(_FakeStore):
    """Fixed dense pool plus a full corpus for BM25 fetch_all."""

    def __init__(self, hits, corpus):
        super().__init__(hits)
        self._corpus = corpus

    async def fetch_all(self, filters=None, limit=20_000):
        return self._corpus[:limit]


class _FakeLLMProvider:
    """Stands in for the LLM provider behind multi-query / compression."""

    def __init__(self, respond):
        self._respond = respond

    async def generate(
        self, prompt, system_prompt=None, temperature=0.0, max_tokens=1024
    ):
        return self._respond(prompt)


_WEB_HIT = {
    "id": "w1",
    "score": 0.91,
    "content": "Set the request_timeout_s parameter to control server timeout.",
    "metadata": {
        "source_url": "https://example.com/docs/intro",
        "title": "Intro to Widgets",
    },
}
_LOCAL_HIT = {
    "id": "l1",
    "score": 0.74,
    "content": "Widgets are documented in the local README config notes.",
    "metadata": {
        "source": "docs/readme.md",
        "file_name": "readme.md",
        "file_type": ".md",
    },
}
_LOW_HIT = {
    "id": "x1",
    "score": 0.05,
    "content": "Completely unrelated content about something else entirely here.",
    "metadata": {"source": "docs/other.txt", "file_name": "other.txt"},
}


def _build_retriever(
    monkeypatch, hits, retrieval: RetrievalConfig, store=None, llm_respond=None
) -> Retriever:
    monkeypatch.setattr(
        retriever_mod, "get_embedding_provider", lambda *a, **k: _FakeEmbedder()
    )
    monkeypatch.setattr(
        retriever_mod,
        "get_vector_store",
        lambda *a, **k: store if store is not None else _FakeStore(hits),
    )
    r = Retriever(
        retrieval=retrieval,
        embedding=EmbeddingConfig(),
        vectorstore=VectorStoreConfig(),
        credentials=CredentialsConfig(),
        llm=LLMConfig() if llm_respond else None,
    )
    if llm_respond:
        r._llm_provider = _FakeLLMProvider(llm_respond)
    return r


# ── Retriever tests ────────────────────────────────────────────────────


def test_similarity_maps_sources_web_and_local(monkeypatch):
    r = _build_retriever(
        monkeypatch, [_WEB_HIT, _LOCAL_HIT], RetrievalConfig(top_k=5)
    )
    chunks = asyncio.run(r.retrieve("how to set server timeout"))
    assert len(chunks) == 2

    web = chunks[0]
    assert web.source_type == "web"
    assert web.source_path_or_url == "https://example.com/docs/intro"
    assert web.title == "Intro to Widgets"

    local = chunks[1]
    assert local.source_type == "local"
    assert local.source_path_or_url == "docs/readme.md"

    # Contract source shape.
    src = web.as_source()
    assert set(src.keys()) == {
        "source_type",
        "source_path_or_url",
        "title",
        "snippet",
        "score",
    }


def test_top_k_limits_results(monkeypatch):
    r = _build_retriever(
        monkeypatch, [_WEB_HIT, _LOCAL_HIT, _LOW_HIT], RetrievalConfig(top_k=1)
    )
    chunks = asyncio.run(r.retrieve("timeout"))
    assert len(chunks) == 1


def test_score_threshold_filters(monkeypatch):
    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT, _LOCAL_HIT, _LOW_HIT],
        RetrievalConfig(top_k=5, score_threshold=0.5),
    )
    chunks = asyncio.run(r.retrieve("timeout"))
    # The 0.05-scored hit is dropped by the threshold.
    assert all(c.source_path_or_url != "docs/other.txt" for c in chunks)


def test_hybrid_search_runs_and_ranks(monkeypatch):
    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT, _LOCAL_HIT, _LOW_HIT],
        RetrievalConfig(top_k=3, search_type="hybrid", hybrid_alpha=0.5),
    )
    chunks = asyncio.run(r.retrieve("server timeout config"))
    assert chunks
    # Query terms overlap the web/local hits, not the unrelated one -> it ranks last.
    assert chunks[0].source_path_or_url in (
        "https://example.com/docs/intro",
        "docs/readme.md",
    )


def test_mmr_search_runs(monkeypatch):
    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT, _LOCAL_HIT, _LOW_HIT],
        RetrievalConfig(top_k=2, search_type="mmr", mmr_diversity=0.5),
    )
    chunks = asyncio.run(r.retrieve("timeout"))
    assert 1 <= len(chunks) <= 2


# ── Advanced retrieval: multi-query / BM25 hybrid / compression ─────────


def test_multi_query_merges_variant_results(monkeypatch):
    # The original query only hits the web doc; the LLM variant hits the
    # local doc — multi-query should union both without duplicates.
    r = _build_retriever(
        monkeypatch,
        [],
        RetrievalConfig(top_k=5, multi_query=True, multi_query_count=2),
        store=_QueryAwareStore(),
        llm_respond=lambda prompt: "widget documentation\nwidget notes",
    )
    chunks = asyncio.run(r.retrieve("timeout"))
    paths = {c.source_path_or_url for c in chunks}
    assert paths == {"https://example.com/docs/intro", "docs/readme.md"}
    assert len(chunks) == 2


def test_multi_query_falls_back_when_llm_fails(monkeypatch):
    def boom(prompt):
        raise RuntimeError("no api key")

    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT],
        RetrievalConfig(top_k=5, multi_query=True),
        llm_respond=boom,
    )
    chunks = asyncio.run(r.retrieve("timeout"))
    assert len(chunks) == 1  # original query still retrieves


def test_bm25_hybrid_surfaces_keyword_only_match(monkeypatch):
    # A doc only findable by keyword: absent from the dense candidates but
    # present in the corpus. BM25 hybrid must pull it in and rank it first.
    keyword_hit = {
        "id": "k1",
        "score": 0.0,
        "content": "pgvector is a PostgreSQL extension for similarity search.",
        "metadata": {"source": "docs/pgvector.md", "file_name": "pgvector.md"},
    }
    store = _CorpusStore(
        [_WEB_HIT, _LOCAL_HIT], [_WEB_HIT, _LOCAL_HIT, keyword_hit]
    )
    r = _build_retriever(
        monkeypatch,
        [],
        RetrievalConfig(
            top_k=3, search_type="hybrid", hybrid_method="bm25", hybrid_alpha=0.3
        ),
        store=store,
    )
    chunks = asyncio.run(r.retrieve("pgvector"))
    assert chunks[0].source_path_or_url == "docs/pgvector.md"


def test_bm25_falls_back_to_token_overlap_without_fetch_all(monkeypatch):
    # _FakeStore has no fetch_all — the BM25 stage must degrade gracefully.
    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT, _LOCAL_HIT, _LOW_HIT],
        RetrievalConfig(top_k=3, search_type="hybrid", hybrid_method="bm25"),
    )
    chunks = asyncio.run(r.retrieve("server timeout config"))
    assert chunks
    assert chunks[0].source_path_or_url != "docs/other.txt"


def test_contextual_compression_extracts_and_drops(monkeypatch):
    def respond(prompt):
        if "request_timeout_s" in prompt:
            return "Set the request_timeout_s parameter."
        return "NO_OUTPUT"

    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT, _LOCAL_HIT],
        RetrievalConfig(top_k=5, contextual_compression=True),
        llm_respond=respond,
    )
    chunks = asyncio.run(r.retrieve("server timeout"))
    # The irrelevant local chunk is dropped; the web chunk is compressed.
    assert len(chunks) == 1
    assert chunks[0].source_path_or_url == "https://example.com/docs/intro"
    assert chunks[0].content == "Set the request_timeout_s parameter."
    assert "request_timeout_s" in chunks[0].snippet


def test_compression_keeps_chunk_when_llm_fails(monkeypatch):
    def boom(prompt):
        raise RuntimeError("provider down")

    r = _build_retriever(
        monkeypatch,
        [_WEB_HIT],
        RetrievalConfig(top_k=5, contextual_compression=True),
        llm_respond=boom,
    )
    chunks = asyncio.run(r.retrieve("timeout"))
    assert len(chunks) == 1
    assert chunks[0].content == _WEB_HIT["content"]


# ── Prompt synthesis + provider factory ─────────────────────────────────


def test_build_context_prompt_numbers_sources():
    chunks = [
        RetrievedChunk(
            source_type="web",
            source_path_or_url="https://example.com/docs/intro",
            title="Intro",
            snippet="...",
            score=0.9,
            content="Set request_timeout_s to control timeout.",
        ),
        RetrievedChunk(
            source_type="local",
            source_path_or_url="docs/readme.md",
            snippet="...",
            score=0.7,
            content="Local config notes.",
        ),
    ]
    prompt = build_context_prompt("How do I set the timeout?", chunks)
    assert "[1]" in prompt and "[2]" in prompt
    assert "https://example.com/docs/intro" in prompt
    assert "docs/readme.md" in prompt
    assert "How do I set the timeout?" in prompt


def test_llm_provider_factory_switching():
    creds = CredentialsConfig(
        anthropic_api_key="a",
        openai_api_key="o",
        groq_api_key="g",
    )
    assert isinstance(
        get_llm_provider(LLMConfig(llm_provider="anthropic"), creds), AnthropicLLM
    )
    assert isinstance(
        get_llm_provider(LLMConfig(llm_provider="openai"), creds),
        OpenAICompatibleLLM,
    )
    assert isinstance(
        get_llm_provider(LLMConfig(llm_provider="groq"), creds),
        OpenAICompatibleLLM,
    )
    assert isinstance(
        get_llm_provider(LLMConfig(llm_provider="ollama"), creds), OllamaLLM
    )


# ── Route tests (JSON + SSE) ────────────────────────────────────────────


class _FakeGenerator:
    """Stands in for Generator: deterministic answer + token stream."""

    def __init__(self, *args, **kwargs):
        pass

    async def generate(self, query, chunks):
        return "Adjust request_timeout_s to set the server timeout. [1]"

    async def generate_stream(self, query, chunks):
        for tok in ["Adjust ", "request_timeout_s", " to set ", "the timeout. [1]"]:
            yield tok


@pytest.fixture
def _patch_playground(monkeypatch):
    from app.routes import playground as pg

    async def fake_retrieve(config, query):
        return [
            RetrievedChunk(
                source_type="web",
                source_path_or_url="https://example.com/docs/intro",
                title="Intro to Widgets",
                snippet="...set request_timeout_s parameter...",
                score=0.89,
                content="Set request_timeout_s to control server timeout.",
            )
        ]

    monkeypatch.setattr(pg, "_retrieve", fake_retrieve)
    monkeypatch.setattr(pg, "Generator", _FakeGenerator)


def test_query_non_streaming_returns_answer_and_sources(
    client, auth_headers, _patch_playground
):
    resp = client.post(
        "/playground/query",
        headers=auth_headers,
        json={"query": "How do I configure the server timeout?", "stream": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "request_timeout_s" in body["answer"]
    assert len(body["sources"]) == 1
    src = body["sources"][0]
    assert src["source_type"] == "web"
    assert src["source_path_or_url"] == "https://example.com/docs/intro"


def test_query_streaming_emits_source_token_done(
    client, auth_headers, _patch_playground
):
    resp = client.post(
        "/playground/query",
        headers=auth_headers,
        json={"query": "How do I configure the server timeout?", "stream": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")

    raw = resp.text
    assert "event: source" in raw
    assert "event: token" in raw
    assert "event: done" in raw

    # The done event carries the assembled answer + sources.
    done_block = raw.split("event: done")[-1]
    data_line = next(
        line for line in done_block.splitlines() if line.startswith("data:")
    )
    done_payload = json.loads(data_line[len("data:") :].strip())
    assert "request_timeout_s" in done_payload["answer"]
    assert done_payload["sources"][0]["source_path_or_url"] == (
        "https://example.com/docs/intro"
    )


def test_query_requires_auth(client):
    resp = client.post(
        "/playground/query", json={"query": "hi", "stream": False}
    )
    assert resp.status_code == 401


def test_query_rejects_empty_query(client, auth_headers):
    resp = client.post(
        "/playground/query",
        headers=auth_headers,
        json={"query": "   ", "stream": False},
    )
    assert resp.status_code == 422

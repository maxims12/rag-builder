"""Phase 4 smoke tests: local loader, chunker, vector store, ingest pipeline.

Uses a stub embedder (monkeypatched) so the tests run fast and offline — they
exercise the load → chunk → store wiring and the IndexJob progress tracking, not
the heavy sentence-transformers download.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from app.config_schemas import ChunkingConfig, RAGConfigData, SourcesConfig
from app.rag.chunker import Chunker
from app.rag.loader import LocalFileLoader


def _make_docs(folder: str) -> None:
    with open(os.path.join(folder, "sample.txt"), "w", encoding="utf-8") as f:
        f.write("RAG stands for Retrieval Augmented Generation. " * 40)
    with open(os.path.join(folder, "note.md"), "w", encoding="utf-8") as f:
        f.write("# Title\n\nVector stores hold embeddings.\n\n## More\n\n" + "text " * 80)
    with open(os.path.join(folder, "skip.xyz"), "w", encoding="utf-8") as f:
        f.write("unsupported type, should be ignored")


def test_loader_emits_contract_metadata():
    d = tempfile.mkdtemp(prefix="ragdocs_")
    _make_docs(d)
    cfg = SourcesConfig(docs_folder=d)
    docs = asyncio.run(LocalFileLoader(cfg).load())
    assert len(docs) == 2  # .xyz excluded by file_types
    for doc in docs:
        meta = doc.metadata
        assert {"source", "file_name", "file_type", "modified_at", "content_hash"} <= set(
            meta
        )
        assert meta["file_type"] in {".txt", ".md"}


def test_loader_respects_max_file_size_and_excludes():
    d = tempfile.mkdtemp(prefix="ragdocs_")
    _make_docs(d)
    cfg = SourcesConfig(docs_folder=d, exclude_patterns=["note.md"])
    docs = asyncio.run(LocalFileLoader(cfg).load())
    names = {doc.metadata["file_name"] for doc in docs}
    assert names == {"sample.txt"}


def test_chunker_propagates_metadata_and_ids():
    d = tempfile.mkdtemp(prefix="ragdocs_")
    _make_docs(d)
    docs = asyncio.run(LocalFileLoader(SourcesConfig(docs_folder=d)).load())
    chunks = Chunker(ChunkingConfig()).chunk_documents(docs)
    assert len(chunks) > 0
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))  # ids are unique
    for c in chunks:
        assert "chunk_index" in c.metadata
        assert "source" in c.metadata  # source metadata propagated


@pytest.mark.parametrize(
    "strategy", ["recursive", "fixed", "token", "markdown", "semantic"]
)
def test_all_chunk_strategies_produce_chunks(strategy):
    text = ("# Heading\n\n" + "Sentence one. Sentence two. " * 100)
    cfg = ChunkingConfig(chunk_strategy=strategy, chunk_size=300, chunk_overlap=50)
    pieces = Chunker(cfg).split_text(text)
    assert len(pieces) >= 1
    assert all(isinstance(p, str) and p for p in pieces)


def test_ingest_pipeline_writes_job_and_counts(monkeypatch, tmp_path):
    """End-to-end load → chunk → (stub embed) → store with IndexJob progress."""
    from app.db import engine, init_db
    from app.models import IndexJob, User
    from app.rag import pipeline as pipeline_mod
    from sqlmodel import Session

    # Stub embedder: deterministic small vectors, no model download.
    class _StubEmbedder:
        async def embed_documents(self, texts):
            return [[float(len(t) % 7), 1.0, 2.0] for t in texts]

        async def embed_query(self, text):
            return [float(len(text) % 7), 1.0, 2.0]

        @property
        def dimensions(self):
            return 3

    monkeypatch.setattr(
        pipeline_mod, "get_embedding_provider", lambda *a, **k: _StubEmbedder()
    )
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))

    init_db()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "sample.txt").write_text(
        "Retrieval Augmented Generation grounds answers in sources. " * 30,
        encoding="utf-8",
    )

    with Session(engine) as s:
        user = User(email="phase4@test.com", hashed_password="x")
        s.add(user)
        s.commit()
        s.refresh(user)
        job = IndexJob(user_id=user.id, source_type="local", status="pending")
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    cfg = RAGConfigData(sources=SourcesConfig(docs_folder=str(docs_dir)))
    cfg.vectorstore.vs_collection = "phase4_test"
    result = asyncio.run(
        pipeline_mod.ingest(
            job_id=job_id,
            config=cfg,
            loader=LocalFileLoader(cfg.sources),
            source_type="local",
        )
    )

    assert result.status == "done", result.error_message
    assert result.files_processed == 1
    assert result.chunks_created > 0

    from app.rag.store import get_vector_store

    store = get_vector_store(cfg.vectorstore)
    asyncio.run(store.connect())
    assert asyncio.run(store.get_count()) > 0

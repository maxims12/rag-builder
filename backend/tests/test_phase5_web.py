"""Phase 5 smoke tests: web loader (single/crawl/sitemap), dedup, downstream reuse.

All network is mocked — these tests never make a real HTTP request. They verify:
  - the web loader emits ParsedDocument records with the contract web-metadata
    shape {source_url, title, fetched_at, content_hash};
  - sitemap.xml parsing works on a fixture string (namespaced + index);
  - content_hash de-duplication: known hashes are skipped, and the same page
    reached twice in one run is emitted once;
  - the crawl mode follows in-domain links and respects same_domain_only;
  - the chunk → embed → store path consumes web records identically to local ones.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config_schemas import (
    ChunkingConfig,
    RAGConfigData,
    WebSourcesConfig,
)
from app.rag import web_loader
from app.rag.chunker import Chunker
from app.rag.loader import ParsedDocument, compute_content_hash
from app.rag.web_loader import WebSourceLoader, extract_url, parse_sitemap

# ── Fixtures / fakes ──────────────────────────────────────────────────

_PAGE_HTML = """
<html>
  <head><title>Intro to Widgets</title></head>
  <body>
    <nav>Home | Docs | Blog</nav>
    <header>Site Header Junk</header>
    <main>
      <h1>Intro to Widgets</h1>
      <p>Widgets are configured through the request_timeout_s parameter.
      You can adjust it in the settings page to control how long fetches wait.</p>
      <p>This is the core documentation content that should survive extraction
      while the navigation and footer boilerplate is stripped away cleanly.</p>
    </main>
    <footer>Copyright 2026 — nav junk footer</footer>
    <a href="/docs/page2">Page Two</a>
    <a href="https://external.example.org/other">External</a>
  </body>
</html>
"""

_PAGE2_HTML = """
<html>
  <head><title>Widgets Page Two</title></head>
  <body>
    <main>
      <h1>Widgets Page Two</h1>
      <p>The second documentation page describes advanced widget configuration
      options and the crawl_depth knob that controls link following behaviour.</p>
    </main>
  </body>
</html>
"""

_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/a</loc></url>
  <url><loc>https://docs.example.com/b</loc></url>
  <url><loc>https://docs.example.com/c</loc></url>
</urlset>
"""

_SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.example.com/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
"""


def _patch_static_fetch(monkeypatch, mapping: dict[str, str]) -> None:
    """Patch the loader's static fetch to serve from an in-memory URL->HTML map."""

    async def _fake_fetch(url, timeout):
        if url in mapping:
            return mapping[url], None
        return "", f"404 not found: {url}"

    monkeypatch.setattr(web_loader, "_fetch_html_static", _fake_fetch)
    # Robots always allows in tests (avoid network for robots.txt).
    async def _allow(self, url):
        return True

    monkeypatch.setattr(web_loader._RobotsChecker, "allowed", _allow)


# ── Sitemap parsing ───────────────────────────────────────────────────


def test_parse_sitemap_namespaced():
    urls = parse_sitemap(_SITEMAP_XML)
    assert urls == [
        "https://docs.example.com/a",
        "https://docs.example.com/b",
        "https://docs.example.com/c",
    ]


def test_parse_sitemap_index():
    urls = parse_sitemap(_SITEMAP_INDEX_XML)
    assert urls == ["https://docs.example.com/sitemap-pages.xml"]


def test_parse_sitemap_malformed_returns_empty():
    assert parse_sitemap("<not valid xml") == []


# ── extract_url + contract metadata shape ─────────────────────────────


def test_extract_url_returns_clean_text_and_metadata(monkeypatch):
    _patch_static_fetch(monkeypatch, {"https://docs.example.com/intro": _PAGE_HTML})
    result = asyncio.run(extract_url("https://docs.example.com/intro"))
    assert result.error is None
    assert result.title == "Intro to Widgets"
    # Boilerplate stripped: nav/footer junk must not be in the clean text.
    assert "request_timeout_s" in result.clean_text
    assert "nav junk footer" not in result.clean_text
    assert result.extracted_text_length == len(result.clean_text)
    assert result.raw_html_length == len(_PAGE_HTML)
    assert result.content_hash == compute_content_hash(result.clean_text)
    # ISO 8601 fetched_at
    assert "T" in result.fetched_at


def test_strip_selectors_drops_nodes(monkeypatch):
    _patch_static_fetch(monkeypatch, {"https://docs.example.com/intro": _PAGE_HTML})
    result = asyncio.run(
        extract_url("https://docs.example.com/intro", strip_selectors=["main"])
    )
    # With <main> stripped, the core content disappears.
    assert "request_timeout_s" not in result.clean_text


def test_single_mode_emits_contract_web_metadata(monkeypatch):
    _patch_static_fetch(monkeypatch, {"https://docs.example.com/intro": _PAGE_HTML})
    cfg = WebSourcesConfig(web_mode="single", web_urls=["https://docs.example.com/intro"])
    docs = asyncio.run(WebSourceLoader(cfg).load())
    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, ParsedDocument)
    assert {"source_url", "title", "fetched_at", "content_hash"} <= set(doc.metadata)
    assert doc.metadata["source_url"] == "https://docs.example.com/intro"
    assert doc.metadata["title"] == "Intro to Widgets"


# ── content_hash dedup ────────────────────────────────────────────────


def test_known_hash_skips_unchanged_page(monkeypatch):
    _patch_static_fetch(monkeypatch, {"https://docs.example.com/intro": _PAGE_HTML})
    # First load to learn the hash.
    cfg = WebSourcesConfig(web_mode="single", web_urls=["https://docs.example.com/intro"])
    first = asyncio.run(WebSourceLoader(cfg).load())
    known = {first[0].metadata["content_hash"]}
    # Re-load with the hash known -> page should be skipped (incremental).
    second = asyncio.run(WebSourceLoader(cfg, known_hashes=known).load())
    assert second == []


def test_intra_run_duplicate_pages_emitted_once(monkeypatch):
    # Two distinct URLs that return identical content -> same hash -> one doc.
    _patch_static_fetch(
        monkeypatch,
        {
            "https://docs.example.com/a": _PAGE_HTML,
            "https://docs.example.com/a/": _PAGE_HTML,
            "https://docs.example.com/mirror": _PAGE_HTML,
        },
    )
    cfg = WebSourcesConfig(
        web_mode="single",
        web_urls=[
            "https://docs.example.com/a",
            "https://docs.example.com/mirror",
        ],
    )
    docs = asyncio.run(WebSourceLoader(cfg).load())
    assert len(docs) == 1


# ── sitemap mode end-to-end (mocked) ──────────────────────────────────


def test_sitemap_mode_fetches_listed_pages(monkeypatch):
    sitemap_url = "https://docs.example.com/sitemap.xml"
    mapping = {
        sitemap_url: _SITEMAP_XML,
        "https://docs.example.com/a": _PAGE_HTML,
        "https://docs.example.com/b": _PAGE2_HTML,
        # c returns empty/error -> dropped
    }
    _patch_static_fetch(monkeypatch, mapping)
    cfg = WebSourcesConfig(web_mode="sitemap", sitemap_url=sitemap_url)
    docs = asyncio.run(WebSourceLoader(cfg).load())
    urls = {d.metadata["source_url"] for d in docs}
    assert urls == {"https://docs.example.com/a", "https://docs.example.com/b"}


# ── crawl mode: in-domain link following + same_domain_only ────────────


def test_crawl_follows_in_domain_links_only(monkeypatch):
    mapping = {
        "https://docs.example.com/docs/intro": _PAGE_HTML,
        "https://docs.example.com/docs/page2": _PAGE2_HTML,
    }
    _patch_static_fetch(monkeypatch, mapping)
    cfg = WebSourcesConfig(
        web_mode="crawl",
        web_urls=["https://docs.example.com/docs/intro"],
        crawl_depth=1,
        max_pages=10,
        same_domain_only=True,
    )
    docs = asyncio.run(WebSourceLoader(cfg).load())
    urls = {d.metadata["source_url"] for d in docs}
    # The external link must never be fetched/included.
    assert "https://external.example.org/other" not in urls
    # Seed page is always included; page2 is reached via the in-domain link.
    assert "https://docs.example.com/docs/intro" in urls
    assert "https://docs.example.com/docs/page2" in urls


def test_crawl_respects_max_pages(monkeypatch):
    mapping = {
        "https://docs.example.com/docs/intro": _PAGE_HTML,
        "https://docs.example.com/docs/page2": _PAGE2_HTML,
    }
    _patch_static_fetch(monkeypatch, mapping)
    cfg = WebSourcesConfig(
        web_mode="crawl",
        web_urls=["https://docs.example.com/docs/intro"],
        crawl_depth=3,
        max_pages=1,
        same_domain_only=True,
    )
    docs = asyncio.run(WebSourceLoader(cfg).load())
    assert len(docs) == 1


# ── downstream reuse: web records flow through chunk → embed → store ───


class _StubEmbedder:
    async def embed_documents(self, texts):
        return [[float(len(t) % 5), 0.5, 1.5] for t in texts]

    async def embed_query(self, text):
        return [float(len(text) % 5), 0.5, 1.5]

    @property
    def dimensions(self):
        return 3


def test_web_records_chunk_identically_to_local():
    """A web ParsedDocument must chunk through the SAME chunker as local files."""
    web_doc = ParsedDocument(
        text="Widget configuration is controlled by request_timeout_s. " * 40,
        metadata={
            "source_url": "https://docs.example.com/intro",
            "title": "Intro",
            "fetched_at": "2026-06-20T00:00:00+00:00",
            "content_hash": compute_content_hash("seed"),
        },
    )
    chunks = Chunker(ChunkingConfig()).chunk_documents([web_doc])
    assert len(chunks) > 0
    for c in chunks:
        # Source web metadata is propagated unchanged onto every chunk.
        assert c.metadata["source_url"] == "https://docs.example.com/intro"
        assert "chunk_id" in c.metadata
        assert "chunk_index" in c.metadata
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))


def test_web_ingest_pipeline_stores_pages(monkeypatch, tmp_path):
    """Full web load → chunk → (stub embed) → store, with pages_fetched tracked."""
    from sqlmodel import Session

    from app.db import engine, init_db
    from app.models import IndexJob, User
    from app.rag import pipeline as pipeline_mod

    _patch_static_fetch(
        monkeypatch,
        {
            "https://docs.example.com/a": _PAGE_HTML,
            "https://docs.example.com/b": _PAGE2_HTML,
        },
    )
    monkeypatch.setattr(
        pipeline_mod, "get_embedding_provider", lambda *a, **k: _StubEmbedder()
    )
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))

    init_db()
    with Session(engine) as s:
        user = User(email="phase5@test.com", hashed_password="x")
        s.add(user)
        s.commit()
        s.refresh(user)
        job = IndexJob(user_id=user.id, source_type="web", status="pending")
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    cfg = RAGConfigData(
        web_sources=WebSourcesConfig(
            web_mode="single",
            web_urls=["https://docs.example.com/a", "https://docs.example.com/b"],
        )
    )
    cfg.vectorstore.vs_collection = "phase5_web_test"

    loader = WebSourceLoader(cfg.web_sources)
    result = asyncio.run(
        pipeline_mod.ingest(
            job_id=job_id, config=cfg, loader=loader, source_type="web"
        )
    )

    assert result.status == "done", result.error_message
    assert result.pages_fetched == 2
    assert result.files_processed == 0
    assert result.chunks_created > 0

    from app.rag.store import get_vector_store

    store = get_vector_store(cfg.vectorstore)
    asyncio.run(store.connect())
    assert asyncio.run(store.get_count()) > 0

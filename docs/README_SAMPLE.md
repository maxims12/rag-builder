# RAG System Builder — Sample Knowledge Base

This document is seeded into the `docs/` folder so your very first indexing run has
something real to ingest. The loader walks this folder, parses the file, chunks it,
embeds the chunks, and stores them in the vector store. Once indexed, head to the
**Playground** page and ask one of the test questions at the bottom — a correct answer
that cites this file confirms the whole pipeline works end to end.

Replace this file with your own documents (PDF, DOCX, Markdown, TXT, HTML, CSV) whenever
you are ready.

## What this app does

RAG System Builder scans a documents folder and a set of web sources, ingests everything
into a single vector collection, and exposes a configurable retrieval-augmented-generation
(RAG) pipeline through a mobile-first dashboard with authentication. You configure each
stage of the pipeline from the UI and test it in the playground.

## The ingestion pipeline

Ingestion runs in four stages, each independently configurable:

1. **Load** — local files are read from the docs folder; web sources are fetched and the
   main content is extracted (navigation, ads, and boilerplate are stripped for quality).
2. **Chunk** — text is split into overlapping segments. Strategies include recursive,
   semantic, fixed, markdown, and token-based splitting. Chunk size and overlap are tunable.
3. **Embed** — each chunk is turned into a vector using the configured embedding provider
   (local sentence-transformers, or API providers such as OpenAI, Cohere, or Voyage).
4. **Store** — vectors are written to the vector store (ChromaDB for development, Qdrant
   for production), behind a single swappable interface.

Every ingestion run is tracked as an IndexJob, so the Overview page can show live progress:
files processed, pages fetched, and chunks created.

## Local file sources

The Sources page configures the local folder scan: the docs folder path, which file types
to include, a maximum file size, exclude patterns, and an optional watch mode. When watch
mode is on, a file-system watcher re-indexes changed files automatically.

## Web sources

The Web Sources page ingests pages from the internet in three modes:

- **Single / multi URL** — fetch specific pages and extract their clean main content.
- **Recursive crawl** — follow in-domain links up to a configured depth, capped by a
  maximum page count, optionally staying on the same domain.
- **Sitemap** — read a sitemap.xml and enqueue every URL it lists.

Each web chunk records its source URL, page title, fetch time, and a content hash. The
content hash lets scheduled re-crawls skip pages that have not changed, so only updated
pages are re-embedded.

## Retrieval and generation

The Retrieval page controls how chunks are selected for a query: the number of results
(top-k), a score threshold, and the search type (similarity, MMR for diversity, or hybrid).
Optional reranking can reorder candidates for relevance.

The selected chunks become the context for the language model, which synthesizes an answer
and streams it token by token. Answers include source citations: a web answer links to its
source URL, and a file answer cites its file path.

## Configuration and security

All settings are stored per user in a local SQLite database and edited section by section
from the dashboard. API keys for providers are kept server-side and are never displayed
again after they are saved. Authentication uses JWT access and refresh tokens with bcrypt
password hashing.

## Test questions to try in the Playground

- What are the four stages of the ingestion pipeline?
- What web source modes does the app support?
- How does the app avoid re-embedding web pages that have not changed?
- What search types are available on the Retrieval page?
- Where are provider API keys stored, and are they shown again after saving?

If retrieval and generation are working correctly, the system should answer these
accurately and cite this document as the source.

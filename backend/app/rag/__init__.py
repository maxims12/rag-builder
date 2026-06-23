"""RAG pipeline package: loader, chunker, embedder, store, pipeline.

All ingestion sources (local files in Phase 4, web sources in Phase 5) emit the
same :class:`~app.rag.loader.ParsedDocument` shape, so the chunk → embed → store
path is shared and reused unchanged.
"""

"""Chunking strategies: split ParsedDocuments into embeddable chunks.

A :class:`Chunk` carries the chunk text plus the *source* document metadata
(propagated unchanged) augmented with ``chunk_index`` and ``chunk_id``. Because
the chunker consumes the shared :class:`~app.rag.loader.ParsedDocument` shape, it
works identically for local files and (Phase 5) web pages.

Strategies (per ``ChunkingConfig.chunk_strategy``):
  - ``recursive``: split on a descending list of separators (paragraph → line →
    sentence → space), packing into ``chunk_size`` with ``chunk_overlap``.
  - ``fixed``: hard character windows with overlap.
  - ``token``: approximate token windows (whitespace tokens) with overlap.
  - ``markdown``: split on markdown headings, then recursively pack oversize
    sections.
  - ``semantic``: sentence-grouping fallback (no extra model dependency) that
    packs whole sentences up to ``chunk_size`` respecting boundaries.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.config_schemas import ChunkingConfig
from app.rag.loader import ParsedDocument

# Rough chars-per-token ratio for the approximate token strategy.
_CHARS_PER_TOKEN = 4

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+.*$", re.MULTILINE)


class Chunk(BaseModel):
    """A single embeddable text chunk plus its propagated source metadata."""

    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _chunk_id(content_hash: str, index: int) -> str:
    """Deterministic, stable id for a chunk (source hash + index)."""
    base = f"{content_hash}:{index}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def _pack(units: List[str], size: int, overlap: int, joiner: str) -> List[str]:
    """Greedily pack text units into windows of ~``size`` chars with overlap."""
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for unit in units:
        unit_len = len(unit)
        if current and current_len + unit_len + len(joiner) > size:
            chunks.append(joiner.join(current))
            # Build overlap tail from the end of the just-emitted window.
            if overlap > 0:
                tail: List[str] = []
                tail_len = 0
                for u in reversed(current):
                    if tail_len + len(u) > overlap:
                        break
                    tail.insert(0, u)
                    tail_len += len(u) + len(joiner)
                current = tail
                current_len = sum(len(u) + len(joiner) for u in current)
            else:
                current = []
                current_len = 0
        current.append(unit)
        current_len += unit_len + len(joiner)
    if current:
        chunks.append(joiner.join(current))
    return chunks


def _split_fixed(text: str, size: int, overlap: int) -> List[str]:
    if size <= 0:
        return [text]
    step = max(1, size - max(0, overlap))
    return [text[i : i + size] for i in range(0, len(text), step)]


def _split_recursive(text: str, cfg: ChunkingConfig) -> List[str]:
    """Recursive split: paragraphs → lines → sentences → fixed fallback."""
    if len(text) <= cfg.chunk_size:
        return [text]

    separators = ["\n\n", "\n"]
    for sep in separators:
        parts = [p for p in text.split(sep) if p.strip()]
        if len(parts) > 1:
            return _pack(parts, cfg.chunk_size, cfg.chunk_overlap, sep)

    if cfg.respect_sentence_boundary:
        sentences = _split_sentences(text)
        if len(sentences) > 1:
            return _pack(sentences, cfg.chunk_size, cfg.chunk_overlap, " ")

    return _split_fixed(text, cfg.chunk_size, cfg.chunk_overlap)


def _split_token(text: str, cfg: ChunkingConfig) -> List[str]:
    """Approximate token windows using whitespace tokenisation."""
    tokens = text.split()
    if not tokens:
        return []
    token_window = max(1, cfg.chunk_size // _CHARS_PER_TOKEN)
    token_overlap = max(0, cfg.chunk_overlap // _CHARS_PER_TOKEN)
    step = max(1, token_window - token_overlap)
    chunks: List[str] = []
    for i in range(0, len(tokens), step):
        chunks.append(" ".join(tokens[i : i + token_window]))
        if i + token_window >= len(tokens):
            break
    return chunks


def _split_markdown(text: str, cfg: ChunkingConfig) -> List[str]:
    """Split on markdown headings, then recursively pack oversize sections."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return _split_recursive(text, cfg)

    sections: List[str] = []
    starts = [m.start() for m in matches]
    # Preamble before the first heading.
    if starts[0] > 0:
        pre = text[: starts[0]].strip()
        if pre:
            sections.append(pre)
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)

    chunks: List[str] = []
    for section in sections:
        if len(section) <= cfg.chunk_size:
            chunks.append(section)
        else:
            chunks.extend(_split_recursive(section, cfg))
    return chunks


def _split_semantic(text: str, cfg: ChunkingConfig) -> List[str]:
    """Sentence-grouping chunking (dependency-free semantic approximation)."""
    sentences = _split_sentences(text)
    if not sentences:
        return _split_recursive(text, cfg)
    return _pack(sentences, cfg.chunk_size, cfg.chunk_overlap, " ")


_STRATEGIES = {
    "recursive": _split_recursive,
    "fixed": lambda t, c: _split_fixed(t, c.chunk_size, c.chunk_overlap),
    "token": _split_token,
    "markdown": _split_markdown,
    "semantic": _split_semantic,
}


class Chunker:
    """Applies the configured chunking strategy to ParsedDocuments."""

    def __init__(self, config: ChunkingConfig) -> None:
        self.config = config
        self._splitter = _STRATEGIES.get(config.chunk_strategy, _split_recursive)

    def split_text(self, text: str) -> List[str]:
        raw = self._splitter(text, self.config)
        # Drop sub-minimum chunks; keep a chunk if it's the only content.
        cleaned = [c.strip() for c in raw if c and c.strip()]
        filtered = [c for c in cleaned if len(c) >= self.config.min_chunk_size]
        if not filtered and cleaned:
            # Don't lose tiny-but-only documents (e.g. a one-line sample file).
            return cleaned
        return filtered

    def chunk_document(self, doc: ParsedDocument) -> List[Chunk]:
        content_hash = doc.metadata.get("content_hash") or hashlib.sha256(
            doc.text.encode("utf-8")
        ).hexdigest()
        chunks: List[Chunk] = []
        for index, piece in enumerate(self.split_text(doc.text)):
            meta = dict(doc.metadata)
            meta["chunk_index"] = index
            meta["chunk_id"] = _chunk_id(content_hash, index)
            chunks.append(Chunk(text=piece, metadata=meta))
        return chunks

    def chunk_documents(self, docs: List[ParsedDocument]) -> List[Chunk]:
        out: List[Chunk] = []
        for doc in docs:
            out.extend(self.chunk_document(doc))
        return out

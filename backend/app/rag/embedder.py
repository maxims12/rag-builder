"""Embedding providers behind one interface (CONTRACT.md §4).

Implements the ``EmbeddingProvider`` protocol for:
  - ``huggingface``: local sentence-transformers (default, no API key).
  - ``openai``: OpenAI embeddings API (key from credentials / settings).

The concrete model / SDK client is **lazy-loaded** inside each provider so this
module imports cleanly even when the model isn't downloaded or the SDK key is
absent. No provider-specific logic leaks into routes — callers use
:func:`get_embedding_provider`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Protocol

from app.config_schemas import CredentialsConfig, EmbeddingConfig

logger = logging.getLogger("app.rag.embedder")

# Known output dimensions for common OpenAI models (used when not overridden).
_OPENAI_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: List[str]) -> List[List[float]]: ...

    async def embed_query(self, text: str) -> List[float]: ...

    @property
    def dimensions(self) -> int: ...


class HuggingFaceEmbedder:
    """Local sentence-transformers embedder. Model is loaded on first use."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model = None  # lazy: SentenceTransformer
        self._dims: Optional[int] = config.emb_dimensions

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformers model: %s", self.config.emb_model)
            self._model = SentenceTransformer(
                self.config.emb_model, device=self.config.emb_device
            )
            if self._dims is None:
                self._dims = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def _encode(self, texts: List[str]) -> List[List[float]]:
        model = self._ensure_model()
        vectors = model.encode(
            texts,
            batch_size=self.config.emb_batch_size,
            normalize_embeddings=self.config.emb_normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    async def embed_query(self, text: str) -> List[float]:
        result = await asyncio.to_thread(self._encode, [text])
        return result[0]

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            self._ensure_model()
        return int(self._dims or 0)


class OpenAIEmbedder:
    """OpenAI embeddings API provider. SDK client created on first use."""

    def __init__(self, config: EmbeddingConfig, api_key: Optional[str]) -> None:
        self.config = config
        self._api_key = api_key
        self._client = None  # lazy: AsyncOpenAI
        self._dims: Optional[int] = config.emb_dimensions or _OPENAI_DIMS.get(
            config.emb_model
        )

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "OpenAI embedding provider selected but no API key configured"
                )
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def _embed(self, texts: List[str]) -> List[List[float]]:
        client = self._ensure_client()
        kwargs = {"model": self.config.emb_model, "input": texts}
        if self.config.emb_dimensions:
            kwargs["dimensions"] = self.config.emb_dimensions
        resp = await client.embeddings.create(**kwargs)
        vectors = [item.embedding for item in resp.data]
        if self._dims is None and vectors:
            self._dims = len(vectors[0])
        return vectors

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        batch = self.config.emb_batch_size
        for i in range(0, len(texts), batch):
            out.extend(await self._embed(texts[i : i + batch]))
        return out

    async def embed_query(self, text: str) -> List[float]:
        result = await self._embed([text])
        return result[0]

    @property
    def dimensions(self) -> int:
        return int(self._dims or 0)


def get_embedding_provider(
    config: EmbeddingConfig,
    credentials: Optional[CredentialsConfig] = None,
) -> EmbeddingProvider:
    """Factory: return the configured embedding provider behind the interface.

    ``huggingface`` (local) is the dev default and needs no key. API providers
    pull their key from the stored credentials.
    """
    creds = credentials or CredentialsConfig()
    provider = config.emb_provider

    if provider == "huggingface":
        return HuggingFaceEmbedder(config)
    if provider == "openai":
        return OpenAIEmbedder(config, creds.openai_api_key)

    # cohere / ollama / voyage are declared in the schema but not implemented in
    # Phase 4. Fall back to the local provider so ingestion still works rather
    # than failing the pipeline; the chosen provider is logged for visibility.
    logger.warning(
        "Embedding provider '%s' not implemented in Phase 4; "
        "falling back to local huggingface embedder.",
        provider,
    )
    return HuggingFaceEmbedder(config)

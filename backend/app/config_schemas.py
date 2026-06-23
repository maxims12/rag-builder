"""Pydantic config models for every RAG pipeline section.

These mirror the CONFIG REFERENCE in AGENTS.md and the Shared Data Schemas in
CONTRACT.md exactly (field names, types, defaults). The full :class:`RAGConfigData`
is persisted per-user as a single JSON blob in the ``RAGConfig`` table.

Casing is snake_case throughout (per CONTRACT.md §1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Section names (used by the /settings/config/{section} routes) ──────
SECTION_NAMES = (
    "sources",
    "web_sources",
    "chunking",
    "embedding",
    "vectorstore",
    "retrieval",
    "llm",
    "system",
    "credentials",
)

# Masking value returned for stored API keys (never the raw secret).
MASKED_VALUE = "******"


class SourcesConfig(BaseModel):
    docs_folder: str = "./docs"
    watch_mode: bool = False
    recursive: bool = True
    file_types: list[str] = Field(
        default_factory=lambda: [".pdf", ".docx", ".md", ".txt", ".html", ".csv"]
    )
    exclude_patterns: list[str] = Field(default_factory=list)
    max_file_size_mb: int = 50
    polling_interval: int = 5


class WebSourcesConfig(BaseModel):
    web_urls: list[str] = Field(default_factory=list)
    web_mode: Literal["single", "crawl", "sitemap"] = "single"
    crawl_depth: int = 2
    max_pages: int = 100
    same_domain_only: bool = True
    sitemap_url: str | None = None
    render_js: bool = False
    strip_selectors: list[str] = Field(default_factory=list)
    respect_robots_txt: bool = True
    request_timeout_s: int = 30
    crawl_concurrency: int = 5
    auto_refresh: bool = False
    refresh_interval_hours: int = 24


class ChunkingConfig(BaseModel):
    chunk_strategy: Literal["recursive", "semantic", "fixed", "markdown", "token"] = (
        "recursive"
    )
    chunk_size: int = 1000
    chunk_overlap: int = 200
    min_chunk_size: int = 100
    respect_sentence_boundary: bool = True


class EmbeddingConfig(BaseModel):
    emb_provider: Literal["openai", "cohere", "huggingface", "ollama", "voyage"] = (
        "huggingface"
    )
    emb_model: str = "BAAI/bge-small-en-v1.5"
    emb_dimensions: int | None = None
    emb_batch_size: int = 32
    emb_normalize: bool = True
    emb_device: Literal["cpu", "cuda"] = "cpu"


class VectorStoreConfig(BaseModel):
    vs_backend: Literal["chroma", "qdrant", "pgvector", "milvus"] = "chroma"
    vs_collection: str = "rag_default"
    vs_distance: Literal["cosine", "euclidean", "dot"] = "cosine"
    vs_hnsw_m: int = 16
    vs_hnsw_ef_construct: int = 100
    vs_on_disk: bool = True


class RetrievalConfig(BaseModel):
    top_k: int = 5
    score_threshold: float = 0.0
    search_type: Literal["similarity", "mmr", "hybrid"] = "similarity"
    mmr_diversity: float = 0.5
    reranking: bool = False
    reranker_model: str = "BAAI/bge-reranker-base"
    hybrid_alpha: float = 0.5
    hybrid_method: Literal["token_overlap", "bm25"] = "token_overlap"
    multi_query: bool = False
    multi_query_count: int = Field(default=3, ge=1, le=8)
    contextual_compression: bool = False


class LLMConfig(BaseModel):
    llm_provider: Literal["anthropic", "openai", "ollama", "groq"] = "anthropic"
    llm_model: str = "claude-opus-4-8"
    temperature: float = 0.0
    max_tokens: int = 1024
    system_prompt: str = "Answer using only the provided context. Cite sources."
    streaming: bool = True


class SystemConfig(BaseModel):
    parallel_workers: int = 4
    cache_embeddings: bool = True
    log_level: str = "INFO"
    rate_limit_rpm: int = 60


class CredentialsConfig(BaseModel):
    openai_api_key: str | None = None
    cohere_api_key: str | None = None
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    voyage_api_key: str | None = None


class RAGConfigData(BaseModel):
    """The complete configuration object persisted per-user as JSON."""

    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    web_sources: WebSourcesConfig = Field(default_factory=WebSourcesConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vectorstore: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)


# Map section name -> Pydantic model, used by the section CRUD routes.
SECTION_MODELS: dict[str, type[BaseModel]] = {
    "sources": SourcesConfig,
    "web_sources": WebSourcesConfig,
    "chunking": ChunkingConfig,
    "embedding": EmbeddingConfig,
    "vectorstore": VectorStoreConfig,
    "retrieval": RetrievalConfig,
    "llm": LLMConfig,
    "system": SystemConfig,
    "credentials": CredentialsConfig,
}

# Credential field names — masked on read, conditionally written on update.
CREDENTIAL_FIELDS = tuple(CredentialsConfig.model_fields.keys())


def mask_credentials(credentials: dict) -> dict:
    """Return a copy of the credentials dict with stored keys masked.

    A stored (non-empty) key becomes ``"******"``; an empty/None key stays ``null``.
    """
    masked: dict[str, str | None] = {}
    for field in CREDENTIAL_FIELDS:
        value = credentials.get(field)
        masked[field] = MASKED_VALUE if value else None
    return masked


def merge_credentials(stored: dict, incoming: dict) -> dict:
    """Merge incoming credential values onto stored ones.

    A credential is only overwritten when the incoming value is a non-empty
    string that is not the masking placeholder. Otherwise the stored value is
    preserved (so a masked round-trip never wipes a real key).
    """
    result = dict(stored)
    for field in CREDENTIAL_FIELDS:
        if field not in incoming:
            continue
        value = incoming[field]
        if isinstance(value, str) and value and value != MASKED_VALUE:
            result[field] = value
        # else: keep stored value (masked placeholder, empty string, or None)
    return result

"""Vector store backends behind one interface (CONTRACT.md §4).

Implements the ``VectorStore`` protocol for:
  - ``chroma``: ChromaDB persistent (on-disk) client — the dev default, works
    with no external service.
  - ``qdrant``: Qdrant client (prod) pointed at ``QDRANT_URL``.

Clients are created lazily in ``connect()`` so importing this module never
requires a running database. No backend-specific calls leak into routes — use
:func:`get_vector_store`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Protocol, TypedDict

from app.config_schemas import VectorStoreConfig

logger = logging.getLogger("app.rag.store")

# Default on-disk location for the Chroma dev backend.
_CHROMA_DEFAULT_PATH = os.environ.get("CHROMA_PATH", "./data/chroma")

# Map contract distance names to backend-specific space/metric identifiers.
_CHROMA_SPACE = {"cosine": "cosine", "euclidean": "l2", "dot": "ip"}


class VectorStoreHit(TypedDict):
    id: str
    score: float
    metadata: Dict[str, Any]
    content: str


class VectorStore(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def upsert(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: List[Dict[str, Any]],
        contents: List[str],
    ) -> None: ...

    async def search(
        self,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorStoreHit]: ...

    async def fetch_all(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20_000,
    ) -> List[VectorStoreHit]: ...

    async def delete(self, filters: Dict[str, Any]) -> None: ...

    async def get_count(self) -> int: ...


def _sanitize_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Chroma metadata values must be str/int/float/bool. Coerce others."""
    clean: Dict[str, Any] = {}
    for key, value in meta.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


class ChromaVectorStore:
    """ChromaDB persistent backend (on-disk by default)."""

    def __init__(self, config: VectorStoreConfig) -> None:
        self.config = config
        self._client = None
        self._collection = None

    async def connect(self) -> None:
        if self._collection is not None:
            return
        import chromadb

        if self.config.vs_on_disk:
            os.makedirs(_CHROMA_DEFAULT_PATH, exist_ok=True)
            self._client = chromadb.PersistentClient(path=_CHROMA_DEFAULT_PATH)
        else:
            self._client = chromadb.EphemeralClient()

        space = _CHROMA_SPACE.get(self.config.vs_distance, "cosine")
        self._collection = self._client.get_or_create_collection(
            name=self.config.vs_collection,
            metadata={
                "hnsw:space": space,
                "hnsw:M": self.config.vs_hnsw_m,
                "hnsw:construction_ef": self.config.vs_hnsw_ef_construct,
            },
        )
        logger.info(
            "Connected Chroma collection '%s' (on_disk=%s)",
            self.config.vs_collection,
            self.config.vs_on_disk,
        )

    async def disconnect(self) -> None:
        # PersistentClient flushes automatically; just drop references.
        self._collection = None
        self._client = None

    async def upsert(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: List[Dict[str, Any]],
        contents: List[str],
    ) -> None:
        if not ids:
            return
        await self.connect()
        self._collection.upsert(
            ids=ids,
            embeddings=vectors,
            metadatas=[_sanitize_metadata(m) for m in metadatas],
            documents=contents,
        )

    async def search(
        self,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorStoreHit]:
        await self.connect()
        where = filters or None
        result = self._collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["metadatas", "documents", "distances"],
        )
        hits: List[VectorStoreHit] = []
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for i, _id in enumerate(ids):
            distance = dists[i] if i < len(dists) else 0.0
            # Convert distance to a similarity-style score (higher = closer).
            score = 1.0 - float(distance)
            hits.append(
                VectorStoreHit(
                    id=str(_id),
                    score=score,
                    metadata=metas[i] if i < len(metas) else {},
                    content=docs[i] if i < len(docs) else "",
                )
            )
        return hits

    async def fetch_all(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20_000,
    ) -> List[VectorStoreHit]:
        await self.connect()
        result = self._collection.get(
            where=filters or None,
            limit=limit,
            include=["metadatas", "documents"],
        )
        hits: List[VectorStoreHit] = []
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        for i, _id in enumerate(ids):
            hits.append(
                VectorStoreHit(
                    id=str(_id),
                    score=0.0,
                    metadata=metas[i] if i < len(metas) else {},
                    content=docs[i] if i < len(docs) else "",
                )
            )
        return hits

    async def delete(self, filters: Dict[str, Any]) -> None:
        await self.connect()
        if filters:
            self._collection.delete(where=filters)

    async def get_count(self) -> int:
        await self.connect()
        return int(self._collection.count())


class QdrantVectorStore:
    """Qdrant backend (prod). Connects to ``QDRANT_URL``."""

    def __init__(self, config: VectorStoreConfig, url: str) -> None:
        self.config = config
        self._url = url
        self._client = None
        self._distance = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance

        self._distance = {
            "cosine": Distance.COSINE,
            "euclidean": Distance.EUCLID,
            "dot": Distance.DOT,
        }.get(self.config.vs_distance, Distance.COSINE)
        self._client = QdrantClient(url=self._url)
        logger.info("Connected Qdrant at %s", self._url)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
        self._client = None

    def _ensure_collection(self, dim: int) -> None:
        from qdrant_client.models import VectorParams

        existing = {c.name for c in self._client.get_collections().collections}
        if self.config.vs_collection not in existing:
            self._client.create_collection(
                collection_name=self.config.vs_collection,
                vectors_config=VectorParams(size=dim, distance=self._distance),
            )

    async def upsert(
        self,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: List[Dict[str, Any]],
        contents: List[str],
    ) -> None:
        if not ids:
            return
        await self.connect()
        from qdrant_client.models import PointStruct

        self._ensure_collection(len(vectors[0]))
        points = []
        for _id, vec, meta, content in zip(ids, vectors, metadatas, contents):
            payload = dict(meta)
            payload["content"] = content
            points.append(PointStruct(id=_id, vector=vec, payload=payload))
        self._client.upsert(
            collection_name=self.config.vs_collection, points=points
        )

    async def search(
        self,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorStoreHit]:
        await self.connect()
        results = self._client.search(
            collection_name=self.config.vs_collection,
            query_vector=query_vector,
            limit=top_k,
        )
        hits: List[VectorStoreHit] = []
        for r in results:
            payload = dict(r.payload or {})
            content = payload.pop("content", "")
            hits.append(
                VectorStoreHit(
                    id=str(r.id),
                    score=float(r.score),
                    metadata=payload,
                    content=content,
                )
            )
        return hits

    async def fetch_all(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20_000,
    ) -> List[VectorStoreHit]:
        await self.connect()
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        existing = {c.name for c in self._client.get_collections().collections}
        if self.config.vs_collection not in existing:
            return []

        conditions = [
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in (filters or {}).items()
        ]
        scroll_filter = Filter(must=conditions) if conditions else None

        hits: List[VectorStoreHit] = []
        offset = None
        while len(hits) < limit:
            points, offset = self._client.scroll(
                collection_name=self.config.vs_collection,
                scroll_filter=scroll_filter,
                limit=min(1024, limit - len(hits)),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                payload = dict(p.payload or {})
                content = payload.pop("content", "")
                hits.append(
                    VectorStoreHit(
                        id=str(p.id), score=0.0, metadata=payload, content=content
                    )
                )
            if offset is None or not points:
                break
        return hits

    async def delete(self, filters: Dict[str, Any]) -> None:
        await self.connect()
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = [
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in (filters or {}).items()
        ]
        if conditions:
            self._client.delete(
                collection_name=self.config.vs_collection,
                points_selector=Filter(must=conditions),
            )

    async def get_count(self) -> int:
        await self.connect()
        existing = {c.name for c in self._client.get_collections().collections}
        if self.config.vs_collection not in existing:
            return 0
        return int(
            self._client.count(collection_name=self.config.vs_collection).count
        )


def get_vector_store(
    config: VectorStoreConfig, qdrant_url: Optional[str] = None
) -> VectorStore:
    """Factory: return the configured vector store backend behind the interface.

    ``chroma`` (on-disk) is the dev default. ``qdrant`` is the prod backend.
    ``pgvector`` / ``milvus`` are declared in the schema but not implemented in
    Phase 4 — they fall back to Chroma so ingestion still works.
    """
    backend = config.vs_backend
    if backend == "chroma":
        return ChromaVectorStore(config)
    if backend == "qdrant":
        url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
        return QdrantVectorStore(config, url)

    logger.warning(
        "Vector store backend '%s' not implemented in Phase 4; "
        "falling back to Chroma.",
        backend,
    )
    return ChromaVectorStore(config)

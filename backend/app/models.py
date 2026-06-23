"""SQLModel table definitions: User, RAGConfig, IndexJob.

These are the persistence tables for the backend. The RAGConfig table stores the
entire pipeline configuration for a user as a single JSON blob (see
``app.config_schemas.RAGConfigData``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Timezone-aware UTC now (stored as ISO 8601 by serializers)."""
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """An authenticated user. The seed admin is created on first boot."""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)


class RAGConfig(SQLModel, table=True):
    """Per-user pipeline configuration, stored as one JSON blob.

    The ``data`` column holds a serialized ``RAGConfigData`` (all sections,
    including raw credential values which are masked on the way out).
    """

    __tablename__ = "rag_configs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, unique=True)
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class IndexJob(SQLModel, table=True):
    """An ingestion job record (local files or web sources).

    Field shape mirrors CONTRACT.md ``GET /pipeline/jobs``.
    """

    __tablename__ = "index_jobs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # "local" | "web"
    source_type: str = Field(default="local")
    # "pending" | "running" | "done" | "error"
    status: str = Field(default="pending")
    files_processed: int = Field(default=0)
    pages_fetched: int = Field(default=0)
    chunks_created: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = Field(default=None)

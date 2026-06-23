"""SQLModel engine + session management.

A single SQLite file (configured via ``DATABASE_URL``) backs the config store.
Tables are auto-created on startup (see ``app.main.lifespan``).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from urllib.parse import urlparse

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# SQLite needs check_same_thread=False so the engine can be shared across the
# threadpool FastAPI uses for sync dependencies.
_connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)


def _ensure_sqlite_dir(database_url: str) -> None:
    """Create the parent directory for a SQLite file DB if it doesn't exist."""
    if not database_url.startswith("sqlite"):
        return
    # Strip the sqlite scheme to get the filesystem path (handles sqlite:///./x).
    path_part = database_url.split("sqlite:///", 1)[-1]
    if not path_part or path_part == ":memory:":
        return
    parent = os.path.dirname(path_part)
    if parent:
        os.makedirs(parent, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=_connect_args,
)


def init_db() -> None:
    """Create all tables registered on SQLModel.metadata if they don't exist."""
    # Import models so they register on SQLModel.metadata before create_all.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a DB session per request."""
    with Session(engine) as session:
        yield session

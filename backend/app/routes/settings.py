"""Configuration CRUD endpoints (CONTRACT.md §2 Configuration & Settings).

The full config is persisted per-user as one JSON blob (``RAGConfig.data``).
Credentials are stored raw but masked on every read; updates only overwrite a
credential when a real (non-empty, non-masked) value is sent.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.auth.deps import get_current_user
from app.config_schemas import (
    SECTION_MODELS,
    RAGConfigData,
    mask_credentials,
    merge_credentials,
)
from app.db import get_session
from app.errors import APIError
from app.models import RAGConfig, User, utcnow

logger = logging.getLogger("app.routes.settings")

router = APIRouter(prefix="/settings", tags=["settings"])


def _reload_autorefresh(user_id: int, *, sources: bool, web: bool) -> None:
    """Restart watcher/scheduler after a relevant config change (Phase 7).

    Guarded so a background-service hiccup never fails the config save. Only the
    affected lane is reloaded: ``sources`` -> file watcher, ``web_sources`` ->
    web re-crawl scheduler.
    """
    try:
        if sources:
            from app.rag.watcher import reload_watcher

            reload_watcher(user_id)
        if web:
            from app.rag.scheduler import reload_scheduler

            reload_scheduler(user_id)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to reload auto-refresh services after config change")


def _get_or_create_config(session: Session, user: User) -> RAGConfig:
    """Return the user's RAGConfig row, creating it with defaults if absent."""
    cfg = session.exec(
        select(RAGConfig).where(RAGConfig.user_id == user.id)
    ).first()
    if cfg is None:
        cfg = RAGConfig(user_id=user.id, data=RAGConfigData().model_dump())
        session.add(cfg)
        session.commit()
        session.refresh(cfg)
    return cfg


def _masked_view(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the full config with credentials masked."""
    view = dict(data)
    view["credentials"] = mask_credentials(data.get("credentials", {}))
    return view


@router.get("/config")
def get_config(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the complete configuration object (credentials masked)."""
    cfg = _get_or_create_config(session, user)
    # Normalize against the schema so any missing/new fields get defaults filled.
    normalized = RAGConfigData(**cfg.data).model_dump()
    # Preserve any stored raw credentials before masking the response.
    normalized["credentials"] = cfg.data.get("credentials", normalized["credentials"])
    return _masked_view(normalized)


@router.put("/config")
def update_config(
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Merge a (possibly partial) config object; missing sections unchanged."""
    cfg = _get_or_create_config(session, user)
    current = RAGConfigData(**cfg.data).model_dump()
    current["credentials"] = cfg.data.get("credentials", current["credentials"])

    for section, model in SECTION_MODELS.items():
        if section not in payload or payload[section] is None:
            continue
        incoming = payload[section]
        if not isinstance(incoming, dict):
            raise APIError(
                422, f"Section '{section}' must be an object", "VALIDATION_ERROR"
            )
        if section == "credentials":
            current["credentials"] = merge_credentials(
                current["credentials"], incoming
            )
            continue
        merged = {**current[section], **incoming}
        try:
            current[section] = model(**merged).model_dump()
        except Exception as exc:  # pydantic ValidationError -> contract error
            raise APIError(422, str(exc), "VALIDATION_ERROR") from exc

    cfg.data = current
    cfg.updated_at = utcnow()
    session.add(cfg)
    session.commit()
    session.refresh(cfg)

    _reload_autorefresh(
        user.id,
        sources="sources" in payload and payload["sources"] is not None,
        web="web_sources" in payload and payload["web_sources"] is not None,
    )
    return _masked_view(current)


@router.get("/config/{section}")
def get_section(
    section: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a single configuration section (credentials masked)."""
    if section not in SECTION_MODELS:
        raise APIError(404, f"Unknown config section '{section}'", "NOT_FOUND")
    cfg = _get_or_create_config(session, user)
    normalized = RAGConfigData(**cfg.data).model_dump()
    if section == "credentials":
        stored = cfg.data.get("credentials", normalized["credentials"])
        return mask_credentials(stored)
    return normalized[section]


@router.put("/config/{section}")
def update_section(
    section: str,
    payload: dict[str, Any],
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Update a single configuration section."""
    if section not in SECTION_MODELS:
        raise APIError(404, f"Unknown config section '{section}'", "NOT_FOUND")
    if not isinstance(payload, dict):
        raise APIError(422, "Section payload must be an object", "VALIDATION_ERROR")

    cfg = _get_or_create_config(session, user)
    current = RAGConfigData(**cfg.data).model_dump()
    current["credentials"] = cfg.data.get("credentials", current["credentials"])

    if section == "credentials":
        current["credentials"] = merge_credentials(current["credentials"], payload)
        result_view: dict[str, Any] = mask_credentials(current["credentials"])
    else:
        model = SECTION_MODELS[section]
        merged = {**current[section], **payload}
        try:
            current[section] = model(**merged).model_dump()
        except Exception as exc:
            raise APIError(422, str(exc), "VALIDATION_ERROR") from exc
        result_view = current[section]

    cfg.data = current
    cfg.updated_at = utcnow()
    session.add(cfg)
    session.commit()

    _reload_autorefresh(
        user.id,
        sources=section == "sources",
        web=section == "web_sources",
    )
    return result_view

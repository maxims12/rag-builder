"""FastAPI application entry point.

Wires the lifespan (table creation + admin seed), CORS, contract-shaped error
handlers, and the routers available so far (auth, settings, health).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.routes import router as auth_router
from app.auth.seed import seed_admin_user
from app.config import settings
from app.db import init_db
from app.errors import APIError, error_payload
from app.routes.pipeline import router as pipeline_router
from app.routes.playground import router as playground_router
from app.routes.settings import router as settings_router
from app.routes.sources import router as sources_router
from app.routes.web_sources import router as web_sources_router

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("app.main")


def _primary_user_id() -> int | None:
    """Return the seeded admin user's id for background tasks (watcher/scheduler).

    Background auto-refresh is single-tenant in Phase 7: it operates on the admin
    user's config. Returns None if no user exists yet.
    """
    from sqlmodel import Session, select

    from app.config import settings as _settings
    from app.db import engine
    from app.models import User

    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.email == _settings.admin_email)
        ).first()
        return user.id if user else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables, seed admin, and start auto-refresh background services."""
    init_db()
    seed_admin_user()

    # Phase 7: start the file watcher + web re-crawl scheduler when their config
    # toggles are enabled. Both are no-ops if disabled or their optional library
    # (watchdog / apscheduler) isn't installed.
    user_id = _primary_user_id()
    if user_id is not None:
        try:
            from app.rag.scheduler import start_scheduler
            from app.rag.watcher import start_watcher

            start_watcher(user_id)
            start_scheduler(user_id)
        except Exception:  # never let background wiring block app startup
            logger.exception("Failed to start auto-refresh background services")

    logger.info("Startup complete: tables ready, admin seeded, auto-refresh wired.")
    try:
        yield
    finally:
        # Clean shutdown of background services.
        try:
            from app.rag.scheduler import stop_scheduler
            from app.rag.watcher import stop_watcher

            stop_watcher()
            stop_scheduler()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Error during auto-refresh shutdown")


app = FastAPI(title="RAG System Builder API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Contract-shaped error handlers (CONTRACT.md §5) ───────────────────
@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.detail, exc.code),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code = {
        401: "UNAUTHORIZED",
        404: "NOT_FOUND",
        422: "VALIDATION_ERROR",
    }.get(exc.status_code, "ERROR")
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code, content=error_payload(detail, code)
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_payload("Request validation failed", "VALIDATION_ERROR"),
    )


# ── Health ────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Routers ───────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(sources_router)
app.include_router(web_sources_router)
app.include_router(pipeline_router)
app.include_router(playground_router)

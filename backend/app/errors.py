"""Standard error payload helpers (CONTRACT.md §5).

Every non-2xx error returns ``{detail, code, timestamp}``. Use :class:`APIError`
to raise contract-shaped errors; the handler in ``app.main`` formats them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException


class APIError(HTTPException):
    """An HTTPException that carries a contract ``code`` alongside ``detail``."""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def error_payload(detail: str, code: str) -> dict[str, str]:
    """Build the standard error body with a UTC ISO 8601 timestamp."""
    return {
        "detail": detail,
        "code": code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

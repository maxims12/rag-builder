"""Password hashing (bcrypt) and JWT encode/decode helpers.

Secrets are read from :mod:`app.config` (pydantic-settings) — never hardcoded.
Access tokens are short-lived and returned in the JSON body; refresh tokens are
long-lived and delivered via an httpOnly cookie (see auth routes).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import settings

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

# bcrypt operates on at most 72 bytes; longer inputs must be truncated before
# hashing/verifying so both paths agree on the compared bytes.
_BCRYPT_MAX_BYTES = 72


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


# ── Password hashing ──────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _prepare(plain_password), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


# ── JWT ───────────────────────────────────────────────────────────────
def _create_token(subject: str | int, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | int) -> str:
    """Create a short-lived access token (carries the user id in ``sub``)."""
    return _create_token(
        subject,
        TOKEN_TYPE_ACCESS,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(subject: str | int) -> str:
    """Create a long-lived refresh token (carries the user id in ``sub``)."""
    return _create_token(
        subject,
        TOKEN_TYPE_REFRESH,
        timedelta(days=settings.refresh_token_expire_days),
    )


class TokenError(Exception):
    """Raised when a token cannot be decoded, is expired, or is the wrong type."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """Decode and validate a JWT, enforcing the expected token type.

    Raises :class:`TokenError` with a contract error code on failure.
    """
    # Choose the error code family based on which token we're validating.
    expired_code = (
        "REFRESH_TOKEN_EXPIRED"
        if expected_type == TOKEN_TYPE_REFRESH
        else "TOKEN_EXPIRED"
    )
    invalid_code = (
        "REFRESH_TOKEN_EXPIRED"
        if expected_type == TOKEN_TYPE_REFRESH
        else "UNAUTHORIZED"
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired", expired_code) from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Token is invalid", invalid_code) from exc

    if payload.get("type") != expected_type:
        raise TokenError("Token has the wrong type", invalid_code)
    if "sub" not in payload:
        raise TokenError("Token is missing subject", invalid_code)
    return payload

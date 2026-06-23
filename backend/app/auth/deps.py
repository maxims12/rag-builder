"""Auth dependencies: extract + validate the Bearer access token, load the user."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlmodel import Session

from app.auth.security import TOKEN_TYPE_ACCESS, TokenError, decode_token
from app.db import get_session
from app.errors import APIError
from app.models import User


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    if not auth_header:
        raise APIError(401, "Authorization header missing", "UNAUTHORIZED")
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise APIError(401, "Authorization header malformed", "UNAUTHORIZED")
    return parts[1].strip()


def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """Resolve the current user from the Bearer access token.

    Raises 401 with ``UNAUTHORIZED`` (missing/malformed/invalid) or
    ``TOKEN_EXPIRED`` (expired access token, triggers frontend refresh).
    """
    token = _extract_bearer_token(request)
    try:
        payload = decode_token(token, TOKEN_TYPE_ACCESS)
    except TokenError as exc:
        raise APIError(401, exc.message, exc.code) from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise APIError(401, "Token subject is invalid", "UNAUTHORIZED") from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise APIError(401, "User not found or inactive", "UNAUTHORIZED")
    return user

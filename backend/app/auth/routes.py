"""Auth endpoints: login, refresh, logout, me (CONTRACT.md §2)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session, select

from app.auth.deps import get_current_user
from app.auth.security import (
    TOKEN_TYPE_REFRESH,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.config import settings
from app.db import get_session
from app.errors import APIError
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/auth/refresh"


# ── Schemas ───────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    success: bool = True
    detail: str = "Successfully logged out"


class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool
    created_at: str


# ── Helpers ───────────────────────────────────────────────────────────
def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )


# ── Routes ────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> TokenResponse:
    """Authenticate and issue an access token; set refresh token cookie."""
    user = session.exec(select(User).where(User.email == body.email)).first()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise APIError(401, "Invalid email or password", "INVALID_CREDENTIALS")
    if not user.is_active:
        raise APIError(401, "Invalid email or password", "INVALID_CREDENTIALS")

    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> TokenResponse:
    """Exchange the refresh-token cookie for a fresh access token."""
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise APIError(
            401, "Refresh token is expired or invalid", "REFRESH_TOKEN_EXPIRED"
        )
    try:
        payload = decode_token(token, TOKEN_TYPE_REFRESH)
    except TokenError as exc:
        raise APIError(401, "Refresh token is expired or invalid", exc.code) from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise APIError(
            401, "Refresh token is expired or invalid", "REFRESH_TOKEN_EXPIRED"
        ) from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise APIError(
            401, "Refresh token is expired or invalid", "REFRESH_TOKEN_EXPIRED"
        )

    # Rotate the refresh cookie alongside the new access token.
    new_access = create_access_token(user.id)
    new_refresh = create_refresh_token(user.id)
    _set_refresh_cookie(response, new_refresh)
    return TokenResponse(access_token=new_access)


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
) -> LogoutResponse:
    """Clear the refresh-token cookie (requires a valid access token)."""
    _clear_refresh_cookie(response)
    return LogoutResponse()


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user's profile."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat(),
    )

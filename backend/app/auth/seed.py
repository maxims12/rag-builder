"""Seed the initial admin user from environment config on first boot."""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.auth.security import hash_password
from app.config import settings
from app.db import engine
from app.models import User

logger = logging.getLogger("app.seed")


def seed_admin_user() -> None:
    """Create the admin user from ADMIN_EMAIL/ADMIN_PASSWORD if it doesn't exist."""
    with Session(engine) as session:
        existing = session.exec(
            select(User).where(User.email == settings.admin_email)
        ).first()
        if existing is not None:
            return
        admin = User(
            email=settings.admin_email,
            hashed_password=hash_password(settings.admin_password),
            is_active=True,
        )
        session.add(admin)
        session.commit()
        logger.info("Seeded admin user: %s", settings.admin_email)

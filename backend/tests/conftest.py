"""Shared pytest fixtures: a TestClient backed by a throwaway SQLite DB.

Env vars are set before importing the app so config/db pick up a temp database
and deterministic admin credentials.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Configure environment BEFORE importing the app modules.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ragtest_"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["JWT_SECRET"] = "pytest_secret_0123456789abcdef0123456789"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "testpass123"

ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    # Context manager triggers lifespan (table creation + admin seed).
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def auth_headers(client):
    resp = client.post(
        "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

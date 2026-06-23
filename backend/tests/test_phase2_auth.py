"""Phase 2 smoke test: login, refresh-cookie, /me gating, logout."""

from __future__ import annotations


def test_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_login_invalid_credentials(client):
    resp = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


def test_login_sets_refresh_cookie_and_returns_access(client):
    resp = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "testpass123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/auth/refresh" in set_cookie


def test_me_with_token(client, auth_headers):
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@example.com"
    assert body["is_active"] is True
    assert "created_at" in body


def test_refresh_and_logout(client):
    # login to seed the refresh cookie on the client jar
    client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "testpass123"},
    )
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["access_token"]

    # logout requires a valid access token
    token = resp.json()["access_token"]
    resp = client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

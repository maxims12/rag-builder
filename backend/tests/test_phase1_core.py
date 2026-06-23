"""Phase 1 smoke test: app boots, health endpoint, tables + admin seeded."""

from __future__ import annotations


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_admin_user_seeded(client):
    # The seeded admin can authenticate -> proves table creation + seed ran.
    resp = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "testpass123"},
    )
    assert resp.status_code == 200

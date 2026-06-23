"""Phase 3 smoke test: config CRUD, credential masking, validation."""

from __future__ import annotations


def test_get_full_config_defaults(client, auth_headers):
    resp = client.get("/settings/config", headers=auth_headers)
    assert resp.status_code == 200
    cfg = resp.json()
    assert set(cfg.keys()) >= {
        "sources",
        "web_sources",
        "chunking",
        "embedding",
        "vectorstore",
        "retrieval",
        "llm",
        "system",
        "credentials",
    }
    assert cfg["chunking"]["chunk_size"] == 1000
    assert cfg["credentials"]["openai_api_key"] is None


def test_put_get_section_roundtrip(client, auth_headers):
    resp = client.put(
        "/settings/config/chunking",
        headers=auth_headers,
        json={"chunk_size": 1500, "chunk_overlap": 300},
    )
    assert resp.status_code == 200
    assert resp.json()["chunk_size"] == 1500

    resp = client.get("/settings/config/chunking", headers=auth_headers)
    assert resp.json()["chunk_size"] == 1500
    assert resp.json()["chunk_overlap"] == 300


def test_credentials_masked_and_preserved(client, auth_headers):
    # Store a real key via full PUT.
    resp = client.put(
        "/settings/config",
        headers=auth_headers,
        json={"credentials": {"openai_api_key": "sk-realkey-123"}},
    )
    assert resp.status_code == 200
    assert resp.json()["credentials"]["openai_api_key"] == "******"

    # A masked round-trip must not wipe the stored key.
    client.put(
        "/settings/config/credentials",
        headers=auth_headers,
        json={"openai_api_key": "******", "cohere_api_key": ""},
    )
    resp = client.get("/settings/config/credentials", headers=auth_headers)
    assert resp.json()["openai_api_key"] == "******"
    assert resp.json()["cohere_api_key"] is None


def test_retrieval_advanced_fields_roundtrip(client, auth_headers):
    resp = client.put(
        "/settings/config/retrieval",
        headers=auth_headers,
        json={
            "multi_query": True,
            "multi_query_count": 5,
            "contextual_compression": True,
            "search_type": "hybrid",
            "hybrid_method": "bm25",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["multi_query"] is True
    assert body["multi_query_count"] == 5
    assert body["contextual_compression"] is True
    assert body["hybrid_method"] == "bm25"

    resp = client.get("/settings/config/retrieval", headers=auth_headers)
    assert resp.json()["hybrid_method"] == "bm25"


def test_retrieval_advanced_fields_validation(client, auth_headers):
    resp = client.put(
        "/settings/config/retrieval",
        headers=auth_headers,
        json={"hybrid_method": "not_a_method"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    resp = client.put(
        "/settings/config/retrieval",
        headers=auth_headers,
        json={"multi_query_count": 99},
    )
    assert resp.status_code == 422


def test_unknown_section_404(client, auth_headers):
    resp = client.get("/settings/config/does_not_exist", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_invalid_value_validation_error(client, auth_headers):
    resp = client.put(
        "/settings/config/chunking",
        headers=auth_headers,
        json={"chunk_strategy": "not_a_real_strategy"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_config_requires_auth(client):
    resp = client.get("/settings/config")
    assert resp.status_code == 401

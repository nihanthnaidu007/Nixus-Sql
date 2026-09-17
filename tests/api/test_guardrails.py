"""Guardrails manifest endpoint (Phase 2, Wave 2 D2.1).

Shape + honesty contract (caps/budgets/estimates only, never a dollar figure)
and the INHERITED fail-closed auth (401 without a key, 503 with none). The
endpoint needs no stubs — a passing request here is itself the proof that it
touches no database and no LLM.
"""
import pytest
from fastapi.testclient import TestClient

from api import main

KEY = "test-api-key-123"


@pytest.fixture
def client():
    return TestClient(main.app)


def test_manifest_shape_and_values(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    resp = client.get("/api/v1/guardrails", headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_cap"] == 1000
    assert body["query_timeout_ms"] == main.settings.query_timeout_ms
    assert body["max_correction_attempts"] == main.settings.max_correction_attempts
    assert body["clarification_round_cap"] == 2
    assert "SELECT" in body["select_only"]
    assert "read-only" in body["read_only_role"]
    assert set(body["models"]) >= {"sql_generation", "embeddings"}
    assert "claude" in body["models"]["sql_generation"]
    assert "X-API-Key" in body["auth"]


def test_manifest_never_claims_a_dollar_spend_ceiling(client, monkeypatch):
    """The honesty contract: the surface shows caps, budgets, timeouts, and
    estimates — never a dollar or token spend ceiling."""
    monkeypatch.setattr(main.settings, "api_key", KEY)
    resp = client.get("/api/v1/guardrails", headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    assert "$" not in resp.text
    assert "spend ceiling" in resp.json()["note"]


def test_manifest_401_without_a_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    resp = client.get("/api/v1/guardrails")
    assert resp.status_code == 401
    assert "X-API-Key" in resp.json()["detail"]


def test_manifest_503_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", None)
    resp = client.get("/api/v1/guardrails")
    assert resp.status_code == 503
    assert "API_KEY" in resp.json()["detail"]

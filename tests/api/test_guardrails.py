"""Guardrails manifest endpoint (Phase 2, Wave 2 D2.1).

Shape + honesty contract (caps/budgets/estimates only, never a dollar figure)
and the INHERITED fail-closed auth (401 without a key, 503 with none). The
endpoint needs no stubs — a passing request here is itself the proof that it
touches no database and no LLM.
"""
import pytest
from fastapi.testclient import TestClient

from api import guardrails, main

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


# --- W2 N1: the manifest names the active target database ---------------------


def test_parse_target_database_extracts_only_the_name():
    parse = guardrails.parse_target_database
    assert (
        parse("postgresql://nixus:pw@localhost:5432/nixus_saas_demo")
        == "nixus_saas_demo"
    )
    assert (
        parse("postgresql://nixus:pw@db:5432/nixus_chinook?sslmode=prefer")
        == "nixus_chinook"
    )
    # Credentials and host are never part of the identity.
    parsed = parse("postgresql://user:secretpw@host.internal:5432/prod_data")
    assert parsed == "prod_data"
    assert "secretpw" not in parsed
    assert "host.internal" not in parsed


def test_parse_target_database_is_none_when_unset_or_pathless():
    parse = guardrails.parse_target_database
    assert parse(None) is None
    assert parse("") is None
    assert parse("postgresql://localhost:5432") is None
    assert parse("not even a url") is None


def test_manifest_names_the_target_database(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    monkeypatch.setattr(
        main.settings,
        "target_database_url",
        "postgresql://nixus:pw@localhost:5432/nixus_saas_demo",
    )
    resp = client.get("/api/v1/guardrails", headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    assert resp.json()["target_database"] == "nixus_saas_demo"


def test_manifest_target_is_none_when_no_target_is_configured(client, monkeypatch):
    """An unset target is an honest null — the UI omits the badge rather than
    inventing a name."""
    monkeypatch.setattr(main.settings, "api_key", KEY)
    monkeypatch.setattr(main.settings, "target_database_url", None)
    resp = client.get("/api/v1/guardrails", headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    assert resp.json()["target_database"] is None

"""API-key auth (Wave 0): 401 without a key, serves with the key, fail-closed 503.

The decision core (:func:`api.auth.check_access`) is tested as a pure function;
the HTTP layer runs through TestClient against the real app with the DB/LLM
dependencies monkeypatched — fully offline, no server, no database.
"""
import pytest
from fastapi.testclient import TestClient

from api import main
from api.auth import HEALTH_PATH, check_access

PROTECTED = "/api/v1/cache-stats"
KEY = "test-api-key-123"


@pytest.fixture
def client():
    return TestClient(main.app)


def _with_key(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


def _with_cache_stats(monkeypatch, value=None):
    async def _stats():
        return value if value is not None else {"entries": 0}

    monkeypatch.setattr(main, "get_cache_stats", _stats)


def _with_health_dependencies(monkeypatch):
    """Stub the health handler's dependencies so no DB/LLM is touched."""

    async def _db():
        return True

    async def _llm():
        return {"status": "ok", "anthropic_connected": True, "openai_connected": True, "checked_at": 0.0}

    monkeypatch.setattr(main, "check_db_connection", _db)
    monkeypatch.setattr(main, "_check_llm_connectivity", _llm)


# ── HTTP behavior through the real app ───────────────────────────────────────
def test_health_is_exempt_without_a_key(client, monkeypatch):
    _with_key(monkeypatch)
    _with_health_dependencies(monkeypatch)
    resp = client.get(HEALTH_PATH)
    assert resp.status_code == 200


def test_protected_route_401_without_a_key(client, monkeypatch):
    _with_key(monkeypatch)
    resp = client.get(PROTECTED)
    assert resp.status_code == 401
    assert "X-API-Key" in resp.json()["detail"]


def test_protected_route_401_on_a_wrong_key(client, monkeypatch):
    _with_key(monkeypatch)
    resp = client.get(PROTECTED, headers={"X-API-Key": "not-the-key"})
    assert resp.status_code == 401


def test_protected_route_serves_with_the_key(client, monkeypatch):
    _with_key(monkeypatch)
    _with_cache_stats(monkeypatch)
    resp = client.get(PROTECTED, headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    assert resp.json() == {"entries": 0}


def test_fail_closed_503_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", None)
    resp = client.get(PROTECTED)
    assert resp.status_code == 503
    body = resp.json()
    assert "API_KEY" in body["detail"]          # setup guidance, not a bare error
    assert "X-API-Key" in body["detail"]


def test_fail_closed_503_when_key_is_the_example_placeholder(client, monkeypatch):
    # A .env copied from .env.example must never count as "configured".
    monkeypatch.setattr(main.settings, "api_key", "your_api_key_here")
    resp = client.get(PROTECTED)
    assert resp.status_code == 503


def test_cors_preflight_is_answered_before_auth(client, monkeypatch):
    """A browser preflight never carries X-API-Key — CORS must answer it, so the
    auth middleware has to sit INSIDE the CORS layer."""
    _with_key(monkeypatch)
    resp = client.options(
        PROTECTED,
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key, content-type",
        },
    )
    assert resp.status_code == 200


# ── Pure decision core ───────────────────────────────────────────────────────
def test_check_access_exempts_health_and_options():
    assert check_access("/api/health", "GET", None, KEY) is None
    assert check_access("/api/v1/run", "OPTIONS", None, None) is None


def test_check_access_guards_only_api_paths():
    assert check_access("/", "GET", None, None) is None
    assert check_access("/docs", "GET", None, None) is None


def test_check_access_503_when_nothing_is_configured():
    rejection = check_access("/api/v1/run", "POST", None, None)
    assert rejection is not None
    assert rejection.status == 503
    assert "API_KEY" in rejection.body["detail"]


def test_check_access_401_on_missing_or_wrong_key():
    assert check_access("/api/v1/run", "POST", None, KEY).status == 401
    assert check_access("/api/v1/run", "POST", "nope", KEY).status == 401


def test_check_access_allows_a_matching_key():
    assert check_access("/api/v1/run", "POST", KEY, KEY) is None

"""Server-issued session ids (Wave 0): checkpoint threads are not client-selectable.

Unit tests for the resolution logic (issue on first use, 404 on unknown ids) plus
TestClient tests proving all three session-bearing endpoints enforce it. The DB
registry and the graph run are monkeypatched — fully offline.
"""
import pytest
from fastapi.testclient import TestClient

from api import main, sessions
from api.sessions import UnknownSessionError, resolve_session_id

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


def _patch_registry(monkeypatch, exists: bool, registered=None):
    """Replace the DB registry: session_exists → fixed answer, register → record."""

    async def _exists(session_id: str) -> bool:
        return exists

    async def _register(session_id: str) -> None:
        if registered is not None:
            registered.append(session_id)

    monkeypatch.setattr(sessions, "session_exists", _exists)
    monkeypatch.setattr(sessions, "register_session", _register)


def _capture_run_query(monkeypatch, captured):
    async def _run(user_query, session_id, **kwargs):
        captured.append(session_id)
        return {"outcome": "ANSWERED", "session_id": session_id}

    monkeypatch.setattr(main, "run_query", _run)


# ── Resolution logic ─────────────────────────────────────────────────────────
async def test_empty_session_issues_a_fresh_registered_id(monkeypatch):
    registered = []
    _patch_registry(monkeypatch, exists=False, registered=registered)

    first = await resolve_session_id("")
    second = await resolve_session_id(None)
    assert first and second and first != second   # every first use gets its own id
    assert first in registered and second in registered


async def test_unknown_session_id_is_rejected(monkeypatch):
    _patch_registry(monkeypatch, exists=False)
    with pytest.raises(UnknownSessionError):
        await resolve_session_id("not-issued-by-the-server")


async def test_issued_session_id_passes_through(monkeypatch):
    _patch_registry(monkeypatch, exists=True)
    assert await resolve_session_id("issued-by-server") == "issued-by-server"


# ── HTTP surface ─────────────────────────────────────────────────────────────
def test_run_rejects_an_unknown_session_with_404(_auth, client, monkeypatch):
    _patch_registry(monkeypatch, exists=False)
    resp = client.post(
        "/api/v1/run",
        json={"user_query": "q", "session_id": "someone-elses-session"},
        headers=HEADERS,
    )
    assert resp.status_code == 404
    assert "not issued" in resp.json()["detail"]


def test_run_issues_and_returns_the_session_id(_auth, client, monkeypatch):
    registered, captured = [], []
    _patch_registry(monkeypatch, exists=False, registered=registered)
    _capture_run_query(monkeypatch, captured)
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": ""}, headers=HEADERS)
    assert resp.status_code == 200
    issued = resp.json()["session_id"]
    assert issued in registered            # server-issued AND registered
    assert captured == [issued]            # the core ran on exactly that id


def test_run_binds_a_previously_issued_session(_auth, client, monkeypatch):
    captured = []
    _patch_registry(monkeypatch, exists=True)
    _capture_run_query(monkeypatch, captured)
    resp = client.post(
        "/api/v1/run",
        json={"user_query": "q", "session_id": "issued-by-server"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert captured == ["issued-by-server"]


def test_stream_rejects_an_unknown_session_with_404(_auth, client, monkeypatch):
    _patch_registry(monkeypatch, exists=False)
    resp = client.post(
        "/api/v1/stream",
        json={"user_query": "q", "session_id": "nope"},
        headers=HEADERS,
    )
    assert resp.status_code == 404   # before the SSE response starts, not inside it


def test_run_sql_rejects_an_unknown_session_with_404(_auth, client, monkeypatch):
    _patch_registry(monkeypatch, exists=False)
    resp = client.post(
        "/api/v1/run-sql",
        json={"sql": "SELECT 1", "session_id": "nope"},
        headers=HEADERS,
    )
    assert resp.status_code == 404

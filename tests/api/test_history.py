"""Query-history (Phase 2 W1 D3): write-on-execute, pagination, filters, auth.

Fully offline: the store and the graph are monkeypatched. The write-on-execute
test runs the REAL run_query (service level) against a fake graph and asserts
the history row is derived from the final state — including the resilience
rule that a history failure never fails the query.
"""
import pytest
from fastapi.testclient import TestClient

from api import main
from nixus.services import query_service as qs

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


def _patch_store_read(monkeypatch, page):
    seen: dict = {}

    async def _list(**kwargs):
        seen.update(kwargs)
        return page

    monkeypatch.setattr("api.history.list_query_history", _list)
    return seen


# ── Auth ─────────────────────────────────────────────────────────────────────
def test_history_401_without_a_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    assert client.get("/api/v1/history").status_code == 401


def test_history_fail_closed_503_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", None)
    assert client.get("/api/v1/history").status_code == 503


# ── List: pagination + filters forwarded to the store ────────────────────────
def test_list_returns_the_page_shape(client, monkeypatch, _auth):
    _patch_store_read(monkeypatch, {
        "items": [{
            "id": 1, "session_id": "s1", "question": "how many artists?",
            "generated_sql": 'SELECT COUNT(*) FROM "Artist"', "status": "ANSWERED",
            "duration_ms": 12.5, "row_count": 1, "created_at": "2026-09-17T00:00:00+00:00",
        }],
        "total": 1, "limit": 50, "offset": 0,
    })
    resp = client.get("/api/v1/history", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "ANSWERED"
    assert body["limit"] == 50 and body["offset"] == 0


def test_list_forwards_pagination_and_filters(client, monkeypatch, _auth):
    seen = _patch_store_read(monkeypatch, {"items": [], "total": 0, "limit": 10, "offset": 20})
    resp = client.get(
        "/api/v1/history?limit=10&offset=20&status=ANSWERED&session_id=s1"
        "&since=2026-09-01T00:00:00Z&until=2026-09-17T00:00:00Z",
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert seen == {
        "limit": 10,
        "offset": 20,
        "status": "ANSWERED",
        "session_id": "s1",
        "since": seen["since"],  # datetime bound, forwarded untouched
        "until": seen["until"],
    }
    assert seen["since"] is not None and seen["until"] is not None


def test_list_rejects_unknown_status_with_400(client, monkeypatch, _auth):
    _patch_store_read(monkeypatch, {"items": [], "total": 0, "limit": 50, "offset": 0})
    resp = client.get("/api/v1/history?status=NOT_A_STATUS", headers=HEADERS)
    assert resp.status_code == 400
    assert "NOT_A_STATUS" in resp.json()["detail"]["error"]


def test_list_validates_pagination_bounds(client, monkeypatch, _auth):
    _patch_store_read(monkeypatch, {"items": [], "total": 0, "limit": 50, "offset": 0})
    assert client.get("/api/v1/history?limit=0", headers=HEADERS).status_code == 422
    assert client.get("/api/v1/history?limit=201", headers=HEADERS).status_code == 422
    assert client.get("/api/v1/history?offset=-1", headers=HEADERS).status_code == 422


# ── Write-on-execute: run_query records history (service level) ─────────────
class _FakeGraph:
    def __init__(self, final_state):
        self._final = final_state
        self.calls: list = []

    async def ainvoke(self, state, config=None):
        self.calls.append(state)
        return self._final


def _final_state(**overrides):
    state = {
        "outcome": "ANSWERED",
        "error": None,
        "generated_sql": 'SELECT COUNT(*) FROM "Artist"',
        "execution_result": {"success": True, "rows": [{"count": 275}], "row_count": 1},
    }
    state.update(overrides)
    return state


def _patch_graph(monkeypatch, final_state):
    fake = _FakeGraph(final_state)
    monkeypatch.setattr(qs, "build_graph", lambda: fake)
    return fake


def test_run_query_writes_history_on_execute(monkeypatch):
    _patch_graph(monkeypatch, _final_state())
    recorded: list = []

    async def _record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("nixus.db.query_history_store.record_query_history", _record)

    import asyncio

    result = asyncio.run(qs.run_query("how many artists?", "sess-1"))
    assert result["outcome"] == "ANSWERED"
    assert len(recorded) == 1
    row = recorded[0]
    assert row["session_id"] == "sess-1"
    assert row["question"] == "how many artists?"
    assert row["generated_sql"] == 'SELECT COUNT(*) FROM "Artist"'
    assert row["status"] == "ANSWERED"
    assert row["row_count"] == 1
    assert row["duration_ms"] >= 0


def test_run_query_history_records_error_and_refusal_paths(monkeypatch):
    """Every terminal state is history: refusal (no SQL) and hard error."""
    import asyncio

    recorded: list = []

    async def _record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("nixus.db.query_history_store.record_query_history", _record)

    _patch_graph(monkeypatch, _final_state(outcome="REFUSED_WRITE", generated_sql="", execution_result=None))
    asyncio.run(qs.run_query("delete everything", "sess-2"))

    _patch_graph(monkeypatch, _final_state(outcome=None, error="connection reset"))
    asyncio.run(qs.run_query("flaky question", "sess-3"))

    assert recorded[0]["status"] == "REFUSED_WRITE"
    assert recorded[0]["row_count"] == 0
    assert recorded[1]["status"] == "ERROR"


def test_history_failure_never_fails_the_query(monkeypatch):
    _patch_graph(monkeypatch, _final_state())

    async def _boom(**kwargs):
        raise RuntimeError("history table missing")

    monkeypatch.setattr("nixus.db.query_history_store.record_query_history", _boom)

    import asyncio

    result = asyncio.run(qs.run_query("how many artists?", "sess-1"))
    assert result["outcome"] == "ANSWERED"  # the query result is unaffected


def test_derive_status_is_honest():
    assert qs.derive_status("ANSWERED", None) == "ANSWERED"
    assert qs.derive_status("REFUSED_AMBIGUOUS", None) == "REFUSED_AMBIGUOUS"
    assert qs.derive_status(None, "boom") == "ERROR"
    assert qs.derive_status(None, None) == "UNKNOWN"

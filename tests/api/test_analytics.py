"""Analytics endpoint (Phase 3 W1 D2): shape, auth fail-closed, and the
aggregates-only contract — fully offline (the store is monkeypatched).

The no-raw-SQL assertion walks the whole payload: no key or string value
anywhere may look like SQL text. The contract is aggregates only.
"""
import pytest
from fastapi.testclient import TestClient

from api import main

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}

SUMMARY = {
    "totals": {
        "runs": 10, "answered": 7, "refused": 2,
        "needs_clarification": 1, "errors": 0,
    },
    "rates": {
        "answered_rate": 70.0, "refusal_rate": 20.0,
        "needs_clarification_rate": 10.0, "error_rate": 0.0,
        "accepted_feedback": 2, "rejected_feedback": 1, "accept_rate": 66.7,
    },
    "latency_ms": {"avg": 412.5, "p95": 933.1, "max": 1500.0},
    "volume": [{"date": "2026-09-17", "runs": 10, "answered": 7}],
    "cache": {"entries": 3, "total_hits": 12, "hit_rate": 80.0},
    "fewshot": {"total": 25, "auto_learned": 15, "seeded": 10},
}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


@pytest.fixture
def _summary(monkeypatch):
    async def _fake():
        return SUMMARY

    monkeypatch.setattr("api.analytics.get_analytics_summary", _fake)


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_strings(v)


# ── Auth: the endpoint inherits the fail-closed middleware ───────────────────
def test_analytics_401_without_a_key(client, monkeypatch, _summary):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    assert client.get("/api/v1/analytics/summary").status_code == 401


def test_analytics_fail_closed_503_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", None)
    assert client.get("/api/v1/analytics/summary").status_code == 503


# ── Shape: the composed summary, unchanged ───────────────────────────────────
def test_summary_passthrough_with_valid_key(client, _auth, _summary):
    resp = client.get("/api/v1/analytics/summary", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == SUMMARY


def test_summary_carries_no_raw_sql(client, _auth, _summary):
    body = client.get("/api/v1/analytics/summary", headers=HEADERS).json()
    # The aggregates-only contract, enforced over every key and string value —
    # no SQL text, no raw column names from the pipeline record.
    for s in _walk_strings(body):
        assert not any(
            token in s.upper()
            for token in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "FROM ")
        ), f"raw SQL-looking string leaked into the analytics payload: {s!r}"
    assert "generated_sql" not in body
    # The composed shapes arrive intact — the exact /cache-stats and
    # /fewshot-stats payloads, not re-derived lookalikes.
    assert body["cache"] == SUMMARY["cache"]
    assert body["fewshot"] == SUMMARY["fewshot"]


def test_summary_exposes_feedback_rates(client, _auth, _summary):
    body = client.get("/api/v1/analytics/summary", headers=HEADERS).json()
    assert body["rates"]["accepted_feedback"] == 2
    assert body["rates"]["rejected_feedback"] == 1
    assert body["rates"]["accept_rate"] == 66.7

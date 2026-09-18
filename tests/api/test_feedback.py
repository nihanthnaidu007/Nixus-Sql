"""History feedback endpoint (Phase 3 W1 D1): shape, verdict validation,
auth fail-closed, linkage demotion — fully offline (the store is
monkeypatched, exactly like tests/api/test_history.py).
"""
import pytest
from fastapi.testclient import TestClient

from api import main

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


def _patch_record(monkeypatch, result):
    seen: dict = {}

    async def _record(history_id, verdict, note=None):
        seen.update({"history_id": history_id, "verdict": verdict, "note": note})
        return result

    monkeypatch.setattr("api.history.record_feedback", _record)
    return seen


# ── Auth: the endpoint inherits the fail-closed middleware ───────────────────
def test_feedback_401_without_a_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    assert client.post("/api/v1/history/1/feedback", json={"verdict": "reject"}).status_code == 401


def test_feedback_fail_closed_503_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", None)
    assert client.post("/api/v1/history/1/feedback", json={"verdict": "reject"}).status_code == 503


# ── Shape: verdict + optional note, forwarded to the store ───────────────────
def test_reject_forwards_verdict_and_note(client, monkeypatch, _auth):
    seen = _patch_record(monkeypatch, {
        "id": 7, "verdict": "reject", "note": "wrong join",
        "fewshot_example_id": 12, "fewshot_disabled": True,
    })
    resp = client.post(
        "/api/v1/history/7/feedback",
        json={"verdict": "reject", "note": "wrong join"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert seen == {"history_id": 7, "verdict": "reject", "note": "wrong join"}
    body = resp.json()
    assert body == {
        "id": 7,
        "verdict": "reject",
        "note": "wrong join",
        "fewshot_example_id": 12,
        "fewshot_disabled": True,
    }


def test_accept_without_note_defaults_the_note(client, monkeypatch, _auth):
    seen = _patch_record(monkeypatch, {
        "id": 3, "verdict": "accept", "note": None,
        "fewshot_example_id": 5, "fewshot_disabled": False,
    })
    resp = client.post("/api/v1/history/3/feedback", json={"verdict": "accept"}, headers=HEADERS)
    assert resp.status_code == 200
    assert seen["note"] is None
    assert seen["verdict"] == "accept"
    assert resp.json()["fewshot_disabled"] is False


def test_feedback_404_when_history_row_missing(client, monkeypatch, _auth):
    async def _none(history_id, verdict, note=None):
        return None

    monkeypatch.setattr("api.history.record_feedback", _none)
    resp = client.post("/api/v1/history/999/feedback", json={"verdict": "reject"}, headers=HEADERS)
    assert resp.status_code == 404
    assert "History row not found" in resp.json()["detail"]["error"]


def test_feedback_rejects_an_unknown_verdict_with_422(client, monkeypatch, _auth):
    _patch_record(monkeypatch, {"id": 1, "verdict": "reject", "note": None,
                                "fewshot_example_id": None, "fewshot_disabled": False})
    resp = client.post("/api/v1/history/1/feedback", json={"verdict": "demote"}, headers=HEADERS)
    assert resp.status_code == 422

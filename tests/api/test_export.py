"""Export endpoints (Phase 2 W1 D1): content types, headers, capped rows, auth.

The HTTP layer runs through TestClient against the real app; the guarded
executor is monkeypatched (the executor's own guardrails are tested in
tests/services/test_export_service.py). Fully offline — no database.
"""
import json

import pytest
from fastapi.testclient import TestClient

from api import main
from nixus.services.export_service import GuardedResult

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}
SELECT = "SELECT \"ArtistId\", \"Name\" FROM \"Artist\""


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


def _result(rows, capped=False):
    return GuardedResult(
        columns=["ArtistId", "Name"],
        rows=rows,
        row_count=len(rows),
        row_limit=1000,
        capped=capped,
    )


def _patch_executor(monkeypatch, result):
    async def _exec(sql):
        return result

    monkeypatch.setattr("api.export.execute_guarded", _exec)


def _sample_rows(n=2):
    return [{"ArtistId": i, "Name": f"Artist {i}"} for i in range(1, n + 1)]


# ── Auth: fail-closed, inherited from the middleware ─────────────────────────
def test_export_401_without_a_key(client, monkeypatch):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    monkeypatch.setattr(main.settings, "api_key", KEY)
    for fmt in ("csv", "xlsx", "json"):
        resp = client.post(f"/api/v1/export/{fmt}", json={"sql": SELECT})
        assert resp.status_code == 401, fmt


def test_export_401_on_a_wrong_key(client, monkeypatch):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    monkeypatch.setattr(main.settings, "api_key", KEY)
    resp = client.post(
        "/api/v1/export/csv", json={"sql": SELECT}, headers={"X-API-Key": "not-the-key"}
    )
    assert resp.status_code == 401


def test_export_fail_closed_503_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", None)
    resp = client.post("/api/v1/export/csv", json={"sql": SELECT})
    assert resp.status_code == 503
    assert "API_KEY" in resp.json()["detail"]


# ── Read-only guard ──────────────────────────────────────────────────────────
def test_export_rejects_non_select(client, _auth):
    resp = client.post(
        "/api/v1/export/csv", json={"sql": "DELETE FROM \"Artist\""}, headers=HEADERS
    )
    assert resp.status_code == 400
    assert "SELECT" in resp.json()["detail"]["error"]


def test_export_surfaces_execution_failure(client, monkeypatch, _auth):
    class _Boom(Exception):
        pass

    async def _fail(sql):
        raise _Boom("column does not exist")

    # ExportQueryError is the contract; any SQLAlchemyError subclass maps to it.
    from nixus.services.export_service import ExportQueryError

    async def _fail_guarded(sql):
        raise ExportQueryError("relation \"missing\" does not exist")

    monkeypatch.setattr("api.export.execute_guarded", _fail_guarded)
    resp = client.post("/api/v1/export/csv", json={"sql": SELECT}, headers=HEADERS)
    assert resp.status_code == 400
    assert "missing" in resp.json()["detail"]["error"]


# ── CSV ──────────────────────────────────────────────────────────────────────
def test_csv_content_type_and_filename_header(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    resp = client.post(
        "/api/v1/export/csv", json={"sql": SELECT, "name": "artists"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"] == 'attachment; filename="artists.csv"'
    body = resp.content.decode("utf-8-sig")
    assert body.splitlines()[0] == "ArtistId,Name"
    assert "1,Artist 1" in body


def test_csv_default_filename_when_name_missing(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    resp = client.post("/api/v1/export/csv", json={"sql": SELECT}, headers=HEADERS)
    assert resp.headers["content-disposition"].startswith("attachment; filename=")
    assert resp.headers["content-disposition"].endswith('.csv"')


def test_csv_capped_export_labels_the_cap(client, monkeypatch, _auth):
    # The service already truncated to the cap and flagged it — the file carries
    # the capped set, the headers label the cap.
    rows = _sample_rows(2)
    _patch_executor(monkeypatch, _result(rows, capped=True))
    resp = client.post("/api/v1/export/csv", json={"sql": SELECT}, headers=HEADERS)
    assert resp.headers["X-Nixus-Capped"] == "true"
    assert resp.headers["X-Nixus-Row-Limit"] == "1000"
    assert resp.content.count(b"\n") == 3  # header + 2 rows


def test_csv_uncapped_has_no_capped_flag(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    resp = client.post("/api/v1/export/csv", json={"sql": SELECT}, headers=HEADERS)
    assert "X-Nixus-Capped" not in resp.headers


# ── XLSX ─────────────────────────────────────────────────────────────────────
def test_xlsx_content_type_and_magic_bytes(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    resp = client.post(
        "/api/v1/export/xlsx", json={"sql": SELECT, "name": "artists"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert (
        resp.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert resp.headers["content-disposition"] == 'attachment; filename="artists.xlsx"'
    # XLSX is a ZIP container: PK\x03\x04 magic.
    assert resp.content[:4] == b"PK\x03\x04"


def test_xlsx_capped_export_labels_the_cap(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows(2), capped=True))
    resp = client.post("/api/v1/export/xlsx", json={"sql": SELECT}, headers=HEADERS)
    assert resp.headers["X-Nixus-Capped"] == "true"


# ── JSON ─────────────────────────────────────────────────────────────────────
def test_json_content_type_and_payload_shape(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows()))
    resp = client.post(
        "/api/v1/export/json", json={"sql": SELECT, "name": "artists"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["content-disposition"] == 'attachment; filename="artists.json"'
    payload = json.loads(resp.content)
    assert payload["columns"] == ["ArtistId", "Name"]
    assert payload["rows"] == _sample_rows()
    assert payload["row_count"] == 2
    assert payload["row_limit"] == 1000
    assert payload["capped"] is False
    assert "exported_at" in payload


def test_json_capped_export_labels_the_cap_inline(client, monkeypatch, _auth):
    _patch_executor(monkeypatch, _result(_sample_rows(2), capped=True))
    resp = client.post("/api/v1/export/json", json={"sql": SELECT}, headers=HEADERS)
    payload = json.loads(resp.content)
    assert payload["capped"] is True
    assert payload["row_count"] == 2  # the capped set, labeled — not more

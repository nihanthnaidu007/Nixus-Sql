"""Saved-query endpoints (Phase 2 W1 D2): CRUD, re-run through the pipeline,
few-shot linkage, auth.

Fully offline: the store functions, the pipeline entry (run_query), and the
few-shot seeding mechanism are monkeypatched. The critical invariant under
test — a re-run calls run_query with the saved natural-language question and
NEVER executes the stored SQL directly.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from api import main
from api import saved_queries as sq

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}

SAVE_BODY = {
    "name": "Top artists",
    "natural_language": "Which artists have the most albums?",
    "generated_sql": 'SELECT "Artist"."Name", COUNT(*) FROM "Album" JOIN "Artist" ON "Album"."ArtistId" = "Artist"."ArtistId" GROUP BY "Artist"."Name" ORDER BY COUNT(*) DESC',
    "tags": [" music ", "catalog", "music"],
}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


@pytest.fixture
def store(monkeypatch):
    """An in-memory store standing in for saved_query_store."""
    rows: dict[int, dict] = {}
    next_id = {"n": 1}

    async def _create(name, natural_language, generated_sql, description=None, tags=None, parameters=None):
        if any(r["name"] == name for r in rows.values()):
            raise IntegrityError("duplicate", None, Exception("unique"))
        row = {
            "id": next_id["n"],
            "name": name,
            "description": description,
            "tags": list(tags or []),
            "natural_language": natural_language,
            "generated_sql": generated_sql,
            "parameters": parameters,
            "created_at": "2026-09-17T00:00:00+00:00",
            "updated_at": "2026-09-17T00:00:00+00:00",
            "last_run_at": None,
        }
        rows[row["id"]] = row
        next_id["n"] += 1
        return row

    async def _get(saved_query_id):
        return rows.get(saved_query_id)

    async def _list(tag=None):
        return [r for r in rows.values() if not tag or tag in r["tags"]]

    async def _delete(saved_query_id):
        return rows.pop(saved_query_id, None) is not None

    async def _record_run(saved_query_id):
        rows[saved_query_id]["last_run_at"] = "2026-09-17T01:00:00+00:00"

    monkeypatch.setattr(sq, "create_saved_query", _create)
    monkeypatch.setattr(sq, "get_saved_query", _get)
    monkeypatch.setattr(sq, "list_saved_queries", _list)
    monkeypatch.setattr(sq, "delete_saved_query", _delete)
    monkeypatch.setattr(sq, "record_saved_query_run", _record_run)
    return rows


def _patch_session(monkeypatch, issued: list):
    async def _resolve(supplied):
        sid = supplied or "issued-1"
        issued.append(sid)
        return sid

    monkeypatch.setattr(sq, "resolve_session_id", _resolve)


# ── Auth (fail-closed, inherited) ────────────────────────────────────────────
def test_saved_queries_401_without_a_key(client, monkeypatch, store):
    monkeypatch.setattr(main.settings, "api_key", KEY)
    assert client.post("/api/v1/saved-queries", json=SAVE_BODY).status_code == 401
    assert client.get("/api/v1/saved-queries").status_code == 401
    assert client.get("/api/v1/saved-queries/1").status_code == 401
    assert client.delete("/api/v1/saved-queries/1").status_code == 401
    assert client.post("/api/v1/saved-queries/1/run", json={}).status_code == 401


def test_saved_queries_fail_closed_503_when_key_unset(client, monkeypatch, store):
    monkeypatch.setattr(main.settings, "api_key", None)
    assert client.get("/api/v1/saved-queries").status_code == 503


# ── CRUD ─────────────────────────────────────────────────────────────────────
def test_create_returns_the_saved_row(client, _auth, store):
    resp = client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    assert resp.status_code == 201
    saved = resp.json()
    assert saved["id"] == 1
    assert saved["name"] == "Top artists"
    # Tags are normalized: stripped, deduplicated, order-preserving.
    assert saved["tags"] == ["music", "catalog"]
    assert saved["last_run_at"] is None


def test_create_rejects_non_select_sql(client, _auth, store):
    body = {**SAVE_BODY, "generated_sql": 'INSERT INTO "Artist" DEFAULT VALUES'}
    resp = client.post("/api/v1/saved-queries", json=body, headers=HEADERS)
    assert resp.status_code == 400


def test_create_409_on_duplicate_name(client, _auth, store):
    first = client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    assert first.status_code == 201
    second = client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]["error"]


def test_list_get_delete_roundtrip(client, _auth, store):
    client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)

    listing = client.get("/api/v1/saved-queries", headers=HEADERS)
    assert listing.status_code == 200
    assert [i["id"] for i in listing.json()["items"]] == [1]

    got = client.get("/api/v1/saved-queries/1", headers=HEADERS)
    assert got.status_code == 200
    assert got.json()["name"] == "Top artists"

    assert client.get("/api/v1/saved-queries/99", headers=HEADERS).status_code == 404

    deleted = client.delete("/api/v1/saved-queries/1", headers=HEADERS)
    assert deleted.status_code == 204
    assert client.delete("/api/v1/saved-queries/1", headers=HEADERS).status_code == 404
    assert client.get("/api/v1/saved-queries/1", headers=HEADERS).status_code == 404


# ── Re-run: through the pipeline, never direct SQL ───────────────────────────
def _patch_pipeline(monkeypatch, final_state):
    calls: list = []

    async def _run(user_query, session_id, **kwargs):
        calls.append({"user_query": user_query, "session_id": session_id})
        return final_state

    monkeypatch.setattr(sq, "run_query", _run)
    return calls


def _answered_state(**overrides):
    state = {
        "outcome": "ANSWERED",
        "session_id": "issued-1",
        "generated_sql": 'SELECT "Name" FROM "Artist"',
        "tables_identified": ["Artist", "Album"],
        "execution_result": {"success": True, "rows": [{"Name": "AC/DC"}], "row_count": 1},
    }
    state.update(overrides)
    return state


def test_rerun_goes_through_the_pipeline(client, monkeypatch, _auth, store):
    client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    issued: list = []
    _patch_session(monkeypatch, issued)
    calls = _patch_pipeline(monkeypatch, _answered_state())

    resp = client.post("/api/v1/saved-queries/1/run", json={}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ANSWERED"
    # The PIPELINE received the saved natural-language question — never the SQL.
    assert calls[0]["user_query"] == SAVE_BODY["natural_language"]
    assert issued == ["issued-1"]
    assert store[1]["last_run_at"] is not None


def test_rerun_never_executes_the_stored_sql(client, monkeypatch, _auth, store):
    """Direct execution would bypass grounding + safety checks; the only
    execution path is the pipeline's execute node inside run_query."""
    client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    _patch_session(monkeypatch, [])
    calls = _patch_pipeline(monkeypatch, _answered_state())
    executed_directly: list = []

    # If anything tried to run the stored SQL through the guarded executor, fail.
    async def _no_direct_exec(sql):
        executed_directly.append(sql)
        raise AssertionError("stored SQL must not be executed directly")

    from nixus.services import export_service

    monkeypatch.setattr(export_service, "execute_guarded", _no_direct_exec)

    resp = client.post("/api/v1/saved-queries/1/run", json={}, headers=HEADERS)
    assert resp.status_code == 200
    assert executed_directly == []
    assert calls[0]["user_query"] == SAVE_BODY["natural_language"]


def test_rerun_feeds_the_fewshot_corpus_on_accepted_runs(client, monkeypatch, _auth, store):
    client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    _patch_session(monkeypatch, [])
    _patch_pipeline(monkeypatch, _answered_state())

    stored: list = []

    async def _store_fewshot(natural_language, sql_query, tables_used, auto_learned=False):
        stored.append((natural_language, sql_query, tables_used, auto_learned))
        return True

    import nixus.db.fewshot_store as fs

    monkeypatch.setattr(fs, "store_fewshot_example", _store_fewshot)

    resp = client.post("/api/v1/saved-queries/1/run", json={}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["fewshot_candidate_recorded"] is True
    nl, sql, tables, auto = stored[0]
    assert nl == SAVE_BODY["natural_language"]
    # The corpus candidate is the run's ACTUAL SQL (possibly corrected), not the stale save.
    assert sql == _answered_state()["generated_sql"]
    assert tables == ["Artist", "Album"]
    assert auto is True  # the existing seeding mechanism's auto_learned flag


def test_rerun_skips_fewshot_linkage_on_refusal(client, monkeypatch, _auth, store):
    client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    _patch_session(monkeypatch, [])
    _patch_pipeline(monkeypatch, _answered_state(outcome="REFUSED_WRITE", execution_result=None))

    stored: list = []

    async def _store_fewshot(natural_language, sql_query, tables_used, auto_learned=False):
        stored.append((natural_language, sql_query))
        return True

    import nixus.db.fewshot_store as fs

    monkeypatch.setattr(fs, "store_fewshot_example", _store_fewshot)

    resp = client.post("/api/v1/saved-queries/1/run", json={}, headers=HEADERS)
    assert resp.status_code == 200
    assert stored == []
    assert "fewshot_candidate_recorded" not in resp.json()


def test_rerun_survives_a_fewshot_store_failure(client, monkeypatch, _auth, store):
    """Corpus bookkeeping is best-effort — the answer still returns."""
    client.post("/api/v1/saved-queries", json=SAVE_BODY, headers=HEADERS)
    _patch_session(monkeypatch, [])
    _patch_pipeline(monkeypatch, _answered_state())

    async def _boom(natural_language, sql_query, tables_used, auto_learned=False):
        raise RuntimeError("corpus store down")

    import nixus.db.fewshot_store as fs

    monkeypatch.setattr(fs, "store_fewshot_example", _boom)

    resp = client.post("/api/v1/saved-queries/1/run", json={}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ANSWERED"


def test_rerun_unknown_saved_query_404(client, monkeypatch, _auth, store):
    _patch_session(monkeypatch, [])
    resp = client.post("/api/v1/saved-queries/99/run", json={}, headers=HEADERS)
    assert resp.status_code == 404

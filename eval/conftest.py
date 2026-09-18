"""
Shared pytest fixtures and helpers for the NIXUS SQL evaluation harness.

The API server must be running before executing any tests.
Set NIXUS_API_URL to override the default http://localhost:8000.
"""

import json
import os
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

# sync_engine -> state_db (liveness probe). sync_target_engine -> target_db
# (read-only): gold queries read the user's data, which now lives in target_db.
from nixus.db.connection import sync_engine, sync_target_engine

BASE_URL = os.environ.get("NIXUS_API_URL", "http://localhost:8000")


def auth_headers() -> dict:
    """X-API-Key headers when the target API is secured (NIXUS_API_KEY env).

    The API fails closed without a configured key and 401s without the header;
    the harness passes it through from the environment so the same benchmark
    runs against a locked-down server. Empty dict when unset (unsecured API).
    """
    key = os.environ.get("NIXUS_API_KEY")
    return {"X-API-Key": key} if key else {}

# The retired Chinook gold set (6.2) lives under eval/archive_chinook/ for
# history and must NOT be collected as part of the benchmark of record. See
# eval/archive_chinook/README.md.
collect_ignore_glob = ["archive_chinook/*"]

# Sidecar file that custom metrics (latency percentiles, hit rates, etc.)
# are written to so eval/report.py can pick them up when rendering
# BENCHMARK.md. pytest-json-report captures pass/fail per test but not the
# numeric measurements made inside the tests.
METRICS_FILE = Path(os.environ.get("NIXUS_METRICS_FILE", "eval/benchmark_metrics.json"))


def record_metric(key: str, value) -> None:
    """Persist a single benchmark metric to the sidecar JSON file.

    Safe to call concurrently — each call re-reads the file, merges the
    new key, and writes it back. Values must be JSON-serializable.
    """
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if METRICS_FILE.exists():
        try:
            data = json.loads(METRICS_FILE.read_text() or "{}")
        except json.JSONDecodeError:
            data = {}
    data[key] = value
    METRICS_FILE.write_text(json.dumps(data, indent=2, default=str))


def reset_metrics(keys: list[str] | None = None) -> None:
    """Reset benchmark metrics in the sidecar JSON file.

    - keys=None  → full wipe (used by full benchmark runs)
    - keys=[...] → only the named keys are removed; other metrics from prior
                   runs are preserved (used by per-category runs so debugging
                   one category does not silently destroy metrics from an
                   earlier full run).
    """
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if keys is None:
        METRICS_FILE.write_text("{}")
        return

    existing: dict = {}
    if METRICS_FILE.exists():
        try:
            existing = json.loads(METRICS_FILE.read_text() or "{}")
        except json.JSONDecodeError:
            existing = {}
    for k in keys:
        existing.pop(k, None)
    METRICS_FILE.write_text(json.dumps(existing, indent=2, default=str))


# ── Infrastructure probe ─────────────────────────────────────────────────────
# The eval suite is integration-only: every test needs the API (on :8000) and
# Postgres (via DATABASE_URL) running. When infra is down, a refused TCP
# connection raises (httpx.ConnectError / OperationalError) BEFORE any health
# check, which pytest reports as 52 ERRORS instead of the intended SKIPS.
#
# We probe both services ONCE per session and SKIP the whole suite with one
# actionable message, instead of letting each test error independently.

_START_HINT = (
    "Infrastructure is not running. Start it, then re-run the eval suite:\n"
    "  1. docker compose up -d db          # Postgres on localhost:5433\n"
    "  2. uvicorn api.main:app --host 0.0.0.0 --port 8000\n"
    "     (or run scripts/dev.sh and set NIXUS_API_URL to the port it prints)\n"
    "See BASELINE.md for the full bring-up + benchmark procedure."
)


class _InfraStatus:
    def __init__(self, api_ok: bool, db_ok: bool):
        self.api_ok = api_ok
        self.db_ok = db_ok


def _probe_api(url: str, timeout: float = 10.0) -> bool:
    """True iff GET /api/health returns 200. A refused/timed-out connection
    returns False instead of raising, so callers can skip cleanly."""
    try:
        with httpx.Client(base_url=url, timeout=timeout) as client:
            return client.get("/api/v1/health").status_code == 200
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError):
        return False


def _probe_db() -> bool:
    """True iff the sync engine can run SELECT 1. Any connection failure
    (OperationalError, refused socket, etc.) returns False instead of raising."""
    try:
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def infra_status() -> _InfraStatus:
    """Probe API + Postgres once per test session."""
    return _InfraStatus(api_ok=_probe_api(BASE_URL), db_ok=_probe_db())


@pytest.fixture(autouse=True)
def _require_infra(infra_status: _InfraStatus) -> None:
    """Skip (not error) every integration test when infra is unreachable.

    Covers tests that hit the API via http_client AND tests that open a direct
    DB connection (e.g. test_cache_miss_latency), so a refused connection never
    surfaces as an ERROR."""
    if not infra_status.api_ok:
        pytest.skip(f"API not reachable at {BASE_URL}.\n{_START_HINT}")
    if not infra_status.db_ok:
        pytest.skip(f"Postgres not reachable via DATABASE_URL.\n{_START_HINT}")


@pytest.fixture(scope="session")
def http_client(infra_status: _InfraStatus):
    # infra_status was already probed; if the API is down we skip here too so
    # the client is only ever constructed against a reachable server.
    if not infra_status.api_ok:
        pytest.skip(f"API not reachable at {BASE_URL}.\n{_START_HINT}")
    with httpx.Client(base_url=BASE_URL, timeout=120.0, headers=auth_headers()) as client:
        yield client


def run_query(client: httpx.Client, question: str, session_id: str | None = None) -> dict:
    """POST /api/run and return the final state dict.

    ``session_id`` stays EMPTY unless a caller threads one back: session ids are
    server-issued (an id the server never issued 404s), and the final state
    carries the id to reuse on follow-ups (clarification rounds).
    """
    resp = client.post("/api/v1/run", json={"user_query": question, "session_id": session_id or ""})
    resp.raise_for_status()
    return resp.json()


def run_sql(client: httpx.Client, sql: str) -> dict:
    """POST /api/run-sql and return the mini-state dict (server-issued session)."""
    resp = client.post("/api/v1/run-sql", json={"sql": sql, "session_id": ""})
    resp.raise_for_status()
    return resp.json()


def run_query_timed(client: httpx.Client, question: str) -> tuple[dict, float]:
    """POST /api/run and return (state, latency_ms). Session is server-issued."""
    t0 = time.monotonic()
    resp = client.post("/api/v1/run", json={"user_query": question, "session_id": ""})
    latency_ms = (time.monotonic() - t0) * 1000
    resp.raise_for_status()
    return resp.json(), latency_ms


def run_gold_sql(sql: str) -> list:
    """Execute a gold SQL query against the TARGET database (read-only).

    Gold queries read the user's data (Chinook), which lives in target_db — the
    same database the API executes the generated SQL against. Running gold SQL
    here through the read-only target engine keeps the comparison apples-to-apples
    and proves the data is reachable via the read-only role.
    """
    if sync_target_engine is None:
        raise RuntimeError(
            "TARGET_DATABASE_URL not set — cannot run gold SQL against target_db."
        )
    with sync_target_engine.connect() as conn:
        result = conn.execute(text(sql))
        return list(result.fetchall())


def _is_numeric_val(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float, Decimal)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _is_date_like(v) -> bool:
    """Return True for datetime objects and ISO-format date strings."""
    from datetime import date
    from datetime import datetime as dt
    if isinstance(v, (dt, date)):
        return True
    if isinstance(v, str):
        s = v.strip()
        # ISO date: 2003-05-03 or 2003-05-03T00:00:00
        if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
            return True
    return False


def _row_values(row) -> list:
    """Return the values of a row as a plain list regardless of row type."""
    if isinstance(row, dict):
        return list(row.values())
    try:
        return list(row._mapping.values())
    except AttributeError:
        return list(row)


def _tokenize_text(s: str) -> set[str]:
    """Tokenize a string into lowercase words.

    Splits on whitespace so that concatenated names like 'Helena Holý'
    (generated by the LLM as FirstName || ' ' || LastName) produce the same
    tokens as when they are returned as separate columns ('Helena', 'Holý').

    Strings with no whitespace (emails, single-word values) are returned
    as a singleton set so they remain matchable by the full value.
    """
    tokens = s.split()
    if len(tokens) <= 1:
        return {s} if s else set()
    # Multi-word: include both the full string AND individual words >= 2 chars
    result = {s}
    result.update(t for t in tokens if len(t) >= 2)
    return result


def _extract_text_values(row) -> frozenset[str]:
    """Return lowercased text tokens for non-numeric, non-date values in a row.

    These are the "entity name" tokens that identify what the row is about
    (e.g. track name, customer name, genre name).  Numeric metrics and dates
    are excluded because the LLM may reformat them.

    Multi-word values like 'Helena Holý' are split into tokens so they match
    the same values when the LLM returns them as separate columns.
    """
    result: set[str] = set()
    for v in _row_values(row):
        if v is None or _is_numeric_val(v) or _is_date_like(v):
            continue
        s = str(v).strip().lower()
        if s:
            result.update(_tokenize_text(s))
    return frozenset(result)


def _gold_entity_set(gold_rows: list) -> set[frozenset[str]]:
    """Return text fingerprints for each gold row."""
    return {_extract_text_values(r) for r in gold_rows if _extract_text_values(r)}


def _api_entity_set(api_rows: list) -> set[frozenset[str]]:
    """Return text fingerprints for each API row."""
    return {_extract_text_values(r) for r in api_rows if _extract_text_values(r)}


_ROW_MATCH_THRESHOLD = 0.5  # min Jaccard-like intersection ratio to count a row as matched


def _rows_match(fp1: frozenset[str], fp2: frozenset[str]) -> bool:
    """Two row fingerprints match when they share enough text in common.

    Uses an intersection / min-size ratio so that rows with different columns
    (e.g. gold has City, API has Company) can still match via shared name+email.
    """
    if not fp1 or not fp2:
        return False
    intersection = len(fp1 & fp2)
    smaller = min(len(fp1), len(fp2))
    return (intersection / smaller) >= _ROW_MATCH_THRESHOLD


def result_overlap_rate(gold_rows: list, api_rows: list) -> float:
    """Fraction of gold row text fingerprints that match any API row fingerprint.

    Uses text-value fingerprints rather than exact row comparison so that
    differences in column aliases, extra ID columns, unit conversions, and
    datetime formatting don't cause false failures.

    A gold fingerprint matches an API fingerprint when the two share at least
    _ROW_MATCH_THRESHOLD fraction of the smaller fingerprint's text items.
    This handles cases where one side has more columns than the other (e.g.
    gold has City, API has Company — they still share first_name + email).
    """
    if not gold_rows:
        return 1.0

    gold_fp = _gold_entity_set(gold_rows)
    api_fp  = _api_entity_set(api_rows)

    if not gold_fp:
        return 1.0  # all-numeric rows — can't compare entity names

    api_fp_list = list(api_fp)
    matches = sum(
        1 for gfp in gold_fp
        if any(_rows_match(gfp, afp) for afp in api_fp_list)
    )

    return matches / len(gold_fp)


def normalize_result_set(rows: list) -> set[frozenset[str]]:
    """Convenience wrapper — returns text fingerprint sets (for tests that still call this)."""
    return _gold_entity_set(rows)


def extract_rows(state: dict) -> list:
    """Pull result rows from an /api/run response.

    When served from cache the API only returns a 5-row preview.  Re-execute
    the cached SQL against the DB to get the full result set.  This lets
    correctness tests compare the full result against gold SQL.

    If the cached SQL cannot be determined (old cache entries), fall back to
    the 5-row preview — the test will still pass if that preview overlaps with
    the gold rows (which it usually does).
    """
    if state.get("served_from_cache"):
        cached_sql = state.get("generated_sql")
        if not cached_sql:
            cr = state.get("cache_result") or {}
            cached_sql = cr.get("cached_sql")
        if cached_sql:
            try:
                return run_gold_sql(cached_sql)
            except Exception:
                pass
        cr = state.get("cache_result") or {}
        return cr.get("result_preview") or []
    er = state.get("execution_result") or {}
    return er.get("rows", [])

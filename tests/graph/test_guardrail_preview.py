"""Pre-execution guardrail preview node (Phase 2, Wave 2 D2.2).

Runs against a mocked engine that RECORDS every statement sent, so the test can
fail loudly if ANALYZE sneaks into the preview statement (ANALYZE executes; the
read-only posture forbids it), and pins the degraded path: any EXPLAIN failure
yields no preview and never raises.
"""
import json

from nixus.graph.nodes import guardrail_preview as gp
from nixus.graph.nodes.guardrail_preview import guardrail_preview_node

PLAN = [{"Plan": {"Node Type": "Seq Scan", "Plan Rows": 42, "Total Cost": 12.34}}]


def _state(sql: str = "SELECT id FROM customers") -> dict:
    return {
        "generated_sql": sql,
        "validation_result": {
            "is_valid": True, "errors": [], "warnings": [], "normalized_sql": sql,
        },
        "stream_updates": [],
        "completed_nodes": [],
        "current_node": "",
    }


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def scalar(self):
        return self._payload


class _FakeConn:
    def __init__(self, statements, payload):
        self._statements = statements
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _FakeTxn()

    async def execute(self, stmt):
        self._statements.append(stmt.text)
        return _FakeResult(self._payload)


class _FakeEngine:
    def __init__(self, statements, payload):
        self._statements = statements
        self._payload = payload

    def connect(self):
        return _FakeConn(self._statements, self._payload)


async def test_preview_runs_plain_explain_and_reports_estimate(monkeypatch):
    statements: list[str] = []
    monkeypatch.setattr(
        gp, "get_target_engine", lambda: _FakeEngine(statements, json.dumps(PLAN))
    )

    state = await guardrail_preview_node(_state())

    assert len(statements) == 2  # the SET LOCAL timeout + the EXPLAIN
    assert statements[0].upper().startswith("SET LOCAL STATEMENT_TIMEOUT")
    explain = statements[1]
    # THE postural assertion: plain EXPLAIN, planning only. ANALYZE would execute.
    assert explain.upper().startswith("EXPLAIN (FORMAT JSON)")
    assert "ANALYZE" not in explain.upper()
    assert state["guardrail_preview"] == {"estimated_rows": 42, "plan_cost": 12.34}
    assert state["stream_updates"][-1]["node"] == "guardrail_preview"
    assert "estimates" in state["stream_updates"][-1]["message"]
    assert state["completed_nodes"] == ["guardrail_preview"]


async def test_preview_accepts_an_already_decoded_payload(monkeypatch):
    """asyncpg decodes the json column to a list; the node must take either wire form."""
    statements: list[str] = []
    monkeypatch.setattr(gp, "get_target_engine", lambda: _FakeEngine(statements, PLAN))

    state = await guardrail_preview_node(_state())

    assert state["guardrail_preview"] == {"estimated_rows": 42, "plan_cost": 12.34}


async def test_preview_degrades_silently_when_the_engine_is_unreachable(monkeypatch):
    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("TARGET_DATABASE_URL not set")

    monkeypatch.setattr(gp, "get_target_engine", lambda: _BrokenEngine())

    state = await guardrail_preview_node(_state())

    assert "guardrail_preview" not in state
    assert state["stream_updates"] == []
    assert state["completed_nodes"] == ["guardrail_preview"]


async def test_preview_degrades_silently_on_an_unexpected_payload(monkeypatch):
    statements: list[str] = []
    monkeypatch.setattr(gp, "get_target_engine", lambda: _FakeEngine(statements, "not-json"))

    state = await guardrail_preview_node(_state())

    assert "guardrail_preview" not in state
    assert state["stream_updates"] == []


async def test_preview_skips_when_there_is_nothing_validated():
    state = await guardrail_preview_node(_state(""))
    assert "guardrail_preview" not in state
    assert state["completed_nodes"] == []  # not run — nothing was validated

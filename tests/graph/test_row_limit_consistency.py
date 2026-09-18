"""G5 consistency — one row cap, every end of the contract.

The MCP ``query`` tool reads ``execution_result["row_limit"]`` and surfaces it
in its sanitized contract — the same field ``GuardedResult`` carries for
``run_sql``. These tests pin the invariants that contract depends on:

1. the execute node's SUCCESS payload carries ``row_limit`` equal to the
   module constant that actually bounded ``fetchmany()`` (it was always
   absent before the G5 fix); the failure payload stays limit-free — a failed
   execution returned no set, so its limit is meaningless (GuardedResult
   semantics);
2. the OVERFLOW threshold in ``check_result`` is that SAME constant object —
   no local copy that can drift from the enforced cap (the class-of-bug the
   G4 remediation warned about in ``api/guardrails.py:20-24``).

Both real graph nodes run against the established fake-engine pattern
(tests/services/test_export_service.py) — no database, no LLM.
"""
import pytest

from nixus.graph.nodes import check_result
from nixus.graph.nodes import execute_query as eq
from nixus.graph.state import SQLAgentState


class _FakeRow:
    """Mimics a SQLAlchemy Row: the node reads ``row._mapping``."""

    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows

    def fetchmany(self, n):
        return [_FakeRow(r) for r in self._rows[:n]]

    def keys(self):
        return self._columns


class _FakeConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, stmt):
        return _FakeResult(["id"], [{"id": 1}])


class _FakeEngine:
    def connect(self):
        return _FakeConn()


class _TimeoutEngine:
    """Engine whose connection dies the way a statement timeout does."""

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def begin(self):
            raise eq.SQLAlchemyError("canceling statement due to statement timeout")

    def connect(self):
        return self._Conn()


def _initial_state() -> SQLAgentState:
    return {
        "user_query": "q",
        "session_id": "s",
        "clarification_context": None,
        "clarification_round": 0,
        "scope_category": None,
        "scope_message": None,
        "outcome": None,
        "clarifying_question": None,
        "reason": None,
        "intent_class": "",
        "extracted_entities": [],
        "cache_result": None,
        "served_from_cache": False,
        "relevant_schemas": [],
        "schema_context": "",
        "tables_identified": [],
        "similar_examples": [],
        "fewshot_context": "",
        "generated_sql": "SELECT 1",
        "validation_result": {"normalized_sql": "SELECT 1"},
        "execution_result": None,
        "result_quality": None,
        "correction_attempts": 0,
        "correction_history": [],
        "chart_config": None,
        "explanation": "",
        "confidence_score": 0.0,
        "current_node": "",
        "completed_nodes": [],
        "is_complete": False,
        "trace_id": None,
        "trace_url": None,
        "error": None,
        "stream_updates": [],
    }


@pytest.fixture
def _engine(monkeypatch):
    monkeypatch.setattr(eq, "get_target_engine", lambda: _FakeEngine())


async def test_success_payload_carries_the_enforced_row_limit(_engine):
    """row_limit in the success payload IS the cap fetchmany() ran under."""
    result = await eq.execute_query_node(_initial_state())
    payload = result["execution_result"]
    assert payload is not None
    assert payload["success"] is True
    assert payload["row_limit"] == eq.ROW_FETCH_LIMIT


async def test_failure_payload_stays_limit_free(monkeypatch):
    """A failed execution returned no set — the payload stays limit-free."""
    monkeypatch.setattr(eq, "get_target_engine", lambda: _TimeoutEngine())
    result = await eq.execute_query_node(_initial_state())
    payload = result["execution_result"]
    assert payload is not None
    assert payload["success"] is False
    assert "row_limit" not in payload


async def test_check_result_judges_overflow_through_the_same_constant(_engine):
    """At exactly ROW_FETCH_LIMIT rows the real check_result node answers
    OVERFLOW, and the payload that fed it names the same cap — the two
    disjuncts of mcp_server's `capped` can never disagree."""
    executed = await eq.execute_query_node(_initial_state())
    payload = executed["execution_result"]
    assert payload is not None
    assert payload["row_limit"] == eq.ROW_FETCH_LIMIT

    payload["row_count"] = eq.ROW_FETCH_LIMIT  # simulate a full fetch
    judged = await check_result.check_result_node(executed)
    assert judged["result_quality"]["status"] == "OVERFLOW"
    # Same object, not an equal copy: no drift is possible by construction.
    assert check_result.ROW_FETCH_LIMIT is eq.ROW_FETCH_LIMIT

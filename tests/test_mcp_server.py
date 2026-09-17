"""MCP stdio server tests — protocol dispatch + tool contracts with faked cores.

The core dependencies (run_query / execute_guarded / session resolution) are
injected, so no LLM, no database, and no network is involved. The sanitized
result contract asserted here is the D3 deliverable: columns/rows/row_count/
row_limit/capped/execution_time_ms/explanation/confidence/served_from_cache,
Decimal/datetime→str, errors sanitized.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import nixus.mcp_server as mcp
from nixus.mcp_server import McpServer, sanitize_value


def _server(run_query=None, execute_guarded=None, sessions=None) -> McpServer:
    return McpServer(
        run_query=run_query,
        execute_guarded=execute_guarded,
        resolve_session_id=sessions or _fake_sessions(),
    )


def _fake_sessions():
    async def resolve(session_id):
        return session_id or "srv-issued"
    return resolve


def _rpc(msg_id, method, params=None) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


# ── sanitization ──────────────────────────────────────────────────────────────

def test_sanitize_value_converts_decimal_datetime_bytes():
    assert sanitize_value(Decimal("12.50")) == "12.50"
    assert sanitize_value(datetime(2026, 9, 17, 10, 30, tzinfo=timezone.utc)) == "2026-09-17 10:30:00+00:00"
    assert sanitize_value(b"raw") == str(b"raw")  # unknown types: str()
    assert sanitize_value(7) == 7
    assert sanitize_value(None) is None


def test_sanitize_error_is_one_line_without_internals():
    text = mcp.sanitize_error(RuntimeError("boom\n\tsecond   line"))
    assert text.startswith("RuntimeError:")
    assert "\n" not in text


# ── protocol ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initialize_returns_capabilities():
    server = _server()
    out = await server.handle_message(_rpc(1, "initialize"))
    result = out["result"]
    assert result["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "nixus-sql"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_tools_list_exposes_query_and_run_sql():
    server = _server()
    out = await server.handle_message(_rpc(2, "tools/list"))
    names = [t["name"] for t in out["result"]["tools"]]
    assert names == ["query", "run_sql"]
    for tool in out["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["required"]


@pytest.mark.asyncio
async def test_notifications_get_no_response():
    server = _server()
    assert await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


@pytest.mark.asyncio
async def test_unknown_method_is_method_not_found():
    server = _server()
    out = await server.handle_message(_rpc(3, "resources/list"))
    assert out["error"]["code"] == mcp.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_batch_mixed_request_and_notification():
    server = _server()
    out = await server.handle_message([
        _rpc(1, "ping"),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ])
    assert isinstance(out, list) and len(out) == 1
    assert out[0]["id"] == 1


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_non_dict_request_is_invalid_request():
    server = _server()
    out = await server.handle_message("not a dict")
    assert out["error"]["code"] == mcp.INVALID_REQUEST


# ── query tool ────────────────────────────────────────────────────────────────

def _graph_state() -> dict:
    return {
        "generated_sql": "SELECT count(*) FROM plans;",
        "execution_result": {
            "columns": ["count"],
            "rows": [{"count": Decimal(3)}],
            "row_count": 1,
            "row_limit": 500,
            "execution_time_ms": 12.5,
        },
        "result_quality": {"status": "OK"},
        "explanation": "Counting plans.",
        "confidence_score": 0.91,
        "served_from_cache": True,
        "outcome": "ANSWERED",
        "error": None,
    }


@pytest.mark.asyncio
async def test_query_tool_returns_sanitized_contract():
    calls = []

    async def fake_run_query(question: str, session_id: str):
        calls.append((question, session_id))
        return _graph_state()

    server = _server(run_query=fake_run_query)
    out = await server.handle_message(_rpc(4, "tools/call", {"name": "query", "arguments": {"question": "How many plans?"}}))
    payload = out["result"]["structuredContent"]
    assert calls == [("How many plans?", "srv-issued")]
    # Decimal cell serialized to str
    assert payload["rows"] == [{"count": "3"}]
    for key in ("columns", "rows", "row_count", "row_limit", "capped",
                "execution_time_ms", "explanation", "confidence", "served_from_cache"):
        assert key in payload, key
    assert payload["served_from_cache"] is True
    assert payload["confidence"] == 0.91
    # MCP envelope: text content mirrors the payload
    assert json.loads(out["result"]["content"][0]["text"])["row_count"] == 1


@pytest.mark.asyncio
async def test_query_tool_overflow_sets_capped():
    state = _graph_state()
    state["execution_result"]["row_count"] = 501
    state["result_quality"] = {"status": "OVERFLOW"}

    async def fake_run_query(question: str, session_id: str):
        return state

    server = _server(run_query=fake_run_query)
    out = await server.handle_message(_rpc(5, "tools/call", {"name": "query", "arguments": {"question": "big"}}))
    assert out["result"]["structuredContent"]["capped"] is True


@pytest.mark.asyncio
async def test_query_tool_refusal_is_a_result_not_a_crash():
    async def refused(question: str, session_id: str):
        return {
            "generated_sql": "", "execution_result": None, "result_quality": None,
            "explanation": "", "confidence_score": 0.0, "served_from_cache": False,
            "outcome": "REFUSED_WRITE", "error": None,
        }

    server = _server(run_query=refused)
    out = await server.handle_message(_rpc(6, "tools/call", {"name": "query", "arguments": {"question": "drop everything"}}))
    payload = out["result"]["structuredContent"]
    assert payload["outcome"] == "REFUSED_WRITE"
    assert payload["rows"] == [] and payload["row_count"] == 0
    assert "isError" not in out["result"]


@pytest.mark.asyncio
async def test_query_tool_failure_is_sanitized_is_error():
    async def boom(question: str, session_id: str):
        raise RuntimeError("engine exploded")

    server = _server(run_query=boom)
    out = await server.handle_message(_rpc(7, "tools/call", {"name": "query", "arguments": {"question": "x"}}))
    assert out["result"]["isError"] is True
    assert out["result"]["content"][0]["text"].startswith("RuntimeError:")


@pytest.mark.asyncio
async def test_query_tool_requires_nonempty_question():
    server = _server()
    out = await server.handle_message(_rpc(8, "tools/call", {"name": "query", "arguments": {"question": "  "}}))
    assert out["result"]["isError"] is True


# ── run_sql tool ──────────────────────────────────────────────────────────────

def _guarded(capped=False):
    return SimpleNamespace(
        columns=["id"], rows=[{"id": 1}], row_count=1,
        row_limit=500, capped=capped,
    )


@pytest.mark.asyncio
async def test_run_sql_executes_readonly_sql_via_guarded():
    calls = []

    async def fake_guarded(sql: str):
        calls.append(sql)
        return _guarded()

    server = _server(execute_guarded=fake_guarded)
    out = await server.handle_message(_rpc(9, "tools/call", {"name": "run_sql", "arguments": {"sql": "SELECT id FROM plans;"}}))
    payload = out["result"]["structuredContent"]
    assert calls == ["SELECT id FROM plans;"]
    assert payload["columns"] == ["id"]
    assert payload["row_limit"] == 500
    assert payload["capped"] is False
    assert payload["served_from_cache"] is False
    for key in ("columns", "rows", "row_count", "row_limit", "capped",
                "execution_time_ms", "explanation", "confidence", "served_from_cache"):
        assert key in payload, key


@pytest.mark.asyncio
async def test_run_sql_rejects_writes_at_the_boundary():
    calls = []

    async def must_not_run(sql: str):
        calls.append(sql)
        raise AssertionError("guarded execution must not be reached")

    server = _server(execute_guarded=must_not_run)
    out = await server.handle_message(_rpc(10, "tools/call", {"name": "run_sql", "arguments": {"sql": "DELETE FROM plans;"}}))
    payload = out["result"]["structuredContent"]
    assert calls == []
    assert payload["error"] == "Only SELECT statements are permitted."
    assert "detail" in payload


@pytest.mark.asyncio
async def test_run_sql_reports_capped_honestly():
    async def capped_guarded(sql: str):
        return _guarded(capped=True)

    server = _server(execute_guarded=capped_guarded)
    out = await server.handle_message(_rpc(11, "tools/call", {"name": "run_sql", "arguments": {"sql": "SELECT id FROM plans;"}}))
    assert out["result"]["structuredContent"]["capped"] is True


@pytest.mark.asyncio
async def test_run_sql_failure_is_sanitized():
    async def boom(sql: str):
        raise RuntimeError("statement timeout hit")

    server = _server(execute_guarded=boom)
    out = await server.handle_message(_rpc(12, "tools/call", {"name": "run_sql", "arguments": {"sql": "SELECT 1;"}}))
    assert out["result"]["isError"] is True
    assert "timeout" in out["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_unknown_tool_is_an_is_error_result():
    server = _server()
    out = await server.handle_message(_rpc(13, "tools/call", {"name": "drop_db", "arguments": {}}))
    assert out["result"]["isError"] is True
    assert "Unknown tool" in out["result"]["content"][0]["text"]


# ── stdio loop ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_serve_processes_lines_until_eof():
    server = _server()
    lines = iter([
        json.dumps(_rpc(1, "initialize")),
        json.dumps(_rpc(2, "tools/list")),
        "\n",  # blank lines are skipped, not EOF
        "not json",  # parse error response
        "",  # EOF
    ])

    async def read_line():
        try:
            return next(lines)
        except StopIteration:
            return ""

    import io
    from unittest import mock

    fake = io.StringIO()
    with mock.patch.object(mcp.sys, "stdout", fake):
        await server.serve(read_line=read_line)

    out_lines = [line for line in fake.getvalue().splitlines() if line]
    assert len(out_lines) == 3
    one, two, three = (json.loads(line) for line in out_lines)
    assert one["result"]["serverInfo"]["name"] == "nixus-sql"
    assert [t["name"] for t in two["result"]["tools"]] == ["query", "run_sql"]
    assert three["error"]["code"] == mcp.PARSE_ERROR

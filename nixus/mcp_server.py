"""NIXUS MCP server — a stdio JSON-RPC 2.0 process exposing two read-only tools.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout (the MCP stdio
convention). STDLIB ONLY (json / asyncio / sys) — no MCP SDK, no sockets, no
HTTP surface. Networked transport is doctrinally rejected for NIXUS: one
shared API key and no per-caller identity would expose the agent loop to
whoever holds the key; stdio is the same trust boundary as the CLI — the
local operator. Logging goes to STDERR (stdout is protocol-only).

Methods: initialize / tools/list / tools/call (plus notifications/initialized,
ignored). Batch requests (a JSON array) are handled per the JSON-RPC 2.0 spec.

Tools — both reuse the EXACT execution paths that exist today (no third
execution path is created):
  * query(question, session_id?) → nixus.services.query_service.run_query —
    the full guardrail ensemble (scope gate, generation-time write blocks,
    syntax validation, live grounding, read-only role, statement timeout,
    row caps, correction budget).
  * run_sql(sql, session_id?)    → is_read_only_sql gate, then
    nixus.services.export_service.execute_guarded — the same guarded
    execution the export endpoints use (statement timeout + ROW_FETCH_LIMIT
    with the honest LIMIT+1 capped probe), mirroring the /run-sql contract.

Sanitized result contract (the same fields the API surfaces, no internals):
    columns, rows, row_count, row_limit, capped, execution_time_ms,
    explanation, confidence, served_from_cache
Decimal / datetime / date values serialize to str; refusals surface as the
graph's own outcome/refusal strings (already user-facing text); exceptions
are reduced to sanitized messages — no tracebacks, no internals.

Lifecycle mirrors the CLI (nixus/cli.py): init_checkpointer() once at boot,
aclose_checkpointer() on shutdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import date, datetime
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()  # before ANY nixus import — same order rule as the CLI

# stdio logging: stdout is RESERVED for the JSON-RPC protocol.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nixus_sql.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "nixus-sql"
SERVER_VERSION = "3.0.0"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "Natural-language question about the data."},
        "session_id": {"type": "string", "description": "Optional existing session id; a new one is issued when omitted."},
    },
    "required": ["question"],
}

_RUN_SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "A single read-only SELECT (or WITH ... SELECT) statement."},
        "session_id": {"type": "string", "description": "Optional existing session id; a new one is issued when omitted."},
    },
    "required": ["sql"],
}

TOOLS = [
    {
        "name": "query",
        "description": (
            "Ask a natural-language question and get SQL generated, guarded, and executed "
            "read-only. Returns the SQL, rows, explanation, and confidence."
        ),
        "inputSchema": _QUERY_SCHEMA,
    },
    {
        "name": "run_sql",
        "description": (
            "Execute one user-provided read-only SELECT under the statement-timeout and "
            "row-cap guardrails. Non-SELECT statements are rejected."
        ),
        "inputSchema": _RUN_SQL_SCHEMA,
    },
]


def sanitize_value(value: object) -> object:
    """One cell → JSON-safe. Decimal/datetime → str (the same serialization the
    API's default=str uses); everything else passes through."""
    if isinstance(value, Decimal | datetime | date | bytes):
        return str(value)
    return value


def sanitize_error(exc: Exception) -> str:
    """Exception → one sanitized line. Types may be named (they are part of the
    product's own error surface); internals (tracebacks, SQL, connection URLs)
    must never leak — str(exc) of NIXUS errors is already user-facing text."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class McpServer:
    """JSON-RPC dispatch with injectable core dependencies (tests pass fakes;
    production wires the real query_service / export_service / sessions)."""

    def __init__(self, *, run_query=None, execute_guarded=None, resolve_session_id=None):
        # Imported here (not module level) so importing nixus.mcp_server stays
        # side-effect-light for tests that inject fakes.
        from api.sessions import resolve_session_id as _resolve
        from nixus.services.export_service import execute_guarded as _guarded
        from nixus.services.query_service import run_query as _run_query

        self._run_query = run_query or _run_query
        self._execute_guarded = execute_guarded or _guarded
        self._resolve_session_id = resolve_session_id or _resolve

    # ── tool implementations ────────────────────────────────────────────────

    async def tool_query(self, args: dict) -> dict:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("query requires a non-empty 'question' string")
        session_id = await self._resolve_session_id(args.get("session_id"))
        output = await self._run_query(question.strip(), session_id)
        execution = output.get("execution_result") or {}
        quality = output.get("result_quality") or {}
        rows = execution.get("rows") or []
        row_limit = execution.get("row_limit")
        return {
            "session_id": session_id,
            "generated_sql": output.get("generated_sql") or "",
            "columns": execution.get("columns") or [],
            "rows": [
                {k: sanitize_value(v) for k, v in row.items()} if isinstance(row, dict) else row
                for row in rows
            ],
            "row_count": execution.get("row_count", 0),
            "row_limit": row_limit,
            "capped": bool(quality.get("status") == "OVERFLOW" or (row_limit is not None and execution.get("row_count", 0) > row_limit)),
            "execution_time_ms": execution.get("execution_time_ms"),
            "explanation": output.get("explanation") or None,
            "confidence": output.get("confidence_score"),
            "served_from_cache": bool(output.get("served_from_cache", False)),
            "outcome": output.get("outcome"),
            "error": output.get("error"),
        }

    async def tool_run_sql(self, args: dict) -> dict:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("run_sql requires a non-empty 'sql' string")
        session_id = await self._resolve_session_id(args.get("session_id"))

        # The /run-sql server-side guard, verbatim: reject at the boundary.
        from nixus.utils.sql_safety import is_read_only_sql
        is_safe, reason = is_read_only_sql(sql)
        if not is_safe:
            return {
                "session_id": session_id,
                "error": "Only SELECT statements are permitted.",
                "detail": reason,
            }

        started = time.monotonic()
        result = await self._execute_guarded(sql)
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        return {
            "session_id": session_id,
            "columns": list(result.columns),
            "rows": [
                {k: sanitize_value(v) for k, v in row.items()} if isinstance(row, dict) else row
                for row in result.rows
            ],
            "row_count": result.row_count,
            "row_limit": result.row_limit,
            "capped": result.capped,
            "execution_time_ms": elapsed_ms,
            "explanation": None,
            "confidence": None,
            "served_from_cache": False,
        }

    # ── JSON-RPC dispatch ────────────────────────────────────────────────────

    def _tool_result(self, payload: dict) -> dict:
        text = json.dumps(payload, default=str)
        return {"content": [{"type": "text", "text": text}], "structuredContent": payload}

    def _tool_error(self, message: str) -> dict:
        return {"content": [{"type": "text", "text": message}], "isError": True}

    async def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return self._tool_error("tools/call arguments must be an object")
        try:
            if name == "query":
                return self._tool_result(await self.tool_query(args))
            if name == "run_sql":
                return self._tool_result(await self.tool_run_sql(args))
            return self._tool_error(f"Unknown tool: {name}")
        except Exception as e:
            # Tool-execution failure → MCP isError result with a sanitized
            # message (protocol-level problems use JSON-RPC errors instead).
            logger.exception("tool %s failed", name)
            return self._tool_error(sanitize_error(e))

    async def handle_message(self, msg: object) -> dict | list | None:
        """Handle one decoded JSON-RPC message (dict or batch list). Returns the
        response payload, or None for notifications (nothing to emit)."""
        if isinstance(msg, list):
            responses = [await self.handle_message(item) for item in msg]
            return [r for r in responses if r is not None]

        if not isinstance(msg, dict):
            return self._rpc_error(None, INVALID_REQUEST, "request must be an object")

        method = msg.get("method")
        msg_id = msg.get("id")
        is_notification = "id" not in msg

        if not isinstance(method, str):
            return None if is_notification else self._rpc_error(msg_id, INVALID_REQUEST, "missing method")

        if method == "initialize":
            return self._rpc_result(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method in ("notifications/initialized", "initialized"):
            return None  # notification — acknowledged by silence
        if method == "tools/list":
            return self._rpc_result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                return None if is_notification else self._rpc_error(msg_id, INVALID_PARAMS, "params must be an object")
            if is_notification:
                return None
            return self._rpc_result(msg_id, await self._call_tool(params))
        if method == "ping":
            return self._rpc_result(msg_id, {})

        # Unknown method: notifications get no error response (JSON-RPC 2.0 §4.2).
        if is_notification:
            return None
        return self._rpc_error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    # ── response helpers ─────────────────────────────────────────────────────

    def _rpc_result(self, msg_id: object, result: object) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _rpc_error(self, msg_id: object, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    # ── stdio loop ───────────────────────────────────────────────────────────

    async def serve(self, read_line=None) -> None:
        """Newline-delimited JSON-RPC over stdin/stdout. ``read_line`` is
        injectable for tests; production reads sys.stdin via an executor."""
        from nixus.graph.graph import aclose_checkpointer, init_checkpointer

        # Checkpointer lifecycle exactly as the CLI owns it: opened once for
        # the process, closed on the way out. Fail-soft: an unavailable
        # checkpointer degrades persistence, not the server.
        try:
            await init_checkpointer()
            logger.info("Checkpointer initialized.")
        except Exception:
            logger.exception("Checkpointer initialization failed; continuing without persistence")

        loop = asyncio.get_running_loop()
        read_line = read_line or (lambda: loop.run_in_executor(None, sys.stdin.readline))
        try:
            while True:
                line = await read_line()
                if not line:
                    break  # EOF — client closed stdin; graceful shutdown
                line = line.strip()
                if not line:
                    continue
                # Typed up front: handle_message returns a response payload,
                # a batch payload, or None for notifications.
                response: dict | list | None = None
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    response = self._rpc_error(None, PARSE_ERROR, f"parse error: {e}")
                else:
                    response = await self.handle_message(msg)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
        finally:
            try:
                await aclose_checkpointer()
            except Exception:
                logger.exception("Failed to close checkpointer")


def main() -> None:
    server = McpServer()
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()

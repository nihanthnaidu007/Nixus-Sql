"""Mid-graph pre-execution guardrail preview (Phase 2, Wave 2 D2.2).

Between grounding and execution, run a PLAIN ``EXPLAIN (FORMAT JSON)`` of the
normalized SQL over the read-only target engine and surface what the planner
says BEFORE the query runs: the estimated row count and the plan cost. They go
into a new ``guardrail_preview`` state field (carried on /run and on the
/stream ``complete`` event) plus one ``stream_updates`` line the execution log
shows live.

PLAIN EXPLAIN ONLY. ``EXPLAIN ANALYZE`` would EXECUTE the statement — the
read-only posture forbids it; this node never constructs one. The test for this
node asserts on the captured statements so an ANALYZE cannot sneak in silently.

Degradation contract: if the EXPLAIN fails for any reason (engine down,
unexpected payload, statement timeout), the run proceeds to execution with NO
preview — a missing estimate must never block an answer.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from nixus.config import settings
from nixus.db.connection import get_target_engine
from nixus.graph.state import SQLAgentState

QUERY_TIMEOUT_MS = settings.query_timeout_ms


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


def _parse_planner_estimate(payload: object) -> dict:
    """The EXPLAIN (FORMAT JSON) payload → {estimated_rows, plan_cost}.

    Accepts both wire forms (asyncpg decodes the json column to a list, other
    drivers hand back a JSON string). Any unexpected shape raises, which the
    caller treats as "no preview" — never as a run failure.
    """
    plan: Any = json.loads(payload) if isinstance(payload, str) else payload
    top = plan[0]["Plan"]
    return {
        "estimated_rows": int(top["Plan Rows"]),
        "plan_cost": float(top["Total Cost"]),
    }


async def guardrail_preview_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "guardrail_preview"
    sql = (state.get("validation_result") or {}).get("normalized_sql", "") or state.get(
        "generated_sql", ""
    )
    if not sql:
        # Nothing validated (cache hit, refusal) — nothing to preview.
        return state

    try:
        async with get_target_engine().connect() as conn:
            async with conn.begin():
                # Planning only. The timeout guard mirrors execute_query's so a
                # pathological plan cannot stall the graph before execution.
                await conn.execute(
                    text(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT_MS}ms'")
                )
                payload = (await conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))).scalar()
        preview = _parse_planner_estimate(payload)
        state["guardrail_preview"] = preview
        state["stream_updates"].append({
            "timestamp": now(), "node": "guardrail_preview",
            "message": (
                f"Pre-execution preview — planner estimates "
                f"~{preview['estimated_rows']:,} rows (cost {preview['plan_cost']:.2f})"
            ),
            "status": "done",
        })
    except Exception:
        # Degrade silently: emit no preview, never block the run.
        pass

    state["completed_nodes"].append("guardrail_preview")
    return state

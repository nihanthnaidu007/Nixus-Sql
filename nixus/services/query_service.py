"""Framework-agnostic entry point for running a single NIXUS SQL query.

This is THE function adapters call to run a query end to end. It imports no web
framework (no FastAPI / Starlette / Streamlit) — the API calls it today; the CLI
(Phase 7) and the React backend (Phase 8) will call the same function, which is
the whole point of the rule-1 core boundary.

Extracted verbatim from ``api.main``'s ``POST /api/run`` handler in 1.1f:
behavior is identical — same initial-state construction, same run config, same
logging, same ``ainvoke`` on the same module-level compiled graph. The streaming
(SSE) path deliberately stays in the API for now; it moves here when a non-HTTP
consumer needs it.
"""
import logging
import time
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig

from nixus.graph.graph import build_graph
from nixus.graph.state import SQLAgentState
from nixus.utils.langsmith_config import get_run_config
from nixus.utils.logging_config import log_query_complete, log_query_start

# Same logger channel the API used for these lines, so log output is unchanged.
logger = logging.getLogger("nixus_sql.api")


def derive_status(outcome: str | None, error: str | None) -> str:
    """The history status for one finished run (pure).

    The graph's outcome discriminator is the truth when present; a run that
    never reached an outcome but carries an error is ERROR; anything else is
    recorded honestly as UNKNOWN rather than guessed.
    """
    if outcome:
        return outcome
    if error:
        return "ERROR"
    return "UNKNOWN"


async def record_history_safely(
    session_id: str,
    question: str,
    final_state: Mapping[str, Any],
    duration_ms: float,
) -> None:
    """Persist one query_history row; a history failure NEVER fails a query.

    Resilience pattern of this codebase (see ruff.toml header): broad
    except-with-log for bookkeeping that must not break the primary path.
    """
    from nixus.db.query_history_store import record_query_history

    try:
        execution = final_state.get("execution_result") or {}
        await record_query_history(
            session_id=session_id,
            question=question,
            generated_sql=final_state.get("generated_sql") or "",
            status=derive_status(final_state.get("outcome"), final_state.get("error")),
            duration_ms=duration_ms,
            row_count=int(execution.get("row_count") or 0),
            # The corpus row this run learned (explain_result stamps it) —
            # the run→few-shot linkage a later feedback/reject demotes.
            fewshot_example_id=final_state.get("fewshot_example_id"),
        )
    except Exception:
        logger.exception(
            "Query-history recording failed for session %s; the query result is unaffected",
            session_id,
        )


def get_thread_config(
    session_id: str,
    base_config: RunnableConfig | dict[str, Any] | None = None,
) -> dict:
    """Merge a LangGraph thread_id into the run config so the AsyncPostgresSaver checkpointer can find the checkpoint."""
    cfg = dict(base_config) if base_config else {}
    cfg["configurable"] = {**(cfg.get("configurable") or {}), "thread_id": session_id}
    return cfg


async def run_query(
    user_query: str,
    session_id: str,
    clarification_context: dict | None = None,
    clarification_round: int = 0,
) -> dict:
    """Run one query through the compiled graph and return the final state dict.

    ``session_id`` must already be resolved by the caller (the API supplies a
    uuid default); session handling stays in the adapter. Returns the raw final
    graph state, exactly as the ``/api/run`` handler previously returned it.

    ``clarification_context`` / ``clarification_round`` carry the stateless
    clarification round-trip (Option B) in with the request. Both default to the
    fresh single-turn case, so a normal query behaves exactly as before.
    """
    initial_state = SQLAgentState(
        user_query=user_query,
        session_id=session_id,
        clarification_context=clarification_context,
        clarification_round=clarification_round,
        scope_category=None, scope_message=None,
        outcome=None, clarifying_question=None, reason=None,
        intent_class="", extracted_entities=[],
        cache_result=None, served_from_cache=False,
        relevant_schemas=[], schema_context="", tables_identified=[],
        similar_examples=[], fewshot_context="",
        generated_sql="",
        validation_result=None, execution_result=None, result_quality=None,
        correction_attempts=0, correction_history=[],
        chart_config=None, explanation="", confidence_score=0.0,
        current_node="", completed_nodes=[], is_complete=False,
        trace_id=None, trace_url=None, error=None, stream_updates=[]
    )
    config = get_thread_config(session_id, get_run_config(
        session_id=session_id,
        user_query=user_query,
        run_name="nixus-sql-query"
    ))
    start = log_query_start(logger, session_id, user_query)
    final_state = await build_graph().ainvoke(initial_state, config=config)
    duration_ms = (time.monotonic() - start) * 1000
    log_query_complete(
        logger,
        session_id=session_id,
        user_query=user_query,
        intent_class=final_state.get("intent_class", "unknown"),
        cache_hit=final_state.get("served_from_cache", False),
        corrections_used=final_state.get("correction_attempts", 0),
        result_quality=(final_state.get("result_quality") or {}).get("status", "unknown"),
        row_count=(final_state.get("execution_result") or {}).get("row_count", 0),
        chart_type=(final_state.get("chart_config") or {}).get("chart_type"),
        duration_ms=duration_ms,
        error=final_state.get("error"),
    )
    # One history row per executed query (Phase 2 W1 D3) — best-effort.
    await record_history_safely(session_id, user_query, final_state, duration_ms)
    return final_state

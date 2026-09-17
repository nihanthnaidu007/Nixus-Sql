from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from nixus.config import settings
from nixus.graph.state import SQLAgentState

load_dotenv()

MAX_ATTEMPTS = settings.max_correction_attempts

from nixus.graph.nodes.check_cache import check_cache_node
from nixus.graph.nodes.check_result import check_result_node
from nixus.graph.nodes.classify_chart import classify_chart_node
from nixus.graph.nodes.execute_query import execute_query_node
from nixus.graph.nodes.explain_result import explain_result_node
from nixus.graph.nodes.generate_sql import generate_sql_node
from nixus.graph.nodes.guardrail_preview import guardrail_preview_node
from nixus.graph.nodes.parse_intent import parse_intent_node
from nixus.graph.nodes.retrieve_fewshot import retrieve_fewshot_node
from nixus.graph.nodes.retrieve_schema import retrieve_schema_node
from nixus.graph.nodes.scope_classifier import (
    scope_classifier_node,
    scope_response_node,
)
from nixus.graph.nodes.self_correct import self_correct_node
from nixus.graph.nodes.validate_syntax import validate_syntax_node
from nixus.graph.nodes.verify_grounding import verify_grounding_node

# The LangGraph checkpointer is NIXUS-owned bookkeeping → STATE database.
# AsyncPostgresSaver uses psycopg3 (not asyncpg) and requires the URL in plain
# `postgresql://` form. Strip any SQLAlchemy driver suffix so it works regardless
# of how the state URL was configured.
_pg_url = (
    (settings.state_url or "")
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("postgresql+psycopg2://", "postgresql://")
    .replace("postgres://", "postgresql://", 1)
)

# These three module-level singletons are created lazily inside the running
# event loop (FastAPI lifespan startup). AsyncPostgresSaver.__init__ calls
# asyncio.get_running_loop(), so it cannot be constructed at module-load time.
#
# The same AsyncPostgresSaver instance must be reused by both the initial
# invoke and any resume invoke (required for interrupt/resume to work across
# calls and across multiple API processes hitting the same Postgres).
_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None
_graph = None


async def init_checkpointer() -> None:
    """Open the checkpoint pool, instantiate the saver inside the running
    event loop, and create the `checkpoints` / `checkpoint_writes` /
    `checkpoint_blobs` tables if they don't exist. Idempotent.

    Must be called once during FastAPI lifespan startup BEFORE the graph is
    first invoked or built.
    """
    global _pool, _checkpointer
    if _checkpointer is not None:
        return
    _pool = AsyncConnectionPool(
        _pg_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await _pool.open()
    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()


async def aclose_checkpointer() -> None:
    """Close the checkpoint connection pool. Called from FastAPI lifespan shutdown."""
    global _pool, _checkpointer, _graph
    if _pool is not None:
        await _pool.close()
    _pool = None
    _checkpointer = None
    _graph = None


def build_graph():
    global _graph
    if _graph is not None:
        return _graph
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized. Call `await init_checkpointer()` "
            "during FastAPI lifespan startup before building the graph."
        )

    workflow = StateGraph(SQLAgentState)

    workflow.add_node("scope_classifier", scope_classifier_node)
    workflow.add_node("scope_response",   scope_response_node)
    workflow.add_node("parse_intent",     parse_intent_node)
    workflow.add_node("check_cache",      check_cache_node)
    workflow.add_node("retrieve_schema",  retrieve_schema_node)
    workflow.add_node("retrieve_fewshot", retrieve_fewshot_node)
    workflow.add_node("generate_sql",     generate_sql_node)
    workflow.add_node("validate_syntax",  validate_syntax_node)
    workflow.add_node("verify_grounding", verify_grounding_node)
    workflow.add_node("guardrail_preview", guardrail_preview_node)
    workflow.add_node("execute_query",    execute_query_node)
    workflow.add_node("check_result",     check_result_node)
    workflow.add_node("self_correct",     self_correct_node)
    workflow.add_node("classify_chart",   classify_chart_node)
    workflow.add_node("explain_result",   explain_result_node)

    workflow.set_entry_point("scope_classifier")

    # Scope gate. ANSWERED (IN_SCOPE) continues into the existing flow unchanged;
    # NEEDS_CLARIFICATION and the refusal outcomes (incl. the AMBIGUOUS-termination
    # cap) route to a terminal that surfaces the clarifying question or reason.
    # Clarification is a stateless request/response round-trip — no interrupt.
    workflow.add_conditional_edges("scope_classifier",
        lambda s: "parse_intent" if s.get("outcome") == "ANSWERED" else "scope_response",
        {"parse_intent": "parse_intent", "scope_response": "scope_response"})
    workflow.add_edge("scope_response", END)

    # parse_intent classifies READ/SCHEMA_QUESTION/AMBIGUOUS (used downstream by
    # generate_sql); write requests are already refused at the scope gate, so it
    # always continues to caching/retrieval.
    workflow.add_edge("parse_intent", "check_cache")

    workflow.add_conditional_edges("check_cache",
        lambda s: "classify_chart" if s["served_from_cache"] else "retrieve_schema",
        {"classify_chart": "classify_chart", "retrieve_schema": "retrieve_schema"})

    workflow.add_edge("retrieve_schema",  "retrieve_fewshot")
    workflow.add_edge("retrieve_fewshot", "generate_sql")
    workflow.add_edge("generate_sql",     "validate_syntax")

    # Syntactically valid SQL now flows through verify_grounding before execution.
    workflow.add_conditional_edges("validate_syntax",
        lambda s: (
            "END" if s.get("error") and "cannot be answered" in (s.get("error") or "").lower()
            else (
                "verify_grounding" if s["validation_result"]["is_valid"]
                else ("self_correct" if s["correction_attempts"] < MAX_ATTEMPTS else "explain_result")
            )
        ),
        {"verify_grounding": "verify_grounding", "self_correct": "self_correct",
         "explain_result": "explain_result", "END": END})

    # Grounded → a pre-execution guardrail preview (plain EXPLAIN: estimated rows +
    # plan cost, degraded silently), THEN execute. Hallucinated identifier → the
    # EXISTING self_correct loop (mirrors validate_syntax: self_correct until the
    # attempt cap, then explain).
    workflow.add_conditional_edges("verify_grounding",
        lambda s: (
            "guardrail_preview" if s["grounding_result"]["is_grounded"]
            else ("self_correct" if s["correction_attempts"] < MAX_ATTEMPTS else "explain_result")
        ),
        {"guardrail_preview": "guardrail_preview", "self_correct": "self_correct",
         "explain_result": "explain_result"})

    workflow.add_edge("guardrail_preview", "execute_query")

    workflow.add_edge("execute_query", "check_result")

    workflow.add_conditional_edges("check_result",
        lambda s: (
            "classify_chart" if s["result_quality"]["is_acceptable"]
            else ("self_correct" if s["correction_attempts"] < MAX_ATTEMPTS else "explain_result")
        ),
        {"classify_chart": "classify_chart", "self_correct": "self_correct", "explain_result": "explain_result"})

    workflow.add_edge("self_correct",   "validate_syntax")
    workflow.add_edge("classify_chart", "explain_result")
    workflow.add_edge("explain_result", END)

    _graph = workflow.compile(checkpointer=_checkpointer)
    return _graph

from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api.auth import APIKeyMiddleware, configured_api_key
from api.sessions import UnknownSessionError, resolve_session_id
from nixus.config import is_placeholder, settings
from nixus.db.connection import check_db_connection, get_state_engine, get_target_engine
from nixus.db.fewshot_store import get_fewshot_stats
from nixus.db.query_cache import evict_stale_cache_entries, get_cache_stats
from nixus.graph.graph import aclose_checkpointer, build_graph, init_checkpointer
from nixus.graph.state import SQLAgentState
from nixus.services.query_service import get_thread_config, run_query
from nixus.utils.langsmith_config import (
    get_run_config,
    get_trace_url,
    is_tracing_enabled,
)
from nixus.utils.logging_config import (
    log_node_event,
    log_query_complete,
    log_query_start,
)
from nixus.utils.sql_safety import is_read_only_sql

logger = logging.getLogger("nixus_sql.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure pgvector + agent tables exist before serving (idempotent).

    Also opens the LangGraph PostgreSQL checkpoint pool, creates the
    `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` tables on first
    run, and evicts stale `query_cache` entries.
    """
    from nixus.db.schema_init import init_database

    # Fail-closed notice: with no real API key the auth middleware 503s every
    # protected route. Loud at startup so an operator never has to guess why.
    if configured_api_key() is None:
        logger.error(
            "API_KEY is not configured — the API is FAIL-CLOSED: every /api route "
            "except /api/health will answer 503 until a key is set. See README, "
            "section 'API authentication & sessions'."
        )

    try:
        await asyncio.to_thread(init_database)
    except Exception:
        logger.exception("Database initialization failed; cache/few-shot endpoints may error until init succeeds")

    try:
        await init_checkpointer()
        build_graph()
        logger.info("LangGraph PostgreSQL checkpointer initialized and graph compiled.")
    except Exception:
        logger.exception("Checkpointer initialization failed; graph state persistence will not work")

    try:
        eviction_stats = await evict_stale_cache_entries(
            max_age_days=settings.cache_max_age_days,
            max_entries=settings.cache_max_entries,
        )
        logger.info(f"Cache eviction on startup: {eviction_stats}")
    except Exception:
        logger.exception("Cache eviction on startup failed; continuing anyway")

    # Advisory schema-drift check: compare the live target structure to what is
    # embedded (via introspection) and log if they diverge. Non-fatal; never
    # auto-reembeds.
    try:
        from nixus.schema.drift import log_drift_at_startup
        await log_drift_at_startup(get_target_engine(), get_state_engine())
    except Exception:
        logger.exception("Schema drift check setup failed; continuing anyway")

    yield

    try:
        await aclose_checkpointer()
    except Exception:
        logger.exception("Failed to close checkpointer connection pool")


app = FastAPI(title="NIXUS SQL API", version="3.0.0", lifespan=lifespan)

_raw_origins = settings.allowed_origins
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# API-key auth FIRST, then CORS: add_middleware prepends, so CORS ends up the
# OUTERMOST layer and answers browser preflights (OPTIONS) itself — the auth
# middleware only ever sees real requests, which carry the X-API-Key header.
app.add_middleware(APIKeyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# All endpoints are served under the versioned /api/v1 prefix (stable contract
# for the UI, the future React frontend, and external consumers). The router is
# mounted on the app at the bottom of this module, after every route is defined.
# Health is additionally aliased at the unversioned /api/health for infra probes.
router = APIRouter(prefix="/api/v1")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch any uncaught exception from a non-streaming endpoint, log the
    full traceback server-side, and return a structured JSON error with a
    short trace_id the user can quote when reporting the failure.

    Note: this handler does NOT intercept errors that originate inside an
    in-flight SSE stream — by that point the response has already begun
    streaming, so Starlette routes the error through the EventSourceResponse
    generator. /api/stream already catches its own exceptions and yields a
    structured `{"event": "error", ...}` SSE event.
    """
    trace_id = str(uuid.uuid4())[:8]
    logger.error(
        f"[{trace_id}] Unhandled exception on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}",
        # This function IS the exception handler (FastAPI passes the active
        # exception in) — hand it over explicitly for the traceback.
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "An unexpected error occurred.",
            "trace_id": trace_id,
            "type": type(exc).__name__,
        },
    )


@app.exception_handler(UnknownSessionError)
async def unknown_session_handler(request: Request, exc: UnknownSessionError):
    """404 for a client-supplied session_id this server never issued.

    Session ids double as LangGraph checkpoint thread ids, so only server-issued
    ids may reach them. The recovery path is in the body: start a session by
    sending an empty session_id, then reuse the id the response returns.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "Unknown session.",
            "detail": (
                "session_id was not issued by this server. Send an empty "
                "session_id to start a new session; the response includes the "
                "id to reuse for follow-ups."
            ),
        },
    )


class ClarificationExchange(BaseModel):
    question: str = ""
    answer: str = ""


class ClarificationContext(BaseModel):
    """Prior clarification turns carried back in by the client (stateless round-trip)."""
    original_question: str = ""
    prior_clarifications: list[ClarificationExchange] = []


class RunRequest(BaseModel):
    user_query: str
    session_id: str = ""
    # Optional + additive: absent for a normal single-turn query (the benchmark
    # path), present only when the client answers a clarifying question.
    clarification_context: ClarificationContext | None = None
    clarification_round: int = 0


class RunSQLRequest(BaseModel):
    sql: str
    session_id: str = ""


class StreamRequest(BaseModel):
    user_query: str
    session_id: str = ""
    clarification_context: ClarificationContext | None = None
    clarification_round: int = 0


@router.post("/run")
async def run_agent(req: RunRequest):
    # HTTP shell: resolve session identity (issue-or-bind), then delegate the
    # graph run to the framework-agnostic service. The core boundary receives
    # plain values (session_id + clarification round-trip), never request types.
    session_id = await resolve_session_id(req.session_id)
    return await run_query(
        req.user_query,
        session_id,
        clarification_context=req.clarification_context.model_dump() if req.clarification_context else None,
        clarification_round=req.clarification_round,
    )


@router.post("/stream")
async def stream_agent(req: StreamRequest):
    """
    SSE endpoint. Streams one event per node completion.
    Each event is a JSON-encoded partial state update.
    Final event has is_complete=True with full result and trace_url.
    """
    session_id = await resolve_session_id(req.session_id)

    initial_state = {
        "user_query": req.user_query,
        "session_id": session_id,
        "clarification_context": req.clarification_context.model_dump() if req.clarification_context else None,
        "clarification_round": req.clarification_round,
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
        "generated_sql": "",
        "validation_result": None,
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
        "stream_updates": []
    }

    config = get_thread_config(session_id, get_run_config(
        session_id=session_id,
        user_query=req.user_query,
        run_name="nixus-sql-stream"
    ))

    stream_start = log_query_start(logger, session_id, req.user_query)

    async def event_generator():
        g = build_graph()
        last_update_count = 0
        root_run_id = None   # run_id of the root chain (set on first on_chain_start)

        NODE_NAMES = {
            "scope_classifier", "scope_response", "parse_intent", "check_cache",
            "retrieve_schema", "retrieve_fewshot", "generate_sql", "validate_syntax",
            "execute_query", "check_result", "self_correct", "classify_chart",
            "explain_result"
        }

        try:
            async for event in g.astream_events(initial_state, config=config, version="v2"):
                kind = event.get("event", "")
                name = event.get("name", "")
                run_id = event.get("run_id", "")

                # Capture the root chain run_id from the very first chain start.
                # This is robust to whatever run_name the config assigns the root.
                if kind == "on_chain_start" and root_run_id is None:
                    root_run_id = run_id

                if kind == "on_chain_end" and name in NODE_NAMES:
                    output = event.get("data", {}).get("output", {})
                    if not isinstance(output, dict):
                        continue

                    all_updates = output.get("stream_updates", [])
                    new_updates = all_updates[last_update_count:]
                    last_update_count = len(all_updates)

                    partial = {
                        "node": name,
                        "completed_nodes": output.get("completed_nodes", []),
                        "current_node": output.get("current_node", name),
                        "stream_updates": new_updates,
                        "intent_class": output.get("intent_class", ""),
                        "extracted_entities": output.get("extracted_entities", []),
                        "tables_identified": output.get("tables_identified", []),
                        "served_from_cache": output.get("served_from_cache", False),
                        "correction_attempts": output.get("correction_attempts", 0),
                        "confidence_score": output.get("confidence_score", 0.0),
                        "error": output.get("error"),
                        "is_complete": False
                    }

                    log_node_event(logger, session_id, name, "complete")
                    yield {"event": "node_complete", "data": json.dumps(partial)}
                    await asyncio.sleep(0)

                elif kind == "on_chain_end" and run_id == root_run_id:
                    # Root chain completed — send the final event regardless of
                    # what run_name the config assigned to the chain
                    output = event.get("data", {}).get("output", {})
                    if not isinstance(output, dict):
                        continue

                    # Build trace URL from root run ID.
                    # get_trace_url is sync (uses time.sleep for retries);
                    # run it in a thread so we don't block the event loop.
                    trace_url = None
                    if root_run_id and is_tracing_enabled():
                        trace_url = await asyncio.to_thread(
                            get_trace_url, str(root_run_id)
                        )

                    final = {
                        "node": "complete",
                        "is_complete": True,
                        "scope_category": output.get("scope_category"),
                        "scope_message": output.get("scope_message"),
                        "outcome": output.get("outcome"),
                        "clarifying_question": output.get("clarifying_question"),
                        "reason": output.get("reason"),
                        "intent_class": output.get("intent_class"),
                        "extracted_entities": output.get("extracted_entities", []),
                        "tables_identified": output.get("tables_identified", []),
                        "generated_sql": output.get("generated_sql"),
                        "validation_result": output.get("validation_result"),
                        "execution_result": output.get("execution_result"),
                        "result_quality": output.get("result_quality"),
                        "chart_config": output.get("chart_config"),
                        "explanation": output.get("explanation"),
                        "confidence_score": output.get("confidence_score", 0.0),
                        # Categorical confidence (5.2): level + legible reasoning,
                        # exposed additively so the verdict is not a bare number.
                        "confidence": output.get("confidence"),
                        "confidence_reasons": output.get("confidence_reasons", []),
                        "confidence_signals": output.get("confidence_signals", {}),
                        "correction_attempts": output.get("correction_attempts", 0),
                        "correction_history": output.get("correction_history", []),
                        "served_from_cache": output.get("served_from_cache", False),
                        "cache_result": output.get("cache_result"),
                        "similar_examples": output.get("similar_examples", []),
                        "stream_updates": output.get("stream_updates", []),
                        "completed_nodes": output.get("completed_nodes", []),
                        "error": output.get("error"),
                        "trace_id": str(root_run_id) if root_run_id else None,
                        "trace_url": trace_url,
                        "session_id": session_id
                    }

                    log_query_complete(
                        logger,
                        session_id=session_id,
                        user_query=req.user_query,
                        intent_class=output.get("intent_class", "unknown") or "unknown",
                        cache_hit=output.get("served_from_cache", False),
                        corrections_used=output.get("correction_attempts", 0),
                        result_quality=(output.get("result_quality") or {}).get("status", "unknown"),
                        row_count=(output.get("execution_result") or {}).get("row_count", 0),
                        chart_type=(output.get("chart_config") or {}).get("chart_type"),
                        duration_ms=(time.monotonic() - stream_start) * 1000,
                        error=output.get("error"),
                    )
                    yield {"event": "complete", "data": json.dumps(final, default=str)}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e), "is_complete": True})}

    return EventSourceResponse(event_generator())


@router.post("/run-sql")
async def run_edited_sql(req: RunSQLRequest):
    """Skips generation — validates and executes user-provided SQL directly.

    Server-side guard: only SELECT (and WITH ... SELECT) statements are
    permitted. Any INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/COMMAND
    is rejected at the API boundary, regardless of caller (UI or curl).
    """
    session_id = await resolve_session_id(req.session_id)

    is_safe, reason = is_read_only_sql(req.sql)
    if not is_safe:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Only SELECT statements are permitted.",
                "detail": reason,
            },
        )

    from nixus.graph.nodes.check_result import check_result_node
    from nixus.graph.nodes.classify_chart import classify_chart_node
    from nixus.graph.nodes.execute_query import execute_query_node
    from nixus.graph.nodes.validate_syntax import validate_syntax_node

    mini_state = SQLAgentState(
        user_query="[user-edited SQL]", session_id=session_id,
        clarification_context=None, clarification_round=0,
        scope_category="IN_SCOPE", scope_message=None,
        outcome="ANSWERED", clarifying_question=None, reason=None,
        generated_sql=req.sql, correction_attempts=0,
        correction_history=[], completed_nodes=[], stream_updates=[],
        intent_class="READ", extracted_entities=[],
        cache_result=None, served_from_cache=False,
        relevant_schemas=[], schema_context="", tables_identified=[],
        similar_examples=[], fewshot_context="",
        validation_result=None, execution_result=None, result_quality=None,
        chart_config=None, explanation="", confidence_score=0.0,
        current_node="", is_complete=False,
        trace_id=None, trace_url=None, error=None
    )
    mini_state = await validate_syntax_node(mini_state)

    validation_result = mini_state.get("validation_result") or {}
    is_valid = validation_result.get("is_valid", False)
    # validate_syntax_node stores errors as a list under "errors"
    errors = validation_result.get("errors") or []
    error_message = errors[0] if errors else validation_result.get("error_message", "SQL validation failed.")

    if is_valid:
        mini_state = await execute_query_node(mini_state)
        mini_state = await check_result_node(mini_state)
        mini_state = await classify_chart_node(mini_state)
    else:
        if not mini_state.get("error"):
            mini_state["error"] = error_message

    return mini_state


# Module-level LLM connectivity cache — avoids burning tokens on every probe.
_llm_health_cache: dict = {"status": "unknown", "checked_at": 0.0}
LLM_HEALTH_CACHE_TTL = settings.llm_health_cache_ttl  # 5 minutes


async def _check_llm_connectivity() -> dict:
    """Check LLM API connectivity via token-free metadata calls.

    Results are cached for LLM_HEALTH_CACHE_TTL seconds so repeated
    health probes (e.g. Streamlit's 30-second polling) do not burn tokens.

    `anthropic.models.list()` and `openai.models.list()` are metadata-only
    endpoints — they do not invoke a model and have zero token cost.
    """
    now = time.monotonic()
    if now - _llm_health_cache["checked_at"] < LLM_HEALTH_CACHE_TTL:
        return _llm_health_cache.copy()

    anthropic_ok = False
    openai_ok = False

    # A placeholder/empty key is reported NOT connected WITHOUT any API call.
    # This is the exact false-confidence case the clean bring-up exposed:
    # health said connected while every call 401'd (7.2 amendment, defect C).
    if is_placeholder(settings.anthropic_api_key):
        logger.info("Anthropic key not configured (placeholder) — reporting not connected.")
    else:
        try:
            import anthropic as _anthropic
            _anthropic.Anthropic().models.list(limit=1)
            anthropic_ok = True
        except Exception as e:
            logger.warning(f"Anthropic health check failed: {type(e).__name__}: {e}")

    if is_placeholder(settings.openai_api_key):
        logger.info("OpenAI key not configured (placeholder) — reporting not connected.")
    else:
        try:
            from openai import AsyncOpenAI as _OAI
            await _OAI().models.list()
            openai_ok = True
        except Exception as e:
            logger.warning(f"OpenAI health check failed: {type(e).__name__}: {e}")

    _llm_health_cache.update({
        "anthropic_connected": anthropic_ok,
        "openai_connected": openai_ok,
        "status": "ok" if (anthropic_ok and openai_ok) else "degraded",
        "checked_at": now,
    })
    logger.info(
        f"LLM connectivity checked: anthropic={anthropic_ok} openai={openai_ok}"
    )
    return _llm_health_cache.copy()


@router.get("/health")
async def health():
    """Fast health check.

    DB connectivity: checked on every call (a single `SELECT 1`, ~1ms).
    LLM connectivity: checked at most once every LLM_HEALTH_CACHE_TTL seconds
    using token-free metadata API calls. This prevents Streamlit's 30-second
    polling from burning Anthropic/OpenAI quota.
    """
    db_ok = await check_db_connection()
    llm_health = await _check_llm_connectivity()

    overall = "ok" if (db_ok and llm_health["status"] == "ok") else "degraded"

    return {
        "status": overall,
        "db_connected": db_ok,
        "anthropic_connected": llm_health.get("anthropic_connected", False),
        "openai_connected": llm_health.get("openai_connected", False),
        "langsmith_tracing": is_tracing_enabled(),
        "llm_last_checked": llm_health.get("checked_at", 0),
        "nodes": [
            "scope_classifier", "scope_response", "parse_intent", "check_cache",
            "retrieve_schema", "retrieve_fewshot", "generate_sql", "validate_syntax",
            "verify_grounding", "execute_query", "check_result", "self_correct",
            "classify_chart", "explain_result"
        ],
        "version": "3.0.0"
    }


@router.get("/cache-stats")
async def cache_stats():
    return await get_cache_stats()


@router.post("/cache-evict")
async def cache_evict(
    max_age_days: int = 30,
    max_entries: int = 10_000,
):
    """Manually trigger cache eviction. Intended for an admin or a scheduled
    job. TTL drops rows not accessed in `max_age_days`; LRU then caps the
    table size at `max_entries`."""
    stats = await evict_stale_cache_entries(
        max_age_days=max_age_days,
        max_entries=max_entries,
    )
    return {"status": "ok", "evicted": stats}


@router.get("/fewshot-stats")
async def fewshot_stats():
    return await get_fewshot_stats()


# Mount every route defined above under /api/v1.
app.include_router(router)

# Unversioned health alias for infrastructure probes (load balancers, uptime
# checks) that expect a stable, version-independent path. Same handler as
# /api/v1/health; this is the only intentionally unversioned route.
app.add_api_route("/api/health", health, methods=["GET"])

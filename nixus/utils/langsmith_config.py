from dotenv import load_dotenv

load_dotenv()

import logging
import time

from langchain_core.runnables.config import RunnableConfig

from nixus.config import settings

TRACING_ENABLED = settings.tracing_enabled
LANGSMITH_PROJECT = settings.langchain_project

logger = logging.getLogger(__name__)

# Module-level singleton so we don't reconstruct the client on every call.
_langsmith_client = None


def get_langsmith_client():
    """Return a cached LangSmith Client instance, or None if not available."""
    global _langsmith_client
    if _langsmith_client is not None:
        return _langsmith_client
    try:
        from langsmith import Client
        _langsmith_client = Client()
        return _langsmith_client
    except Exception:
        return None


def get_run_config(
    session_id: str,
    user_query: str,
    run_name: str = "nixus-sql-query"
) -> RunnableConfig:
    """
    Returns a RunnableConfig that attaches LangSmith metadata to a graph
    invocation. When LANGCHAIN_TRACING_V2=true, every LLM call and graph node
    is recorded with run_name, searchable tags, and structured metadata.
    """
    if not TRACING_ENABLED:
        return RunnableConfig(
            run_name=run_name,
            tags=["nixus-sql"],
            metadata={
                "session_id": session_id,
                "user_query": user_query[:100],
                "project": LANGSMITH_PROJECT
            }
        )

    return RunnableConfig(
        run_name=run_name,
        tags=[
            "nixus-sql",
            f"session:{session_id[:8]}",
        ],
        metadata={
            "session_id": session_id,
            "user_query": user_query[:100],
            "project": LANGSMITH_PROJECT,
            "version": "3.0.0"
        }
    )


def get_trace_url(run_id: str) -> str | None:
    """
    Return a LangSmith trace URL for the given run_id using the public API.

    Uses `client.read_run()` (public method) to fetch the Run object, then
    `client.get_run_url()` (public method) to construct the URL. Both are
    part of the documented LangSmith SDK surface. No private methods are used.

    The run may not be fully uploaded yet (propagation delay of a few seconds),
    so we retry up to 3 times with 1-second sleeps before giving up.

    Returns None if tracing is disabled or the URL cannot be retrieved.
    """
    if not TRACING_ENABLED:
        return None

    client = get_langsmith_client()
    if client is None:
        return None

    for attempt in range(3):
        try:
            run = client.read_run(run_id)
            if run:
                return client.get_run_url(run=run)
        except Exception as exc:
            if attempt < 2:
                time.sleep(1)
            else:
                logger.debug(
                    f"Could not retrieve LangSmith trace URL for run {run_id}: {exc}"
                )

    return None


def is_tracing_enabled() -> bool:
    return TRACING_ENABLED

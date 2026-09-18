"""Guardrails manifest endpoint (Phase 2, Wave 2 D2.1).

A STATIC read of the enforced limits — zero database access, zero tokens, no
per-request computation. The route sits under the app-wide ``APIKeyMiddleware``
(fail-closed 401/503, inherited — no per-route auth code), so the manifest is
exactly as protected as every other /api/v1 route.

HONESTY CONTRACT: this surface reports caps, budgets, timeouts, and estimates
ONLY. NIXUS has no dollar/token spend ceiling — the "cost ceiling" is the
guardrail ensemble itself (row cap, statement timeout, correction budget,
clarification cap, SELECT-only gate, read-only role, fail-closed auth) — and
neither this payload nor any UI copy built on it may claim otherwise.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter

from nixus.config import settings

# Single source of truth: ROW_FETCH_LIMIT lives at the enforcement site
# (execute_query), the same convention export_service.py uses — importing the
# check_result.py copy would let the manifest's row cap silently drift from
# the enforced one.
from nixus.graph.nodes.execute_query import ROW_FETCH_LIMIT
from nixus.graph.scope import CLARIFICATION_ROUND_CAP

router = APIRouter(prefix="/guardrails", tags=["guardrails"])

# Model names in play, mirrored from their construction sites (static by design —
# the manifest reads no secrets and calls no provider):
#   sql_generation / self_correction → generate_sql.py:51, self_correct.py:46
#   intent / scope / explanation     → parse_intent.py:16, scope_classifier.py:43,
#                                      explain_result.py:100
#   embeddings                       → nixus/utils/embeddings.py:11
_MODELS = {
    "sql_generation": "claude-sonnet-4-5",
    "self_correction": "claude-sonnet-4-5",
    "intent_and_scope": "claude-haiku-4-5",
    "explanation": "claude-haiku-4-5",
    "embeddings": "text-embedding-3-small",
}


def parse_target_database(url: str | None) -> str | None:
    """The target's DATABASE NAME — the human-facing identity (W2 N1).

    The path segment of the connection URL, and nothing else: credentials and
    host never leave the settings module (the manifest names WHICH database the
    instance queries; it does not describe the network it sits on). None when
    unset or path-less — the UI omits the badge rather than guess.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        # A malformed URL must never break the static manifest.
        return None
    # A database name only counts when the URL actually points at a host —
    # urlparse is lax and will happily parse free text as a path.
    if not parsed.netloc:
        return None
    return parsed.path.lstrip("/").strip() or None


@router.get("")
async def guardrails_manifest() -> dict:
    return {
        "target_database": parse_target_database(settings.target_database_url),
        "row_cap": ROW_FETCH_LIMIT,
        "query_timeout_ms": settings.query_timeout_ms,
        "max_correction_attempts": settings.max_correction_attempts,
        "clarification_round_cap": CLARIFICATION_ROUND_CAP,
        "select_only": (
            "Only SELECT (and WITH … SELECT) statements are executed. "
            "INSERT/UPDATE/DELETE/DDL are rejected before execution."
        ),
        "read_only_role": (
            "The target database is reached through a read-only PostgreSQL role; "
            "the application holds no write privilege on user data."
        ),
        "models": _MODELS,
        "auth": (
            "Fail-closed API key (X-API-Key header). Requests without a valid key "
            "are rejected 401; with no key configured, every route answers 503."
        ),
        "note": (
            "These are caps, budgets, timeouts, and estimates only. NIXUS does not "
            "have a dollar or token spend ceiling."
        ),
    }

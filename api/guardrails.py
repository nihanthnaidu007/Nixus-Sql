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

from fastapi import APIRouter

from nixus.config import settings
from nixus.graph.nodes.check_result import ROW_FETCH_LIMIT
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


@router.get("")
async def guardrails_manifest() -> dict:
    return {
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

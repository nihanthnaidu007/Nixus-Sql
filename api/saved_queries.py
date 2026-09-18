"""Saved-query endpoints (Phase 2, Wave 1 D2) — CRUD + pipeline re-run.

Every route sits under the app-wide ``APIKeyMiddleware``, so a request without
a valid key never reaches a handler (fail-closed, inherited — no per-route
auth code).

THE POSTURE LINE, in one place: re-running a saved query feeds
``natural_language`` back through ``run_query`` — the full pipeline (scope →
schema retrieval → generation → grounding → safety checks → execution). The
stored ``generated_sql`` is reference data; it is NEVER executed directly.
Accepted re-runs (ANSWERED with a successful execution) feed the few-shot
corpus through the existing ``store_fewshot_example`` mechanism.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from api.sessions import resolve_session_id
from nixus.db.saved_query_store import (
    create_saved_query,
    delete_saved_query,
    get_saved_query,
    list_saved_queries,
    record_saved_query_run,
)
from nixus.services.query_service import run_query
from nixus.utils.sql_safety import is_read_only_sql

logger = logging.getLogger("nixus_sql.api.saved_queries")

router = APIRouter(prefix="/saved-queries", tags=["saved-queries"])

MAX_TAGS = 10
MAX_TAG_LENGTH = 50


class SavedQueryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    natural_language: str = Field(min_length=1, max_length=10_000)
    generated_sql: str = Field(min_length=1, max_length=100_000)
    description: str | None = Field(default=None, max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    parameters: dict | None = None


class SavedQueryRunRequest(BaseModel):
    # Absent/empty → the server issues a fresh session (same rule as /run).
    session_id: str = ""


def normalize_tags(tags: list[str]) -> list[str]:
    """Strip, drop empties, de-duplicate (order-preserving), cap the count."""
    seen: list[str] = []
    for raw in tags:
        tag = raw.strip()[:MAX_TAG_LENGTH]
        if tag and tag.lower() not in {t.lower() for t in seen}:
            seen.append(tag)
        if len(seen) >= MAX_TAGS:
            break
    return seen


@router.post("", status_code=201)
async def create_saved_query_endpoint(req: SavedQueryCreate):
    """Persist a named query. The stored SQL must itself be read-only — a
    saved query is reference data, and the guard is cheap defense in depth
    (re-runs go through the pipeline regardless, never this SQL)."""
    is_safe, reason = is_read_only_sql(req.generated_sql)
    if not is_safe:
        raise HTTPException(
            status_code=400,
            detail={"error": "Only SELECT statements can be saved.", "detail": reason},
        )
    try:
        saved = await create_saved_query(
            name=req.name.strip(),
            natural_language=req.natural_language.strip(),
            generated_sql=req.generated_sql.strip(),
            description=(req.description.strip() or None) if req.description else None,
            tags=normalize_tags(req.tags),
            parameters=req.parameters,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail={"error": f"A saved query named '{req.name.strip()}' already exists."},
        ) from None
    return saved


@router.get("")
async def list_saved_queries_endpoint(tag: str | None = None):
    return {"items": await list_saved_queries(tag=tag)}


@router.get("/{query_id}")
async def get_saved_query_endpoint(query_id: int):
    saved = await get_saved_query(query_id)
    if saved is None:
        raise HTTPException(status_code=404, detail={"error": "Saved query not found."})
    return saved


@router.delete("/{query_id}", status_code=204)
async def delete_saved_query_endpoint(query_id: int):
    if not await delete_saved_query(query_id):
        raise HTTPException(status_code=404, detail={"error": "Saved query not found."})


@router.post("/{query_id}/run")
async def run_saved_query_endpoint(query_id: int, req: SavedQueryRunRequest | None = None):
    """Re-run through the FULL pipeline — never direct SQL execution.

    The saved natural-language question is what the graph sees, so grounding,
    safety checks, and self-correction all apply exactly as for a typed query.
    An accepted run (ANSWERED + successful execution) is recorded as a few-shot
    candidate via the existing seeding mechanism; any failure there is logged
    and skipped — the corpus never blocks an answer.
    """
    saved = await get_saved_query(query_id)
    if saved is None:
        raise HTTPException(status_code=404, detail={"error": "Saved query not found."})

    session_id = await resolve_session_id((req or SavedQueryRunRequest()).session_id)
    final_state = await run_query(saved["natural_language"], session_id)

    await record_saved_query_run(query_id)

    outcome = final_state.get("outcome")
    execution = final_state.get("execution_result") or {}
    if outcome == "ANSWERED" and execution.get("success"):
        sql = final_state.get("generated_sql") or saved["generated_sql"]
        tables = [str(t) for t in (final_state.get("tables_identified") or [])]
        try:
            from nixus.db.fewshot_store import store_fewshot_example

            stored = await store_fewshot_example(
                natural_language=saved["natural_language"],
                sql_query=sql,
                tables_used=tables,
                auto_learned=True,
            )
            # store_fewshot_example now returns the new row's id (None when
            # duplicate-suppressed) — the response shape stays a bool.
            final_state["fewshot_candidate_recorded"] = bool(stored)
        except Exception:
            # Corpus bookkeeping is best-effort — the answer itself succeeded.
            logger.exception(
                "Few-shot linkage for saved query %d failed; answer returned anyway",
                query_id,
            )

    return final_state

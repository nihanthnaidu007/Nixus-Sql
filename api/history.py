"""Query-history endpoints (Phase 2 W1 D3; Phase 3 W1 D1 feedback).

Sits under the app-wide ``APIKeyMiddleware`` (fail-closed, inherited). Reads
the ``query_history`` table the pipeline writes on every executed query; an
unknown status value is a 400, not a silently empty page.

The feedback endpoint (D1) records an explicit human verdict on one history
row. A reject tombstones the few-shot example that run learned (via
``feedback_store.record_feedback``), and the retrieval gate in
``fewshot_store.search_fewshots`` filters tombstoned rows — rejected SQL is
never re-served as a few-shot example, enforced at the gate, not per-writer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from nixus.db.feedback_store import record_feedback
from nixus.db.query_history_store import KNOWN_STATUSES, list_query_history

router = APIRouter(prefix="/history", tags=["history"])


class FeedbackRequest(BaseModel):
    verdict: Literal["accept", "reject"]
    note: str | None = None


@router.post("/{history_id}/feedback")
async def record_history_feedback_endpoint(
    history_id: int,
    req: FeedbackRequest,
):
    """Record an explicit verdict on one history row.

    Reject demotes the few-shot example the run learned (tombstone — the
    retrieval gate stops serving it). Accept records the endorsement; a row
    with no linkage (refusals, errors, duplicate-suppressed runs) records the
    verdict with nothing to demote.
    """
    recorded = await record_feedback(history_id, req.verdict, req.note)
    if recorded is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "History row not found."},
        )
    return recorded


@router.get("")
async def list_history_endpoint(
    session_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    since: Annotated[
        datetime | None, Query(description="Only rows created at/after this instant")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Only rows created at/before this instant")
    ] = None,
):
    if status is not None and status not in KNOWN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"error": f"Unknown status '{status}'. Valid: {', '.join(KNOWN_STATUSES)}"},
        )
    return await list_query_history(
        session_id=session_id,
        status=status,
        limit=limit,
        offset=offset,
        since=since,
        until=until,
    )

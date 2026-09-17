"""Query-history endpoint (Phase 2, Wave 1 D3) — paginated per-session history.

Sits under the app-wide ``APIKeyMiddleware`` (fail-closed, inherited). Reads
the ``query_history`` table the pipeline writes on every executed query; an
unknown status value is a 400, not a silently empty page.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from nixus.db.query_history_store import KNOWN_STATUSES, list_query_history

router = APIRouter(prefix="/history", tags=["history"])


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

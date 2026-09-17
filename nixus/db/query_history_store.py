"""Per-session query-history store in the STATE database.

The table is created by migration ``0003_saved_queries_query_history.sql``.
Same engine access pattern as session_store / fewshot_store: the async state
engine, raw SQL, no ORM.

One row per executed query (written by ``nixus.services.record_history`` on
both the /run and /stream paths), read back paginated with optional session
and status filters.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from nixus.db.connection import state_engine

_ROW_SQL = """
    SELECT id, session_id, question, generated_sql, status,
           duration_ms, row_count, created_at
    FROM query_history
"""

# Filter values are compared against this allowlist; anything else is dropped
# rather than answered as an empty page (a typo should be visible, not silent).
KNOWN_STATUSES = (
    "ANSWERED",
    "NEEDS_CLARIFICATION",
    "REFUSED_OUT_OF_SCOPE",
    "REFUSED_WRITE",
    "REFUSED_AMBIGUOUS",
    "ERROR",
)


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "session_id": row[1],
        "question": row[2],
        "generated_sql": row[3],
        "status": row[4],
        "duration_ms": float(row[5]) if row[5] is not None else 0.0,
        "row_count": int(row[6]) if row[6] is not None else 0,
        "created_at": row[7].isoformat() if isinstance(row[7], datetime) else row[7],
    }


async def record_query_history(
    session_id: str,
    question: str,
    generated_sql: str,
    status: str,
    duration_ms: float,
    row_count: int,
) -> int:
    """Insert one history row; returns its id. Callers wrap this in the
    repo's resilience pattern so a history failure can never break a query."""
    async with state_engine.begin() as conn:
        result = await conn.execute(text("""
            INSERT INTO query_history
                (session_id, question, generated_sql, status, duration_ms, row_count)
            VALUES
                (:sid, :question, :sql, :status, :duration, :rows)
            RETURNING id
        """), {
            "sid": session_id,
            "question": question,
            "sql": generated_sql,
            "status": status,
            "duration": float(duration_ms),
            "rows": int(row_count),
        })
        return int(result.scalar_one())


async def list_query_history(
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """Paginated history, newest first, with the total for paging controls.

    An unknown ``status`` value returns an empty page with ``total: 0`` — the
    API layer validates against ``KNOWN_STATUSES`` first and answers 400, so
    this branch only guards direct store callers.
    """
    clauses: list = []
    params: dict = {"limit": limit, "offset": offset}
    if session_id:
        clauses.append("session_id = :sid")
        params["sid"] = session_id
    if status:
        if status not in KNOWN_STATUSES:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        clauses.append("status = :status")
        params["status"] = status
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("created_at <= :until")
        params["until"] = until
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    async with state_engine.connect() as conn:
        total = (
            await conn.execute(
                text(f"SELECT COUNT(*) FROM query_history{where}"), params,
            )
        ).scalar_one()
        rows = (
            await conn.execute(
                text(_ROW_SQL + where + " ORDER BY created_at DESC, id DESC"
                     " LIMIT :limit OFFSET :offset"),
                params,
            )
        ).fetchall()

    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }

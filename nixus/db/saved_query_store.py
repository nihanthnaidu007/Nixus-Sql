"""Saved-query store in the STATE database (read-write application bookkeeping).

The table is created by migration ``0003_saved_queries_query_history.sql``.
Same engine access pattern as session_store / fewshot_store: the async state
engine, raw SQL, no ORM.

A saved query is a named, tagged (natural language, SQL) pair. Re-runs NEVER
execute ``generated_sql`` directly — the API layer feeds ``natural_language``
back through the full pipeline; this store only records that a run happened.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text

# saved_queries is NIXUS-owned bookkeeping (read + write) → STATE database.
from nixus.db.connection import state_engine

_ROW_SQL = """
    SELECT id, name, description, tags, natural_language, generated_sql,
           parameters_json, created_at, updated_at, last_run_at
    FROM saved_queries
"""


def _row_to_dict(row) -> dict:
    """One asyncpg/SQLAlchemy row → the API-facing dict (JSON decoded)."""
    params = row[6]
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "tags": list(row[3]) if row[3] else [],
        "natural_language": row[4],
        "generated_sql": row[5],
        "parameters": json.loads(params) if params else None,
        "created_at": row[7].isoformat() if isinstance(row[7], datetime) else row[7],
        "updated_at": row[8].isoformat() if isinstance(row[8], datetime) else row[8],
        "last_run_at": row[9].isoformat() if isinstance(row[9], datetime) else None,
    }


async def create_saved_query(
    name: str,
    natural_language: str,
    generated_sql: str,
    description: str | None = None,
    tags: list | None = None,
    parameters: dict | None = None,
) -> dict:
    """Insert a saved query and return the created row.

    A duplicate name violates the table's UNIQUE constraint and surfaces as
    ``sqlalchemy.exc.IntegrityError`` — the API layer maps it to 409.
    """
    params_json = json.dumps(parameters) if parameters else None
    async with state_engine.begin() as conn:
        result = await conn.execute(text("""
            INSERT INTO saved_queries
                (name, description, tags, natural_language, generated_sql, parameters_json)
            VALUES
                (:name, :description, CAST(:tags AS TEXT[]), :nl, :sql, :params)
            RETURNING id
        """), {
            "name": name,
            "description": description,
            "tags": tags or [],
            "nl": natural_language,
            "sql": generated_sql,
            "params": params_json,
        })
        new_id = result.scalar_one()
    saved = await get_saved_query(new_id)
    if saved is None:  # pragma: no cover — RETURNING guarantees the row exists
        raise RuntimeError("saved query vanished immediately after insert")
    return saved


async def list_saved_queries(tag: str | None = None) -> list:
    """All saved queries, newest first; optionally filtered by tag membership."""
    sql = _ROW_SQL
    params: dict = {}
    if tag:
        sql += " WHERE :tag = ANY(tags)"
        params["tag"] = tag
    sql += " ORDER BY updated_at DESC"
    async with state_engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_saved_query(saved_query_id: int) -> dict | None:
    async with state_engine.connect() as conn:
        row = (
            await conn.execute(text(_ROW_SQL + " WHERE id = :id"), {"id": saved_query_id})
        ).fetchone()
    return _row_to_dict(row) if row else None


async def delete_saved_query(saved_query_id: int) -> bool:
    """Delete by id; True iff a row was removed."""
    async with state_engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM saved_queries WHERE id = :id"), {"id": saved_query_id},
        )
        return result.rowcount > 0


async def record_saved_query_run(saved_query_id: int) -> None:
    """Stamp ``last_run_at`` after a pipeline re-run completes."""
    async with state_engine.begin() as conn:
        await conn.execute(
            text("UPDATE saved_queries SET last_run_at = now() WHERE id = :id"),
            {"id": saved_query_id},
        )

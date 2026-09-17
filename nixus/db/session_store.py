"""Issued-session registry in the STATE database (read-write bookkeeping).

The table is created by migration ``0002_api_sessions.sql``. Only the server
ever INSERTs here — client input can at most be checked against this table —
so "present in api_sessions" is exactly "issued by this server", which is what
makes the checkpoint-thread allowlist unforgeable.

Same engine access pattern as query_cache / fewshot_store: the async state
engine, raw SQL, no ORM.
"""
from sqlalchemy import text

from nixus.db.connection import state_engine


async def register_session(session_id: str) -> None:
    """Record a server-issued session id (idempotent)."""
    async with state_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO api_sessions (session_id) VALUES (:sid) "
                "ON CONFLICT (session_id) DO NOTHING"
            ),
            {"sid": session_id},
        )


async def session_exists(session_id: str) -> bool:
    """True iff the id was issued by this server (present in api_sessions)."""
    async with state_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT 1 FROM api_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )
        return row.fetchone() is not None

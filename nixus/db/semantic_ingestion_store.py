"""Semantic-source ingestion bookkeeping in the STATE database.

The table is created by migration ``0004_semantic_ingestions.sql``. Same
engine access pattern as the other stores: the async state engine, raw SQL,
no ORM.

One row per semantic source (currently: a dbt manifest path, keyed by
normalized path). ``record_ingestion`` is the idempotency gate for the W3
manifest connector: re-ingesting an UNCHANGED source (same content hash) is a
reported no-op; a changed hash upserts and reports what changed. All functions
return plain data so embed-time callers can log outcomes without new failure
modes (fail-soft doctrine — DB problems here degrade to "recorded=False" logs,
never a broken embed).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from nixus.db.connection import state_engine


@dataclass
class IngestionRecord:
    source: str
    source_kind: str
    content_hash: str
    stats_json: str | None
    ingested_at: datetime | None


def _row_to_record(row) -> IngestionRecord:
    return IngestionRecord(
        source=row[0],
        source_kind=row[1],
        content_hash=row[2],
        stats_json=row[3],
        ingested_at=row[4],
    )


async def get_last_ingestion(source: str) -> IngestionRecord | None:
    async with state_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT source, source_kind, content_hash, stats_json, ingested_at "
                    "FROM semantic_ingestions WHERE source = :source"
                ),
                {"source": source},
            )
        ).fetchone()
    return _row_to_record(row) if row else None


async def record_ingestion(
    source: str, content_hash: str, source_kind: str = "dbt_manifest", stats: dict | None = None
) -> bool:
    """Upsert one ingestion record. Returns True when the source was NEW or
    its hash CHANGED; False when the same hash was already recorded (the
    caller's re-ingest is a logged no-op)."""
    async with state_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT content_hash FROM semantic_ingestions WHERE source = :source"),
                {"source": source},
            )
        ).fetchone()
        if row and row[0] == content_hash:
            return False
        await conn.execute(
            text(
                "INSERT INTO semantic_ingestions (source, source_kind, content_hash, stats_json, ingested_at) "
                "VALUES (:source, :kind, :hash, :stats, now()) "
                "ON CONFLICT (source) DO UPDATE SET "
                "source_kind = EXCLUDED.source_kind, "
                "content_hash = EXCLUDED.content_hash, "
                "stats_json = EXCLUDED.stats_json, "
                "ingested_at = now()"
            ),
            {"source": source, "kind": source_kind, "hash": content_hash, "stats": json.dumps(stats) if stats else None},
        )
    return True

"""Dimension-aware pgvector store maintenance (provider resilience).

The three pgvector stores (0001_initial_schema.sql) declare their vector
columns as ``vector(1536)`` — OpenAI's text-embedding-3-small width. pgvector
locks a column to the width it was created at: after switching
``EMBEDDINGS_PROVIDER`` (e.g. to Ollama's nomic-embed-text at 768), every
insert fails with a raw ``expected 1536 dimensions, not 768`` error.

Two hooks keep the stores aligned with the ACTIVE provider:

* :func:`ensure_store_dims` — runs at API startup. Empty stores are resized to
  the active width (dimension-aware creation for fresh installs); non-empty
  mismatches are reported to the caller, never auto-truncated (data loss is a
  decision, not a side effect).
* :func:`rebuild_vector_stores` — the destructive half of
  ``nixus reembed-stores``: empty + resize + re-index in one transaction.

Column widths are read from ``pg_attribute.atttypmod`` (the same mechanism the
AXIOM re-embed precedent uses): ``vector(n)`` reports n, untyped ``vector``
reports -1.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from nixus.db.connection import state_engine

# (table, vector column, HNSW index) — exactly the three stores the baseline
# migration creates. Names are module constants, never user input. The index
# names are kept for reference (Postgres rebuilds them across the empty-table
# ALTER in rebuild_vector_stores).
VECTOR_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("schema_embeddings", "embedding", "schema_emb_idx"),
    ("fewshot_examples", "embedding", "fewshot_emb_idx"),
    ("query_cache", "query_embedding", "cache_emb_idx"),
)


async def get_vector_column_dims() -> dict[str, int | None]:
    """Current locked width of each vector column, keyed ``table.column``.

    None = column exists but is untyped ``vector`` (no width declared).
    """
    async with state_engine.connect() as conn:
        result: dict[str, int | None] = {}
        for table, column, _index in VECTOR_COLUMNS:
            width = await _column_dim(conn, table, column)
            result[f"{table}.{column}"] = width
    return result


async def ensure_store_dims(dim: int) -> tuple[list[str], list[str]]:
    """Align every vector column to ``dim`` when the store is EMPTY.

    Returns ``(resized, blocked)``: ``resized`` lists ``table.column`` entries
    altered to the active width (the fresh-install path — the tables were just
    created by the migrations and hold no rows); ``blocked`` lists entries whose
    store still holds rows at a DIFFERENT width. Blocked stores are never
    touched here: re-embedding them is ``nixus reembed-stores``' explicit,
    announced job.
    """
    resized: list[str] = []
    blocked: list[str] = []
    async with state_engine.begin() as conn:
        for table, column, _index in VECTOR_COLUMNS:
            current = await _column_dim(conn, table, column)
            if current is None or current == dim:
                continue
            count = (
                await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar() or 0
            if count == 0:
                await conn.execute(
                    text(
                        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector({dim})"
                    )
                )
                resized.append(f"{table}.{column}")
            else:
                blocked.append(f"{table}.{column}")
    return resized, blocked


async def rebuild_vector_stores(
    conn: AsyncConnection, dim: int
) -> list[tuple[str, str, str, str]]:
    """Destructive: empty the three stores and resize columns to ``dim``.

    Runs on the CALLER's connection so the whole provider switch — truncate,
    resize, few-shot re-insertion, constraint restoration — is one transaction
    (either everything or nothing). Must only run on EMPTY tables (the callers
    re-insert or truncate first); the empty-table ALTER lets Postgres rebuild
    the HNSW indexes itself, so no manual index juggling.

    FK safety: ``query_history.fewshot_example_id`` references
    ``fewshot_examples(id)``, which makes even an empty-table TRUNCATE fail.
    Referencing foreign keys are dropped first and RETURNED so the caller can
    restore them (``readd_referencing_constraints``) AFTER re-inserting the
    rows those references point at — history never dangles at commit.

    Returns the dropped constraints as ``(table, name, column, referenced_table)``
    tuples for :func:`readd_referencing_constraints`.
    """
    dropped = await _drop_referencing_constraints(conn)
    for table, _column, _index in VECTOR_COLUMNS:
        await conn.execute(text(f"TRUNCATE TABLE {table}"))
    for table, column, _index in VECTOR_COLUMNS:
        await conn.execute(
            text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector({dim})")
        )
    return dropped


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


async def _drop_referencing_constraints(
    conn: AsyncConnection,
) -> list[tuple[str, str, str, str]]:
    """Drop and record every FK that references one of the vector stores.

    Identifiers come from the system catalog and are regex-validated before
    any interpolation — a catalog value that looks wrong is an error, never
    SQL input.
    """
    tables = [table for table, _c, _i in VECTOR_COLUMNS]
    rows = (
        await conn.execute(
            text(
                """
            SELECT con.conname, con.conrelid::regclass::text AS on_table,
                   att.attname AS on_column, con.confrelid::regclass::text AS ref_table
            FROM pg_constraint con
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
              ON k.ord = 1
            JOIN pg_attribute att
              ON att.attrelid = con.conrelid AND att.attnum = k.attnum
            WHERE con.contype = 'f'
              AND con.confrelid::regclass::text = ANY(:tables)
            """
            ),
            {"tables": tables},
        )
    ).fetchall()
    dropped: list[tuple[str, str, str, str]] = []
    for name, on_table, on_column, ref_table in rows:
        for ident in (name, on_table, on_column, ref_table):
            if not _IDENT.match(ident):
                raise ValueError(f"unexpected catalog identifier: {ident!r}")
        await conn.execute(text(f'ALTER TABLE "{on_table}" DROP CONSTRAINT "{name}"'))
        dropped.append((on_table, name, on_column, ref_table))
    return dropped


async def readd_referencing_constraints(
    conn: AsyncConnection,
    dropped: list[tuple[str, str, str, str]],
) -> None:
    """Restore FKs dropped by :func:`rebuild_vector_stores` (same transaction)."""
    for on_table, name, on_column, ref_table in dropped:
        await conn.execute(
            text(
                f'ALTER TABLE "{on_table}" ADD CONSTRAINT "{name}" '
                f'FOREIGN KEY ("{on_column}") REFERENCES "{ref_table}" (id)'
            )
        )


async def _column_dim(conn, table: str, column: str) -> int | None:
    """Locked width of one vector column (None = untyped or missing table)."""
    row = (
        await conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = (:table)::regclass AND attname = :column "
                "AND attisdropped = false"
            ),
            {"table": table, "column": column},
        )
    ).fetchone()
    if row is None or row[0] is None or row[0] < 0:
        return None
    return int(row[0])

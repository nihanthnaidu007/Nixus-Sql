"""``nixus reembed-stores`` — rebuild ALL pgvector stores under the active provider.

    python -m nixus.db.reembed_stores            # plan mode — prints, changes nothing, exit 2
    python -m nixus.db.reembed_stores --yes      # destructive rebuild, exit 0 on success

Why it exists: pgvector locks columns to the width they were created at, so
switching ``EMBEDDINGS_PROVIDER`` (or a model width change — OpenAI's
text-embedding-3-small is 1536, Ollama's nomic-embed-text is 768) makes every
stored vector incomparable with newly embedded ones and hard-fails inserts.
The provider-switch contract is a FULL re-embed. Unlike AXIOM's
``python -m axiom.reembed --yes`` (whose document stores had to ask the user to
re-upload), every Nixus store retains its text, so this command recomputes
vectors from stored source:

* ``schema_embeddings`` — re-introspected + re-embedded from the live target
  via the existing ``embed_target_schema`` pipeline.
* ``fewshot_examples`` — rows re-embedded from their stored ``natural_language``
  with ids and disabled (tombstone) flags preserved; human-curated exemplars
  are never dropped.
* ``query_cache`` — TRUNCATED, not re-embedded: entries are reproducible
  computations (TTL/LRU-evictable by design), and re-embedding the cache would
  burn the embed budget to recreate data that refills itself on first use.

Checkpointer/history tables hold no vectors and are untouched.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from nixus.config import settings
from nixus.db.connection import get_state_engine, get_target_engine, state_engine
from nixus.db.store_dims import (
    VECTOR_COLUMNS,
    readd_referencing_constraints,
    rebuild_vector_stores,
)
from nixus.utils.embeddings import embed_texts


async def _store_counts() -> dict[str, int]:
    async with state_engine.connect() as conn:
        counts: dict[str, int] = {}
        for table, _column, _index in VECTOR_COLUMNS:
            counts[table] = int(
                (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()
                or 0
            )
    return counts


def _print_plan(dim: int) -> None:
    provider = settings.embeddings_provider
    model = (
        settings.ollama_embedding_model
        if provider == "ollama"
        else "text-embedding-3-small"
    )
    print(f"Active provider : {provider} ({model}, {dim}-dim)")
    print("Will rebuild    : schema_embeddings  (re-introspect + re-embed from target)")
    print(
        "                  fewshot_examples   (re-embed from stored natural_language; ids/disabled preserved)"
    )
    print(
        "                  query_cache        (truncated — reproducible, refills on use)"
    )
    print(
        "Untouched       : history, saved queries, feedback, checkpointer, target data"
    )
    print("Re-run with --yes to apply.")


async def _run(assume_yes: bool) -> int:
    dim = settings.embedding_dim  # validates the provider first
    if not assume_yes:
        _print_plan(dim)
        return 2

    before = await _store_counts()
    print(
        f"Rebuilding vector stores at {dim} dims "
        f"(fewshot={before['fewshot_examples']} rows, cache={before['query_cache']} rows)..."
    )

    # Snapshot fewshot rows BEFORE the destructive rebuild (id + disabled preserved).
    async with state_engine.connect() as conn:
        fewshot_rows = (
            (await conn.execute(text("SELECT * FROM fewshot_examples")))
            .mappings()
            .all()
        )

    # Re-embed BEFORE opening the write transaction: the embed call is network
    # I/O and must never hold the schema locks (ALTER) open while it runs.
    fewshot_vectors = (
        await embed_texts([row["natural_language"] for row in fewshot_rows])
        if fewshot_rows
        else []
    )

    # One transaction: truncate + resize + few-shot re-insert + constraint
    # restore commit atomically — query_history's FK never dangles at commit.
    async with state_engine.begin() as conn:
        dropped_fks = await rebuild_vector_stores(conn, dim)

        for row, vector in zip(fewshot_rows, fewshot_vectors):
            columns = list(row.keys())
            params = dict(row)
            params["embedding"] = str(vector)
            names = ", ".join(columns)
            binds = ", ".join(f":{c}" for c in columns)
            await conn.execute(
                text(f"INSERT INTO fewshot_examples ({names}) VALUES ({binds})"),
                params,
            )
        if fewshot_rows:
            await conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('fewshot_examples', 'id'), "
                    "(SELECT MAX(id) FROM fewshot_examples))"
                )
            )
            print(
                f"  fewshot_examples : re-embedded {len(fewshot_rows)} rows (ids preserved)"
            )

        await readd_referencing_constraints(conn, dropped_fks)

    # Schema: the existing introspection pipeline re-embeds from the live target.
    from nixus.schema.embed import embed_target_schema

    embedded = await embed_target_schema(get_target_engine(), get_state_engine())
    print(
        f"  schema_embeddings: re-embedded {embedded} tables from target introspection"
    )

    # 3. Cache: truncated by the rebuild; refills on first use.
    print("  query_cache      : truncated (refills automatically)")

    print("Done. All stores now use the active provider's vector width.")
    return 0


def main(assume_yes: bool = False) -> int:
    """Entry point for the CLI and ``python -m nixus.db.reembed_stores``."""
    if not assume_yes and "--yes" in sys.argv[1:]:
        assume_yes = True  # direct module invocation
    try:
        return asyncio.run(_run(assume_yes))
    except Exception as e:  # surfaced to the operator, never a silent failure
        print(f"reembed-stores failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

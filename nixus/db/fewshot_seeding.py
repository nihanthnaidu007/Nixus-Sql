"""Cold-start few-shot seeding from the committed benchmark corpus.

A fresh deployment starts with an empty ``fewshot_examples`` table, so early
queries retrieve zero exemplars — the generated SQL quality dip every new
install otherwise pays for until enough queries have been auto-learned. This
module closes that gap deterministically: the benchmark's gold question↔SQL
pairs ARE the exemplars, so we seed them.

Design rules:
  * ANSWERABLE gold pairs only — scope/refusal cases never become exemplars
    (teaching the model to answer is the point; teaching it to answer
    out-of-scope questions is the opposite).
  * Idempotent by identity, not by embedding: questions already present in the
    store (by exact natural-language match) are skipped with a single SQL read
    — no embedding API calls, so a warm start costs nothing and stays working
    even when the embedding provider is unreachable.
  * Never raises into startup: the API lifespan calls this inside its own
    try/except, and per-item failures are counted, not propagated — a seeding
    hiccup must not take the API down. The returned SeedStats reports what
    happened.
  * The default source is the SaaS corpus (``eval/saas_gold.py``) because the
    compose quickstart deploys ``nixus_saas_demo`` as the target; the archived
    Chinook corpus is available with ``source="chinook"``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text

# fewshot_examples is NIXUS-owned bookkeeping (read + LEARN) → STATE database.
from nixus.db.connection import state_engine
from nixus.db.fewshot_store import store_fewshot_example

logger = logging.getLogger("nixus_sql.api")

# Default corpus follows the compose quickstart target (nixus_saas_demo).
_DEFAULT_SOURCE = "saas"


@dataclass
class SeedStats:
    """Outcome of one seeding pass; logged at startup."""

    source: str
    corpus_size: int
    stored: int
    skipped_existing: int
    failed: int


def _corpus_items(source: str) -> list[dict]:
    """Load answerable (question, gold_sql) pairs from a benchmark corpus.

    Import is local so a missing/renamed corpus module fails per call, not at
    import time of every consumer of this package.
    """
    if source == "saas":
        from eval.saas_gold import ANSWERABLE

        return [
            {"question": q["question"], "sql": q["gold_sql"]}
            for q in ANSWERABLE
            if q.get("question") and q.get("gold_sql")
        ]
    if source == "chinook":
        from eval.archive_chinook.gold_queries import GOLD_QUERIES

        return [
            {"question": q["question"], "sql": q["gold_sql"]}
            for q in GOLD_QUERIES
            if q.get("question") and q.get("gold_sql")
        ]
    raise ValueError(f"unknown few-shot seed source: {source!r}")


def tables_in_sql(sql: str) -> list[str]:
    """Extract distinct table names from SQL via sqlglot (parse-failure safe)."""
    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return []
    return sorted({t.name for t in parsed.find_all(exp.Table) if t.name})


async def _existing_questions() -> set:
    async with state_engine.connect() as conn:
        rows = await conn.execute(text("SELECT natural_language FROM fewshot_examples"))
        return {r[0] for r in rows.fetchall()}


async def seed_fewshots_from_corpus(source: str = _DEFAULT_SOURCE) -> SeedStats:
    """Seed ``fewshot_examples`` from a benchmark corpus (idempotent).

    Skips questions already stored (a cold start pays the embedding cost once);
    near-duplicates inside the corpus are caught by the store's own similarity
    check and counted as skipped. Per-item failures (e.g. the embedding
    provider being down on a true cold start) are counted and logged, never
    raised.
    """
    items = _corpus_items(source)
    try:
        existing = await _existing_questions()
    except Exception:
        # Store read failed → attempt every item; per-item reporting below.
        existing = set()

    stored = skipped = failed = 0
    for item in items:
        if item["question"] in existing:
            skipped += 1
            continue
        try:
            if await store_fewshot_example(
                natural_language=item["question"],
                sql_query=item["sql"],
                tables_used=tables_in_sql(item["sql"]),
                auto_learned=False,  # seeded, not auto-learned (get_fewshot_stats splits on this)
            ):
                stored += 1
            else:
                skipped += 1  # near-duplicate of an already-stored exemplar
        except Exception:
            # Never swallow silently, but never abort the pass either.
            failed += 1
            logger.warning(
                "Few-shot seeding: failed to store one %s exemplar (%d failed so far)",
                source, failed,
            )

    stats = SeedStats(
        source=source, corpus_size=len(items), stored=stored,
        skipped_existing=skipped, failed=failed,
    )
    if failed:
        logger.warning(
            "Few-shot seeding incomplete: %d/%d stored, %d skipped, %d failed "
            "(embedding provider unreachable on a cold start?)",
            stored, len(items), skipped, failed,
        )
    return stats

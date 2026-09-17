"""Seed curated metric questions into fewshot_examples (idempotent, fail-soft).

A metric is (NL question phrasings, verified SQL, referenced tables). Seeding
its question↔SQL pairs into the EXISTING ``fewshot_examples`` store means
metric questions flow through the regular few-shot retrieval with zero graph
changes (the W3 locked decision): the curated pair is retrieved by embedding
similarity exactly like the benchmark pairs ``fewshot_seeding.py`` seeds.

Idempotency: exact natural-language match against the store is checked first
(one SQL read — no embedding API calls on a warm start); the store's own 0.98
near-duplicate check catches paraphrases. Per-item failures are counted and
logged, never raised — seeding must not take startup down.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text

from nixus.config import settings

# fewshot_examples is NIXUS-owned bookkeeping (read + LEARN) → STATE database.
from nixus.db.connection import state_engine
from nixus.db.fewshot_seeding import tables_in_sql
from nixus.db.fewshot_store import store_fewshot_example
from nixus.semantic.registry import MetricsFile, load_metrics_file

logger = logging.getLogger("nixus_sql.semantic")


@dataclass
class MetricSeedStats:
    """Outcome of one metric-seeding pass; logged at startup."""

    path: str
    metrics_loaded: int
    stored: int
    skipped_existing: int
    failed: int


async def _existing_questions() -> set[str]:
    async with state_engine.connect() as conn:
        rows = await conn.execute(text("SELECT natural_language FROM fewshot_examples"))
        return {r[0] for r in rows.fetchall()}


async def seed_metrics_from_yaml(path: str | None = None) -> MetricSeedStats:
    """Seed every validated metric question into ``fewshot_examples``.

    Fail-soft end to end: a missing/invalid YAML degrades to a zero-work pass
    (metrics_loaded=0 with the reason logged by the registry); per-item store
    failures are counted, never raised.
    """
    metrics_path = path or settings.semantic_metrics_path
    loaded: MetricsFile = load_metrics_file(metrics_path)

    items = [
        {"question": q, "sql": m.sql}
        for m in loaded.metrics
        for q in m.questions
    ]

    stats = MetricSeedStats(
        path=metrics_path, metrics_loaded=len(loaded.metrics),
        stored=0, skipped_existing=0, failed=0,
    )
    if not items:
        return stats

    try:
        existing = await _existing_questions()
    except Exception:
        # Store read failed → attempt every item; per-item reporting below.
        existing = set()

    for item in items:
        if item["question"] in existing:
            stats.skipped_existing += 1
            continue
        try:
            if await store_fewshot_example(
                natural_language=item["question"],
                sql_query=item["sql"],
                tables_used=tables_in_sql(item["sql"]),
                auto_learned=False,  # curated seed, not auto-learned
            ):
                stats.stored += 1
            else:
                stats.skipped_existing += 1  # near-duplicate of a stored exemplar
        except Exception:
            # Never swallow silently, but never abort the pass either.
            stats.failed += 1
            logger.warning(
                "Metric seeding: failed to store one exemplar for %s (%d failed so far)",
                metrics_path, stats.failed,
            )

    if stats.failed:
        logger.warning(
            "Metric seeding incomplete from %s: %d/%d stored, %d skipped, %d failed",
            metrics_path, stats.stored, len(items), stats.skipped_existing, stats.failed,
        )
    return stats

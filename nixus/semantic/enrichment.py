"""Metric vocabulary → schema-embedding enrichment (pure helpers).

At embed time each metric's name + description is merged into the rendered
description of every table it references — and ONLY those tables — so
``retrieve_schema`` surfaces the curated vocabulary through the existing
retrieval path with zero graph changes (the W3 locked decision). The metric
SQL itself is NOT embedded here: it reaches the generator via the few-shot
corpus (nixus/semantic/seeding.py).
"""
from __future__ import annotations

from nixus.semantic.registry import Metric

# Section header used in the enriched description text. Kept literal and
# searchable: this text flows into schema_embeddings.description and from
# there verbatim into the generator prompt (schema_context).
METRIC_SECTION_HEADER = "Curated metrics for this table:"

# Long curated descriptions are capped at embed time too — the enrichment is
# prompt-surface text (R3 posture), and the registry cap (if any) is a policy
# decision that should not silently change embedding sizes.
MAX_METRIC_DESC_CHARS = 300


def _shorten(text: str, limit: int = MAX_METRIC_DESC_CHARS) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def metric_vocabulary_by_table(metrics: list[Metric]) -> dict[str, str]:
    """Group metric vocabulary by referenced table → {table: rendered block}.

    Keys are the metric's declared table names normalized the same way the
    registry normalizes them (lowercased, whitespace-collapsed); callers match
    against introspected qualified names with the same normalization.
    """
    by_table: dict[str, list[str]] = {}
    for m in metrics:
        for table in m.tables:
            by_table.setdefault(table, []).append(f"- {m.name}: {_shorten(m.description)}")
    return {
        table: METRIC_SECTION_HEADER + "\n" + "\n".join(entries)
        for table, entries in by_table.items()
    }


def append_metric_vocabulary(description: str, vocabulary: str | None) -> str:
    """Append the metric-vocabulary block to a rendered table description."""
    if not vocabulary:
        return description
    return f"{description}\n{vocabulary}"

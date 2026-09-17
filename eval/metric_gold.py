"""Metric-question eval slice (W3) — the semantic layer's answer to the gold set.

SHAPE: mirrors eval/saas_gold.py — flat case dicts (id / tier / question /
gold_sql / ordered) consumable by the same result-equivalence harness — but the
corpus is DERIVED, not hand-listed: every metric in semantic/metrics.yaml
contributes its curated questions and its VERIFIED SQL. One source of truth: add
a metric to the YAML and it is evaluated here automatically.

Scoring doctrine is the benchmark of record's (eval/run_saas_benchmark.py):
each case passes iff the API's answer is result-equivalent to the metric's gold
SQL against the deterministic nixus_saas seed. A metric question failing while
its SQL passes means the semantic layer failed to surface the metric — exactly
what this slice exists to catch.
"""
from __future__ import annotations

from eval.result_equivalence import (
    results_equivalent,  # noqa: F401  (re-exported for the pytest view)
)
from nixus.config import settings
from nixus.semantic.registry import load_metrics_file


def build_metric_cases() -> list[dict]:
    """Every metric exemplar question → one eval case.

    Loaded through the production registry (strict validation, fail-soft) so
    the eval measures the same file the API seeds from. An invalid metrics file
    yields an empty corpus — the API must log-and-skip, and so must this.
    """
    loaded = load_metrics_file(settings.semantic_metrics_path)
    cases: list[dict] = []
    for metric in loaded.metrics:
        for i, question in enumerate(metric.questions):
            cases.append({
                "id": f"W3-{metric.name}#{i + 1}",
                "tier": "metric",
                "metric": metric.name,
                "ordered": False,  # metric SQL is aggregate-first; multiset compare
                "question": question,
                "gold_sql": metric.sql,
            })
    return cases


METRIC_CASES: list[dict] = build_metric_cases()

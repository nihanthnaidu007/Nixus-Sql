"""W3 semantic-layer-lite: curated metric registry, few-shot seeding, and
embed-time vocabulary enrichment.

A "metric" here is NIXUS-user curation: a business question phrased in natural
language, the VERIFIED SQL that answers it, and the tables that SQL reads.
Metrics live in an in-repo YAML file (`semantic/metrics.yaml`) — dbt does not
own them (the dbt manifest connector owns descriptions; see nixus/schema/dbt.py).

Everything in this package follows the fail-soft ingestion doctrine
(`nixus.db.fewshot_seeding`): a missing, unparseable, or invalid source is
logged and skipped — it NEVER blocks startup, and per-entry failures are
counted, not propagated.

This package's __init__ intentionally exports ONLY the pure registry/enrichment
surface: importing it must never require a database (the seeding module wires
`nixus.db.connection` at import time and is imported directly by its callers).
"""
from nixus.semantic.enrichment import (
    METRIC_SECTION_HEADER,
    append_metric_vocabulary,
    metric_vocabulary_by_table,
)
from nixus.semantic.registry import (
    Metric,
    MetricsFile,
    load_metrics_file,
    validate_metric,
)

__all__ = [
    "Metric",
    "MetricsFile",
    "load_metrics_file",
    "validate_metric",
    "METRIC_SECTION_HEADER",
    "metric_vocabulary_by_table",
    "append_metric_vocabulary",
]

"""Metric registry: load + strictly validate the curated metrics YAML.

Schema (documented in ``semantic/metrics.yaml`` — keep the two in sync):

    version: 1                       # optional, must be 1 when present
    metrics:                         # REQUIRED list of metric entries
      - name: monthly_recurring_revenue   # slug: ^[a-z][a-z0-9_]{0,63}$
        description: >-              # REQUIRED non-empty one-liner
          Human meaning of the metric...
        questions:                   # REQUIRED 1..N NL phrasings
          - "What is our MRR?"
        sql: |                       # REQUIRED verified read-only SELECT
          SELECT sum(p.monthly_price) ...
        tables: [plans, subscriptions]  # REQUIRED 1..N referenced tables

Doctrine (fewshot_seeding.py): ingestion is FAIL-SOFT. A missing, unparseable,
or schema-invalid FILE is logged and skipped whole; an invalid ENTRY inside a
valid file is skipped individually with its reason. Nothing here ever raises
into startup.

"Strict" means exactly this schema: unknown keys (top-level or per-entry),
empty strings, wrong types, non-slug names, and SQL that fails the read-only
gate are all ERRORS. A metric's SQL must pass ``is_read_only_sql`` (the same
sqlglot AST + regex gate generation runs) — a curated metric is treated with
the same suspicion as generated SQL, only earlier.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nixus.utils.sql_safety import is_read_only_sql

logger = logging.getLogger("nixus_sql.semantic")

# slug-ish identifier, used in logs and in the schema-embedding vocabulary
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

SUPPORTED_VERSION = 1

_TOP_LEVEL_KEYS = {"version", "metrics"}
_METRIC_KEYS = {"name", "description", "questions", "sql", "tables"}


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    questions: tuple[str, ...]
    sql: str
    tables: tuple[str, ...]


@dataclass
class MetricsFile:
    """Outcome of one load pass; ``errors`` carries one line per skip."""

    path: str
    metrics: list[Metric] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.metrics)


def _norm_tables(raw: object) -> tuple[str, ...] | None:
    """Normalize the ``tables`` field: non-empty list of non-empty bare or
    schema-qualified names, lowercased for match-time comparison."""
    if not isinstance(raw, list) or not raw:
        return None
    out: list[str] = []
    for t in raw:
        if not isinstance(t, str) or not t.strip():
            return None
        name = " ".join(t.strip().lower().split())
        if '"' in name or "\\" in name:
            return None
        out.append(name)
    return tuple(out)


def validate_metric(raw: object, index: int) -> tuple[Metric | None, str | None]:
    """Strictly validate one metric entry → (metric, None) or (None, reason)."""
    where = f"metrics[{index}]"
    if not isinstance(raw, dict):
        return None, f"{where}: entry must be a mapping, got {type(raw).__name__}"

    unknown = sorted(set(raw) - _METRIC_KEYS)
    if unknown:
        return None, f"{where}: unknown field(s) {unknown} (allowed: {sorted(_METRIC_KEYS)})"
    missing = sorted(_METRIC_KEYS - set(raw))
    if missing:
        return None, f"{where}: missing required field(s) {missing}"

    name = raw["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return None, f"{where}: name must match {_NAME_RE.pattern!r}, got {name!r}"

    description = raw["description"]
    if not isinstance(description, str) or not description.strip():
        return None, f"{where}: description must be a non-empty string"

    questions = raw["questions"]
    if not isinstance(questions, list) or not questions:
        return None, f"{where}: questions must be a non-empty list"
    clean_questions: list[str] = []
    for i, q in enumerate(questions):
        if not isinstance(q, str) or not q.strip():
            return None, f"{where}.questions[{i}]: must be a non-empty string"
        clean_questions.append(" ".join(q.split()))

    sql = raw["sql"]
    if not isinstance(sql, str) or not sql.strip():
        return None, f"{where}: sql must be a non-empty string"
    read_only, reason = is_read_only_sql(sql)
    if not read_only:
        return None, f"{where}: sql fails the read-only gate ({reason})"

    tables = _norm_tables(raw["tables"])
    if tables is None:
        return None, f"{where}: tables must be a non-empty list of table names"

    return (
        Metric(
            name=name,
            description=" ".join(description.split()),
            questions=tuple(dict.fromkeys(clean_questions)),
            sql=" ".join(sql.split()),
            tables=tables,
        ),
        None,
    )


def parse_metrics_document(raw: object, source: str) -> MetricsFile:
    """Validate a parsed YAML document against the file-level schema."""
    out = MetricsFile(path=source)
    if not isinstance(raw, dict):
        out.errors.append(f"{source}: top level must be a mapping, got {type(raw).__name__}")
        return out
    unknown = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown:
        out.errors.append(f"{source}: unknown top-level field(s) {unknown} (allowed: {sorted(_TOP_LEVEL_KEYS)})")
        return out
    version = raw.get("version", SUPPORTED_VERSION)
    if version != SUPPORTED_VERSION:
        out.errors.append(f"{source}: unsupported version {version!r} (supported: {SUPPORTED_VERSION})")
        return out
    metrics = raw.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        out.errors.append(f"{source}: 'metrics' must be a non-empty list")
        return out

    seen_names: set[str] = set()
    for i, entry in enumerate(metrics):
        metric, reason = validate_metric(entry, i)
        if reason:
            out.errors.append(reason)
            continue
        assert metric is not None
        if metric.name in seen_names:
            out.errors.append(f"{source}: duplicate metric name {metric.name!r} at index {i}")
            continue
        seen_names.add(metric.name)
        out.metrics.append(metric)
    return out


def load_metrics_file(path: str | Path) -> MetricsFile:
    """Load + validate a metrics YAML file. FAIL-SOFT: every problem (missing
    file, unparseable YAML, schema errors) is logged and returned as errors —
    never raised."""
    source = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Semantic metrics file not found: %s (skipping metric layer)", source)
        return MetricsFile(path=source, errors=[f"{source}: file not found"])
    except OSError as e:
        logger.warning("Semantic metrics file unreadable: %s (%s)", source, e)
        return MetricsFile(path=source, errors=[f"{source}: unreadable ({e})"])

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        logger.warning("Semantic metrics file is not valid YAML: %s (%s)", source, e)
        return MetricsFile(path=source, errors=[f"{source}: invalid YAML ({e})"])

    out = parse_metrics_document(raw, source)
    for reason in out.errors:
        logger.warning("Semantic metrics: %s", reason)
    logger.info("Semantic metrics loaded: %d metric(s) from %s", len(out.metrics), source)
    return out

"""Curated-metric registry tests — strict schema, fail-soft loading.

The registry treats a curated metric with the same suspicion as generated SQL:
every entry's SQL must pass the read-only gate, and every schema violation is an
error with a reason — while a bad FILE degrades to a logged skip, never raising
into startup.
"""
from __future__ import annotations

import yaml

from nixus.semantic.registry import (
    load_metrics_file,
    parse_metrics_document,
    validate_metric,
)


def _valid_metric(**overrides) -> dict:
    base = {
        "name": "active_mrr",
        "description": "Monthly recurring revenue over active subscriptions.",
        "questions": ["What is our MRR?"],
        "sql": "SELECT sum(p.monthly_price) FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status = 'active';",
        "tables": ["subscriptions", "plans"],
    }
    base.update(overrides)
    return base


# ── per-entry validation ──────────────────────────────────────────────────────

def test_valid_metric_parses():
    metric, reason = validate_metric(_valid_metric(), 0)
    assert reason is None
    assert metric is not None
    assert metric.name == "active_mrr"
    assert metric.questions == ("What is our MRR?",)
    # SQL is whitespace-normalized for stable few-shot storage
    assert "\n" not in metric.sql
    assert metric.tables == ("subscriptions", "plans")


def test_unknown_field_is_an_error():
    _, reason = validate_metric(_valid_metric(owner="analytics"), 0)
    assert reason is not None and "unknown field" in reason


def test_missing_field_is_an_error():
    for missing in ("name", "description", "questions", "sql", "tables"):
        raw = _valid_metric()
        del raw[missing]
        _, reason = validate_metric(raw, 0)
        assert reason is not None and missing in reason, missing


def test_non_slug_name_is_rejected():
    for bad in ("MRR", "2fast", "has space", "has-dash", ""):
        _, reason = validate_metric(_valid_metric(name=bad), 0)
        assert reason is not None and "name" in reason, bad


def test_write_sql_is_rejected_by_read_only_gate():
    _, reason = validate_metric(_valid_metric(sql="DELETE FROM plans;"), 0)
    assert reason is not None and "read-only" in reason
    _, reason = validate_metric(
        _valid_metric(sql="SELECT 1; DROP TABLE plans;"), 0
    )
    assert reason is not None and "read-only" in reason


def test_empty_questions_and_blank_strings_rejected():
    _, reason = validate_metric(_valid_metric(questions=[]), 0)
    assert reason is not None and "questions" in reason
    _, reason = validate_metric(_valid_metric(questions=["  "]), 0)
    assert reason is not None
    _, reason = validate_metric(_valid_metric(description="   "), 0)
    assert reason is not None


def test_questions_whitespace_normalized_and_deduped():
    metric, reason = validate_metric(
        _valid_metric(questions=["What is  our   MRR?", "What is our MRR?"]), 0
    )
    assert reason is None
    assert metric.questions == ("What is our MRR?",)


def test_tables_normalized_lowercase():
    metric, reason = validate_metric(_valid_metric(tables=["  Plans "]), 0)
    assert reason is None
    assert metric.tables == ("plans",)
    for bad in ([], [" "], [None], ['"quoted"']):
        _, reason = validate_metric(_valid_metric(tables=bad), 0)
        assert reason is not None, bad


# ── file-level validation ─────────────────────────────────────────────────────

def test_parse_document_happy_path_and_duplicates():
    doc = {"version": 1, "metrics": [_valid_metric(), _valid_metric()]}
    out = parse_metrics_document(doc, "test.yaml")
    assert not out.ok  # duplicate name
    assert any("duplicate" in e for e in out.errors)
    assert len(out.metrics) == 1  # first occurrence wins, second skipped


def test_parse_document_unknown_top_level_and_bad_version():
    out = parse_metrics_document({"version": 1, "metrics": [], "extra": 1}, "t.yaml")
    assert any("unknown top-level" in e for e in out.errors)
    out = parse_metrics_document({"version": 2, "metrics": [_valid_metric()]}, "t.yaml")
    assert any("version" in e for e in out.errors)


def test_one_bad_entry_does_not_poison_valid_siblings():
    doc = {
        "version": 1,
        "metrics": [
            _valid_metric(),
            _valid_metric(name="BAD_NAME"),
            _valid_metric(name="second_metric", questions=["Q2?"], sql="SELECT count(*) FROM users;"),
        ],
    }
    out = parse_metrics_document(doc, "t.yaml")
    assert [m.name for m in out.metrics] == ["active_mrr", "second_metric"]
    assert len(out.errors) == 1  # the bad entry's reason, logged not raised


def test_non_mapping_top_level_is_error_not_crash():
    out = parse_metrics_document(["not", "a", "mapping"], "t.yaml")
    assert out.metrics == []
    assert any("mapping" in e for e in out.errors)


# ── fail-soft file loading ────────────────────────────────────────────────────

def test_missing_file_is_soft(tmp_path):
    out = load_metrics_file(tmp_path / "nope.yaml")
    assert out.metrics == []
    assert out.errors and "not found" in out.errors[0]


def test_invalid_yaml_is_soft(tmp_path):
    bad = tmp_path / "metrics.yaml"
    bad.write_text("metrics: [unclosed")
    out = load_metrics_file(str(bad))
    assert out.metrics == []
    assert any("invalid YAML" in e for e in out.errors)


def test_real_repo_metrics_yaml_loads_clean():
    """The committed SaaS metric set is valid — the file the eval slice derives from."""
    out = load_metrics_file("semantic/metrics.yaml")
    assert out.ok, out.errors
    assert len(out.metrics) >= 6
    for m in out.metrics:
        assert m.questions and m.sql.lower().startswith("select") and m.tables


def test_yaml_document_roundtrip():
    """A minimal valid document written as real YAML parses to one metric."""
    text = yaml.safe_dump({"version": 1, "metrics": [_valid_metric()]})
    raw = yaml.safe_load(text)
    out = parse_metrics_document(raw, "rt.yaml")
    assert out.ok, out.errors
    assert out.metrics[0].name == "active_mrr"

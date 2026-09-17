"""Metric-seeding tests — idempotent, fail-soft, zero graph involvement.

The state DB is faked at the seam the seeding module actually uses
(``_existing_questions`` read + ``store_fewshot_example`` write), so these tests
exercise the pass accounting and idempotency logic with no database.
"""
from __future__ import annotations

import pytest
import yaml

import nixus.semantic.seeding as seeding
from nixus.semantic.seeding import MetricSeedStats, seed_metrics_from_yaml


@pytest.fixture()
def fake_store(monkeypatch):
    """An in-memory fewshot store: stored rows + call log."""
    stored: list[dict] = []

    async def fake_existing() -> set[str]:
        return {item["natural_language"] for item in stored}

    async def fake_store(*, natural_language: str, sql_query: str, tables_used, auto_learned):
        if any(item["natural_language"] == natural_language for item in stored):
            return False  # store's own near-duplicate guard
        stored.append({
            "natural_language": natural_language,
            "sql_query": sql_query,
            "tables_used": list(tables_used),
            "auto_learned": auto_learned,
        })
        return True

    monkeypatch.setattr(seeding, "_existing_questions", fake_existing)
    monkeypatch.setattr(seeding, "store_fewshot_example", fake_store)
    return stored


def _write_yaml(tmp_path, metrics) -> str:
    p = tmp_path / "metrics.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "metrics": metrics}))
    return str(p)


def _metric(name="active_mrr", questions=("What is our MRR?",),
            sql="SELECT sum(p.monthly_price) FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status = 'active';",
            tables=("subscriptions", "plans")) -> dict:
    return {
        "name": name, "description": "MRR over active subscriptions.",
        "questions": list(questions), "sql": sql, "tables": list(tables),
    }


@pytest.mark.asyncio
async def test_seeds_one_row_per_question(tmp_path, fake_store):
    path = _write_yaml(tmp_path, [_metric(questions=("Q one?", "Q two?"))])
    stats = await seed_metrics_from_yaml(path)
    assert (stats.stored, stats.skipped_existing, stats.failed) == (2, 0, 0)
    assert stats.metrics_loaded == 1
    assert {item["natural_language"] for item in fake_store} == {"Q one?", "Q two?"}
    # curated seed, never auto-learned; tables derived from the SQL
    assert all(item["auto_learned"] is False for item in fake_store)
    assert all("subscriptions" in item["tables_used"] for item in fake_store)


@pytest.mark.asyncio
async def test_second_pass_is_a_no_op(tmp_path, fake_store):
    path = _write_yaml(tmp_path, [_metric(questions=("Q one?",))])
    first = await seed_metrics_from_yaml(path)
    second = await seed_metrics_from_yaml(path)
    assert first.stored == 1
    assert second.stored == 0
    assert second.skipped_existing == 1  # exact NL match short-circuits, no store call
    assert len(fake_store) == 1


@pytest.mark.asyncio
async def test_store_refusal_counts_as_skipped_not_failed(tmp_path, monkeypatch):
    """A store-level near-duplicate refusal (False) is a SKIP, not a failure."""
    async def refusing_store(**kwargs):
        return False

    async def empty_existing() -> set[str]:
        return set()

    monkeypatch.setattr(seeding, "store_fewshot_example", refusing_store)
    monkeypatch.setattr(seeding, "_existing_questions", empty_existing)
    path = _write_yaml(tmp_path, [_metric(questions=("Q one?",))])
    stats = await seed_metrics_from_yaml(path)
    assert stats.stored == 0
    assert stats.skipped_existing == 1
    assert stats.failed == 0


@pytest.mark.asyncio
async def test_store_failure_counts_as_failed_and_pass_continues(tmp_path, monkeypatch):
    calls = {"n": 0}

    async def flaky_store(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return True

    async def fake_existing() -> set[str]:
        return set()

    monkeypatch.setattr(seeding, "store_fewshot_example", flaky_store)
    monkeypatch.setattr(seeding, "_existing_questions", fake_existing)
    path = _write_yaml(tmp_path, [_metric(questions=("Q one?", "Q two?"))])
    stats = await seed_metrics_from_yaml(path)
    assert stats.failed == 1
    assert stats.stored == 1  # the pass continued after the failure


@pytest.mark.asyncio
async def test_invalid_yaml_is_a_zero_work_pass(tmp_path, fake_store):
    path = _write_yaml(tmp_path, [{"name": "BAD NAME", "description": "x",
                                   "questions": ["q"], "sql": "SELECT 1;", "tables": ["t"]}])
    stats = await seed_metrics_from_yaml(path)
    assert stats.metrics_loaded == 0
    assert stats.stored == 0 and fake_store == []


@pytest.mark.asyncio
async def test_missing_file_is_a_zero_work_pass(tmp_path, fake_store):
    stats = await seed_metrics_from_yaml(str(tmp_path / "nope.yaml"))
    assert stats == MetricSeedStats(path=str(tmp_path / "nope.yaml"),
                                    metrics_loaded=0, stored=0, skipped_existing=0, failed=0)

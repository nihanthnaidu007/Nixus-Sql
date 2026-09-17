"""
RETIRED (6.2) — Chinook SQL-correctness gold set. ARCHIVED, NOT the benchmark.

This is the source of the E02 failure and of the broken column-overlap
comparison (``result_overlap_rate``) it relied on — see eval/result_equivalence.py
for the honest replacement. It is kept here for history only and is NOT collected
by pytest (eval/conftest.py ``collect_ignore_glob``). The benchmark of record is
eval/run_saas_benchmark.py / eval/test_saas_correctness.py.

──────────────────────────────────────────────────────────────────────────────
SQL correctness tests — Category 1 of the NIXUS SQL evaluation harness.

For each of the 30 gold queries:
  1. Execute gold_sql directly against the DB to get the expected result set.
  2. Ask the API the natural language question.
  3. Measure how many gold rows appear in the API result (overlap rate).
  4. Pass if overlap >= OVERLAP_THRESHOLD (default 0.7).

Non-negotiable bar: ≥ 24 / 30 tests must pass (80% sql_correctness_rate).
"""

import pytest

from eval.archive_chinook.gold_queries import GOLD_QUERIES
from eval.conftest import (
    extract_rows,
    result_overlap_rate,
    run_gold_sql,
    run_query,
)

OVERLAP_THRESHOLD = 0.70


@pytest.mark.parametrize("gold", GOLD_QUERIES, ids=[q["id"] for q in GOLD_QUERIES])
def test_sql_correctness(http_client, gold):
    gold_rows = run_gold_sql(gold["gold_sql"])

    if not gold_rows:
        pytest.skip(f"{gold['id']}: gold SQL returned no rows — skipping")

    state = run_query(http_client, gold["question"])

    error = state.get("error")
    assert not error, (
        f"{gold['id']}: API returned error — {error}\n"
        f"question: {gold['question']}"
    )

    api_rows = extract_rows(state)
    assert api_rows, (
        f"{gold['id']}: API returned 0 rows\n"
        f"question: {gold['question']}\n"
        f"generated_sql: {state.get('generated_sql')}"
    )

    overlap = result_overlap_rate(gold_rows, api_rows)
    assert overlap >= OVERLAP_THRESHOLD, (
        f"{gold['id']}: overlap {overlap:.1%} < {OVERLAP_THRESHOLD:.0%}\n"
        f"question: {gold['question']}\n"
        f"generated_sql: {state.get('generated_sql')}\n"
        f"gold_rows[:3]: {gold_rows[:3]}\n"
        f"api_rows[:3]: {api_rows[:3]}"
    )

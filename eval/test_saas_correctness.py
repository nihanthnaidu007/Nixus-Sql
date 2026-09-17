"""SaaS correctness + scope tests (pytest view of the benchmark of record).

Each answerable case is scored by result-equivalence to its gold SQL; each scope
case is scored on the scope gate's outcome. This is the pytest representation of
eval/run_saas_benchmark.py (which emits the JSON report). Run with the target
pointed at nixus_saas and embeddings re-embedded for SaaS:

    pytest eval/test_saas_correctness.py     # (TARGET_DATABASE_URL -> nixus_saas)

A lower pass rate than the retired Chinook suite is EXPECTED — this is the first
untuned, honest measurement.
"""
import pytest

from eval.run_saas_benchmark import score_answerable_case, score_scope_case
from eval.saas_gold import ANSWERABLE, SCOPE


@pytest.mark.parametrize("case", ANSWERABLE, ids=[c["id"] for c in ANSWERABLE])
def test_saas_answerable(http_client, case):
    r = score_answerable_case(http_client, case)
    assert r["passed"], (
        f"{case['id']} ({case['tier']}): {r['reason']}\n"
        f"question: {case['question']}\n"
        f"generated_sql: {r.get('generated_sql')}\n"
        f"gold_rows={r.get('gold_row_count')} gen_rows={r.get('gen_row_count')}\n"
        f"first_mismatch: {r.get('first_mismatch')}"
    )


@pytest.mark.parametrize("case", SCOPE, ids=[c["id"] for c in SCOPE])
def test_saas_scope(http_client, case):
    r = score_scope_case(http_client, case)
    assert r["passed"], (
        f"{case['id']}: {r['reason']}\n"
        f"expected_outcome={case['expected_outcome']} "
        f"scope_category={r.get('scope_category')} sql_executed={r.get('sql_executed')}"
    )

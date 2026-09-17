"""Metric-question eval slice (W3) — pytest view, mirroring test_saas_correctness.py.

Each case: the metric's curated NL phrasing asked through the live API, scored
by result-equivalence against the metric's verified SQL. Requires a running API
over the nixus_saas seed (same as the SaaS benchmark); skips cleanly otherwise.
"""
import pytest

from eval.metric_gold import METRIC_CASES
from eval.run_saas_benchmark import score_answerable_case


@pytest.mark.parametrize("case", METRIC_CASES, ids=[c["id"] for c in METRIC_CASES])
def test_metric_question(http_client, case):
    r = score_answerable_case(http_client, case)
    assert r["passed"], (
        f"{case['id']} ({case['metric']}): {r['reason']}\n"
        f"question: {case['question']}\n"
        f"generated_sql: {r.get('generated_sql')}\n"
        f"gold_rows={r.get('gold_row_count')} gen_rows={r.get('gen_row_count')}\n"
        f"first_mismatch: {r.get('first_mismatch')}"
    )

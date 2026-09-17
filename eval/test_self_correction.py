"""
Self-correction resilience tests — Category 3 of the NIXUS SQL evaluation harness.

These 5 questions are intentionally tricky — they involve unusual joins,
window functions, CTEs, or ambiguous phrasing that is likely to produce an
initial SQL error.  The test simply asserts that the system recovers and
returns a non-empty result (no final error), regardless of how many correction
attempts were needed.

Passes if: the API returns rows and no error for all 5 queries.
"""

import pytest

from eval.conftest import extract_rows, run_query

TRICKY_QUERIES = [
    # Requires window function PARTITION BY with aggregate in CTE
    "For each country, which customer has spent the most money?",
    # Requires 5-table join across Artist→Album→Track→InvoiceLine→Invoice
    "What is the total invoice revenue per artist, considering only paid invoices?",
    # Requires self-join or lateral join (employees and their manager's name)
    "Show each employee with the name of their direct manager.",
    # Ambiguous table name (Track appears in both PlaylistTrack and InvoiceLine)
    "Which tracks have never been purchased?",
    # Complex HAVING with subquery
    "List genres whose average track price is higher than the overall average track price.",
]


@pytest.mark.parametrize("question", TRICKY_QUERIES, ids=[f"tricky_{i+1:02d}" for i in range(len(TRICKY_QUERIES))])
def test_self_correction_resilience(http_client, question):
    state = run_query(http_client, question)

    error = state.get("error")
    assert not error, (
        f"Self-correction failed for question:\n  {question}\n"
        f"Error: {error}\n"
        f"Correction attempts: {state.get('correction_attempts', 0)}\n"
        f"Generated SQL: {state.get('generated_sql')}"
    )

    rows = extract_rows(state)
    assert rows is not None, (
        f"No execution_result for question:\n  {question}\n"
        f"State keys: {list(state.keys())}"
    )

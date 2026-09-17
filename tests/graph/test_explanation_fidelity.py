"""Explanation-fidelity tests (M3): does the explanation match the EXECUTED result?

``is_overstated`` catches world-claims (style); ``explanation_matches_result``
catches factual mismatches against the executed rows: wrong row counts and
cited values that appear in no returned row. Both checks share the governing
rule — mangling a good explanation is worse than missing a claim — so the
MUST-NOT-FLAG set here is the false-positive guard and is deliberately larger
than the MUST-FLAG set.
"""
import pytest

from nixus.graph.explanation_check import (
    describe_result_plainly,
    explanation_matches_result,
)

ROWS = [
    {"artist": "X", "revenue": 1200},
    {"artist": "Y", "revenue": 1100},
]
COLUMNS = ["artist", "revenue"]


def check(text, row_count=2, rows=ROWS, question=""):
    return explanation_matches_result(text, rows, COLUMNS, row_count, question=question)


# --- MUST-NOT-FLAG: faithful explanations must survive untouched -------------


@pytest.mark.parametrize(
    "text,row_count,rows,question",
    [
        ("The query returned 2 rows.", 2, ROWS, ""),
        ("The query returned 12 rows.", 12, ROWS, ""),
        ("One row matched: the top artist is X at $1,200.", 1, ROWS[:1], ""),
        ("A single row was returned.", 1, ROWS[:1], ""),
        ("No rows matched the query.", 0, [], ""),
        ("Zero results came back.", 0, [], ""),
        # "top/first N" selectors describe a subset, not the result size.
        ("Here are the top 3 rows by revenue.", 2, ROWS, ""),
        ("The first 5 records are shown below.", 2, ROWS, ""),
        # Row counts with thousands separators are claims, not data values —
        # and must not double-flag as a cited value absent from the cells.
        ("The query returned 1,200 rows in total.", 1200, ROWS, ""),
        # Correctly cited data values (money, decimals, string-rendered cells).
        ("The top artist earned $1,200, ahead of Y at $1,100.", 2, ROWS, ""),
        ("Tracks are priced at $0.99 each.", 2, [{"price": 0.99}], ""),
        ("Tracks are priced at $0.99 each.", 2, [{"price": "0.9900"}], ""),
        ("The total came to $1,850.50 for the period.", 2, [{"total": 1850.50}], ""),
        ("The total came to 1,850 units across regions.", 2, [{"units": "1850"}], ""),
        # Derived figures (average/difference) are legitimately absent from rows.
        ("The average across the entries is $1,150.", 2, ROWS, ""),
        ("Revenue differs by $100 between the two artists.", 2, ROWS, ""),
        # The user's own threshold, echoed back — not a result claim.
        ("No artist cleared the $5,000 threshold you set.", 2, ROWS,
         "top artists above $5,000"),
        # Bare integers (years, ranks) are not checkable data values.
        ("All results are from 2023.", 2, ROWS, ""),
        ("Artist X holds the number 1 position.", 2, ROWS, ""),
        # Empty result: a cited threshold describes the query, not a row.
        ("No tracks cost more than $5.00 in the catalog.", 0, [], ""),
    ],
    ids=[
        "exact-count", "exact-count-12", "one-row", "single-row-phrasing",
        "no-rows-empty", "zero-results", "top-n-selector", "first-n-selector",
        "grouped-count-claim", "cited-values", "cited-decimal",
        "cited-decimal-trailing-zeros-string",
        "cited-float", "cited-string-cell", "derived-average", "derived-difference",
        "question-echo", "bare-integer-year", "bare-integer-rank",
        "empty-result-threshold",
    ],
)
def test_faithful_explanations_pass(text, row_count, rows, question):
    result = explanation_matches_result(text, rows, COLUMNS, row_count, question=question)
    assert result.consistent, (
        f"FALSE POSITIVE — faithful explanation flagged: {text!r} "
        f"(problems={result.problems})"
    )


# --- MUST-FLAG: factual mismatches the style check cannot see -----------------

@pytest.mark.parametrize(
    "text,row_count,rows,question,expected_in_problems",
    [
        ("The query returned 5 rows.", 2, ROWS, "", "5 rows"),
        ("The query returned 5 rows.", 12, ROWS, "", "5 rows"),
        ("No rows matched the query.", 2, ROWS, "", "no rows"),
        ("A single row was returned.", 3, ROWS, "", "row"),
        # Hallucinated figure — nowhere in the executed rows.
        ("The top artist earned $1,850, ahead of Y.", 2, ROWS, "", "$1,850"),
        # Near-miss decimal — the classic off-by-digit hallucination.
        ("Tracks cost $1.90 each.", 2, [{"price": 1.99}], "", "$1.90"),
        # Boundary over-match: the cited 0.99 must NOT pass because a cell
        # holds 10.99 (same digits, larger number) — and $1.50 is not a match
        # for a 21.50 cell either. A cited figure matches only its own number.
        ("Tracks cost $0.99 each.", 2, [{"price": 10.99}], "", "$0.99"),
        ("The fee was $1.50.", 2, [{"fee": 21.50}], "", "$1.50"),
        # Thousands-grouped value absent from the rows.
        ("Sales reached 9,500 units.", 2, [{"units": 950}], "", "9,500"),
    ],
    ids=["wrong-count", "wrong-count-12", "no-rows-but-2", "single-but-3",
         "hallucinated-money", "near-miss-decimal",
         "boundary-tail-0-99-vs-10-99", "boundary-mid-1-50-vs-21-50",
         "grouped-value"],
)
def test_unfaithful_explanations_flag(text, row_count, rows, question, expected_in_problems):
    result = explanation_matches_result(text, rows, COLUMNS, row_count, question=question)
    assert not result.consistent, (
        f"missed a factual mismatch: {text!r} (row_count={row_count})"
    )
    assert any(expected_in_problems in p for p in result.problems), result.problems


def test_grouped_row_count_is_a_claim_not_a_value():
    # "1,200 rows" when 1,200 rows actually returned: consistent, and the value
    # pass must NOT additionally complain that 1,200 is absent from the cells.
    result = check("The query returned 1,200 rows in total.", row_count=1200)
    assert result.consistent, result.problems


def test_empty_and_none_explanations_are_consistent():
    assert explanation_matches_result("", ROWS, COLUMNS, 2).consistent
    assert explanation_matches_result(None, ROWS, COLUMNS, 2).consistent  # type: ignore[arg-type]


# --- the deterministic fallback is faithful by construction -------------------

def test_fallback_descriptions_always_pass_their_own_fidelity_check():
    for rows, count in [([], 0), (ROWS[:1], 1), (ROWS, 2)]:
        out = describe_result_plainly(rows, COLUMNS, count)
        result = explanation_matches_result(out, rows, COLUMNS, count)
        assert result.consistent, f"fallback not faithful at count={count}: {out!r}"


# --- node integration: fidelity failures drive the same regenerate-once flow --


def _state(row_count=2, question="top artists by revenue"):
    return {
        "user_query": question,
        "session_id": "test-session",
        "generated_sql": "SELECT artist, revenue FROM artists ORDER BY revenue DESC LIMIT 5",
        "correction_attempts": 0,
        "served_from_cache": False,
        "cache_result": None,
        # Not "GOOD" → the node skips the few-shot/cache writes (no DB/network).
        "result_quality": {"status": "PARTIAL"},
        "validation_result": {"warnings": []},
        "chart_config": {},
        "tables_identified": ["artists"],
        "execution_result": {
            "rows": [dict(r) for r in ROWS],
            "columns": COLUMNS,
            "row_count": row_count,
            "execution_time_ms": 3.0,
        },
        "completed_nodes": [],
        "stream_updates": [],
    }


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _ScriptedLLM:
    """Returns scripted answers in order; records the prompts it received."""

    def __init__(self, *args, **kwargs):
        self._answers = []
        self.prompts = []

    def script(self, *answers):
        self._answers = list(answers)
        return self

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return _FakeResp(self._answers.pop(0))


async def test_node_regenerates_on_row_count_mismatch(monkeypatch):
    import nixus.graph.nodes.explain_result as er

    llm = _ScriptedLLM()
    llm.script(
        "The query returned 5 rows, led by artist X.",       # wrong count → flag
        "The query returned 2 rows, led by X at $1,200.",    # faithful → used
    )
    monkeypatch.setattr(er, "ChatAnthropic", lambda *a, **k: llm)

    out = await er.explain_result_node(_state())

    assert out["explanation"] == "The query returned 2 rows, led by X at $1,200."
    # The strict suffix must quote the specific fidelity violation.
    assert "returned 5 rows" in llm.prompts[1] or "5 rows" in llm.prompts[1]


async def test_node_falls_back_when_fidelity_never_passes(monkeypatch):
    import nixus.graph.nodes.explain_result as er

    llm = _ScriptedLLM()
    llm.script(
        "The query returned 5 rows.",
        "Exactly 9 rows matched the filter.",                # still wrong
    )
    monkeypatch.setattr(er, "ChatAnthropic", lambda *a, **k: llm)

    state = _state()
    out = await er.explain_result_node(state)

    assert out["explanation"] == describe_result_plainly(
        state["execution_result"]["rows"], COLUMNS, 2
    )


async def test_node_passes_faithful_explanation_through_unchanged(monkeypatch):
    import nixus.graph.nodes.explain_result as er

    llm = _ScriptedLLM()
    good = "The query returned 2 rows: artist X at $1,200 and artist Y at $1,100."
    llm.script(good)
    monkeypatch.setattr(er, "ChatAnthropic", lambda *a, **k: llm)

    out = await er.explain_result_node(_state())

    assert out["explanation"] == good
    assert len(llm.prompts) == 1  # no regeneration burned

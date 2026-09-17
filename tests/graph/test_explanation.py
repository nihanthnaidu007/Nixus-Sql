"""Explanation-honesty tests (prompt 5.1).

The benchmark scores SQL correctness, not prose, so it is a weak guard for the
explanation. This suite is the real check: ``is_overstated`` is pure, so it is
exercised directly.

The MUST-NOT-FLAG set is the FALSE-POSITIVE GUARD. The governing rule mirrors the
grounding checker: mangling a good description is worse than missing an occasional
editorialization, so every clearly-descriptive sentence MUST survive untouched.
The MUST-FLAG set proves the backstop still catches blatant world-claims
(causation, motivation, prediction, recommendation).
"""
import pytest

from nixus.graph.explanation_check import (
    describe_result_plainly,
    is_overstated,
)

# Editorializing — claims about the WORLD the rows cannot prove. Each MUST flag.
MUST_FLAG = [
    "Sales rose because of the summer promotion.",          # causal
    "This suggests customers prefer premium tiers.",         # speculative
    "Revenue will likely continue to grow next quarter.",    # predictive
    "The company should focus on its top three products.",   # prescriptive
    "The decline reflects weakening demand.",                # speculative/world
]


# Descriptive — states only what the rows/query literally are. None may flag.
MUST_NOT_FLAG = [
    "The top artist by revenue is X at $1,200; the next four range from $800 to $1,100.",
    "The query returned 5 rows.",
    "No rows matched the given filter.",
    "The data shows three categories, with Rock having the highest count.",
    "Revenue is reported per month for 2023.",  # describes the query's scope
]


@pytest.mark.parametrize("text", MUST_FLAG)
def test_must_flag(text):
    result = is_overstated(text, question="any question")
    assert result.overstated, (
        f"expected editorializing to be flagged but it was not: {text!r} "
        f"(triggers={result.triggers})"
    )
    assert result.triggers, "overstated verdict must carry its triggers"


@pytest.mark.parametrize("text", MUST_NOT_FLAG)
def test_must_not_flag(text):
    result = is_overstated(text, question="any question")
    assert not result.overstated, (
        f"descriptive sentence was wrongly flagged (false positive): {text!r} "
        f"(triggers={result.triggers})"
    )


def test_empty_and_none_explanations_are_not_overstated():
    assert not is_overstated("", "q").overstated
    assert not is_overstated(None, "q").overstated  # type: ignore[arg-type]


def test_causal_marker_about_the_query_is_not_flagged():
    # "filtered ... because" describes the SQL, not a real-world cause.
    text = "Results were filtered to 2023 because that is the period you asked about."
    assert not is_overstated(text, "q").overstated


def test_speculative_marker_pointing_at_data_is_not_flagged():
    # "indicates 5 matching rows" is data-talk, not a real-world inference.
    text = "The filter indicates 5 matching rows in the result."
    assert not is_overstated(text, "q").overstated


# --- deterministic fallback rendering ----------------------------------------

def test_describe_result_plainly_empty():
    assert describe_result_plainly([], [], 0) == "No rows matched the query."


def test_describe_result_plainly_single_row():
    rows = [{"artist": "X", "revenue": 1200}]
    out = describe_result_plainly(rows, ["artist", "revenue"], 1)
    assert "1 row" in out
    assert "artist = X" in out
    assert not is_overstated(out, "q").overstated


def test_describe_result_plainly_multi_row_is_descriptive():
    rows = [{"artist": "X", "revenue": 1200}, {"artist": "Y", "revenue": 1100}]
    out = describe_result_plainly(rows, ["artist", "revenue"], 2)
    assert "2 rows" in out
    # The fallback can never itself be overstated.
    assert not is_overstated(out, "q").overstated


# --- integration: the backstop replaces editorializing in the node -----------

@pytest.fixture
def _editorializing_state():
    return {
        "user_query": "top 5 artists by total invoice revenue",
        "session_id": "test-session",
        "generated_sql": "SELECT name, revenue FROM artists ORDER BY revenue DESC LIMIT 5",
        "correction_attempts": 0,
        "served_from_cache": False,
        "cache_result": None,
        # Not "GOOD" → the node skips the few-shot/cache writes (no DB/network).
        "result_quality": {"status": "PARTIAL"},
        "validation_result": {"warnings": []},
        "chart_config": {},
        "tables_identified": ["artists"],
        "execution_result": {
            "rows": [
                {"name": "X", "revenue": 1200},
                {"name": "Y", "revenue": 1100},
            ],
            "columns": ["name", "revenue"],
            "row_count": 2,
            "execution_time_ms": 3.0,
        },
        "completed_nodes": [],
        "stream_updates": [],
    }


async def test_backstop_falls_back_when_model_keeps_editorializing(
    monkeypatch, _editorializing_state
):
    """If both generation attempts editorialize, the node emits the safe,
    deterministic description rather than the world-claim text."""
    import nixus.graph.nodes.explain_result as er

    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        def __init__(self, *args, **kwargs):
            pass

        async def ainvoke(self, prompt):
            # Always editorializes (causal + prescriptive) → never passes.
            return _FakeResp(
                "Revenue rose because of strong demand, and the company "
                "should expand its catalog to keep growing."
            )

    monkeypatch.setattr(er, "ChatAnthropic", _FakeLLM)

    out = await er.explain_result_node(_editorializing_state)
    final = out["explanation"]

    assert not is_overstated(final).overstated, (
        f"backstop failed to replace editorializing text: {final!r}"
    )
    rows = _editorializing_state["execution_result"]["rows"]
    cols = _editorializing_state["execution_result"]["columns"]
    assert final == describe_result_plainly(rows, cols, len(rows))

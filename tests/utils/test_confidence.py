"""Rule-table tests for the categorical confidence model (prompt 5.2).

The rules are pure and deterministic, so the whole table is asserted directly —
this is where their legibility pays off. The governing invariant: HIGH is
reachable ONLY by the clean path (no clarification, zero corrections, clean
grounding). A clarified query can NEVER be HIGH.
"""
import pytest

from nixus.utils.confidence import (
    ConfidenceLevel,
    assess_confidence,
    confidence_badge,
    level_to_score,
)

# Signal kwargs for a fully clean run.
CLEAN = {
    "clarification_happened": False,
    "correction_attempts": 0,
    "grounded_cleanly": True,
    "row_count": 5,
}


def test_clean_path_is_high_with_no_reasons():
    a = assess_confidence(**CLEAN)
    assert a.level is ConfidenceLevel.HIGH
    assert a.reasons == []
    assert a.signals == {
        "clarification_happened": False,
        "correction_attempts": 0,
        "grounded_cleanly": True,
        "row_count": 5,
    }


def test_clarification_alone_is_medium_never_high():
    a = assess_confidence(**{**CLEAN, "clarification_happened": True})
    assert a.level is ConfidenceLevel.MEDIUM
    assert any("clarification" in r.lower() for r in a.reasons)


def test_correction_alone_is_medium():
    a = assess_confidence(**{**CLEAN, "correction_attempts": 1})
    assert a.level is ConfidenceLevel.MEDIUM
    assert any("self-correction" in r.lower() or "correct" in r.lower() for r in a.reasons)


def test_conservative_grounding_alone_is_medium():
    a = assess_confidence(**{**CLEAN, "grounded_cleanly": False})
    assert a.level is ConfidenceLevel.MEDIUM
    assert any("grounding" in r.lower() for r in a.reasons)


def test_two_signals_compound_to_low():
    a = assess_confidence(
        **{**CLEAN, "clarification_happened": True, "correction_attempts": 2}
    )
    assert a.level is ConfidenceLevel.LOW
    assert len(a.reasons) >= 2


def test_three_signals_is_low():
    a = assess_confidence(
        clarification_happened=True,
        correction_attempts=1,
        grounded_cleanly=False,
        row_count=10,
    )
    assert a.level is ConfidenceLevel.LOW
    assert len(a.reasons) == 3


def test_empty_result_alone_does_not_lower_high():
    # An empty result can be correct: a clean run with 0 rows is still HIGH.
    a = assess_confidence(**{**CLEAN, "row_count": 0})
    assert a.level is ConfidenceLevel.HIGH
    assert a.reasons == []
    assert a.signals["row_count"] == 0


def test_empty_result_compounds_existing_uncertainty_to_low():
    # One uncertainty signal (clarification) → MEDIUM; an empty result on top
    # compounds it to LOW.
    a = assess_confidence(
        **{**CLEAN, "clarification_happened": True, "row_count": 0}
    )
    assert a.level is ConfidenceLevel.LOW
    assert any("empty" in r.lower() for r in a.reasons)


def test_none_row_count_is_not_treated_as_empty():
    a = assess_confidence(**{**CLEAN, "clarification_happened": True, "row_count": None})
    # Only the clarification signal → MEDIUM, not LOW (None is "no info", not 0).
    assert a.level is ConfidenceLevel.MEDIUM


@pytest.mark.parametrize(
    "kwargs",
    [
        {"clarification_happened": True},
        {"correction_attempts": 1},
        {"grounded_cleanly": False},
        {"clarification_happened": True, "correction_attempts": 3},
        {"correction_attempts": 1, "grounded_cleanly": False},
        {"row_count": 0, "clarification_happened": True},
    ],
)
def test_high_is_unreachable_under_any_uncertainty_signal(kwargs):
    a = assess_confidence(**{**CLEAN, **kwargs})
    assert a.level is not ConfidenceLevel.HIGH


def test_reasons_present_iff_not_high():
    # HIGH ⟺ no reasons; any downgrade carries at least one reason.
    high = assess_confidence(**CLEAN)
    assert high.level is ConfidenceLevel.HIGH and not high.reasons
    downgraded = assess_confidence(**{**CLEAN, "correction_attempts": 1})
    assert downgraded.level is not ConfidenceLevel.HIGH and downgraded.reasons


# --- back-compat shim --------------------------------------------------------

def test_level_to_score_ordering():
    assert (
        level_to_score(ConfidenceLevel.HIGH)
        > level_to_score(ConfidenceLevel.MEDIUM)
        > level_to_score(ConfidenceLevel.LOW)
    )


def test_confidence_badge_accepts_level_string_and_numeric():
    assert confidence_badge("HIGH") == ("HIGH", "high")
    assert confidence_badge(ConfidenceLevel.LOW) == ("LOW", "low")
    # Legacy numeric still maps via thresholds.
    assert confidence_badge(0.95) == ("HIGH", "high")
    assert confidence_badge(0.70) == ("MEDIUM", "medium")
    assert confidence_badge(0.10) == ("LOW", "low")

"""Categorical confidence model (prompt 5.2).

Confidence reports what the system KNOWS about its own process — never a number
with no empirical basis. There are NO invented weights here (no "grounding is
worth 0.4"). Instead a small set of explicit, legible RULES over real signals
produces a categorical verdict (HIGH / MEDIUM / LOW), and every downgrade carries
a plain-English reason a user can read.

The rules, in full:

  * Start at HIGH.
  * Each of these is an UNCERTAINTY SIGNAL that appends a reason:
      - the query went through clarification (its intent was ambiguous),
      - the SQL needed self-correction (it was not right the first time),
      - grounding did not pass cleanly (it passed only via a conservative,
        fail-open fallback rather than a verified check).
  * Zero uncertainty signals -> HIGH.  Exactly one -> MEDIUM.  Two or more -> LOW.
  * An EMPTY result does NOT by itself lower confidence (an empty result can be
    correct); it is recorded in the signals. It only COMPOUNDS: empty plus any
    existing uncertainty signal drops the verdict to LOW.

So HIGH is reachable ONLY by the clean path: no clarification, zero corrections,
clean grounding. A clarified query can never be HIGH — the core honesty fix.

The verdict is a pure, deterministic function of the signals: given the signals,
a reader can predict the level. That legibility is the whole point, and it is
exactly what the unit tests pin down.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConfidenceLevel(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ConfidenceAssessment:
    level: ConfidenceLevel
    signals: dict
    reasons: list = field(default_factory=list)


def assess_confidence(
    clarification_happened: bool,
    correction_attempts: int,
    grounded_cleanly: bool,
    row_count: int | None = None,
) -> ConfidenceAssessment:
    """Derive a categorical confidence verdict from real process signals.

    See the module docstring for the complete rule table.
    """
    reasons: list = []

    if clarification_happened:
        reasons.append(
            "This query required clarification, so its intent was not unambiguous."
        )
    if correction_attempts and correction_attempts > 0:
        plural = "s" if correction_attempts != 1 else ""
        reasons.append(
            f"The SQL was not correct on the first attempt "
            f"({correction_attempts} self-correction{plural})."
        )
    if not grounded_cleanly:
        reasons.append(
            "Grounding did not pass cleanly — it relied on a conservative "
            "fallback rather than a fully verified check."
        )

    n = len(reasons)
    if n == 0:
        level = ConfidenceLevel.HIGH
    elif n == 1:
        level = ConfidenceLevel.MEDIUM
    else:
        level = ConfidenceLevel.LOW

    # An empty result never lowers a clean HIGH on its own, but it compounds any
    # existing uncertainty into LOW.
    empty = row_count is not None and row_count == 0
    if empty and n >= 1:
        level = ConfidenceLevel.LOW
        reasons.append("The result is empty, which compounds the uncertainty above.")

    signals = {
        "clarification_happened": bool(clarification_happened),
        "correction_attempts": int(correction_attempts or 0),
        "grounded_cleanly": bool(grounded_cleanly),
        "row_count": row_count,
    }
    return ConfidenceAssessment(level=level, signals=signals, reasons=reasons)


# --- Back-compat helpers -----------------------------------------------------
# The old numeric API (compute_confidence_score) is gone; its two call sites in
# explain_result now call assess_confidence directly. These remain only to give
# the legacy SSE/UI `confidence_score` field a representative number and to keep
# `confidence_badge` working for the UI (which now passes the categorical level).

_LEVEL_SCORE = {
    ConfidenceLevel.HIGH: 0.95,
    ConfidenceLevel.MEDIUM: 0.70,
    ConfidenceLevel.LOW: 0.40,
}


def level_to_score(level: ConfidenceLevel) -> float:
    """Representative numeric for the legacy ``confidence_score`` field."""
    return _LEVEL_SCORE.get(level, 0.0)


def _coerce_level(value) -> ConfidenceLevel:
    if isinstance(value, ConfidenceLevel):
        return value
    if isinstance(value, str):
        try:
            return ConfidenceLevel[value.upper()]
        except KeyError:
            pass
    try:  # legacy numeric score
        score = float(value)
    except (TypeError, ValueError):
        return ConfidenceLevel.LOW
    if score >= 0.85:
        return ConfidenceLevel.HIGH
    if score >= 0.65:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def confidence_badge(value) -> tuple:
    """``(LABEL, css_class)`` for the UI badge.

    Accepts a :class:`ConfidenceLevel`, a level string ("HIGH"/...), or a legacy
    numeric score, so existing call sites keep working during the transition.
    """
    level = _coerce_level(value)
    return (level.value, level.value.lower())

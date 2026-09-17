"""Backstop for result-explanation honesty (prompt 5.1).

The explain_result prompt is the primary guard: it instructs the model to
DESCRIBE the returned rows and forbids claims the data cannot support. This
module is the *scrutiny trigger* that runs after generation and catches the
blatant editorializing the prompt failed to prevent.

Governing rule: descriptive-but-rich, not boring. We flag claims-ABOUT-THE-WORLD
(causation, motivation, prediction, recommendation) — NOT descriptions of what
the query/data literally is. "The query filtered to 2023" and "the data shows
5 rows" are descriptive and MUST survive; "sales rose because of X" and "the
company should expand" are world-claims and MUST trigger.

The trigger is deliberately conservative: when a causal/speculative marker
points at the literal data (numbers, rows, results) or at the query mechanics
(filtered/grouped/limited), we treat it as descriptive and do NOT flag. When it
points at a real-world cause/motivation/future, we flag. Recommendations are
never descriptive, so they flag directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class OverstatementResult:
    """Verdict from ``is_overstated``.

    ``overstated`` is the boolean the caller acts on. ``triggers`` lists the
    ``(category, matched_phrase)`` pairs that fired, for logging and for quoting
    back the violated rule in the stricter regeneration.
    """

    overstated: bool
    triggers: list = field(default_factory=list)


# How far past a marker we look for its object, in characters.
_GUARD_WINDOW = 60

# The marker's object is *data-talk* (numbers, rows, results, counts) → the
# sentence is describing the result set, not making a real-world claim.
_DATA_OBJECT = re.compile(
    r"\b("
    r"\d[\d,.]*"  # any number / figure
    r"|rows?|records?|entr(?:y|ies)|results?|values?|columns?"
    r"|categor(?:y|ies)|matching|matched|returned|counts?|totals?"
    r"|the\s+(?:result|data|query|table|rows?)"
    r")\b",
    re.IGNORECASE,
)

# Query-mechanics words: a causal/temporal marker sitting next to these is
# describing what the SQL did ("filtered to 2023 because ...") — not a
# real-world cause. Allowed by the prompt, so it must not flag.
_QUERY_MECHANICS = re.compile(
    r"\b("
    r"filter(?:ed|s|ing)?|limit(?:ed|s|ing)?|group(?:ed|s|ing)?"
    r"|sort(?:ed|s|ing)?|order(?:ed|ing)?|select(?:ed|s|ing)?|join(?:ed|s|ing)?"
    r"|aggregat\w+|rank(?:ed|s|ing)?|query|sql|requested|you\s+asked|as\s+asked"
    r")\b",
    re.IGNORECASE,
)

# --- World-claim markers, by category ----------------------------------------
# Longer alternatives are listed first so the alternation prefers them.

_CAUSAL = re.compile(
    r"\b("
    r"because\s+of|because|due\s+to|caused\s+by|led\s+to|driven\s+by|drove"
    r"|as\s+a\s+result\s+of|resulted\s+in|owing\s+to|thanks\s+to"
    r"|stems\s+from|attributable\s+to|on\s+account\s+of"
    r")\b",
    re.IGNORECASE,
)

_SPECULATIVE = re.compile(
    r"\b("
    r"suggest(?:s|ing)?|impl(?:ies|ying|ied)|indicat\w+|reflect(?:s|ing)?"
    r"|appears?\s+to|seems?\s+to|points?\s+to|hints?\s+at|reveals?\s+that"
    r"|likely|probably|presumably"
    r")\b",
    re.IGNORECASE,
)

_PREDICTIVE = re.compile(
    r"\b("
    r"will|won't|expected\s+to|going\s+to|forecast(?:s|ed)?|projected\s+to"
    r"|trending\s+toward|trend\s+toward|continue\s+to|poised\s+to"
    r"|next\s+(?:quarter|month|year|week|period)"
    r")\b",
    re.IGNORECASE,
)

# Recommendations are never descriptive, so these are unguarded.
_PRESCRIPTIVE = re.compile(
    r"\b("
    r"should|shouldn't|recommend\w*|ought\s+to|advis\w+"
    r"|consider\s+\w+ing"
    r"|must\s+(?:focus|invest|prioriti\w+|consider|increase|reduce|expand|target)"
    r"|need\s+to\s+(?:focus|invest|prioriti\w+|consider|increase|reduce|expand|target)"
    r")\b",
    re.IGNORECASE,
)

# (category, pattern, guarded). Guarded markers get the descriptive escape;
# prescriptive ones flag directly.
_MARKERS = [
    ("causal", _CAUSAL, True),
    ("speculative", _SPECULATIVE, True),
    ("predictive", _PREDICTIVE, True),
    ("prescriptive", _PRESCRIPTIVE, False),
]


def _is_descriptive_context(text: str, match: re.Match) -> bool:
    """True when a guarded marker is describing the data/query, not the world.

    Two escapes, both conservative (prefer NOT flagging clearly-descriptive
    text): the marker's object is data-talk (a number, "rows", "results"), or
    the marker sits beside query-mechanics ("filtered ... because").
    """
    after = text[match.end(): match.end() + _GUARD_WINDOW]
    if _DATA_OBJECT.search(after):
        return True
    around = text[max(0, match.start() - 40): match.end() + _GUARD_WINDOW]
    return bool(_QUERY_MECHANICS.search(around))


def is_overstated(explanation: str, question: str = "") -> OverstatementResult:
    """Scan a generated explanation for editorializing.

    Returns an :class:`OverstatementResult`. ``question`` is accepted for
    caller symmetry and future context-sensitivity; the verdict does not depend
    on it — the data cannot support a world-claim regardless of what was asked
    (asking "why did sales drop?" does not license inventing a cause).
    """
    text = explanation or ""
    triggers: list = []
    for category, pattern, guarded in _MARKERS:
        for match in pattern.finditer(text):
            if guarded and _is_descriptive_context(text, match):
                continue
            triggers.append((category, match.group(0).strip()))
    return OverstatementResult(overstated=bool(triggers), triggers=triggers)


def describe_result_plainly(
    rows: list,
    columns: list,
    row_count: int,
    user_query: str = "",
) -> str:
    """Deterministic, strictly-descriptive rendering of a result set.

    Last-resort fallback when generation editorializes twice. It states only
    what the rows literally contain — counts and concrete values — so it can
    never itself be overstated. This is the *only* place a non-model
    description is emitted, and only after one regeneration has failed.
    """
    if not rows or not row_count:
        return "No rows matched the query."

    cols = list(columns) if columns else list(rows[0].keys())

    def _fmt(row) -> str:
        if not isinstance(row, dict):
            return str(row)
        parts = [f"{c} = {row.get(c)}" for c in cols[:4]]
        return "; ".join(parts)

    if row_count == 1:
        return f"The query returned 1 row: {_fmt(rows[0])}."

    return (
        f"The query returned {row_count} rows. "
        f"The first is {_fmt(rows[0])}."
    )

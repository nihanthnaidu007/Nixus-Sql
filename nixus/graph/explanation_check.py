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

Style is only half of faithfulness (M3). ``explanation_matches_result`` is the
OTHER half: a deterministic check that the explanation's factual claims — row
counts and cited data values — actually match the executed result. "Returned 5
rows" when 12 came back, or a cited dollar figure that appears in no returned
row, is a hallucination the style check cannot see. Both checks share the same
governing rule (a false positive that mangles a good explanation is worse than
a missed claim), so the fidelity check is conservative: values the user's own
question mentions (echoed thresholds/filters), derived figures
(averages/differences/percentages), and anything ambiguous all PASS.
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


# --- fidelity: does the explanation match the EXECUTED result? ---------------

# Word-number row counts ("one row", "three records", "no rows").
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# A claim about HOW MANY rows the result contains: "returned 12 rows",
# "one record", "no results", "zero matches", "a single row". Deliberately
# excludes claims about other nouns ("three categories") — only row counts are
# checkable against ``row_count``.
_ROW_COUNT_CLAIM = re.compile(
    r"\b(?:(?P<neg>no|zero)|(?P<num>\d{1,3}(?:,\d{3})+|\d+|"
    + "|".join(_WORD_NUMBERS)
    + r"))\s+(?P<kind>rows?|records?|results?|entr(?:y|ies)|items?|matches?)\b"
    r"|\ba\s+single\s+(?:rows?|records?|results?|entr(?:y|ies)|items?|matches?)\b",
    re.IGNORECASE,
)

# Selector phrases ("top 5 rows", "the first 3 records") describe a SUBSET of
# the result, not its size — never a row-count claim.
_SELECTOR_BEFORE = re.compile(r"\b(?:top|first|next|showing|last)\s+(?:the\s+)?$", re.IGNORECASE)

# Values styled like DATA (currency, thousands-grouped, or decimal). Bare
# integers are excluded on purpose: row counts, years, and "top 5" selectors
# are integers too, and flagging those would be a false-positive machine.
_CITED_VALUE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b"
)

# A sentence containing a derived figure (average, difference, percentage, …)
# cannot be checked value-for-value — the derivation is legitimately absent
# from the rows. Skip the value check for that whole sentence.
_DERIVED_FIGURE = re.compile(
    r"\b(averag\w*|mean|median|differ\w*|combined|sum\b|growth"
    r"|percent\w*|ratio)\b|%",
    re.IGNORECASE,
)


@dataclass
class FidelityResult:
    """Verdict from ``explanation_matches_result``.

    ``consistent`` is the boolean the caller acts on; ``problems`` names each
    mismatch in plain language so the stricter regeneration can quote it back.
    """

    consistent: bool
    problems: list = field(default_factory=list)


def _parse_count(token: str) -> int:
    token = token.lower().replace(",", "")
    if token in _WORD_NUMBERS:
        return _WORD_NUMBERS[token]
    return int(token)


def _cell_values(rows: list) -> tuple[set, set]:
    """Numeric and string renderings of every cell in the executed rows.

    Numeric cells are compared at 2-decimal precision (money); string cells are
    kept verbatim so a cited figure that only matches textually still counts.
    """
    numbers: set = set()
    strings: set = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numbers.add(round(float(value), 2))
            strings.add(str(value))
            try:  # numeric-looking strings ("1850.0", "12.34")
                numbers.add(round(float(str(value).replace(",", "")), 2))
            except ValueError:
                pass
    return numbers, strings


# Numbers embedded in string cells ("$1,200", "12.34 USD"), extracted as
# whole tokens so a cited figure can only ever match its own number — never
# a larger one that merely contains the same digits ("0.99" vs "10.99").
_CELL_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")


def _bounded_number_matches(cited_raw: str, cell: str) -> bool:
    """True when the cited figure occurs in ``cell`` as a standalone number.

    Boundary-aware replacement for plain substring containment, which passed
    a cited ``0.99`` because a cell held ``10.99`` (same digits, wrong
    boundary). Each side is normalized the same way the numeric-cell set is
    (strip $/commas, 2-decimal money precision), which also equates
    trailing-zero drift ("0.99" ↔ "0.9900"). A token that will not parse is
    skipped — the caller's conservative fail-open rule then decides.
    """
    try:
        cited = round(float(cited_raw), 2)
    except ValueError:
        return False
    for match in _CELL_NUMBER.finditer(cell):
        candidate = match.group(0).lstrip("$").replace(",", "")
        try:
            if round(float(candidate), 2) == cited:
                return True
        except ValueError:
            continue
    return False


def _value_matches(token: str, numbers: set, strings: set) -> bool:
    raw = token.replace("$", "").replace(",", "").strip()
    try:
        cited = round(float(raw), 2)
    except ValueError:
        return False
    if cited in numbers:
        return True
    # String cells: match the cited figure only as a bounded number in the
    # cell. Plain containment would let "0.99" pass because a cell holds
    # "10.99"; the bounded comparison still equates decimal drift ("1850" vs
    # a cell rendered "1850.0", "0.99" vs "0.9900") via 2-decimal rounding.
    return any(_bounded_number_matches(raw, s) for s in strings)


def explanation_matches_result(
    explanation: str,
    rows: list,
    columns: list,
    row_count: int,
    question: str = "",
) -> FidelityResult:
    """Verify the explanation's factual claims against the executed result.

    Two claim types are checkable deterministically:

    1. ROW-COUNT claims ("returned 5 rows", "no results") must equal
       ``row_count``;
    2. DATA-VALUE citations ($ figures, thousands-grouped or decimal numbers)
       must appear among the executed rows' values.

    Everything else PASSES (the governing rule): integers without currency or
    grouping (years, "top 5"), values the user's own question mentions (echoed
    filters), figures in a derivation-signaling context
    (average/difference/percentage), explanations of an EMPTY result (nothing
    to match against; a cited threshold describes the query, not a row), and
    any claim shape not matched by the two patterns above.

    ``columns`` is accepted for caller symmetry with the other backstops; the
    verdict does not depend on it — row counts and cell values already cover
    the checkable claims (a named-column check would false-positive on
    question phrasing the result merely aliases).
    """
    text = explanation or ""
    if not text.strip():
        return FidelityResult(consistent=True)

    problems: list = []

    # 1. Row-count claims. Record the spans that were actually checked so the
    # value pass can skip them ("returned 1,200 rows" is a claim, not a value).
    claim_spans: list = []
    for match in _ROW_COUNT_CLAIM.finditer(text):
        if _SELECTOR_BEFORE.search(text[: match.start()]):
            continue  # "the top 3 rows" — a subset, not the result size
        claim_spans.append(match.span())
        if match.group("num") is None and match.group("neg") is None:
            claimed, claimed_str, kind = 1, "a single", "row"
        else:
            claimed_no_rows = bool(match.group("neg"))
            claimed = 0 if claimed_no_rows else _parse_count(match.group("num"))
            claimed_str = "no" if claimed_no_rows else str(claimed)
            kind = match.group("kind")
        if claimed != row_count:
            problems.append(
                f"says '{claimed_str} {kind}' but the query "
                f"returned {row_count} row{'s' if row_count != 1 else ''}"
            )

    # 2. Cited data values. Skipped entirely for an empty result: with no rows
    # there is nothing to match against, and a cited threshold ("no tracks
    # above $0.99") describes the query, not a row.
    if row_count > 0:
        numbers, strings = _cell_values(rows)
        question_normalized = question.replace("$", "").replace(",", "")
        for token_match in _CITED_VALUE.finditer(text):
            start, _end = token_match.span()
            if any(s <= start < e for s, e in claim_spans):
                continue  # a row-count claim, already verified in pass 1
            # A figure near a derivation signal (average, difference, …) is
            # legitimately absent from the rows — the window approximates the
            # containing sentence.
            window = text[max(0, start - 80): start + 80]
            if _DERIVED_FIGURE.search(window):
                continue
            token = token_match.group(0).strip()
            raw = token.replace("$", "").replace(",", "").strip()
            if raw and raw in question_normalized:
                continue  # the user's own figure, echoed back
            if not _value_matches(token, numbers, strings):
                problems.append(
                    f"cites {token}, which does not appear in the returned rows"
                )

    return FidelityResult(consistent=not problems, problems=problems)


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

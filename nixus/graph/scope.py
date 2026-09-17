"""Scope classification for the graph entry.

Pure, unit-testable logic that decides whether an input is an in-scope data
question, needs clarification, is out of scope, or is a (refused) write request.

THE GOVERNING RULE: err toward ACCEPTING input as in-scope. Refuse ONLY what is
confidently NOT a data question. When unsure whether something is a question or
noise, prefer NEEDS_CLARIFICATION (ask) over OUT_OF_SCOPE (refuse) — asking is
recoverable, refusing is a dead end.

This module does NO network/DB/LLM work. The conservative regex fast-path and
the deterministic write detector live here and run with zero token cost; the
LLM classification *prompt* and *result handling* are pure helpers here, while
the actual model call lives in the thin node (``scope_classifier.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ScopeCategory(str, Enum):
    IN_SCOPE = "IN_SCOPE"                      # a real question about the data
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"  # ambiguous; ask a follow-up
    OUT_OF_SCOPE = "OUT_OF_SCOPE"              # confidently not a database question
    WRITE_REFUSAL = "WRITE_REFUSAL"            # a request to modify data (read-only)


@dataclass
class ScopeResult:
    category: ScopeCategory
    reason: str = ""          # for OUT_OF_SCOPE / WRITE_REFUSAL
    clarification: str = ""   # for NEEDS_CLARIFICATION


# ── Canned messages surfaced to the user ─────────────────────────────────────
OUT_OF_SCOPE_MESSAGE = (
    "I can only answer questions about the data in this database. That input "
    "doesn't look like a data question — try asking about your tables, rows, "
    "or metrics."
)
WRITE_REFUSAL_MESSAGE = (
    "NIXUS is read-only. I can query and analyze the data, but I can't insert, "
    "update, delete, or otherwise modify it."
)
CLARIFY_FALLBACK = (
    "Could you rephrase that as a specific question about the data? For "
    "example, name the table, metric, or filter you're interested in."
)
AMBIGUOUS_TERMINATION_MESSAGE = (
    "I asked for clarification but still couldn't determine what you're looking "
    "for. Please start a new question that names the specific table, metric, or "
    "filter you want."
)

# After this many clarification rounds of continued ambiguity the server stops
# asking and returns a clean refusal (REFUSED_AMBIGUOUS). Bounds the loop the
# original spec forgot to terminate.
CLARIFICATION_ROUND_CAP = 2


# Response-outcome discriminators (returned in the /api/v1 JSON).
OUTCOME_ANSWERED = "ANSWERED"
OUTCOME_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
OUTCOME_REFUSED_OUT_OF_SCOPE = "REFUSED_OUT_OF_SCOPE"
OUTCOME_REFUSED_WRITE = "REFUSED_WRITE"
OUTCOME_REFUSED_AMBIGUOUS = "REFUSED_AMBIGUOUS"


# ── Conservative regex fast-path ─────────────────────────────────────────────
# Catches ONLY unambiguous non-questions. When in doubt, returns None so the
# input is deferred to the LLM classifier. It exists to save a token on obvious
# junk, NOT to make subtle refusal decisions.

_FENCED_CODE = re.compile(r"```")
_TRACEBACK = re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE)
_PY_FILE_LINE = re.compile(r'^\s*File ".+", line \d+', re.MULTILINE)
_JS_STACK = re.compile(r"^\s*at\s+\S.*\(.*:\d+(?::\d+)?\)\s*$", re.MULTILINE)
# Exception class line, e.g. "ValueError: bad" / "java.lang.NullPointerException:"
_EXCEPTION_LINE = re.compile(
    r"^\s*(Exception in thread|[A-Za-z_][\w.]*(Error|Exception)\b\s*:)",
    re.MULTILINE,
)
# Log lines: ISO timestamp + level, or a line that *starts* with a bare level.
_LOG_LINE = re.compile(
    r"(?:^|\n)[ \t]*(?:"
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*\b(?:ERROR|WARN|WARNING|INFO|DEBUG|FATAL|CRITICAL)\b"
    r"|\[(?:ERROR|WARN|WARNING|FATAL|CRITICAL|DEBUG|INFO)\]"
    r"|(?:ERROR|WARN|WARNING|FATAL|CRITICAL)::?\s"
    r")"
)
# Shell commands that essentially never begin an English data question.
# Deliberately conservative: tokens that double as ordinary English words
# (make, service, brew, poetry, …) are EXCLUDED so a real question is never
# mistaken for a command.
_SHELL_CMDS = (
    "docker", "docker-compose", "git", "sudo", "npm", "npx", "pnpm", "yarn",
    "pip", "pip3", "curl", "wget", "kubectl", "helm", "ssh", "scp", "chmod",
    "chown", "mkdir", "rmdir", "systemctl", "apt", "apt-get", "yum", "dnf",
    "virtualenv", "uvicorn", "gunicorn", "terraform", "ansible",
)
_SHELL_LINE = re.compile(
    r"^[ \t]*(?:\$[ \t]*|#[ \t]*|>[ \t]*)?(?:" + "|".join(_SHELL_CMDS) + r")\b[ \t]+\S",
    re.IGNORECASE | re.MULTILINE,
)


def regex_prefilter(text: str) -> ScopeCategory | None:
    """Return OUT_OF_SCOPE for UNAMBIGUOUS non-questions; None for anything that
    could be natural language (defer to the LLM classifier).

    Conservative by design: when in doubt, returns None.
    """
    if not text or not text.strip():
        return None
    if _FENCED_CODE.search(text):
        return ScopeCategory.OUT_OF_SCOPE
    if (
        _TRACEBACK.search(text)
        or _PY_FILE_LINE.search(text)
        or _JS_STACK.search(text)
        or _EXCEPTION_LINE.search(text)
    ):
        return ScopeCategory.OUT_OF_SCOPE
    if _LOG_LINE.search(text):
        return ScopeCategory.OUT_OF_SCOPE
    if _SHELL_LINE.search(text):
        return ScopeCategory.OUT_OF_SCOPE
    return None


# ── Deterministic write detection ────────────────────────────────────────────
# Catches the clear DML/DDL forms with zero false positives on data questions
# (no in-scope question begins with one of these imperative verbs). Anything
# subtler is left to the LLM classifier, which can also return WRITE_REFUSAL.
_WRITE_VERBS = (
    "delete", "remove", "drop", "truncate", "insert", "update", "alter",
    "wipe", "purge", "erase", "rename", "destroy",
)
_WRITE_LEAD = re.compile(
    r"^\s*(?:please\s+|kindly\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|"
    r"i\s+want\s+to\s+|i'?d\s+like\s+(?:to|you\s+to)\s+|let'?s\s+|"
    r"go\s+ahead\s+and\s+|now\s+)*"
    r"(?:" + "|".join(_WRITE_VERBS) + r")\b",
    re.IGNORECASE,
)
_SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
    r"DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|FUNCTION|TRIGGER|SEQUENCE)|"
    r"TRUNCATE\s+(?:TABLE\s+)?\w|ALTER\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)|"
    r"CREATE\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)|REPLACE\s+INTO|MERGE\s+INTO)\b",
    re.IGNORECASE,
)


def detect_write_request(text: str) -> bool:
    """True iff ``text`` is clearly a request to modify data/schema."""
    if not text:
        return False
    return bool(_WRITE_LEAD.match(text) or _SQL_WRITE.search(text))


def classify_scope(text: str, schema_context: str | None = None) -> ScopeResult | None:
    """Deterministic fast-path. Returns a confident ScopeResult for inputs that
    can be decided WITHOUT an LLM (regex junk → OUT_OF_SCOPE, clear write
    request → WRITE_REFUSAL), or None to defer to the LLM classifier.

    Never returns IN_SCOPE / NEEDS_CLARIFICATION — that judgement needs the LLM.
    """
    if regex_prefilter(text) is ScopeCategory.OUT_OF_SCOPE:
        return ScopeResult(ScopeCategory.OUT_OF_SCOPE, reason=OUT_OF_SCOPE_MESSAGE)
    if detect_write_request(text):
        return ScopeResult(ScopeCategory.WRITE_REFUSAL, reason=WRITE_REFUSAL_MESSAGE)
    return None


# ── LLM classifier (pure prompt + result handling) ───────────────────────────
CLASSIFIER_SYSTEM_PROMPT = """You are the scope gate for NIXUS, a read-only \
natural-language-to-SQL assistant over a relational database.

{schema}

Classify the USER INPUT into EXACTLY ONE category:

- IN_SCOPE: any plausible question about the data — retrieving, counting, \
aggregating, ranking, filtering, or comparing rows; asking what tables/columns \
exist or how the data is structured. Terse questions ("revenue by month?", \
"top 5 artists") are IN_SCOPE. Trailing run/test tokens or timestamps are noise \
— ignore them and judge the underlying question.
- NEEDS_CLARIFICATION: it looks like a data question but is too vague to act on \
("show me the top ones", "what about last year"). Suggest a short follow-up.
- OUT_OF_SCOPE: clearly NOT about this database — general chit-chat, a coding \
question, an error/stack-trace/log dump, or shell/config text.
- WRITE_REFUSAL: a request to INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or \
otherwise modify data or schema. NIXUS is read-only.

CRITICAL BIAS: prefer IN_SCOPE. A real but terse question is IN_SCOPE, never \
OUT_OF_SCOPE. When uncertain between IN_SCOPE and OUT_OF_SCOPE, choose \
NEEDS_CLARIFICATION. NEVER refuse a plausible data question.

USER INPUT:
{query}

Respond ONLY with valid JSON, no markdown:
{{"category": "IN_SCOPE|NEEDS_CLARIFICATION|OUT_OF_SCOPE|WRITE_REFUSAL", \
"clarification": "a follow-up question (only if NEEDS_CLARIFICATION, else \"\")", \
"reason": "one short sentence (only if OUT_OF_SCOPE, else \"\")"}}"""


def build_classifier_prompt(user_text: str, schema_context: str | None = None) -> str:
    """Render the classifier prompt, embedding whatever schema context exists."""
    schema = (
        f"AVAILABLE DATA:\n{schema_context}"
        if schema_context
        else "AVAILABLE DATA: a relational SQL database of business records."
    )
    return CLASSIFIER_SYSTEM_PROMPT.format(schema=schema, query=user_text)


def result_from_llm(category: str, clarification: str = "", reason: str = "") -> ScopeResult:
    """Normalize a raw LLM category string into a ScopeResult.

    Fail-open: any unrecognized/garbled category defaults to IN_SCOPE so a parse
    error never turns into a refusal.
    """
    try:
        cat = ScopeCategory(str(category).strip().upper())
    except (ValueError, AttributeError):
        return ScopeResult(ScopeCategory.IN_SCOPE)
    if cat is ScopeCategory.NEEDS_CLARIFICATION:
        return ScopeResult(cat, clarification=clarification or CLARIFY_FALLBACK)
    if cat is ScopeCategory.OUT_OF_SCOPE:
        return ScopeResult(cat, reason=reason or OUT_OF_SCOPE_MESSAGE)
    if cat is ScopeCategory.WRITE_REFUSAL:
        return ScopeResult(cat, reason=WRITE_REFUSAL_MESSAGE)
    return ScopeResult(ScopeCategory.IN_SCOPE)


# ── Stateless clarification round-trip (Option B) ────────────────────────────
def build_clarified_query(user_query: str, clarification_context: dict | None) -> str:
    """Fold a follow-up's prior clarification context into a single self-contained
    question for re-classification (and, when resolved, for generation).

    ``clarification_context`` shape: {original_question: str,
    prior_clarifications: [{question, answer}, ...]}. The list already includes
    the user's latest answer (the current request's user_query echoes it), so it
    is not appended a second time.

    With no context this returns ``user_query`` verbatim — the normal single-turn
    path is unchanged.
    """
    if not clarification_context:
        return user_query
    parts: list[str] = []
    original = (clarification_context.get("original_question") or user_query or "").strip()
    if original:
        parts.append(original)
    for pc in clarification_context.get("prior_clarifications") or []:
        question = (pc.get("question") or "").strip()
        answer = (pc.get("answer") or "").strip()
        if question:
            parts.append(f"Clarifying question: {question}")
        if answer:
            parts.append(f"Answer: {answer}")
    return "\n".join(parts) if parts else user_query


def effective_clarification_round(clarification_round: int, clarification_context: dict | None) -> int:
    """The round count the SERVER trusts for termination. Defends against a client
    that mismanages the counter by also counting the clarifications already
    carried in the context — whichever is larger wins."""
    prior = 0
    if clarification_context:
        prior = len(clarification_context.get("prior_clarifications") or [])
    return max(int(clarification_round or 0), prior)


def outcome_for(category: ScopeCategory, effective_round: int) -> str:
    """Map a scope category + the effective round to a response outcome.

    NEEDS_CLARIFICATION becomes REFUSED_AMBIGUOUS once the round cap is reached —
    this is where the loop terminates, enforced server-side.
    """
    if category is ScopeCategory.IN_SCOPE:
        return OUTCOME_ANSWERED
    if category is ScopeCategory.OUT_OF_SCOPE:
        return OUTCOME_REFUSED_OUT_OF_SCOPE
    if category is ScopeCategory.WRITE_REFUSAL:
        return OUTCOME_REFUSED_WRITE
    # NEEDS_CLARIFICATION
    if effective_round >= CLARIFICATION_ROUND_CAP:
        return OUTCOME_REFUSED_AMBIGUOUS
    return OUTCOME_NEEDS_CLARIFICATION

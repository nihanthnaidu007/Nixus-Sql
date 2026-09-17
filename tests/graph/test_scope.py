"""Scope-classifier tests — the false-positive guard for the graph entry.

THE GOVERNING RULE under test: err toward ACCEPTING input as in-scope. The
must-accept cases below are real (if terse) data questions; they must NEVER be
refused. The must-refuse cases are unambiguous non-questions or write requests.

The deterministic core (regex fast-path + write detection + result plumbing)
runs with zero network cost and is the must-pass guard. A small set of
LLM-backed tests (skipped without an API key) exercises the IN_SCOPE bias and
the NEEDS_CLARIFICATION judgement that only the model can make.
"""
import os

import pytest

from nixus.config import is_placeholder
from nixus.graph.scope import (
    ScopeCategory,
    build_classifier_prompt,
    classify_scope,
    detect_write_request,
    regex_prefilter,
    result_from_llm,
)

# Real questions about the data — the false-positive guard. NEVER OUT_OF_SCOPE.
MUST_ACCEPT = [
    "revenue by month?",                          # terse
    "what columns does the customers table have?",  # schema question
    "top 5 artists by total invoice revenue",     # jargon-y but valid
    "show me the tracks table",                   # mentions a table name
]

# Unambiguous non-questions the regex fast-path must catch (no LLM needed).
REGEX_JUNK = {
    "docker_compose_error": (
        "version: '3.8'\nservices:\n  db:\n    image: postgres\n"
        "ERROR: port is already allocated"
    ),
    "shell_command": "docker compose up -d",
    "python_traceback": (
        'Traceback (most recent call last):\n  File "x.py", line 3\nValueError: bad'
    ),
    "fenced_code_block": "```python\nprint('hello')\n```",
    "js_stack_frame": "    at Object.<anonymous> (/app/index.js:10:15)",
    "log_line": "2024-01-02 10:00:00 ERROR connection refused",
}

# Requests to modify data → WRITE_REFUSAL (NIXUS is read-only).
WRITE_REQUESTS = [
    "delete all customers",
    "drop the orders table",
    "drop table invoices",
    "update tracks set name='x'",
    "truncate the invoice table",
]


# ── Regex fast-path: must DEFER (None) on anything that could be language ─────
@pytest.mark.parametrize("q", MUST_ACCEPT)
def test_regex_defers_on_natural_language(q):
    """The fast-path must not refuse natural language — it returns None so the
    LLM classifier decides."""
    assert regex_prefilter(q) is None


@pytest.mark.parametrize("q", WRITE_REQUESTS)
def test_regex_defers_on_write_requests(q):
    """Write requests are natural language, not regex junk — the fast-path
    defers them (write detection handles them, not the junk filter)."""
    assert regex_prefilter(q) is None


# ── Regex fast-path: must CATCH unambiguous junk ─────────────────────────────
@pytest.mark.parametrize("name,text", list(REGEX_JUNK.items()))
def test_regex_catches_unambiguous_junk(name, text):
    assert regex_prefilter(text) is ScopeCategory.OUT_OF_SCOPE


# ── Deterministic classify_scope: must-accept is never refused ───────────────
@pytest.mark.parametrize("q", MUST_ACCEPT)
def test_classify_scope_never_refuses_accept(q):
    result = classify_scope(q)
    # The deterministic path defers a plausible question to the LLM (None) and
    # NEVER returns a refusal category for it.
    assert result is None or result.category not in (
        ScopeCategory.OUT_OF_SCOPE,
        ScopeCategory.WRITE_REFUSAL,
    )
    assert result is None  # specifically: deferred, not decided


# ── Deterministic write detection ────────────────────────────────────────────
@pytest.mark.parametrize("q", WRITE_REQUESTS)
def test_write_requests_classified_write_refusal(q):
    result = classify_scope(q)
    assert result is not None
    assert result.category is ScopeCategory.WRITE_REFUSAL


@pytest.mark.parametrize("q", MUST_ACCEPT + [
    "create a chart of revenue by month",
    "show me the top ones",
    "list customers from Brazil",
    "which tracks have never been purchased?",
])
def test_write_detection_no_false_positive_on_reads(q):
    assert detect_write_request(q) is False


# ── The motivating failure: a docker-compose paste must be OUT_OF_SCOPE ───────
def test_docker_compose_paste_out_of_scope():
    result = classify_scope(REGEX_JUNK["docker_compose_error"])
    assert result is not None
    assert result.category is ScopeCategory.OUT_OF_SCOPE
    assert result.reason  # a user-facing reason is attached


# ── LLM result plumbing (pure) ───────────────────────────────────────────────
def test_result_from_llm_maps_categories():
    assert result_from_llm("IN_SCOPE").category is ScopeCategory.IN_SCOPE
    assert result_from_llm("needs_clarification").category is ScopeCategory.NEEDS_CLARIFICATION
    assert result_from_llm("OUT_OF_SCOPE").category is ScopeCategory.OUT_OF_SCOPE
    assert result_from_llm("WRITE_REFUSAL").category is ScopeCategory.WRITE_REFUSAL


def test_result_from_llm_fails_open_to_in_scope():
    # A garbled/unknown category must NEVER become a refusal.
    assert result_from_llm("???").category is ScopeCategory.IN_SCOPE
    assert result_from_llm("").category is ScopeCategory.IN_SCOPE
    assert result_from_llm(None).category is ScopeCategory.IN_SCOPE


def test_result_from_llm_clarification_payload():
    r = result_from_llm("NEEDS_CLARIFICATION", clarification="Which metric?")
    assert r.clarification == "Which metric?"
    # Falls back to a non-empty prompt when the model omits one.
    assert result_from_llm("NEEDS_CLARIFICATION").clarification


def test_prompt_embeds_query_and_schema():
    prompt = build_classifier_prompt("revenue?", "Tables available: Invoice, Track")
    assert "revenue?" in prompt
    assert "Invoice" in prompt
    # Bias instruction is present.
    assert "IN_SCOPE" in prompt


# ── LLM-backed behaviour (requires an API key; skipped otherwise) ────────────
_NEEDS_KEY = pytest.mark.skipif(
    is_placeholder(os.getenv("ANTHROPIC_API_KEY")),
    reason="needs a REAL ANTHROPIC_API_KEY for live classification",
)
_SCHEMA = (
    "Tables available: Artist, Album, Track, Genre, Customer, Invoice, "
    "InvoiceLine, Employee, Playlist, PlaylistTrack, MediaType"
)


@_NEEDS_KEY
@pytest.mark.parametrize("q", MUST_ACCEPT)
async def test_llm_never_refuses_real_questions(q):
    from nixus.graph.nodes.scope_classifier import classify_query
    result = await classify_query(q, _SCHEMA)
    # IN_SCOPE, or at worst NEEDS_CLARIFICATION — NEVER OUT_OF_SCOPE/WRITE_REFUSAL.
    assert result.category in (
        ScopeCategory.IN_SCOPE,
        ScopeCategory.NEEDS_CLARIFICATION,
    )


@_NEEDS_KEY
async def test_llm_clarifies_genuinely_ambiguous():
    from nixus.graph.nodes.scope_classifier import classify_query
    result = await classify_query("show me the top ones", _SCHEMA)
    assert result.category is ScopeCategory.NEEDS_CLARIFICATION


@_NEEDS_KEY
async def test_llm_refuses_clear_non_data():
    from nixus.graph.nodes.scope_classifier import classify_query
    result = await classify_query("what's your favorite movie?", _SCHEMA)
    assert result.category is ScopeCategory.OUT_OF_SCOPE

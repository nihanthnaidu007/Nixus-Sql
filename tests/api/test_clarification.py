"""Stateless clarification round-trip + termination tests (Phase 4.2).

The clarification flow is request/response (Option B) — no interrupt, no paused
session. These tests exercise the scope-classifier node (state in / state out) and
the pure decision helpers, so the round-trip and the termination cap are proven
deterministically. A few LLM-backed cases (skipped without an API key) confirm the
real classifier asks once, resolves on an answer, and the normal path is unchanged.
"""
import os

import pytest

import nixus.graph.nodes.scope_classifier as sc
from nixus.config import is_placeholder
from nixus.graph.nodes.scope_classifier import scope_classifier_node
from nixus.graph.scope import (
    CLARIFICATION_ROUND_CAP,
    ScopeCategory,
    ScopeResult,
    build_clarified_query,
    effective_clarification_round,
    outcome_for,
)


def _state(user_query, **extra):
    """Minimal graph state for driving scope_classifier_node directly."""
    s = {
        "user_query": user_query,
        "clarification_context": None,
        "clarification_round": 0,
        "completed_nodes": [],
        "stream_updates": [],
        "schema_context": "",
    }
    s.update(extra)
    return s


def _fake_classifier(result: ScopeResult):
    async def _fake(text, schema_context=""):
        return result
    return _fake


# ── Pure decision logic (deterministic, no network) ─────────────────────────
def test_outcome_mapping_and_cap():
    assert outcome_for(ScopeCategory.IN_SCOPE, 0) == "ANSWERED"
    assert outcome_for(ScopeCategory.OUT_OF_SCOPE, 0) == "REFUSED_OUT_OF_SCOPE"
    assert outcome_for(ScopeCategory.WRITE_REFUSAL, 0) == "REFUSED_WRITE"
    # Ambiguity is asked below the cap, refused at/above it.
    assert outcome_for(ScopeCategory.NEEDS_CLARIFICATION, 0) == "NEEDS_CLARIFICATION"
    assert outcome_for(ScopeCategory.NEEDS_CLARIFICATION, CLARIFICATION_ROUND_CAP - 1) == "NEEDS_CLARIFICATION"
    assert outcome_for(ScopeCategory.NEEDS_CLARIFICATION, CLARIFICATION_ROUND_CAP) == "REFUSED_AMBIGUOUS"
    assert outcome_for(ScopeCategory.NEEDS_CLARIFICATION, CLARIFICATION_ROUND_CAP + 5) == "REFUSED_AMBIGUOUS"


def test_effective_round_defends_against_bad_counter():
    # Client says round 0 but context already carries 2 answered clarifications.
    ctx = {"original_question": "q", "prior_clarifications": [
        {"question": "a?", "answer": "x"}, {"question": "b?", "answer": "y"}]}
    assert effective_clarification_round(0, ctx) == 2
    assert effective_clarification_round(5, ctx) == 5  # counter wins when larger


def test_build_clarified_query_folds_context_and_is_passthrough_when_absent():
    assert build_clarified_query("revenue by month?", None) == "revenue by month?"
    ctx = {"original_question": "show me the top ones",
           "prior_clarifications": [{"question": "top what?", "answer": "artists by revenue"}]}
    combined = build_clarified_query("artists by revenue", ctx)
    assert "show me the top ones" in combined
    assert "artists by revenue" in combined


# ── Node: ambiguous fresh query asks once, generates no SQL ──────────────────
async def test_fresh_ambiguous_asks_for_clarification(monkeypatch):
    monkeypatch.setattr(sc, "classify_query",
                        _fake_classifier(ScopeResult(ScopeCategory.NEEDS_CLARIFICATION,
                                                     clarification="Top what — artists, tracks?")))
    state = await scope_classifier_node(_state("show me the top ones"))
    assert state["outcome"] == "NEEDS_CLARIFICATION"
    assert state["clarifying_question"]            # non-empty question
    assert not state.get("generated_sql")          # no SQL generated at the gate
    assert state["scope_category"] != "IN_SCOPE"   # routes to scope_response, not generation


# ── Node: a follow-up that resolves proceeds to ANSWERED ─────────────────────
async def test_followup_resolves_to_answered(monkeypatch):
    monkeypatch.setattr(sc, "classify_query",
                        _fake_classifier(ScopeResult(ScopeCategory.IN_SCOPE)))
    ctx = {"original_question": "show me the top ones",
           "prior_clarifications": [{"question": "top what?", "answer": "artists by total revenue"}]}
    state = await scope_classifier_node(_state("artists by total revenue",
                                               clarification_context=ctx, clarification_round=1))
    assert state["outcome"] == "ANSWERED"
    # The resolved (combined) question flows downstream to generation, not just the answer.
    assert "show me the top ones" in state["user_query"]
    assert "artists by total revenue" in state["user_query"]


# ── Node: termination at the cap (server-enforced) ──────────────────────────
async def test_termination_at_cap_refuses_instead_of_asking(monkeypatch):
    monkeypatch.setattr(sc, "classify_query",
                        _fake_classifier(ScopeResult(ScopeCategory.NEEDS_CLARIFICATION,
                                                     clarification="still unclear?")))
    ctx = {"original_question": "show me the top ones",
           "prior_clarifications": [{"question": "q1", "answer": "a1"},
                                    {"question": "q2", "answer": "a2"}]}
    state = await scope_classifier_node(_state("a2", clarification_context=ctx,
                                               clarification_round=CLARIFICATION_ROUND_CAP))
    assert state["outcome"] == "REFUSED_AMBIGUOUS"
    assert not state["clarifying_question"]   # does NOT ask again
    assert state["reason"]                     # carries a clear termination reason


async def test_defensive_cap_when_client_never_increments(monkeypatch):
    # Client keeps sending round=0 but context shows 2 prior clarifications.
    monkeypatch.setattr(sc, "classify_query",
                        _fake_classifier(ScopeResult(ScopeCategory.NEEDS_CLARIFICATION,
                                                     clarification="still unclear?")))
    ctx = {"original_question": "show me the top ones",
           "prior_clarifications": [{"question": "q1", "answer": "a1"},
                                    {"question": "q2", "answer": "a2"}]}
    state = await scope_classifier_node(_state("a2", clarification_context=ctx,
                                               clarification_round=0))
    assert state["outcome"] == "REFUSED_AMBIGUOUS"   # server uses len(prior), not the bogus counter


# ── Node: refusals carry their reasons (deterministic, no LLM) ──────────────
async def test_out_of_scope_refused():
    state = await scope_classifier_node(_state("docker compose up -d"))
    assert state["outcome"] == "REFUSED_OUT_OF_SCOPE"
    assert state["reason"]
    assert not state.get("generated_sql")


async def test_write_request_refused():
    state = await scope_classifier_node(_state("delete all customers"))
    assert state["outcome"] == "REFUSED_WRITE"
    assert "read-only" in state["reason"].lower()


# ── Node: the normal single-turn path is unchanged (benchmark guard) ────────
async def test_normal_in_scope_is_answered_and_unmodified(monkeypatch):
    monkeypatch.setattr(sc, "classify_query",
                        _fake_classifier(ScopeResult(ScopeCategory.IN_SCOPE)))
    state = await scope_classifier_node(_state("How many tracks does each genre have?"))
    assert state["outcome"] == "ANSWERED"
    assert state["scope_category"] == "IN_SCOPE"
    assert state["user_query"] == "How many tracks does each genre have?"  # not rewritten
    assert state["clarifying_question"] == ""
    assert state["reason"] == ""


# ── LLM-backed (skipped without an API key) ─────────────────────────────────
_NEEDS_KEY = pytest.mark.skipif(
    is_placeholder(os.getenv("ANTHROPIC_API_KEY")),
    reason="needs a REAL ANTHROPIC_API_KEY for live classification",
)
_SCHEMA = ("Tables available: Artist, Album, Track, Genre, Customer, Invoice, "
           "InvoiceLine, Employee, Playlist, PlaylistTrack, MediaType")


@_NEEDS_KEY
async def test_llm_ambiguous_then_resolved_roundtrip():
    from nixus.graph.nodes.scope_classifier import classify_query
    # Fresh ambiguous question → asks.
    first = await classify_query("show me the top ones", _SCHEMA)
    assert first.category is ScopeCategory.NEEDS_CLARIFICATION
    assert first.clarification
    # The combined follow-up resolves it → in-scope.
    combined = build_clarified_query(
        "top artists by total revenue",
        {"original_question": "show me the top ones",
         "prior_clarifications": [{"question": first.clarification,
                                   "answer": "top artists by total revenue"}]},
    )
    resolved = await classify_query(combined, _SCHEMA)
    assert resolved.category is ScopeCategory.IN_SCOPE


@_NEEDS_KEY
async def test_llm_normal_question_is_in_scope():
    from nixus.graph.nodes.scope_classifier import classify_query
    result = await classify_query("How many tracks does each genre have?", _SCHEMA)
    assert result.category is ScopeCategory.IN_SCOPE

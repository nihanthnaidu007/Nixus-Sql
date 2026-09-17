"""Graph-entry scope classifier (thin node over ``nixus.graph.scope``).

Decides, before any retrieval or generation, whether the user's input is an
in-scope data question (IN_SCOPE → continues into the existing flow), needs
clarification, is out of scope, or is a refused write request. The deterministic
fast-path (regex junk / clear write request) short-circuits without an LLM call;
everything else defers to a single small LLM classification biased toward
IN_SCOPE / NEEDS_CLARIFICATION.
"""
import logging
from datetime import datetime

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from nixus.config import settings
from nixus.db.schema_store import list_schema_rows
from nixus.graph.scope import (
    AMBIGUOUS_TERMINATION_MESSAGE,
    CLARIFY_FALLBACK,
    OUT_OF_SCOPE_MESSAGE,
    OUTCOME_ANSWERED,
    OUTCOME_NEEDS_CLARIFICATION,
    OUTCOME_REFUSED_AMBIGUOUS,
    ScopeCategory,
    ScopeResult,
    build_clarified_query,
    build_classifier_prompt,
    classify_scope,
    effective_clarification_round,
    outcome_for,
    result_from_llm,
)
from nixus.graph.state import SQLAgentState
from nixus.utils.retry import llm_retry

load_dotenv()

logger = logging.getLogger(__name__)

llm = ChatAnthropic(
    model="claude-haiku-4-5",
    anthropic_api_key=(settings.anthropic_api_key or ""),
    temperature=0.0,
    max_tokens=256,
)


class ScopeClassification(BaseModel):
    category: str
    clarification: str = ""
    reason: str = ""


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def _schema_summary() -> str:
    """Cheap table-name list (no embedding call) so the classifier knows what
    'the data' is. Best-effort: a DB hiccup degrades to an empty summary, which
    the classifier handles by assuming a generic relational database."""
    try:
        rows = await list_schema_rows()
        if rows:
            return "Tables available: " + ", ".join(r["table_name"] for r in rows)
    except Exception as e:  # best-effort context only
        logger.debug("scope classifier schema summary unavailable: %s", e)
    return ""


async def classify_query(text: str, schema_context: str = "") -> ScopeResult:
    """Classify one user input. Deterministic fast-path first, else one LLM call.

    Fail-open: any LLM/parse failure returns IN_SCOPE so a transient error never
    turns a real question into a refusal.
    """
    deterministic = classify_scope(text)
    if deterministic is not None:
        return deterministic

    schema = schema_context or await _schema_summary()
    structured_llm = llm.with_structured_output(ScopeClassification)

    @llm_retry
    async def _call_llm(prompt: str):
        return await structured_llm.ainvoke(prompt)

    try:
        raw = await _call_llm(build_classifier_prompt(text, schema))
        return result_from_llm(raw.category, raw.clarification, raw.reason)
    except Exception as e:  # never refuse on a transient failure
        logger.warning("scope classifier LLM failed (%s) — defaulting IN_SCOPE", e)
        return ScopeResult(ScopeCategory.IN_SCOPE)


async def scope_classifier_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "scope_classifier"

    raw_query = state.get("user_query", "") or ""
    clar_context = state.get("clarification_context")
    clar_round = state.get("clarification_round") or 0

    # Fold any prior clarification context into a single self-contained question.
    # For a fresh single-turn query (no context) this is the query verbatim, so
    # the normal path is unchanged. For a follow-up, downstream generation needs
    # the resolved question, so we rewrite user_query to the combined form.
    combined = build_clarified_query(raw_query, clar_context)
    if clar_context:
        state["user_query"] = combined

    result = await classify_query(combined, state.get("schema_context") or "")

    # Termination is decided here (server-side), not trusted to the client.
    eff_round = effective_clarification_round(clar_round, clar_context)
    outcome = outcome_for(result.category, eff_round)

    state["scope_category"] = result.category.value
    state["outcome"] = outcome

    if outcome == OUTCOME_NEEDS_CLARIFICATION:
        question = result.clarification or CLARIFY_FALLBACK
        state["clarifying_question"] = question
        state["reason"] = ""
        state["scope_message"] = question
    elif outcome == OUTCOME_REFUSED_AMBIGUOUS:
        state["clarifying_question"] = ""
        state["reason"] = AMBIGUOUS_TERMINATION_MESSAGE
        state["scope_message"] = AMBIGUOUS_TERMINATION_MESSAGE
    elif outcome == OUTCOME_ANSWERED:
        state["clarifying_question"] = ""
        state["reason"] = ""
        state["scope_message"] = ""
    else:  # REFUSED_OUT_OF_SCOPE / REFUSED_WRITE
        state["clarifying_question"] = ""
        state["reason"] = result.reason or OUT_OF_SCOPE_MESSAGE
        state["scope_message"] = state["reason"]

    state["completed_nodes"].append("scope_classifier")
    state["stream_updates"].append({
        "timestamp": now(), "node": "scope_classifier",
        "message": f"Scope: {result.category.value} → {outcome}",
        "status": "done",
    })
    return state


async def scope_response_node(state: SQLAgentState) -> SQLAgentState:
    """Terminal for non-ANSWERED outcomes. Surfaces the clarifying question (a
    calm prompt, not an error) or the refusal reason, and completes the run.

    The clarification round-trip is stateless: the run ends here and the client
    re-sends with the answer + accumulated context. No interrupt/checkpointer
    pause is involved.
    """
    state["current_node"] = "scope_response"
    outcome = state.get("outcome", "")
    message = state.get("scope_message", "")

    if outcome == OUTCOME_NEEDS_CLARIFICATION:
        # A question, not a failure — surfaced as the response, no error set.
        state["explanation"] = message or CLARIFY_FALLBACK
        stream_status = "done"
    else:
        # REFUSED_OUT_OF_SCOPE / REFUSED_WRITE / REFUSED_AMBIGUOUS — a definitive
        # (non-fatal) refusal carrying a clear reason.
        refusal = message or OUT_OF_SCOPE_MESSAGE
        state["error"] = refusal
        state["explanation"] = refusal
        stream_status = "error"

    # Make the outcome visible on the intelligence strip (parse_intent never ran).
    state["intent_class"] = outcome or state.get("scope_category") or "OUT_OF_SCOPE"
    state["is_complete"] = True
    state["completed_nodes"].append("scope_response")
    state["stream_updates"].append({
        "timestamp": now(), "node": "scope_response",
        "message": state.get("explanation", ""),
        "status": stream_status,
    })
    return state

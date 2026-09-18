import json
import logging
from datetime import datetime

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

load_dotenv()

from nixus.db.fewshot_store import store_fewshot_example
from nixus.db.query_cache import store_cache_entry
from nixus.graph.explanation_check import (
    describe_result_plainly,
    explanation_matches_result,
    is_overstated,
)
from nixus.graph.state import SQLAgentState
from nixus.utils.confidence import assess_confidence, level_to_score
from nixus.utils.embeddings import embed_text
from nixus.utils.retry import llm_retry

logger = logging.getLogger(__name__)

EXPLAIN_PROMPT = """You describe a SQL query result to a business user in plain, useful language.

Original question: {user_query}
SQL used: {sql}
Result: {result_summary}

Write 1-3 sentences that DESCRIBE what the returned rows actually contain. Be
specific and genuinely informative about THIS result set — not a thin row dump.

DO:
- Lead with the direct answer to the question using the concrete values in the
  rows: the top/bottom item, the key figures, the range across the rows, the
  row count, notable comparisons that are literally present (e.g. how the top
  few cluster, the leader vs. the rest).
- If the result is empty, say plainly that no rows matched. If there is a single
  row, describe just that row. If there are only a few rows, describe them
  without generalizing beyond them.

DO NOT:
- State causes about the world ("because", "due to", "driven by", "led to"). You
  may describe what the query did (e.g. "limited to 2023"), but never assert why
  a real-world number is the way it is.
- Speculate or infer real-world conclusions ("suggests", "implies", "indicates
  that [a business takeaway]", "likely", "probably", "reflects [a trend]").
- Predict or forecast ("will", "expected to", "going to", "trending toward").
- Recommend or advise ("should", "recommend", "consider doing").
- Generalize beyond the returned rows (no claims about all customers / products /
  periods when the result is a narrow slice).

Describe the result accurately and richly — but claim nothing the rows cannot prove."""

# Appended on the single regeneration when the first attempt failed the
# honesty backstops. It quotes what fired — overstatement triggers and
# explanation-vs-result fidelity problems — so the model corrects the
# specific violation.
STRICT_SUFFIX = """

IMPORTANT — your previous explanation violated the rules: {problems}. That is
not allowed. Do not state causes, predictions, or recommendations, and do not
infer real-world conclusions. Do not state row counts or figures the result
does not actually contain. Describe ONLY what the rows literally show: the
values, the ranges, and the row count."""


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


def _store_confidence(state, *, correction_attempts, grounded_cleanly, row_count):
    """Assess categorical confidence from the real process signals and write the
    verdict (level + reasons + signals, plus the legacy numeric) into state.

    All four signals already exist by the time this node runs (5.2 inventory):
    clarification from the request round-trip, corrections from the self-correct
    loop, grounding from verify_grounding, and the row count from execution.
    """
    clarification_happened = (
        bool(state.get("clarification_round"))
        or state.get("clarification_context") is not None
    )
    assessment = assess_confidence(
        clarification_happened=clarification_happened,
        correction_attempts=correction_attempts,
        grounded_cleanly=grounded_cleanly,
        row_count=row_count,
    )
    state["confidence"] = assessment.level.value
    state["confidence_reasons"] = assessment.reasons
    state["confidence_signals"] = assessment.signals
    state["confidence_score"] = level_to_score(assessment.level)
    return assessment


async def explain_result_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "explain_result"
    llm = ChatAnthropic(
        model="claude-haiku-4-5",
        temperature=0.3,
        max_tokens=256,
    )

    result = state.get("execution_result") or {}
    rows = result.get("rows", [])
    row_count = result.get("row_count", 0)

    cr = state.get("cache_result")
    if state.get("served_from_cache") and cr:
        if cr.get("explanation"):
            state["explanation"] = cr["explanation"]
            # A cache hit serves a previously clean, GOOD first-pass result, so
            # grounding was clean and there were no corrections on that run. The
            # clarification signal still applies: a clarified query that hits the
            # cache went through clarification and so can never be HIGH.
            _store_confidence(
                state,
                correction_attempts=0,
                grounded_cleanly=True,
                row_count=cr.get("row_count", 0),
            )
            state["is_complete"] = True
            state["completed_nodes"].append("explain_result")
            state["stream_updates"].append({
                "timestamp": now(), "node": "explain_result",
                "message": f"Complete (cached) — confidence: {state['confidence']}",
                "status": "done",
            })
            return state

    result_summary = f"Total: {row_count} rows. First 5: {json.dumps(rows[:5], default=str)}"

    @llm_retry
    async def _call_llm(prompt: str):
        return await llm.ainvoke(prompt)

    base_prompt = EXPLAIN_PROMPT.format(
        user_query=state["user_query"],
        sql=state["generated_sql"],
        result_summary=result_summary,
    )
    response = await _call_llm(base_prompt)
    explanation = response.content.strip()

    # Backstop (prompt 5.1 + M3 fidelity): the prompt does the main work; here
    # we catch what it let through. Two deterministic checks run on every
    # generation — overstatement (world-claims) and fidelity (row counts /
    # cited values that don't match the executed result). On any failure,
    # REGENERATE ONCE with a stricter instruction quoting the violations, then
    # fall back to a strictly descriptive deterministic rendering. One retry
    # max — latency stays bounded.
    verdict = is_overstated(explanation, state["user_query"])
    fidelity = explanation_matches_result(
        explanation, rows, result.get("columns", []), row_count, question=state["user_query"]
    )

    if verdict.overstated or not fidelity.consistent:
        trigger_phrases = sorted({phrase for _, phrase in verdict.triggers})
        parts = []
        if trigger_phrases:
            parts.append(f"editorializing: {', '.join(trigger_phrases)}")
        if not fidelity.consistent:
            parts.append("; ".join(fidelity.problems))
        problems_str = " | ".join(parts)
        logger.info(
            "Explanation failed the honesty backstop (%s) — regenerating once "
            "(session=%s)",
            problems_str, state.get("session_id", "unknown")[:8],
        )
        response = await _call_llm(base_prompt + STRICT_SUFFIX.format(problems=problems_str))
        explanation = response.content.strip()

        still_bad = is_overstated(explanation, state["user_query"]).overstated or not (
            explanation_matches_result(
                explanation, rows, result.get("columns", []), row_count,
                question=state["user_query"],
            ).consistent
        )
        if still_bad:
            logger.info(
                "Explanation still failed the backstop after retry — using "
                "deterministic description (session=%s)",
                state.get("session_id", "unknown")[:8],
            )
            explanation = describe_result_plainly(
                rows, result.get("columns", []), row_count, state["user_query"]
            )

    state["explanation"] = explanation

    quality = state.get("result_quality") or {}
    # grounded_cleanly: grounding both ran (checked) AND found no hallucination.
    # checked=False means grounding was skipped/fail-open (a conservative
    # blind-spot pass), which caps confidence at MEDIUM rather than HIGH.
    grounding = state.get("grounding_result") or {}
    grounded_cleanly = bool(grounding.get("is_grounded")) and bool(grounding.get("checked"))
    _store_confidence(
        state,
        correction_attempts=state["correction_attempts"],
        grounded_cleanly=grounded_cleanly,
        row_count=result.get("row_count"),
    )

    if state["correction_attempts"] == 0 and quality.get("status") == "GOOD":
        try:
            stored = await store_fewshot_example(
                natural_language=state["user_query"],
                sql_query=state["generated_sql"],
                tables_used=state.get("tables_identified", []),
                auto_learned=True,
            )
            if not stored:
                logger.debug("Few-shot skipped (near-duplicate detected)")
        except Exception as e:
            logger.warning(
                "Non-critical write failed in explain_result "
                "(session=%s): %s: %s",
                state.get("session_id", "unknown")[:8], type(e).__name__, e,
            )

    if quality.get("status") == "GOOD" and result:
        try:
            embedding = await embed_text(state["user_query"])
            await store_cache_entry(
                user_query=state["user_query"],
                query_embedding=embedding,
                generated_sql=state["generated_sql"],
                result_preview=rows[:5],
                row_count=row_count,
                execution_time_ms=result.get("execution_time_ms", 0),
                chart_type=(state.get("chart_config") or {}).get("chart_type"),
                explanation=state["explanation"],
            )
        except Exception as e:
            logger.warning(
                "Non-critical write failed in explain_result "
                "(session=%s): %s: %s",
                state.get("session_id", "unknown")[:8], type(e).__name__, e,
            )

    state["is_complete"] = True
    state["completed_nodes"].append("explain_result")
    state["stream_updates"].append({
        "timestamp": now(), "node": "explain_result",
        "message": f"Complete — confidence: {state['confidence']} | corrections: {state['correction_attempts']}/3",
        "status": "done",
    })
    return state

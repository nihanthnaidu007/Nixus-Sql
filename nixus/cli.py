"""NIXUS command-line interface — a THIN terminal adapter over the core.

Three commands, no more:

    nixus query "question"   ask a question; render SQL + rows + insight +
                             confidence-with-reasons; clarify interactively;
                             refuse cleanly.
    nixus reembed            re-introspect + re-embed the target schema.
    nixus health             check state_db + target_db reachability.

The governing rule (same as the API adapter): this file reuses the
framework-agnostic core — ``nixus.services.query_service.run_query`` — the EXACT
function the API calls. It does NOT import graph nodes to re-run logic, does NOT
drive the graph itself, and does NOT reimplement scope / grounding / confidence /
explanation. Its only job is terminal I/O. If the CLI and the API gave different
answers to the same question, the CLI would be doing something it must not.

Conventions (chosen + stated): a refusal is a valid outcome, not a crash, so the
``query`` command exits 0 and prints the reason. ``health`` exits 1 only when a
database is genuinely unreachable. Off a TTY, clarification is single-shot (print
the question and exit — never hang waiting on stdin).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from dotenv import load_dotenv

# Load env the way every NIXUS script does, so the CLI works with .venv/bin/python
# against the configured STATE/TARGET database URLs.
load_dotenv()

from sqlalchemy import text

# Terminal rendering (pure formatting; no logic) lives in a sibling helper so this
# adapter stays thin.
from nixus.cli_render import render_answer, render_refusal
from nixus.db.connection import get_state_engine, get_target_engine

# The LangGraph checkpointer lifecycle — opened/closed around the run exactly as
# the API does in its FastAPI lifespan. This is adapter INFRASTRUCTURE the core
# requires (not query logic): the API's lifespan owns it for the server; the CLI
# owns it for one invocation. The CLI still drives the run only through run_query.
from nixus.graph.graph import aclose_checkpointer, init_checkpointer

# THE core entry — the same one api/main.py calls. Imported here so it is also the
# patch point the adapter tests target.
from nixus.services.query_service import run_query

# Outcome discriminator values the core returns. Held as plain literals (not
# imported from the graph) so the adapter never reaches into core logic — it only
# branches on the verdict the core already decided.
ANSWERED = "ANSWERED"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
REFUSED_OUT_OF_SCOPE = "REFUSED_OUT_OF_SCOPE"
REFUSED_WRITE = "REFUSED_WRITE"
REFUSED_AMBIGUOUS = "REFUSED_AMBIGUOUS"
REFUSALS = {REFUSED_OUT_OF_SCOPE, REFUSED_WRITE, REFUSED_AMBIGUOUS}


# ── query (interactive clarification over the stateless round-trip) ──────────
def _accumulate(context, original: str, question: str, answer: str) -> dict:
    """Grow the stateless clarification context the core expects: the original
    question plus each (question, answer) turn. No server-side paused state."""
    context = context or {"original_question": original, "prior_clarifications": []}
    context["prior_clarifications"].append({"question": question, "answer": answer})
    return context


async def _run_query_interactive(question: str) -> int:
    # Open the checkpoint pool the graph needs, the same lifecycle the API runs in
    # its lifespan; always close it. The interaction itself goes through run_query.
    await init_checkpointer()
    try:
        return await _clarification_loop(question)
    finally:
        await aclose_checkpointer()


async def _clarification_loop(question: str) -> int:
    session_id = str(uuid.uuid4())
    original = question
    current = question
    context: dict | None = None
    clarification_round = 0

    while True:
        state = await run_query(
            current,
            session_id,
            clarification_context=context,
            clarification_round=clarification_round,
        )
        outcome = state.get("outcome") or ANSWERED

        if outcome == NEEDS_CLARIFICATION:
            cq = state.get("clarifying_question") or "Could you clarify your question?"
            print("I need one clarification:")
            print(f"  {cq}")
            if not sys.stdin.isatty():
                # Non-interactive (piped) — do NOT hang. Single-shot fallback.
                print()
                print("(non-interactive input — re-run `nixus query` with more "
                      "detail to answer this.)")
                return 0
            answer = input("> ").strip()
            context = _accumulate(context, original, cq, answer)
            current = answer
            clarification_round += 1
            continue

        if outcome in REFUSALS:
            render_refusal(state)
            return 0  # a refusal is a valid outcome, not an error

        render_answer(state)
        return 0


def cmd_query(question: str) -> int:
    return asyncio.run(_run_query_interactive(question))


# ── reembed (reuse the existing pipeline; do not duplicate it) ───────────────
def cmd_reembed() -> int:
    # Call the SAME pipeline `python -m nixus.schema.reembed` runs (it prints the
    # tables introspected + embedding rows written). The CLI only wraps it.
    from nixus.schema import reembed as reembed_module

    asyncio.run(reembed_module._run(skip_if_exists=False))
    return 0


# ── health (engines only — a trivial SELECT 1 on each) ───────────────────────
async def _ping(engine) -> tuple[bool, str | None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # any failure is an unreachable DB
        return False, str(exc).splitlines()[0]


async def _run_health() -> int:
    checks = [("state_db", get_state_engine()), ("target_db", get_target_engine())]
    all_ok = True
    for name, engine in checks:
        ok, detail = await _ping(engine)
        all_ok = all_ok and ok
        status = "OK" if ok else f"FAIL — {detail}"
        print(f"{name:<10} {status}")
    return 0 if all_ok else 1


def cmd_health() -> int:
    return asyncio.run(_run_health())


# ── argparse wiring ──────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nixus",
        description="NIXUS — grounded natural-language SQL agent (terminal interface).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("query", help="Ask a question against the configured database.")
    q.add_argument("question", help="The natural-language question (quote it).")
    sub.add_parser("reembed", help="Re-introspect + re-embed the target schema.")
    sub.add_parser("health", help="Check state_db + target_db reachability.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "query":
        return cmd_query(args.question)
    if args.command == "reembed":
        return cmd_reembed()
    if args.command == "health":
        return cmd_health()
    return 2  # unreachable: argparse enforces a valid subcommand


if __name__ == "__main__":
    sys.exit(main())

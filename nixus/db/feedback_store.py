"""Explicit-reject feedback store (Phase 3, Wave 1 D1).

Records a human verdict ('accept' | 'reject') on one query_history row and,
for a reject, demotes the few-shot example that run created — in ONE
transaction, so a verdict can never half-apply.

The demotion is a TOMBSTONE (``fewshot_examples.disabled = TRUE``), not a
delete: the audit trail survives, and the retrieval gate
(``fewshot_store.search_fewshots``) plus the duplicate check
(``fewshot_store._is_duplicate``) both filter disabled rows — rejected SQL is
never re-served AND a rejected near-duplicate cannot block re-learning the
corrected query.

A later 'accept' records the verdict but does NOT un-disable: once rejected,
an example stays out of the corpus (the safe direction; the same run will
re-learn a fresh row if it was good).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import text

from nixus.db.connection import state_engine

Verdict = Literal["accept", "reject"]


async def record_feedback(
    history_id: int,
    verdict: Verdict,
    note: str | None = None,
) -> dict | None:
    """Stamp the verdict on one history row; demote its learned example on reject.

    Returns the recorded feedback (including whether the linked few-shot row
    was disabled), or None when the history row does not exist.
    """
    async with state_engine.begin() as conn:
        row = (
            await conn.execute(text("""
                UPDATE query_history
                   SET feedback_verdict = :verdict,
                       feedback_note = :note,
                       feedback_at = now()
                 WHERE id = :id
                RETURNING id, fewshot_example_id
            """), {"verdict": verdict, "note": note, "id": history_id})
        ).fetchone()

        if row is None:
            return None

        fewshot_id = int(row[1]) if row[1] is not None else None
        fewshot_disabled = False
        if verdict == "reject" and fewshot_id is not None:
            result = await conn.execute(text("""
                UPDATE fewshot_examples
                   SET disabled = TRUE
                 WHERE id = :fid AND NOT disabled
            """), {"fid": fewshot_id})
            fewshot_disabled = result.rowcount > 0

    return {
        "id": int(row[0]),
        "verdict": verdict,
        "note": note,
        "fewshot_example_id": fewshot_id,
        "fewshot_disabled": fewshot_disabled,
    }


async def get_history_feedback(history_id: int) -> dict | None:
    """Read one history row's feedback state (id, verdict, note, when)."""
    async with state_engine.connect() as conn:
        row = (
            await conn.execute(text("""
                SELECT id, feedback_verdict, feedback_note, feedback_at
                  FROM query_history
                 WHERE id = :id
            """), {"id": history_id})
        ).fetchone()

    if row is None:
        return None
    return {
        "id": int(row[0]),
        "verdict": row[1],
        "note": row[2],
        "at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
    }

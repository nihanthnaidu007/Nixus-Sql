"""Aggregates-only analytics store (Phase 3, Wave 1 D2).

Reads counts and rates over the ``query_history`` record the pipeline writes
on every executed query, and COMPOSES the existing ``/cache-stats`` and
``/fewshot-stats`` payloads by calling their store functions — one definition
of each shape, so the summary can never drift from the endpoints it mirrors.

Invariants: aggregates only — a count, a rate, a percentile. No ``generated_sql``
or question text ever enters this module's output; the pipeline record is the
one raw surface and it stays behind /history's per-session filter.
"""
from __future__ import annotations

from sqlalchemy import text

from nixus.db.connection import state_engine
from nixus.db.fewshot_store import get_fewshot_stats
from nixus.db.query_cache import get_cache_stats

_DAILY_WINDOW_DAYS = 14


async def get_analytics_summary() -> dict:
    """One snapshot of system health, from the run record outward.

    Layout:
      totals   — runs by outcome class (status counts over the full record)
      rates    — outcome rates (0-100) + feedback accept rate over reviewed rows
      latency  — avg / p95 / max over executed runs (duration_ms, ms)
      volume   — per-day run and answered counts, last N days (UTC)
      cache    — the exact /cache-stats shape (reused, not re-derived)
      fewshot  — the exact /fewshot-stats shape (reused, not re-derived)
    """
    async with state_engine.connect() as conn:
        outcomes = (
            await conn.execute(text("""
                SELECT
                    COUNT(*) AS runs,
                    SUM(CASE WHEN status = 'ANSWERED' THEN 1 ELSE 0 END) AS answered,
                    SUM(CASE WHEN status LIKE 'REFUSED%' THEN 1 ELSE 0 END) AS refused,
                    SUM(CASE WHEN status = 'NEEDS_CLARIFICATION' THEN 1 ELSE 0 END)
                        AS needs_clarification,
                    SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS errors,
                    AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms END),
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms),
                    MAX(duration_ms)
                FROM query_history
            """))
        ).fetchone()
        assert outcomes is not None  # bare aggregate → exactly one row

        volume_rows = (
            await conn.execute(text("""
                SELECT
                    (created_at AT TIME ZONE 'UTC')::date AS day,
                    COUNT(*) AS runs,
                    SUM(CASE WHEN status = 'ANSWERED' THEN 1 ELSE 0 END) AS answered
                FROM query_history
                WHERE created_at >= (now() - :window::interval)
                GROUP BY day
                ORDER BY day
            """), {"window": f"{_DAILY_WINDOW_DAYS} days"})
        ).fetchall()

        feedback = (
            await conn.execute(text("""
                SELECT
                    SUM(CASE WHEN feedback_verdict = 'accept' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN feedback_verdict = 'reject' THEN 1 ELSE 0 END)
                FROM query_history
            """))
        ).fetchone()
        assert feedback is not None

    runs = int(outcomes[0] or 0)
    answered = int(outcomes[1] or 0)
    refused = int(outcomes[2] or 0)
    needs_clarification = int(outcomes[3] or 0)
    errors = int(outcomes[4] or 0)
    avg_ms, p95_ms, max_ms = outcomes[5], outcomes[6], outcomes[7]
    accepted = int(feedback[0] or 0)
    rejected = int(feedback[1] or 0)
    reviewed = accepted + rejected

    def _rate(part: int) -> float:
        return round(part / runs * 100, 1) if runs > 0 else 0.0

    return {
        "totals": {
            "runs": runs,
            "answered": answered,
            "refused": refused,
            "needs_clarification": needs_clarification,
            "errors": errors,
        },
        "rates": {
            "answered_rate": _rate(answered),
            "refusal_rate": _rate(refused),
            "needs_clarification_rate": _rate(needs_clarification),
            "error_rate": _rate(errors),
            "accepted_feedback": accepted,
            "rejected_feedback": rejected,
            "accept_rate": round(accepted / reviewed * 100, 1) if reviewed > 0 else 0.0,
        },
        "latency_ms": {
            # None when nothing has executed yet — absence is honest.
            "avg": round(float(avg_ms), 1) if avg_ms is not None else None,
            "p95": round(float(p95_ms), 1) if p95_ms is not None else None,
            "max": round(float(max_ms), 1) if max_ms is not None else None,
        },
        "volume": [
            {
                "date": row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]),
                "runs": int(row[1] or 0),
                "answered": int(row[2] or 0),
            }
            for row in volume_rows
        ],
        "cache": await get_cache_stats(),
        "fewshot": await get_fewshot_stats(),
    }

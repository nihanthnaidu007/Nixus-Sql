"""Aggregates-only analytics endpoint (Phase 3, Wave 1 D2).

GET /analytics/summary — one snapshot of outcome counts/rates, latency
aggregates, 14-day volume, and the existing /cache-stats + /fewshot-stats
shapes (composed by the store, never re-derived). Aggregates only: the
response carries no generated SQL and no question text — the pipeline record
stays behind /history's per-session filter.

Sits under the app-wide ``APIKeyMiddleware`` (fail-closed, inherited).
"""
from __future__ import annotations

from fastapi import APIRouter

from nixus.db.analytics_store import get_analytics_summary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def analytics_summary():
    """Counts and rates over the run record — nothing raw."""
    return await get_analytics_summary()

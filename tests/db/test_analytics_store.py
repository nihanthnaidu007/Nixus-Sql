"""Analytics store aggregation math, on REAL Postgres (Phase 3 W1 D2).

Self-provisions a throwaway database and applies the FULL migration sequence
through the real runner (no pre-split needed — the aggregates only care about
the final schema). Asserts the outcome counts/rates, latency aggregates, the
14-day volume roll-up, and the feedback counts over seeded history rows.
Skips cleanly without a live Postgres (CI's pgvector service runs it).
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nixus.config import settings
from nixus.db.migrations import runner

TEST_DB = "nixus_analytics_test"

_ADMIN_URL = make_url(settings.state_url) if settings.state_url else None


def _pg_kwargs(database: str) -> dict:
    assert _ADMIN_URL is not None
    return {
        "host": _ADMIN_URL.host,
        "port": _ADMIN_URL.port,
        "user": _ADMIN_URL.username,
        "password": _ADMIN_URL.password,
        "database": database,
    }


async def _postgres_reachable() -> bool:
    try:
        admin = await asyncpg.connect(**_pg_kwargs("postgres"))
        await admin.close()
        return True
    except (OSError, asyncpg.PostgresError):
        return False


async def _drop_db() -> None:
    admin = await asyncpg.connect(**_pg_kwargs("postgres"))
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            TEST_DB,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def analytics_db_url():
    if _ADMIN_URL is None:
        pytest.skip("STATE_DATABASE_URL not set — cannot provision throwaway db.")
    if not asyncio.run(_postgres_reachable()):
        pytest.skip(
            "Postgres not reachable — analytics aggregation tests need a live database."
        )

    async def _setup(admin_base_url: URL) -> str:
        await _drop_db()
        admin = await asyncpg.connect(**_pg_kwargs("postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await admin.close()
        # render_as_string(hide_password=False): str(url) MASKS the password as
        # literal '***', so the runner's DSN fails scram auth (CI 2026-09-18).
        url = admin_base_url.set(
            drivername="postgresql+asyncpg", database=TEST_DB
        ).render_as_string(hide_password=False)
        # The FULL sequence, through the real runner, against the throwaway DB.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(runner.settings, "state_database_url", url)
            applied = await runner.apply_migrations()
            assert applied, "the runner must set the schema up from scratch"
        return url

    url = asyncio.run(_setup(_ADMIN_URL))
    yield url
    asyncio.run(_drop_db())


def test_aggregates_over_seeded_history(analytics_db_url):
    engine = create_async_engine(analytics_db_url)
    # The analytics store composes get_cache_stats/get_fewshot_stats from their
    # own modules — all three must read the throwaway DB.
    import nixus.db.analytics_store as analytics_store
    import nixus.db.fewshot_store as fewshot_store
    import nixus.db.query_cache as query_cache

    mp = pytest.MonkeyPatch()
    for module in (analytics_store, fewshot_store, query_cache):
        mp.setattr(module, "state_engine", engine)

    async def _seed_and_read():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        now = datetime.now(timezone.utc)
        try:
            # 6 answered runs (durations 100..600), 2 refused, 1 error,
            # 1 needs_clarification → 10 runs, 60% answered.
            # Non-executed runs carry duration 0.0 — the schema (migration 0003)
            # is NOT NULL DEFAULT 0, matching what production writes for refusals.
            specs = [
                ("ANSWERED", 100.0, now - timedelta(days=1)),
                ("ANSWERED", 200.0, now - timedelta(days=1)),
                ("ANSWERED", 300.0, now - timedelta(days=2)),
                ("ANSWERED", 400.0, now - timedelta(days=2)),
                ("ANSWERED", 500.0, now - timedelta(hours=1)),
                ("ANSWERED", 600.0, now - timedelta(hours=2)),
                ("REFUSED_OUT_OF_SCOPE", 0.0, now - timedelta(days=3)),
                ("REFUSED_WRITE", 0.0, now - timedelta(days=3)),
                ("ERROR", 0.0, now - timedelta(days=4)),
                (
                    "NEEDS_CLARIFICATION",
                    0.0,
                    now - timedelta(days=40),
                ),  # outside window
            ]
            for i, (status, dur, created) in enumerate(specs):
                await conn.execute(
                    "INSERT INTO query_history (session_id, question, "
                    "generated_sql, status, duration_ms, created_at) "
                    "VALUES ($1, $2, '', $3, $4, $5)",
                    f"sess-{uuid.uuid4()}",
                    f"q{i}",
                    status,
                    dur,
                    created,
                )
            # Feedback: 2 accepts, 1 reject.
            await conn.execute(
                "UPDATE query_history SET feedback_verdict = 'accept' "
                "WHERE duration_ms IN (100.0, 200.0)"
            )
            await conn.execute(
                "UPDATE query_history SET feedback_verdict = 'reject' "
                "WHERE duration_ms = 300.0"
            )
        finally:
            await conn.close()

        try:
            return await analytics_store.get_analytics_summary()
        finally:
            # Dispose on the loop that owns the pool's connections — disposing
            # from a foreign loop (or the GC) leaves cross-loop close futures.
            await engine.dispose()

    try:
        s = asyncio.run(_seed_and_read())
        assert s["totals"]["runs"] == 10
        assert s["totals"]["answered"] == 6
        assert s["totals"]["refused"] == 2
        assert s["totals"]["needs_clarification"] == 1
        assert s["totals"]["errors"] == 1
        assert s["rates"]["answered_rate"] == 60.0
        assert s["rates"]["refusal_rate"] == 20.0
        assert s["rates"]["error_rate"] == 10.0
        # p95 over {100..600} by linear interpolation: 0.95 * 5 = 4.75 → 575.0.
        assert s["latency_ms"]["avg"] == 350.0
        assert s["latency_ms"]["p95"] == 575.0
        assert s["latency_ms"]["max"] == 600.0
        assert s["rates"]["accepted_feedback"] == 2
        assert s["rates"]["rejected_feedback"] == 1
        assert s["rates"]["accept_rate"] == 66.7
        # 14-day window: the 40-day-old run stays out. Distinct in-window days:
        # today (the hour-offset runs), days 1-2 (answered), day 3 (refusals),
        # day 4 (error) — five buckets, oldest first.
        days = [d["date"] for d in s["volume"]]
        assert len(days) == 5
        assert days == sorted(days)
        assert sum(d["runs"] for d in s["volume"]) == 9
        today = s["volume"][-1]
        assert today["runs"] == 2 and today["answered"] == 2
        assert all(d["answered"] <= d["runs"] for d in s["volume"])
        # The composed shapes ride along on the same state DB.
        assert set(s["cache"]) == {"entries", "total_hits", "hit_rate"}
        assert set(s["fewshot"]) == {"total", "auto_learned", "seeded"}
        # Aggregates only — no raw column value ever appears in the payload.
        assert "generated_sql" not in str(s)
    finally:
        mp.undo()

"""Migration 0005 round-trip + the reject invariant, on REAL Postgres.

Self-provisions a throwaway database (the test_introspect.py pattern — CI's
pgvector service runs these; without a live Postgres they skip cleanly):

1. Round-trip: apply 0001–0004, insert pre-0005 corpus rows, apply 0005
   through the REAL migration runner, and assert its backfill, the feedback
   columns, and the constraints (verdict CHECK, few-shot FK).
2. Reject-not-served invariant: store a few-shot through the REAL store
   (fake embeddings), find it through the REAL retrieval gate, reject it via
   record_feedback, and assert it is never re-served AND no longer blocks
   re-learning the corrected query.
"""

import asyncio
import math
import zlib
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nixus.config import settings
from nixus.db.migrations import runner

TEST_DB = "nixus_feedback_test"
MIGRATIONS = Path(runner.MIGRATIONS_DIR)
PRE_0005 = [
    "0001_initial_schema.sql",
    "0002_api_sessions.sql",
    "0003_saved_queries_query_history.sql",
    "0004_semantic_ingestions.sql",
]

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


async def _apply_through_0004() -> None:
    """Apply 0001–0004 exactly as the runner does — each file in its own
    transaction, recorded in schema_migrations — so the runner's
    apply_migrations() then picks up exactly 0005."""
    conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
    try:
        await conn.execute(runner._CREATE_TRACKING)
        for name in PRE_0005:
            version = name.split("_")[0]
            async with conn.transaction():
                await conn.execute((MIGRATIONS / name).read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (version, filename) VALUES ($1, $2)",
                    version,
                    name,
                )
    finally:
        await conn.close()


def _test_db_url() -> str | None:
    if _ADMIN_URL is None:
        return None
    # render_as_string(hide_password=False): str(url) MASKS the password as
    # literal '***', so the runner's DSN fails scram auth (CI 2026-09-18).
    return _ADMIN_URL.set(
        drivername="postgresql+asyncpg", database=TEST_DB
    ).render_as_string(hide_password=False)


def _vec() -> str:
    """A 1536-dim literal matching the vector(1536) column."""
    return "[" + ",".join(["0.1"] * 1536) + "]"


@pytest.fixture(scope="module")
def migrated_db_url():
    if _ADMIN_URL is None:
        pytest.skip("STATE_DATABASE_URL not set — cannot provision throwaway db.")
    if not asyncio.run(_postgres_reachable()):
        pytest.skip("Postgres not reachable — migration tests need a live database.")

    async def _setup() -> str:
        await _drop_db()
        admin = await asyncpg.connect(**_pg_kwargs("postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await admin.close()
        await _apply_through_0004()
        return _test_db_url() or ""

    url = asyncio.run(_setup())
    yield url
    asyncio.run(_drop_db())


@pytest.fixture
def runner_over_test_db(monkeypatch, migrated_db_url):
    """Point the migration runner's config source at the throwaway DB."""
    monkeypatch.setattr(runner.settings, "state_database_url", migrated_db_url)


# ── 1. Migration round-trip ──────────────────────────────────────────────────
def test_0005_round_trip_backfill_and_constraints(runner_over_test_db, migrated_db_url):
    """Pre-0005 rows → the REAL apply_migrations() → backfill + constraints."""

    async def _seed_then_apply():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            for nl, auto in [("learned q", True), ("seeded q", False)]:
                await conn.execute(
                    "INSERT INTO fewshot_examples (natural_language, sql_query, "
                    "query_type, embedding, auto_learned) "
                    "VALUES ($1, 'SELECT 1', 'filter', CAST($2 AS vector), $3)",
                    nl,
                    _vec(),
                    auto,
                )
        finally:
            await conn.close()
        return await runner.apply_migrations()

    applied = asyncio.run(_seed_then_apply())
    assert applied == ["0005"], "the runner must pick up exactly the new migration"

    async def _check():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            # The migration's own backfill UPDATE ran over the pre-0005 rows.
            rows = await conn.fetch(
                "SELECT natural_language, source, disabled FROM fewshot_examples"
            )
            by_nl = {r["natural_language"]: (r["source"], r["disabled"]) for r in rows}
            assert by_nl["learned q"] == ("auto", False)
            assert by_nl["seeded q"] == ("seed", False)

            # New rows default honestly.
            await conn.execute(
                "INSERT INTO fewshot_examples (natural_language, sql_query, "
                "query_type, embedding) VALUES ('n2', 'SELECT 2', 'filter', CAST($1 AS vector))",
                _vec(),
            )
            defaults = await conn.fetchrow(
                "SELECT source, disabled FROM fewshot_examples WHERE natural_language = 'n2'"
            )
            assert defaults["source"] == "seed" and defaults["disabled"] is False

            # History: feedback columns exist; verdict CHECK and the FK bite.
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'query_history'"
            )
            names = {r["column_name"] for r in cols}
            assert {
                "fewshot_example_id",
                "feedback_verdict",
                "feedback_note",
                "feedback_at",
            } <= names

            await conn.execute(
                "INSERT INTO query_history (session_id, question, generated_sql, "
                "status, fewshot_example_id) "
                "VALUES ('s1', 'q', '', 'ANSWERED', "
                "(SELECT id FROM fewshot_examples WHERE natural_language = 'learned q'))"
            )
            try:
                await conn.execute(
                    "UPDATE query_history SET feedback_verdict = 'bogus' WHERE session_id = 's1'"
                )
                raise AssertionError("CHECK constraint missing for feedback_verdict")
            except asyncpg.CheckViolationError:
                pass
            try:
                await conn.execute(
                    "INSERT INTO query_history (session_id, question, generated_sql, "
                    "status, fewshot_example_id) VALUES ('s2', 'q', '', 'ANSWERED', 999999)"
                )
                raise AssertionError("FK constraint missing for fewshot_example_id")
            except asyncpg.ForeignKeyViolationError:
                pass
        finally:
            await conn.close()

    asyncio.run(_check())


# ── 2. The reject invariant through the REAL stores ──────────────────────────
async def _fake_embed(text: str) -> list[float]:
    """Deterministic 1536-dim vector with per-text DIRECTION (constant vectors
    are always cosine-1.0 to each other, which would fake duplicates). zlib.crc32
    instead of hash() so the direction — and therefore every similarity in the
    file — is stable across processes, not just within one."""
    seed = float(zlib.crc32(text.encode()) % 97) + 1.0
    return [math.sin(seed * (i + 1)) for i in range(1536)]


def test_rejected_sql_is_never_re_served_and_unblocks_relearning(
    runner_over_test_db, migrated_db_url, monkeypatch
):
    # The full sequence, via the real runner (idempotent — 0005 already applied).
    asyncio.run(runner.apply_migrations())

    engine = create_async_engine(migrated_db_url)
    # The store modules imported `state_engine` into their own namespaces —
    # repoint each at the throwaway DB for the duration.
    import nixus.db.feedback_store as feedback_store
    import nixus.db.fewshot_store as fewshot_store
    import nixus.db.query_history_store as history_store

    for module in (fewshot_store, feedback_store, history_store):
        monkeypatch.setattr(module, "state_engine", engine)
    monkeypatch.setattr("nixus.utils.embeddings.embed_text", _fake_embed)

    async def _flow():
        vec = await _fake_embed("how many artists are there?")
        stored_id = await fewshot_store.store_fewshot_example(
            natural_language="how many artists are there?",
            sql_query='SELECT COUNT(*) FROM "Artist"',
            tables_used=["Artist"],
            auto_learned=True,
        )
        assert isinstance(stored_id, int)

        # Served before the reject.
        served = await fewshot_store.search_fewshots(vec, limit=5, threshold=0.0)
        assert any(ex["sql_query"] == 'SELECT COUNT(*) FROM "Artist"' for ex in served)

        # A run that learned it, then the explicit reject.
        history_id = await history_store.record_query_history(
            session_id="sess-feedback",
            question="how many artists are there?",
            generated_sql='SELECT COUNT(*) FROM "Artist"',
            status="ANSWERED",
            duration_ms=1.0,
            row_count=1,
            fewshot_example_id=stored_id,
        )
        recorded = await feedback_store.record_feedback(
            history_id, "reject", note="counts the wrong table"
        )
        assert recorded is not None
        assert recorded["verdict"] == "reject"
        assert recorded["fewshot_example_id"] == stored_id
        assert recorded["fewshot_disabled"] is True

        # INVARIANT: the rejected example is never re-served. The throwaway DB
        # cohabits with the round-trip test's corpus (module-scoped fixture),
        # so the assertion is per-example, not emptiness.
        served_after = await fewshot_store.search_fewshots(vec, limit=5, threshold=0.0)
        assert all(
            ex["natural_language"] != "how many artists are there?"
            for ex in served_after
        )

        # …and its tombstone no longer blocks re-learning the corrected query:
        # same question (same vector → 1.0 similarity vs the disabled row),
        # corrected SQL — without the gate this returns None as a duplicate.
        relearned = await fewshot_store.store_fewshot_example(
            natural_language="how many artists are there?",
            sql_query='SELECT COUNT(DISTINCT artist_id) FROM "Artist"',
            tables_used=["Artist"],
            auto_learned=True,
        )
        assert isinstance(relearned, int) and relearned != stored_id

        # Accept on an unlinked row records the verdict with nothing to demote.
        unlinked = await history_store.record_query_history(
            session_id="sess-feedback",
            question="refused question",
            generated_sql="",
            status="REFUSED_WRITE",
            duration_ms=0.5,
            row_count=0,
        )
        accepted = await feedback_store.record_feedback(unlinked, "accept")
        assert accepted == {
            "id": unlinked,
            "verdict": "accept",
            "note": None,
            "fewshot_example_id": None,
            "fewshot_disabled": False,
        }

    try:
        asyncio.run(_flow())
    finally:
        asyncio.run(engine.dispose())

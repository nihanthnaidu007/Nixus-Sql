"""Dimension-aware vector stores + the re-embed command, on REAL Postgres.

Self-provisions a throwaway database (the test_fewshot_feedback_gate.py /
test_analytics.py pattern — CI's pgvector service runs these; without a live
Postgres they skip cleanly):

1. The real 0001 migrations lock the three vector columns at vector(1536);
   store_dims.get_vector_column_dims() reads the truth from pg_attribute.
2. ensure_store_dims() resizes EMPTY stores to the active width (pgvector
   rejects mismatched inserts otherwise) and never truncates a non-empty
   store — re-embedding is the only sanctioned destructive path.
3. rebuild_vector_stores() empties + resizes on the caller's connection,
   dropping and restoring the query_history→fewshot_examples FK so history
   rows never dangle.
4. The reembed_stores CLI command re-embeds few-shot rows (preserving ids and
   disabled flags), re-introspects schema targets, and truncates the
   disposable query cache — plan mode exits 2 without touching anything.

All embedding calls are faked; no OpenAI/Ollama traffic.
"""

import asyncio

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from nixus.config import settings
from nixus.db.migrations import runner

TEST_DB = "nixus_storedims_test"

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


def _vec(dim: int, value: float = 0.1) -> str:
    return "[" + ",".join([str(value)] * dim) + "]"


def _expected_dims(dim: int) -> dict[str, int]:
    """{table.column: dim} for the three real vector columns."""
    from nixus.db import store_dims

    return {f"{table}.{column}": dim for table, column, _ in store_dims.VECTOR_COLUMNS}


def _reset_dims(dim: int) -> None:
    """Normalize the shared throwaway DB to a given width: the destructive
    rebuild truncates leftovers from a previous test AND resizes the columns,
    making every test order-independent. query_history is cleared too (test
    normalization only — production rebuilds never touch history, but leftover
    history rows from one test would break the FK restore of the next)."""
    from nixus.db import store_dims

    async def _go():
        async with store_dims.state_engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE query_history"))
            dropped = await store_dims.rebuild_vector_stores(conn, dim)
            await store_dims.readd_referencing_constraints(conn, dropped)

    asyncio.run(_go())


@pytest.fixture(scope="module")
def migrated_db_url():
    if _ADMIN_URL is None:
        pytest.skip("STATE_DATABASE_URL not set — cannot provision throwaway db.")
    if not asyncio.run(_postgres_reachable()):
        pytest.skip("Postgres not reachable — store-dim tests need a live database.")

    async def _setup() -> str:
        await _drop_db()
        admin = await asyncpg.connect(**_pg_kwargs("postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await admin.close()
        # Full real sequence through the migration runner.
        original = runner.settings.state_database_url
        runner.settings.state_database_url = _test_db_url()
        try:
            await runner.apply_migrations()
        finally:
            runner.settings.state_database_url = original
        return _test_db_url() or ""

    url = asyncio.run(_setup())
    yield url
    asyncio.run(_drop_db())


def _test_db_url() -> str | None:
    if _ADMIN_URL is None:
        return None
    return _ADMIN_URL.set(
        drivername="postgresql+asyncpg", database=TEST_DB
    ).render_as_string(hide_password=False)


@pytest.fixture
def stores_over_test_db(migrated_db_url, monkeypatch):
    """Point every store-touching module's state_engine at the throwaway DB
    (they imported `state_engine` into their own namespaces at import time).

    NullPool: these tests make several independent asyncio.run() calls (reset,
    seed, act, assert) — each is its own event loop, and a default pool would
    hand a connection bound to one loop into another ("Future attached to a
    different loop"). NullPool opens a fresh connection per use instead.
    """
    engine = create_async_engine(migrated_db_url, poolclass=NullPool)
    import nixus.db.reembed_stores as reembed_stores
    import nixus.db.store_dims as store_dims

    monkeypatch.setattr(store_dims, "state_engine", engine)
    monkeypatch.setattr(reembed_stores, "state_engine", engine)
    yield
    asyncio.run(engine.dispose())


# ── 1. Ground truth of the real migrations ───────────────────────────────────
def test_real_migrations_lock_the_three_columns_at_1536(
    stores_over_test_db, migrated_db_url
):
    from nixus.db import store_dims

    _reset_dims(1536)
    dims = asyncio.run(store_dims.get_vector_column_dims())
    assert dims == _expected_dims(1536)


# ── 2. Empty-store resize (the startup alignment path) ───────────────────────
def test_ensure_store_dims_resizes_empty_stores_to_active_width(
    stores_over_test_db, migrated_db_url
):
    from nixus.db import store_dims

    _reset_dims(1536)  # these tests share one throwaway db — normalize first
    resized, blocked = asyncio.run(store_dims.ensure_store_dims(768))
    assert resized == [f"{t}.{c}" for t, c, _ in store_dims.VECTOR_COLUMNS]
    assert blocked == []

    dims = asyncio.run(store_dims.get_vector_column_dims())
    assert dims == _expected_dims(768)

    async def _insert_768():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            await conn.execute(
                "INSERT INTO schema_embeddings (table_name, description, "
                "columns_json, embedding) "
                "VALUES ('t', 'desc', '[]', CAST($1 AS vector))",
                _vec(768),
            )
        finally:
            await conn.close()

    asyncio.run(_insert_768())  # the resized column accepts the active width


def test_ensure_store_dims_never_truncates_a_nonempty_store(
    stores_over_test_db, migrated_db_url
):
    from nixus.db import store_dims

    _reset_dims(1536)

    async def _seed():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            await conn.execute(
                "INSERT INTO schema_embeddings (table_name, description, "
                "columns_json, embedding) "
                "VALUES ('t', 'desc', '[]', CAST($1 AS vector))",
                _vec(1536),
            )
        finally:
            await conn.close()

    asyncio.run(_seed())
    # schema_embeddings is non-empty at the WRONG width → blocked, never touched.
    # The other two stores are empty (the reset truncated them) → they resize.
    resized, blocked = asyncio.run(store_dims.ensure_store_dims(768))
    assert resized == ["fewshot_examples.embedding", "query_cache.query_embedding"]
    assert blocked == ["schema_embeddings.embedding"]

    dims = asyncio.run(store_dims.get_vector_column_dims())
    assert dims["schema_embeddings.embedding"] == 1536  # untouched

    async def _row_intact():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            return await conn.fetchval("SELECT COUNT(*) FROM schema_embeddings")
        finally:
            await conn.close()

    assert asyncio.run(_row_intact()) == 1  # the blocking row was not destroyed


# ── 3. Destructive rebuild (the re-embed path) ───────────────────────────────
def test_rebuild_vector_stores_empties_and_resizes_all_stores(
    stores_over_test_db, migrated_db_url
):
    from nixus.db import store_dims

    _reset_dims(768)  # seed at the starting width

    async def _seed_all_three():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            await conn.execute(
                "INSERT INTO schema_embeddings (table_name, description, "
                "columns_json, embedding) "
                "VALUES ('t', 'desc', '[]', CAST($1 AS vector))",
                _vec(768),
            )
            await conn.execute(
                "INSERT INTO fewshot_examples (natural_language, sql_query, "
                "query_type, embedding, auto_learned) "
                "VALUES ('q', 'SELECT 1', 'filter', CAST($1 AS vector), true)",
                _vec(768),
            )
            await conn.execute(
                "INSERT INTO query_cache (user_query, query_embedding, generated_sql) "
                "VALUES ('q', CAST($1 AS vector), 'SELECT 1')",
                _vec(768),
            )
        finally:
            await conn.close()

    asyncio.run(_seed_all_three())

    async def _rebuild():
        async with store_dims.state_engine.begin() as conn:
            dropped = await store_dims.rebuild_vector_stores(conn, 768)
            await store_dims.readd_referencing_constraints(conn, dropped)

    asyncio.run(_rebuild())

    async def _check():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            counts = {}
            for table in ("schema_embeddings", "fewshot_examples", "query_cache"):
                counts[table] = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            fks = await conn.fetchval(
                "SELECT COUNT(*) FROM pg_constraint WHERE contype = 'f' "
                "AND confrelid = 'fewshot_examples'::regclass"
            )
        finally:
            await conn.close()
        return counts, fks

    counts, fk_count = asyncio.run(_check())
    assert counts == {
        "schema_embeddings": 0,
        "fewshot_examples": 0,
        "query_cache": 0,
    }
    assert fk_count == 1  # the dropped query_history FK was restored
    assert asyncio.run(store_dims.get_vector_column_dims()) == _expected_dims(768)


def test_rebuild_preserves_history_rows_referencing_fewshots(
    stores_over_test_db, migrated_db_url
):
    """The FK drop/restore cycle must not lose the referencing side: history
    rows survive the rebuild and still point at the re-inserted few-shot.
    Mirrors the production ordering — re-embedding re-inserts the referenced
    few-shot rows BEFORE the constraints are restored."""
    from nixus.db import store_dims

    _reset_dims(1536)

    async def _seed():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            fs_id = await conn.fetchval(
                "INSERT INTO fewshot_examples (natural_language, sql_query, "
                "query_type, embedding, auto_learned) "
                "VALUES ('q', 'SELECT 1', 'filter', CAST($1 AS vector), true) "
                "RETURNING id",
                _vec(1536),
            )
            await conn.execute(
                "INSERT INTO query_history (session_id, question, generated_sql, "
                "status, duration_ms, row_count, fewshot_example_id) "
                "VALUES ('s', 'q', 'SELECT 1', 'ANSWERED', 1, 0, $1)",
                fs_id,
            )
        finally:
            await conn.close()
        return fs_id

    fs_id = asyncio.run(_seed())

    async def _rebuild_and_reinsert():
        async with store_dims.state_engine.begin() as conn:
            dropped = await store_dims.rebuild_vector_stores(conn, 768)
            assert dropped, "the query_history FK must have been dropped"
            # The re-embed half: rows return at their preserved ids.
            await conn.execute(
                text(
                    "INSERT INTO fewshot_examples (id, natural_language, sql_query, "
                    "query_type, embedding, auto_learned) "
                    "VALUES (:fs_id, 'q', 'SELECT 1', 'filter', CAST(:vec AS vector), true)"
                ),
                {"fs_id": fs_id, "vec": _vec(768)},
            )
            await store_dims.readd_referencing_constraints(conn, dropped)

    asyncio.run(_rebuild_and_reinsert())

    async def _verify():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            history = await conn.fetchval("SELECT COUNT(*) FROM query_history")
            still_points = await conn.fetchval(
                "SELECT COUNT(*) FROM query_history h "
                "JOIN fewshot_examples f ON f.id = h.fewshot_example_id"
            )
        finally:
            await conn.close()
        return history, still_points

    history, still_points = asyncio.run(_verify())
    assert history == 1  # history untouched by the rebuild
    assert still_points == 1  # and the restored FK still resolves


# ── 4. The reembed-stores command ────────────────────────────────────────────
@pytest.fixture
def seeded_stores(stores_over_test_db):
    """A 1536-dim few-shot row and a cache row; returns (asyncpg kwargs,
    seeded fewshot id) for direct inspection."""
    _reset_dims(1536)  # the re-embed flow runs 1536 → 768

    async def _seed():
        conn = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            fs_id = await conn.fetchval(
                "INSERT INTO fewshot_examples (natural_language, sql_query, "
                "query_type, embedding, auto_learned, disabled) "
                "VALUES ('how many artists?', 'SELECT COUNT(*) FROM \"Artist\"', "
                "'aggregate', CAST($1 AS vector), true, true) "
                "RETURNING id",
                _vec(1536),
            )
            await conn.execute(
                "INSERT INTO query_cache (user_query, query_embedding, generated_sql) "
                "VALUES ('cached q', CAST($1 AS vector), 'SELECT 1')",
                _vec(1536),
            )
        finally:
            await conn.close()
        return fs_id

    fs_id = asyncio.run(_seed())
    return _pg_kwargs(TEST_DB), fs_id


def test_reembed_plan_mode_touches_nothing(seeded_stores, capsys):
    from nixus.db import reembed_stores

    pg, _fs_id = seeded_stores

    exit_code = asyncio.run(reembed_stores._run(assume_yes=False))
    assert exit_code == 2  # plan exits nonzero so scripts can gate on it
    out = capsys.readouterr().out
    assert "Re-run with --yes" in out  # the plan announces the gated apply step

    async def _counts():
        conn = await asyncpg.connect(**pg)
        try:
            few = await conn.fetchval("SELECT COUNT(*) FROM fewshot_examples")
            cache = await conn.fetchval("SELECT COUNT(*) FROM query_cache")
        finally:
            await conn.close()
        return few, cache

    assert asyncio.run(_counts()) == (1, 1)  # plan changed nothing


def test_reembed_yes_reembeds_fewshot_and_truncates_cache(
    seeded_stores, monkeypatch, capsys
):
    from nixus.db import reembed_stores

    reembed_calls = []

    async def _fake_embed_texts(texts):
        reembed_calls.append(list(texts))
        return [[0.5] * 768 for _ in texts]

    schema_calls = []

    async def _fake_embed_schema(target_engine=None, state_engine=None):
        schema_calls.append(state_engine)

    import nixus.db.reembed_stores as reembed_module
    import nixus.schema.embed as embed_module

    # Activate the Ollama side of settings so settings.embedding_dim resolves
    # to 768 — consistent with the faked 768-dim embeddings above.
    monkeypatch.setattr(reembed_module.settings, "embeddings_provider", "ollama")
    # reembed_stores imported embed_texts into its own namespace — patch there.
    monkeypatch.setattr(reembed_module, "embed_texts", _fake_embed_texts)
    # embed_target_schema is imported inside _run from nixus.schema.embed.
    monkeypatch.setattr(embed_module, "embed_target_schema", _fake_embed_schema)

    exit_code = asyncio.run(reembed_stores._run(assume_yes=True))
    assert exit_code == 0
    # few-shot text re-embedded at the NEW width; schema re-introspected once.
    assert reembed_calls == [["how many artists?"]]
    assert len(schema_calls) == 1

    pg, fs_id = seeded_stores

    async def _verify():
        conn = await asyncpg.connect(**pg)
        try:
            row = await conn.fetchrow(
                "SELECT id, natural_language, disabled, vector_dims(embedding) AS d "
                "FROM fewshot_examples"
            )
            cache = await conn.fetchval("SELECT COUNT(*) FROM query_cache")
        finally:
            await conn.close()
        return row, cache

    row, cache = asyncio.run(_verify())
    assert row["id"] == fs_id  # the re-insert preserved the row's identity
    assert row["natural_language"] == "how many artists?"
    assert row["disabled"] is True  # disabled few-shots are not re-armed
    assert row["d"] == 768  # re-embedded at the active width
    assert cache == 0  # the disposable cache is truncated, not re-embedded

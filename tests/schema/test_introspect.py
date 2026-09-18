"""Adversarial introspection test (prompt 2.2).

Provisions a deliberately nasty schema in a throwaway database (created/dropped
via an admin/owner connection — the read-only role cannot create), introspects it
through a real SQLAlchemy AsyncEngine (the production code path), and asserts the
result is correct on the HARD cases: multiple schemas, a 50+ column table, a
composite FK with the ordering trap, array/enum/jsonb/numeric type fidelity,
nullable correctness, composite PK ordering, and comments.

Self-contained: it creates and DROPs its own ``nixus_introspect_test`` database.
It never touches the real target and never runs ``down -v``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nixus.config import settings
from nixus.schema.introspect import introspect_schema
from nixus.schema.models import IntrospectedSchema

TEST_DB = "nixus_introspect_test"
_DDL = (Path(__file__).resolve().parents[1] / "fixtures" / "adversarial_schema.sql").read_text()

# Owner/admin credentials (the role that may CREATE DATABASE). Reuse the state
# URL's host/port/user/password; only the database name changes.
_ADMIN_URL = make_url(settings.state_url) if settings.state_url else None


def _pg_kwargs(database: str) -> dict:
    # _ADMIN_URL is None only when STATE_DATABASE_URL is unset; every caller
    # here only runs when the state DB is reachable, so the assert always holds.
    assert _ADMIN_URL is not None
    return {
        "host": _ADMIN_URL.host,
        "port": _ADMIN_URL.port,
        "user": _ADMIN_URL.username,
        "password": _ADMIN_URL.password,
        "database": database,
    }


async def _postgres_reachable() -> bool:
    """True iff the admin connection (CREATE DATABASE rights) can be established."""
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


async def _provision_and_introspect() -> IntrospectedSchema:
    # Fresh throwaway database (drop any leftover from a previous failed run).
    await _drop_db()
    admin = await asyncpg.connect(**_pg_kwargs("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await admin.close()

    try:
        # Apply the adversarial DDL via the owner connection.
        work = await asyncpg.connect(**_pg_kwargs(TEST_DB))
        try:
            await work.execute(_DDL)
        finally:
            await work.close()

        # Introspect through a real AsyncEngine — the production code path.
        assert _ADMIN_URL is not None
        engine = create_async_engine(
            _ADMIN_URL.set(drivername="postgresql+asyncpg", database=TEST_DB)
        )
        try:
            return await introspect_schema(engine)
        finally:
            await engine.dispose()
    finally:
        await _drop_db()


@pytest.fixture(scope="module")
def introspected() -> IntrospectedSchema:
    if _ADMIN_URL is None:
        pytest.skip("STATE_DATABASE_URL not set — cannot provision throwaway db.")
    if not asyncio.run(_postgres_reachable()):
        pytest.skip("Postgres not reachable — introspection tests need a live database.")
    return asyncio.run(_provision_and_introspect())


# ── helpers ──────────────────────────────────────────────────────────────────

def _table(schema: IntrospectedSchema, qualified: str):
    by_qual = {t.qualified_name: t for t in schema.tables}
    assert qualified in by_qual, f"{qualified} missing; have {sorted(by_qual)}"
    return by_qual[qualified]


def _col(table, name: str):
    by_name = {c.name: c for c in table.columns}
    assert name in by_name, f"column {name} missing from {table.qualified_name}"
    return by_name[name]


def _fk(schema: IntrospectedSchema, constraint_name: str):
    for fk in schema.foreign_keys:
        if fk.constraint_name == constraint_name:
            return fk
    raise AssertionError(f"FK {constraint_name} missing; have {[f.constraint_name for f in schema.foreign_keys]}")


# ── assertions (the real proof) ──────────────────────────────────────────────

def test_tables_from_both_schemas_present(introspected):
    quals = {t.qualified_name for t in introspected.tables}
    assert "public.orders" in quals
    assert "billing.invoice" in quals
    schemas = {t.schema for t in introspected.tables}
    assert "public" in schemas and "billing" in schemas


def test_system_schemas_excluded(introspected):
    for t in introspected.tables:
        assert t.schema not in ("pg_catalog", "information_schema")
        assert not t.schema.startswith("pg_toast")
        assert not t.schema.startswith("pg_temp")
    # Only our two user schemas should appear.
    assert {t.schema for t in introspected.tables} == {"public", "billing"}


def test_wide_table_all_columns_in_order(introspected):
    wide = _table(introspected, "public.wide_table")
    assert len(wide.columns) == 61, f"expected 61 cols, got {len(wide.columns)}"
    names = [c.name for c in wide.columns]
    assert names[0] == "id"
    assert names[1:] == [f"col_{i:03d}" for i in range(1, 61)]


def test_composite_fk_pairs_in_correct_order(introspected):
    fk = _fk(introspected, "child_parent_fk")
    assert fk.from_schema == "public" and fk.from_table == "child"
    assert fk.to_schema == "public" and fk.to_table == "parent"
    # Exact ordered pairing — the ordering trap. from[i] references to[i].
    assert fk.from_columns == ["p_tenant_id", "p_parent_code"]
    assert fk.to_columns == ["tenant_id", "parent_code"]


def test_single_column_fk_captured(introspected):
    fk = _fk(introspected, "child_order_fk")
    assert fk.from_columns == ["order_id"]
    assert fk.to_table == "orders" and fk.to_columns == ["order_id"]


def test_array_column_type_fidelity(introspected):
    tags = _col(_table(introspected, "public.orders"), "tags")
    assert tags.data_type.endswith("[]"), tags.data_type
    assert tags.data_type == "text[]"


def test_enum_column_values_in_order(introspected):
    status = _col(_table(introspected, "public.orders"), "status")
    assert "order_status" in status.data_type
    assert status.enum_values == ["active", "cancelled", "trial"]


def test_jsonb_column_type(introspected):
    metadata = _col(_table(introspected, "public.orders"), "metadata")
    assert metadata.data_type == "jsonb"
    assert metadata.enum_values is None


def test_numeric_precision_preserved(introspected):
    amount = _col(_table(introspected, "public.orders"), "amount")
    assert amount.data_type == "numeric(12,2)"


def test_nullable_vs_not_null(introspected):
    orders = _table(introspected, "public.orders")
    assert _col(orders, "amount").is_nullable is False
    assert _col(orders, "status").is_nullable is False
    assert _col(orders, "note").is_nullable is True
    assert _col(orders, "tags").is_nullable is True


def test_composite_primary_key_ordered(introspected):
    parent = _table(introspected, "public.parent")
    # PK definition order, NOT physical/attnum order (the trap).
    assert parent.primary_key == ["tenant_id", "parent_code"]
    assert _col(parent, "tenant_id").is_primary_key is True
    assert _col(parent, "parent_code").is_primary_key is True
    assert _col(parent, "label").is_primary_key is False


def test_comments_captured(introspected):
    orders = _table(introspected, "public.orders")
    assert orders.comment == "Orders with enum/array/jsonb/numeric columns."
    assert _col(orders, "status").comment == "Lifecycle status of the order."
    parent = _table(introspected, "public.parent")
    assert parent.comment == "Parent table with a composite primary key."


def test_cross_schema_fk_resolves_to_schema(introspected):
    fk = _fk(introspected, "billing_invoice_order_fk")
    assert fk.from_schema == "billing" and fk.from_table == "invoice"
    assert fk.to_schema == "public" and fk.to_table == "orders"
    assert fk.from_columns == ["order_id"] and fk.to_columns == ["order_id"]

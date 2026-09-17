"""Read any PostgreSQL database's real structure from the system catalogs.

``introspect_schema(engine)`` returns a typed :class:`IntrospectedSchema` for
whatever database the given SQLAlchemy AsyncEngine points at — production passes
``get_target_engine()`` (the read-only target); tests pass a temp engine to a
throwaway database. It is generic by construction: no table or schema name is
hardcoded, only the SYSTEM-schema exclusions below.

Read-only and side-effect-free: SELECTs against pg_catalog only, never a write or
DDL. Type fidelity comes from ``pg_catalog.format_type`` (faithful to arrays,
precision, varchar length, enum names — unlike information_schema, which flattens
arrays to ARRAY and enums to USER-DEFINED). Foreign keys come from
``pg_constraint`` with the conkey/confkey ordinal arrays unnested *together* so
composite FKs pair correctly: from_columns[i] references to_columns[i].
"""
from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from nixus.schema.models import (
    Column,
    ForeignKey,
    IntrospectedSchema,
    Table,
)

# The ONLY hardcoded schema names allowed: PostgreSQL's system schemas. Two are
# exact; pg_toast* / pg_temp* are matched by pattern. Everything else (public and
# any user-created schema) is in scope. Passed as a parameter so callers/tests
# could override the exact-name exclusions if ever needed.
SYSTEM_SCHEMAS: tuple[str, ...] = ("pg_catalog", "information_schema")

# Shared schema-scope predicate. `n` must be the pg_namespace alias in the query.
_SCHEMA_FILTER = (
    "n.nspname NOT IN :excluded "
    "AND n.nspname NOT LIKE 'pg_toast%' "
    "AND n.nspname NOT LIKE 'pg_temp%'"
)


def _q(sql: str):
    """text() with the expanding system-schema exclusion bound in."""
    return text(sql).bindparams(bindparam("excluded", expanding=True))


# Base tables (relkind 'r') in user schemas. Views and partitioned-table parents
# are intentionally out of scope for V1 (tables are the requirement).
_TABLES_SQL = _q(f"""
    SELECT c.oid AS table_oid,
           n.nspname AS schema,
           c.relname AS name,
           obj_description(c.oid, 'pg_class') AS comment
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND {_SCHEMA_FILTER}
    ORDER BY n.nspname, c.relname
""")

# Columns in ordinal order; nothing dropped (attnum>0, NOT attisdropped). Type via
# format_type; enum-ness via the column type's typtype ('e').
_COLUMNS_SQL = _q(f"""
    SELECT a.attrelid AS table_oid,
           a.attname AS name,
           pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
           NOT a.attnotnull AS is_nullable,
           pg_get_expr(ad.adbin, ad.adrelid) AS default,
           col_description(a.attrelid, a.attnum) AS comment,
           a.atttypid AS type_oid,
           t.typtype::text AS type_kind   -- ::text so it returns 'e', not bytes b'e'
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_type t ON t.oid = a.atttypid
    LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
    WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind = 'r' AND {_SCHEMA_FILTER}
    ORDER BY a.attrelid, a.attnum
""")

# Primary keys, composite-aware and ordered (conkey unnested WITH ORDINALITY).
_PK_SQL = _q(f"""
    SELECT con.conrelid AS table_oid,
           a.attname AS column_name,
           k.ord AS ordinal
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
    WHERE con.contype = 'p' AND {_SCHEMA_FILTER}
    ORDER BY con.conrelid, k.ord
""")

# Foreign keys. conkey/confkey unnested TOGETHER so referencing and referenced
# columns pair positionally — the critical bit for composite FKs. Scope is FKs
# whose FROM table is in a user schema; the TO schema is captured as-is (so a
# cross-schema FK keeps the correct to_schema).
_FK_SQL = _q("""
    SELECT con.oid AS constraint_oid,
           con.conname AS constraint_name,
           fn.nspname AS from_schema,
           fc.relname AS from_table,
           fa.attname AS from_column,
           tn.nspname AS to_schema,
           tc.relname AS to_table,
           ta.attname AS to_column,
           k.ord AS ordinal
    FROM pg_constraint con
    JOIN pg_class fc ON fc.oid = con.conrelid
    JOIN pg_namespace fn ON fn.oid = fc.relnamespace
    JOIN pg_class tc ON tc.oid = con.confrelid
    JOIN pg_namespace tn ON tn.oid = tc.relnamespace
    CROSS JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(conkey, confkey, ord)
    JOIN pg_attribute fa ON fa.attrelid = con.conrelid AND fa.attnum = k.conkey
    JOIN pg_attribute ta ON ta.attrelid = con.confrelid AND ta.attnum = k.confkey
    WHERE con.contype = 'f'
      AND fn.nspname NOT IN :excluded
      AND fn.nspname NOT LIKE 'pg_toast%'
      AND fn.nspname NOT LIKE 'pg_temp%'
    ORDER BY con.oid, k.ord
""")

# Enum *definitions*: every enum type's labels in sort order. Structural type
# metadata (pg_enum), not row data.
_ENUM_SQL = text("""
    SELECT e.enumtypid AS type_oid, e.enumlabel AS label
    FROM pg_enum e
    ORDER BY e.enumtypid, e.enumsortorder
""")


async def introspect_schema(engine: AsyncEngine) -> IntrospectedSchema:
    """Introspect the database behind ``engine`` and return its typed structure.

    Pure read: catalog SELECTs only. Accepts any SQLAlchemy AsyncEngine, so it is
    generic over whatever connection it is handed (a read-only target in prod, a
    throwaway db in tests).
    """
    excluded = list(SYSTEM_SCHEMAS)

    async with engine.connect() as conn:
        database = (await conn.execute(text("SELECT current_database()"))).scalar()

        enum_rows = (await conn.execute(_ENUM_SQL)).mappings().all()
        table_rows = (await conn.execute(_TABLES_SQL, {"excluded": excluded})).mappings().all()
        column_rows = (await conn.execute(_COLUMNS_SQL, {"excluded": excluded})).mappings().all()
        pk_rows = (await conn.execute(_PK_SQL, {"excluded": excluded})).mappings().all()
        fk_rows = (await conn.execute(_FK_SQL, {"excluded": excluded})).mappings().all()

    # enum type oid -> ordered labels
    enums: dict[int, list[str]] = {}
    for r in enum_rows:
        enums.setdefault(r["type_oid"], []).append(r["label"])

    # table oid -> ordered PK column names
    pks: dict[int, list[str]] = {}
    for r in pk_rows:
        pks.setdefault(r["table_oid"], []).append(r["column_name"])

    # Build tables, preserving discovery order (schema, name).
    tables_by_oid: dict[int, Table] = {}
    for r in table_rows:
        tables_by_oid[r["table_oid"]] = Table(
            schema=r["schema"],
            name=r["name"],
            columns=[],
            primary_key=pks.get(r["table_oid"], []),
            comment=r["comment"],
        )

    # Attach columns in ordinal order (query is ORDER BY attrelid, attnum).
    for r in column_rows:
        table = tables_by_oid.get(r["table_oid"])
        if table is None:
            continue
        pk_cols = pks.get(r["table_oid"], [])
        is_enum = r["type_kind"] == "e"
        table.columns.append(Column(
            name=r["name"],
            data_type=r["data_type"],
            is_nullable=r["is_nullable"],
            default=r["default"],
            is_primary_key=r["name"] in pk_cols,
            comment=r["comment"],
            enum_values=enums.get(r["type_oid"]) if is_enum else None,
        ))

    # Assemble foreign keys, grouping ordinal rows per constraint (by oid, since
    # constraint NAMES are not globally unique) and preserving the positional
    # column pairing.
    fks_by_oid: dict[int, ForeignKey] = {}
    for r in fk_rows:
        fk = fks_by_oid.get(r["constraint_oid"])
        if fk is None:
            fk = ForeignKey(
                constraint_name=r["constraint_name"],
                from_schema=r["from_schema"],
                from_table=r["from_table"],
                from_columns=[],
                to_schema=r["to_schema"],
                to_table=r["to_table"],
                to_columns=[],
            )
            fks_by_oid[r["constraint_oid"]] = fk
        fk.from_columns.append(r["from_column"])
        fk.to_columns.append(r["to_column"])

    return IntrospectedSchema(
        database=database,
        tables=list(tables_by_oid.values()),
        foreign_keys=list(fks_by_oid.values()),
    )

"""Grounded SQL verification — does the generated SQL reference only tables and
columns that actually EXIST in the schema?

This is the code embodiment of "trust over capability": we VERIFY grounding
instead of merely asking the model to be grounded. ``check_grounding`` is a pure,
unit-testable function over a :class:`SchemaView` (the authoritative
table -> columns map). The graph node in ``nodes/verify_grounding.py`` is a thin
state-in/state-out wrapper around it.

STRICTNESS — Option B, and THE GOVERNING RULE: a false positive (rejecting valid
SQL) is worse than a false negative (missing a hallucination). When unsure, PASS.
  - TABLE references in FROM/JOIN are verified rigorously: a real table that is
    provably not in the schema (and is not a CTE/derived source) is flagged.
  - COLUMN references are confidence-gated: flagged only when they belong to a
    confidently-resolved real table that demonstrably lacks them. Anything
    ambiguous — unqualified columns with several tables in scope, CTE/subquery
    columns, query-defined aliases, stars, computed expressions — PASSES.

Syntax is NOT this module's job: if sqlglot cannot parse the SQL we return a
"not checked" result and let ``validate_syntax`` own it. Identifier matching is
case-insensitive, which is the false-positive-safe direction (Postgres folds
unquoted identifiers; mis-cased identifiers fail at execution anyway).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from nixus.schema.models import IntrospectedSchema


@dataclass(frozen=True)
class SchemaView:
    """Authoritative table -> columns map for grounding.

    Keyed by table name -> the table's column names. TABLE KEYS are normalized to
    lower case on construction, so a caller that hand-builds with original-case
    keys (e.g. ``{'Track': ...}``) behaves IDENTICALLY to lower-cased keys — the
    type cannot be silently misused. Same-named tables (after normalization) are
    merged (union of columns), which only makes verification MORE permissive — the
    false-positive-safe direction.

    COLUMN-NAME CASE IS PRESERVED. Postgres preserves the case of quoted
    identifiers, and ``column_exists`` compares case-insensitively, so columns are
    stored exactly as introspected and matched without case sensitivity (the
    semantics CHECK 4 relied on). Only the TABLE key is normalized.
    """

    tables: dict[str, set[str]]

    def __post_init__(self) -> None:
        # Normalize table keys to lower case at construction (merging any keys
        # that collide once folded). Frozen dataclass → assign via object.
        normalized: dict[str, set[str]] = {}
        for name, cols in self.tables.items():
            normalized.setdefault(name.lower(), set()).update(cols)
        object.__setattr__(self, "tables", normalized)

    def has_table(self, name: str) -> bool:
        return name.lower() in self.tables

    def columns_of(self, name: str) -> set[str]:
        return self.tables.get(name.lower(), set())

    def column_exists(self, table: str, column: str) -> bool:
        return column.lower() in {c.lower() for c in self.columns_of(table)}


@dataclass
class GroundingResult:
    is_grounded: bool
    hallucinated_tables: list[str] = field(default_factory=list)
    hallucinated_columns: list[str] = field(default_factory=list)
    message: str = ""
    # False when the SQL could not be parsed → grounding was NOT performed (a
    # syntax concern owned by validate_syntax, not a grounding failure).
    checked: bool = True


def schema_view_from_introspection(schema: IntrospectedSchema) -> SchemaView:
    """Build a :class:`SchemaView` from a live ``IntrospectedSchema``.

    Introspection (not the retrieved top-k schema context) is the authoritative
    source: top-k retrieval omits tables, and a missing-but-valid table would be
    a false positive. The full catalog cannot.
    """
    tables: dict[str, set[str]] = {}
    for t in schema.tables:
        cols = tables.setdefault(t.name.lower(), set())
        cols.update(c.name for c in t.columns)
    return SchemaView(tables=tables)


def check_grounding(sql: str, schema: SchemaView) -> GroundingResult:
    """Verify that ``sql`` references only tables/columns present in ``schema``."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except ParseError:
        return GroundingResult(True, checked=False,
                               message="SQL did not parse; grounding skipped (syntax validated elsewhere).")
    if tree is None:
        return GroundingResult(True, checked=False, message="Empty parse; grounding skipped.")

    # CTE names and query-defined aliases are valid identifiers that must NEVER be
    # flagged (a CTE is a real source; an alias like `... AS "Total"` is not a
    # schema column even when later referenced in ORDER BY/HAVING).
    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    defined_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}

    # Classify every FROM/JOIN source by the name it is referenced under (alias if
    # present, else table name): real tables we can check columns against, vs.
    # "non-checkable" sources (CTE refs, subquery/derived aliases, hallucinated
    # tables) whose columns we must not check.
    alias_real_tables: dict[str, set[str]] = {}
    non_checkable: set[str] = set()
    hallucinated_tables: list[str] = []
    seen_tables: set[str] = set()

    for t in tree.find_all(exp.Table):
        name = t.name
        ref = (t.alias or name).lower()
        if name.lower() in cte_names:
            non_checkable.add(ref)
            continue
        if not schema.has_table(name):  # rigorous: a real table MUST exist
            if name.lower() not in seen_tables:
                seen_tables.add(name.lower())
                hallucinated_tables.append(name)
            non_checkable.add(ref)  # don't column-check an unknown table
            continue
        alias_real_tables.setdefault(ref, set()).add(name)

    for sub in tree.find_all(exp.Subquery):
        if sub.alias:
            non_checkable.add(sub.alias.lower())

    distinct_real = {n.lower() for names in alias_real_tables.values() for n in names}
    real_names = {n for names in alias_real_tables.values() for n in names}
    # An unqualified column is only confidently resolvable when EXACTLY one real
    # table is in scope and there is nothing else it could come from.
    single_table = next(iter(real_names)) if (len(distinct_real) == 1 and not non_checkable) else None

    hallucinated_columns: list[str] = []
    seen_cols: set[str] = set()

    def flag_column(table: str, name: str) -> None:
        offender = f"{table}.{name}"
        if offender.lower() not in seen_cols:
            seen_cols.add(offender.lower())
            hallucinated_columns.append(offender)

    for col in tree.find_all(exp.Column):
        name = col.name
        if not name or name == "*" or isinstance(col.this, exp.Star):
            continue
        if name.lower() in defined_aliases:  # query-defined alias, not a schema column
            continue

        qualifier = col.table
        if qualifier:
            q = qualifier.lower()
            reals = alias_real_tables.get(q)
            if q in non_checkable or not reals:
                continue  # CTE/derived/unknown qualifier → cannot/should not check
            if not any(schema.column_exists(rt, name) for rt in reals):
                flag_column(min(reals), name)
        elif single_table is not None:
            if not schema.column_exists(single_table, name):
                flag_column(single_table, name)
        # else: unqualified + ambiguous scope → PASS (do not guess)

    is_grounded = not hallucinated_tables and not hallucinated_columns
    return GroundingResult(
        is_grounded=is_grounded,
        hallucinated_tables=hallucinated_tables,
        hallucinated_columns=hallucinated_columns,
        message=_build_message(hallucinated_tables, hallucinated_columns, schema),
    )


def _build_message(tables: list[str], columns: list[str], schema: SchemaView) -> str:
    if not tables and not columns:
        return "All table and column references are grounded in the schema."
    parts = [f"table '{t}' does not exist in the database schema" for t in tables]
    for col in columns:
        tbl, _, cname = col.partition(".")
        avail = sorted(schema.columns_of(tbl))
        avail_str = ", ".join(avail[:20]) if avail else "(none)"
        parts.append(f"column '{cname}' does not exist on table '{tbl}'. Available columns: {avail_str}")
    return "Schema grounding failed: " + "; ".join(parts)

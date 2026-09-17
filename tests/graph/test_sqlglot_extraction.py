"""sqlglot edge-case extraction suite — the faithful adversary for grounding.

Grounding's guarantee rests on sqlglot's AST extraction correctly seeing EVERY
table and column inside the hard SQL shapes real generation emits. If a construct
silently mis-parses, grounding has a blind spot. This suite hunts those blind
spots across Option A constructs (nested/recursive CTEs, window functions, scalar
& IN/EXISTS subqueries, JOIN..USING, LATERAL, CASE, aggregate FILTER, Postgres
casts, quoting variants, UNION). It is NOT exhaustive conformance.

For each construct there are TWO guards, exercised THROUGH ``check_grounding`` (the
way grounding actually uses extraction):
  (a) a VALID query → must be GROUNDED  (the false-positive guard for that shape)
  (b) a query hiding a HALLUCINATED identifier → must be FLAGGED with the exact
      name (proves extraction sees INTO the construct, not just around it)

THE GOVERNING RULE: a false positive (rejecting valid SQL) is worse than a false
negative. Where extraction cannot confidently resolve an identifier, grounding
PASSES (defers to execution + self_correct). Two such CONSERVATIVE blind spots are
documented below with explicit tests asserting the err-toward-PASS behavior — they
are false negatives by design, never false positives, and require no code change.

FINDINGS (see TestConservativeBlindSpots):
  • JOIN..USING column lists are NOT column-checked: sqlglot represents them as
    exp.Identifier, not exp.Column, so find_all(exp.Column) never sees them. The
    joined TABLES are still verified; a nonexistent USING column is passed.
  • UNQUALIFIED columns inside a multi-source scope (CTE bodies, UNION branches,
    multi-table FROM) are passed: grounding only checks an unqualified column when
    exactly one real table is in scope, else it is ambiguous → PASS.
Qualified-column and hidden-table hallucinations ARE caught inside every construct.
"""
import pytest

from nixus.graph.grounding import SchemaView, check_grounding

VIEW = SchemaView(tables={
    "Track":   {"TrackId", "Name", "AlbumId", "Milliseconds", "UnitPrice"},
    "Album":   {"AlbumId", "Title", "ArtistId"},
    "Artist":  {"ArtistId", "Name"},
    "Invoice": {"InvoiceId", "CustomerId", "Total", "InvoiceDate"},
})


def flagged_names(result) -> str:
    return " ".join(result.hallucinated_tables + result.hallucinated_columns) + " " + result.message


# ── (a) VALID-case false-positive guards: every hard shape must be GROUNDED ──────
VALID_CASES = [
    ("nested_cte",
     'WITH a AS (SELECT "AlbumId" FROM "Track"), b AS (SELECT "AlbumId" FROM a) SELECT * FROM b'),
    ("window_function",
     'SELECT "Name", row_number() OVER (PARTITION BY "AlbumId" ORDER BY "Milliseconds") FROM "Track"'),
    ("scalar_subquery_in_select",
     'SELECT "Name", (SELECT count(*) FROM "Album" WHERE "Album"."AlbumId" = "Track"."AlbumId") FROM "Track"'),
    ("subquery_in_where_in",
     'SELECT "Name" FROM "Track" WHERE "AlbumId" IN (SELECT "AlbumId" FROM "Album")'),
    ("subquery_in_where_exists",
     'SELECT "Name" FROM "Track" t WHERE EXISTS (SELECT 1 FROM "Album" a WHERE a."AlbumId" = t."AlbumId")'),
    ("join_using",
     'SELECT "Title" FROM "Track" JOIN "Album" USING ("AlbumId")'),
    ("lateral_join",
     ('SELECT t."Name", x.* FROM "Track" t, '
      'LATERAL (SELECT "Title" FROM "Album" WHERE "Album"."AlbumId" = t."AlbumId") x')),
    ("case_expression",
     "SELECT CASE WHEN \"Milliseconds\" > 1000 THEN 'long' ELSE 'short' END FROM \"Track\""),
    ("aggregate_filter",
     'SELECT count(*) FILTER (WHERE "UnitPrice" > 1) FROM "Track"'),
    ("postgres_casts",
     'SELECT "UnitPrice"::numeric, CAST("Milliseconds" AS int) FROM "Track"'),
    ("union",
     'SELECT "Name" FROM "Track" UNION SELECT "Title" FROM "Album"'),
    ("union_all",
     'SELECT "Name" FROM "Track" UNION ALL SELECT "Title" FROM "Album"'),
    ("recursive_cte_numbers",
     'WITH RECURSIVE nums AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM nums WHERE n < 5) SELECT n FROM nums'),
    ("recursive_cte_real_table",
     ('WITH RECURSIVE t AS (SELECT "AlbumId" FROM "Track" WHERE "AlbumId" = 1 '
      'UNION ALL SELECT "AlbumId" FROM t) SELECT * FROM t')),
]


@pytest.mark.parametrize("name,sql", VALID_CASES, ids=[c[0] for c in VALID_CASES])
def test_valid_constructs_are_grounded(name, sql):
    r = check_grounding(sql, VIEW)
    assert r.checked is True
    assert r.is_grounded, f"FALSE POSITIVE on {name}: {r.message}"


# ── (b) blind-spot guards: a hallucination hidden INSIDE the construct is caught ─
# Each proves extraction recurses into the construct, via a form grounding can
# confidently resolve (qualified column, or a nonexistent table).
HIDDEN_CAUGHT_CASES = [
    ("nested_cte / qualified col",
     'WITH a AS (SELECT "Track"."Bogus" FROM "Track"), b AS (SELECT "AlbumId" FROM a) SELECT * FROM b', "Bogus"),
    ("window / partition-by col",
     'SELECT "Name", row_number() OVER (PARTITION BY "Bogus" ORDER BY "Milliseconds") FROM "Track"', "Bogus"),
    ("scalar_subquery / qualified col",
     'SELECT "Name", (SELECT "Album"."Bogus" FROM "Album" WHERE "Album"."AlbumId" = "Track"."AlbumId") FROM "Track"', "Bogus"),
    ("where_in / hidden table",
     'SELECT "Name" FROM "Track" WHERE "AlbumId" IN (SELECT "AlbumId" FROM "Bogus")', "Bogus"),
    ("where_in / qualified col",
     'SELECT "Name" FROM "Track" WHERE "AlbumId" IN (SELECT "Album"."Bogus" FROM "Album")', "Bogus"),
    ("join_using / hidden table",
     'SELECT "Title" FROM "Track" JOIN "Bogus" USING ("AlbumId")', "Bogus"),
    ("lateral / qualified col",
     ('SELECT t."Name", x.* FROM "Track" t, '
      'LATERAL (SELECT "Album"."Bogus" FROM "Album" WHERE "Album"."AlbumId" = t."AlbumId") x'), "Bogus"),
    ("case / unqualified col (single table)",
     "SELECT CASE WHEN \"Bogus\" > 1000 THEN 'long' ELSE 'short' END FROM \"Track\"", "Bogus"),
    ("filter / unqualified col (single table)",
     'SELECT count(*) FILTER (WHERE "Bogus" > 1) FROM "Track"', "Bogus"),
    ("cast / unqualified col (single table)",
     'SELECT "Bogus"::numeric FROM "Track"', "Bogus"),
    ("union / hidden table",
     'SELECT "Name" FROM "Track" UNION SELECT "Title" FROM "Bogus"', "Bogus"),
    ("union / qualified col",
     'SELECT t."Bogus" FROM "Track" t UNION SELECT "Title" FROM "Album"', "Bogus"),
    ("recursive_cte / qualified col in base term",
     'WITH RECURSIVE t AS (SELECT "Track"."Bogus" FROM "Track" UNION ALL SELECT "Bogus" FROM t) SELECT * FROM t', "Bogus"),
]


@pytest.mark.parametrize("name,sql,offender", HIDDEN_CAUGHT_CASES, ids=[c[0] for c in HIDDEN_CAUGHT_CASES])
def test_hidden_hallucination_is_flagged(name, sql, offender):
    r = check_grounding(sql, VIEW)
    assert not r.is_grounded, f"BLIND SPOT — extraction missed the hallucination in {name}"
    assert offender in flagged_names(r), f"{name}: offender {offender!r} not named in {flagged_names(r)!r}"


# ── Identifier-quoting semantics (stated deliberately, not incidental) ───────────
# Grounding matches identifiers CASE-INSENSITIVELY — the false-positive-safe
# direction. So an unquoted `track` matches schema "Track" for grounding even
# though Postgres (which folds unquoted to lower) would reject it at execution.
# That genuine case-mismatch is left to execution/self_correct: grounding errs
# toward PASS and never rejects on casing alone. A truly-unknown name is flagged.
def test_quoting_quoted_pascalcase_grounded():
    assert check_grounding('SELECT "Name" FROM "Track"', VIEW).is_grounded


def test_quoting_unquoted_folds_and_matches_case_insensitively():
    # Documented intent: case-insensitive match → GROUNDED (conservative).
    assert check_grounding('SELECT name FROM track', VIEW).is_grounded


def test_quoting_unknown_identifier_still_flagged():
    r = check_grounding('SELECT x FROM bogus', VIEW)
    assert not r.is_grounded and "bogus" in flagged_names(r).lower()


def test_recursive_self_reference_is_not_a_hallucinated_table():
    # The recursive self-reference (FROM nums inside the nums CTE) must be treated
    # as a valid CTE source, never flagged as an unknown table.
    r = check_grounding(
        'WITH RECURSIVE nums AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM nums WHERE n < 5) SELECT n FROM nums',
        VIEW)
    assert r.is_grounded and not r.hallucinated_tables


# ── Documented CONSERVATIVE blind spots (err-toward-PASS; no code change) ────────
# These are false negatives BY DESIGN. The suite found them; the governing rule
# says when extraction cannot confidently resolve an identifier, grounding PASSES
# and execution/self_correct is the backstop. Asserting them here pins the
# intended behavior so a future change that started REJECTING them would be caught.
class TestConservativeBlindSpots:

    def test_join_using_column_not_checked(self):
        # FINDING: sqlglot parses USING (...) columns as exp.Identifier, not
        # exp.Column → grounding does not column-check them. The joined tables ARE
        # verified; a nonexistent USING column is conservatively passed.
        r = check_grounding('SELECT "Title" FROM "Track" JOIN "Album" USING ("Bogus")', VIEW)
        assert r.is_grounded, "USING-column was rejected — must stay conservative (PASS)"

    def test_unqualified_col_in_cte_body_passed(self):
        # Unqualified col inside a CTE body: the CTE ref makes scope multi-source →
        # ambiguous → PASS. (A QUALIFIED hallucination here IS caught — see above.)
        r = check_grounding('WITH a AS (SELECT "Bogus" FROM "Track") SELECT * FROM a', VIEW)
        assert r.is_grounded

    def test_unqualified_col_in_union_branch_passed(self):
        # UNION puts >1 real table in scope → unqualified col is ambiguous → PASS.
        r = check_grounding('SELECT "Bogus" FROM "Track" UNION SELECT "Title" FROM "Album"', VIEW)
        assert r.is_grounded

    def test_unqualified_col_in_recursive_cte_passed(self):
        r = check_grounding(
            'WITH RECURSIVE t AS (SELECT "Bogus" FROM "Track" UNION ALL SELECT n FROM t) SELECT * FROM t',
            VIEW)
        assert r.is_grounded

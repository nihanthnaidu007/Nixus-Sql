"""Unit tests for the grounding checker (prompt 3.1).

``check_grounding`` is exercised directly against a small hand-built SchemaView —
no database. The PASS cases are the FALSE-POSITIVE GUARD: the governing rule is
that rejecting valid SQL is worse than missing a hallucination, so every valid
shape (aliases, CTEs, subqueries, stars, computed/aliased expressions, ambiguous
unqualified columns) MUST come back grounded. The FAIL cases prove the checker
still catches provable hallucinations and names the offending identifier.
"""
import pytest

from nixus.graph.grounding import SchemaView, check_grounding

# Tables: Track{TrackId, Name, AlbumId}, Album{AlbumId, Title}
VIEW = SchemaView(tables={
    "track": {"TrackId", "Name", "AlbumId"},
    "album": {"AlbumId", "Title"},
})


PASS_CASES = [
    ("simple valid", 'SELECT "Name" FROM "Track"'),
    ("alias resolution", 'SELECT t."Name" FROM "Track" t'),
    ("join with aliases",
     'SELECT t."Name", a."Title" FROM "Track" t JOIN "Album" a ON t."AlbumId" = a."AlbumId"'),
    ("CTE", 'WITH r AS (SELECT "TrackId" FROM "Track") SELECT * FROM r'),
    ("CTE column reference",
     'WITH r AS (SELECT "TrackId" AS id FROM "Track") SELECT r.id FROM r'),
    ("subquery/derived alias",
     'SELECT s.x FROM (SELECT "TrackId" AS x FROM "Track") s'),
    ("SELECT *", 'SELECT * FROM "Track"'),
    ("aggregate/computed", 'SELECT count(*), max("AlbumId") FROM "Track"'),
    ("ambiguous unqualified column across tables",
     'SELECT "Title" FROM "Track" t JOIN "Album" a ON t."AlbumId" = a."AlbumId"'),
    # Extra false-positive guards modeled on real gold queries (D05/F01 style):
    ("alias used in ORDER BY (not a schema column)",
     'SELECT count(*) AS "Cnt" FROM "Track" ORDER BY "Cnt" DESC'),
]


@pytest.mark.parametrize("label,sql", PASS_CASES, ids=[c[0] for c in PASS_CASES])
def test_pass_cases_are_grounded(label, sql):
    result = check_grounding(sql, VIEW)
    assert result.is_grounded, (
        f"FALSE POSITIVE on '{label}': {result.message} "
        f"(tables={result.hallucinated_tables}, columns={result.hallucinated_columns})"
    )


def test_hallucinated_table_is_flagged():
    result = check_grounding('SELECT * FROM "Customers"', VIEW)
    assert not result.is_grounded
    assert "Customers" in result.hallucinated_tables
    assert "Customers" in result.message


def test_hallucinated_column_on_real_table_is_flagged():
    result = check_grounding('SELECT "Revenue" FROM "Track"', VIEW)
    assert not result.is_grounded
    assert any("Revenue" in c for c in result.hallucinated_columns)
    assert "Revenue" in result.message


def test_qualified_hallucinated_column_is_flagged():
    result = check_grounding('SELECT t."Revenue" FROM "Track" t', VIEW)
    assert not result.is_grounded
    assert any("Revenue" in c for c in result.hallucinated_columns)
    assert "Revenue" in result.message


def test_parse_failure_is_not_a_grounding_failure():
    # Syntax is validate_syntax's job — a parse error must NOT be reported as a
    # grounding failure (checked=False, is_grounded=True so it passes through).
    result = check_grounding("SELECT FROM WHERE )(", VIEW)
    assert result.is_grounded
    assert result.checked is False

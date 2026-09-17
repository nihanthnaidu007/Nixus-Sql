"""
Chart classification tests — Category 5 of the NIXUS SQL evaluation harness.

Uses /api/run-sql (not /api/run) to bypass LLM generation and directly test
the classify_chart_node with known, controlled result shapes.

Chart logic summary:
  - Column name contains date/time/year/month/day/… keyword  +  numeric  →  "line"
  - categorical + numeric, sorted DESC (is_monotonic_decreasing)           →  "bar"
  - categorical + numeric, NOT sorted DESC, ≤ PIE_MAX_SLICES rows/cats     →  "pie"
  - two or more numeric columns (no categorical/date)                      →  "scatter"
  - < 2 rows, or no matching column types                                  →  "none"
"""

import pytest

from eval.conftest import run_sql

# Each entry: (test_id, sql, expected_chart_type)
CHART_CASES = [
    (
        "line_year_revenue",
        # "Year" column triggers the date-keyword check → "line"
        (
            'SELECT EXTRACT(YEAR FROM "InvoiceDate")::INT AS "Year", '
            'ROUND(SUM("Total")::NUMERIC, 2) AS "Revenue" '
            'FROM "Invoice" GROUP BY "Year" ORDER BY "Year"'
        ),
        "line",
    ),
    (
        "bar_genre_count_desc",
        # Sorted DESC → is_monotonic_decreasing=True → "bar"
        (
            'SELECT g."Name" AS "Genre", COUNT(t."TrackId") AS "TrackCount" '
            'FROM "Genre" g JOIN "Track" t ON g."GenreId" = t."GenreId" '
            'GROUP BY g."GenreId", g."Name" ORDER BY "TrackCount" DESC LIMIT 25'
        ),
        "bar",
    ),
    (
        "bar_artist_albums_desc",
        # Top-10 ranked list → is_ranked=True → "bar"
        (
            'SELECT ar."Name" AS "Artist", COUNT(al."AlbumId") AS "AlbumCount" '
            'FROM "Artist" ar JOIN "Album" al ON ar."ArtistId" = al."ArtistId" '
            'GROUP BY ar."ArtistId", ar."Name" ORDER BY "AlbumCount" DESC LIMIT 10'
        ),
        "bar",
    ),
    (
        "pie_mediatype_distribution",
        # 5 rows, sorted by name ASC (not DESC) → not is_ranked → "pie"
        # MediaType has exactly 5 distinct types ≤ PIE_MAX_SLICES=6
        (
            'SELECT mt."Name" AS "MediaType", COUNT(t."TrackId") AS "TrackCount" '
            'FROM "MediaType" mt JOIN "Track" t ON mt."MediaTypeId" = t."MediaTypeId" '
            'GROUP BY mt."MediaTypeId", mt."Name" ORDER BY mt."Name"'
        ),
        "pie",
    ),
    (
        "scatter_two_numerics",
        # Two numeric columns, no categorical/date → "scatter"
        (
            'SELECT "Milliseconds", "UnitPrice" '
            'FROM "Track" WHERE "AlbumId" = 1 ORDER BY "TrackId"'
        ),
        "scatter",
    ),
    (
        "none_single_row",
        # Only 1 row → < 2 rows → "none"
        'SELECT "Name", "Milliseconds" FROM "Track" WHERE "TrackId" = 1',
        "none",
    ),
    (
        "none_text_only",
        # All text columns, no numeric → "none"
        (
            'SELECT "FirstName", "LastName", "Country" '
            'FROM "Customer" ORDER BY "LastName" LIMIT 5'
        ),
        "none",
    ),
]


@pytest.mark.parametrize(
    "sql,expected",
    [(sql, expected) for _, sql, expected in CHART_CASES],
    ids=[tid for tid, _, _ in CHART_CASES],
)
def test_chart_classification(http_client, sql, expected):
    state = run_sql(http_client, sql)

    error = state.get("error")
    assert not error, f"run-sql returned error: {error}\nSQL: {sql}"

    chart_config = state.get("chart_config") or {}
    actual = chart_config.get("chart_type", "none")

    assert actual == expected, (
        f"Expected chart_type={expected!r}, got {actual!r}\n"
        f"SQL: {sql}\n"
        f"Reasoning: {chart_config.get('reasoning')}"
    )

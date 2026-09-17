"""SchemaView robustness tests (Phase 3 follow-up to 3.1 CHECK 3).

3.1 surfaced a latent trap: SchemaView lookups lowercase the QUERY side, so a
caller that hand-built the view with original-case keys (``{'Track': ...}``)
silently matched nothing and every table read as "unknown" — over-strict misuse.
SchemaView now normalizes table keys on construction, so capitalized and
lower-cased keys behave identically. These tests lock that in by running the exact
verbatim spot-check that failed in 3.1 CHECK 3, BOTH ways, and asserting parity.
"""
import pytest

from nixus.graph.grounding import SchemaView, check_grounding

# Same tables, two key casings — must behave identically after normalization.
CAP = SchemaView(tables={"Track": {"TrackId", "Name", "AlbumId"},
                         "Album": {"AlbumId", "Title"}})
LOW = SchemaView(tables={"track": {"TrackId", "Name", "AlbumId"},
                         "album": {"AlbumId", "Title"}})

# The exact spot-check from 3.1 CHECK 3 (verbatim queries + expected grounded-ness).
SPOTCHECK = [
    ('WITH r AS (SELECT "TrackId" AS id FROM "Track") SELECT r.id FROM r', True),
    ('SELECT s.x FROM (SELECT "TrackId" AS x FROM "Track") s', True),
    ('SELECT "Revenue" FROM "Track"', False),
    ('SELECT * FROM "Customers"', False),
]


def test_construction_lowercases_table_keys():
    assert set(CAP.tables.keys()) == {"track", "album"}
    assert CAP.has_table("Track") and CAP.has_table("track") and CAP.has_table("TRACK")


def test_column_case_semantics_preserved():
    # Columns stored original-case, compared case-insensitively (CHECK 4 relied on
    # this — quoted PascalCase columns must still match). NOT changed by this fix.
    assert CAP.columns_of("track") == {"TrackId", "Name", "AlbumId"}
    assert CAP.column_exists("Track", "name") and CAP.column_exists("Track", "Name")


@pytest.mark.parametrize("sql,expected_grounded", SPOTCHECK,
                         ids=[s[:40] for s, _ in SPOTCHECK])
def test_spotcheck_identical_for_capitalized_and_lowercased_keys(sql, expected_grounded):
    cap = check_grounding(sql, CAP)
    low = check_grounding(sql, LOW)
    # The previously-failing verbatim spot-check now passes regardless of casing,
    assert cap.is_grounded is expected_grounded, f"capitalized-key view: {cap.message}"
    assert low.is_grounded is expected_grounded, f"lowercased-key view: {low.message}"
    # and the two key-casings produce byte-identical findings.
    assert cap.is_grounded == low.is_grounded
    assert cap.hallucinated_tables == low.hallucinated_tables
    assert cap.hallucinated_columns == low.hallucinated_columns


def test_colliding_case_keys_merge_columns():
    # Two keys that fold to the same table merge their columns (permissive).
    merged = SchemaView(tables={"Track": {"TrackId"}, "track": {"Name"}})
    assert merged.columns_of("track") == {"TrackId", "Name"}

"""
Load Chinook sample data into the TARGET database.

Chinook is sample TARGET data (the user's data), so it lives in its own database
and is loaded through a writable OWNER connection — the app's read-only target
role cannot create tables or insert. The connection URL comes from
``TARGET_ADMIN_DATABASE_URL`` (the owner connection to the target DB), falling
back to the legacy ``DATABASE_URL`` for back-compat. The table schema is ensured
idempotently before inserting, so this is safe against a freshly created
(empty-schema) target.

Default: downloads official ChinookData.json (PascalCase columns) from GitHub,
truncates the demo tables, and inserts rows.

Legacy: set CHINOOK_SOURCE_URL to a PostgreSQL URL whose DB already has the
lowercase Chinook tables (genre, artist, …) to copy from instead of using JSON.

Flags:
  --skip-if-exists  If the "Artist" table already has rows, exit 0 and do
                    nothing. Safe for repeated boot / CI runs.
  --force           Truncate and re-insert all data, even if rows exist.
                    Use this to reset the demo data.
  (no flags)        Default safe path: refuse to truncate when data exists,
                    print a warning, and ask the operator to pass --force.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import create_engine, text

from nixus.config import settings
from nixus.db.schema_init import CHINOOK_DDL

# Writable OWNER connection to the TARGET database. The app never uses this; only
# this one-time seed does. Prefer TARGET_ADMIN_DATABASE_URL; fall back to the
# legacy DATABASE_URL so existing single-DB setups still work.
_admin_url = settings.target_admin_url or settings.database_url
if not _admin_url:
    raise RuntimeError(
        "Set TARGET_ADMIN_DATABASE_URL (the writable owner connection to the "
        "target database) to seed Chinook. See .env.example."
    )
# str-annotated: the guard above is what makes this non-optional for mypy.
TARGET_ADMIN_URL: str = _admin_url

CHINOOK_JSON_DEFAULT = (
    "https://raw.githubusercontent.com/lerocha/chinook-database/"
    "master/ChinookDatabase/DataSources/ChinookData.json"
)

# FK-safe insert order (matches upstream JSON bundle)
JSON_TABLE_ORDER = [
    "Genre",
    "MediaType",
    "Artist",
    "Album",
    "Employee",
    "Customer",
    "Track",
    "Invoice",
    "InvoiceLine",
    "Playlist",
    "PlaylistTrack",
]

LEGACY_TABLES = [
    ("genre", '"Genre"', [("genre_id", "GenreId"), ("name", "Name")]),
    ("media_type", '"MediaType"', [("media_type_id", "MediaTypeId"), ("name", "Name")]),
    ("artist", '"Artist"', [("artist_id", "ArtistId"), ("name", "Name")]),
    ("album", '"Album"', [("album_id", "AlbumId"), ("title", "Title"), ("artist_id", "ArtistId")]),
    ("employee", '"Employee"', [
        ("employee_id", "EmployeeId"), ("last_name", "LastName"), ("first_name", "FirstName"),
        ("title", "Title"), ("reports_to", "ReportsTo"), ("birth_date", "BirthDate"),
        ("hire_date", "HireDate"), ("address", "Address"), ("city", "City"),
        ("state", "State"), ("country", "Country"), ("postal_code", "PostalCode"),
        ("phone", "Phone"), ("fax", "Fax"), ("email", "Email"),
    ]),
    ("customer", '"Customer"', [
        ("customer_id", "CustomerId"), ("first_name", "FirstName"), ("last_name", "LastName"),
        ("company", "Company"), ("address", "Address"), ("city", "City"),
        ("state", "State"), ("country", "Country"), ("postal_code", "PostalCode"),
        ("phone", "Phone"), ("fax", "Fax"), ("email", "Email"), ("support_rep_id", "SupportRepId"),
    ]),
    ("track", '"Track"', [
        ("track_id", "TrackId"), ("name", "Name"), ("album_id", "AlbumId"),
        ("media_type_id", "MediaTypeId"), ("genre_id", "GenreId"), ("composer", "Composer"),
        ("milliseconds", "Milliseconds"), ("bytes", "Bytes"), ("unit_price", "UnitPrice"),
    ]),
    ("invoice", '"Invoice"', [
        ("invoice_id", "InvoiceId"), ("customer_id", "CustomerId"), ("invoice_date", "InvoiceDate"),
        ("billing_address", "BillingAddress"), ("billing_city", "BillingCity"),
        ("billing_state", "BillingState"), ("billing_country", "BillingCountry"),
        ("billing_postal_code", "BillingPostalCode"), ("total", "Total"),
    ]),
    ("invoice_line", '"InvoiceLine"', [
        ("invoice_line_id", "InvoiceLineId"), ("invoice_id", "InvoiceId"),
        ("track_id", "TrackId"), ("unit_price", "UnitPrice"), ("quantity", "Quantity"),
    ]),
    ("playlist", '"Playlist"', [("playlist_id", "PlaylistId"), ("name", "Name")]),
    ("playlist_track", '"PlaylistTrack"', [("playlist_id", "PlaylistId"), ("track_id", "TrackId")]),
]


def _fetch_chinook_json() -> dict:
    import urllib.request

    url = os.environ.get("CHINOOK_JSON_URL", CHINOOK_JSON_DEFAULT)
    print(f"◈ Fetching Chinook dataset from:\n  {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Nixus-Sql-Agent/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ensure_schema(dst) -> None:
    """Create the Chinook tables in the target if absent (idempotent).

    Uses ``CREATE TABLE IF NOT EXISTS`` (the canonical DDL), so a freshly created
    target — or one provisioned by ``scripts/init-target-db.sql`` — is ready to
    receive rows. Runs through the owner connection.
    """
    with dst.connect() as conn:
        conn.execute(text(CHINOOK_DDL))
        conn.commit()


def _artist_count(dst) -> int:
    """Return current row count of the "Artist" table (0 if table missing)."""
    try:
        with dst.connect() as conn:
            return int(conn.execute(text('SELECT COUNT(*) FROM "Artist"')).scalar() or 0)
    except Exception:
        return 0


def _truncate_chinook(dst) -> None:
    print("  → Truncating existing Chinook tables...")
    with dst.connect() as conn:
        conn.execute(
            text(
                """
                TRUNCATE TABLE
                    "PlaylistTrack",
                    "InvoiceLine",
                    "Invoice",
                    "Playlist",
                    "Track",
                    "Customer",
                    "Employee",
                    "Album",
                    "Artist",
                    "Genre",
                    "MediaType"
                RESTART IDENTITY CASCADE
                """
            )
        )
        conn.commit()


def migrate_from_json(dst) -> int:
    data = _fetch_chinook_json()
    _truncate_chinook(dst)

    total = 0
    with dst.connect() as conn:
        for table in JSON_TABLE_ORDER:
            rows = data.get(table) or []
            if not rows:
                print(f"  → Inserting {table} (0 rows — skipped)")
                continue
            cols = list(rows[0].keys())
            col_sql = ", ".join(f'"{c}"' for c in cols)
            ph = ", ".join(f":{c}" for c in cols)
            stmt = text(f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph})')
            for row in rows:
                conn.execute(stmt, {c: row.get(c) for c in cols})
            print(f"  → Inserting {table} ({len(rows)} rows)")
            total += len(rows)
        conn.commit()

    return total


def migrate_from_legacy_postgres(src_url: str, dst) -> int:
    src = create_engine(src_url)
    _truncate_chinook(dst)
    total = 0
    with src.connect() as s, dst.connect() as d:
        d.execute(text("SET session_replication_role = replica"))
        for src_tbl, dst_tbl, cols in LEGACY_TABLES:
            src_cols = ", ".join(c[0] for c in cols)
            dst_cols = ", ".join(f'"{c[1]}"' for c in cols)
            placeholders = ", ".join(f":{c[0]}" for c in cols)
            rows = s.execute(text(f"SELECT {src_cols} FROM {src_tbl}")).mappings().all()
            if rows:
                stmt = text(f"INSERT INTO {dst_tbl} ({dst_cols}) VALUES ({placeholders})")
                for r in rows:
                    d.execute(stmt, dict(r))
            label = dst_tbl.strip('"')
            print(f"  → Inserting {label} ({len(rows)} rows)")
            total += len(rows)
        d.execute(text("SET session_replication_role = DEFAULT"))
        d.commit()
    return total


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load Chinook sample data into NIXUS_SQL.",
    )
    parser.add_argument(
        "--skip-if-exists",
        action="store_true",
        help='Exit 0 without changes when "Artist" already has rows.',
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate and re-insert even if data already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dst = create_engine(TARGET_ADMIN_URL)
    _ensure_schema(dst)
    legacy = os.environ.get("CHINOOK_SOURCE_URL", "").strip()

    existing = _artist_count(dst)

    if args.force:
        if existing > 0:
            print(f"◈ --force passed; will truncate {existing} existing artists and re-seed.")
    elif args.skip_if_exists:
        if existing > 0:
            print(
                f'◈ Chinook data already present (Artist count: {existing}). '
                f'Skipping migration. Use --force to override.'
            )
            return
    else:
        if existing > 0:
            print(
                f'⚠ Chinook data already present (Artist count: {existing}).\n'
                f'  Refusing to truncate without an explicit flag. Re-run with one of:\n'
                f'    --skip-if-exists   to no-op when data exists (safe for repeated boots)\n'
                f'    --force            to reset and re-seed all Chinook data'
            )
            sys.exit(1)

    print("◈ Chinook migration starting...")
    if legacy:
        print(f"◈ Using legacy CHINOOK_SOURCE_URL Postgres copy:\n  {legacy}")
        total = migrate_from_legacy_postgres(legacy, dst)
    else:
        total = migrate_from_json(dst)
    print(f"◈ Chinook migration complete. Total rows inserted: {total}")


if __name__ == "__main__":
    main()

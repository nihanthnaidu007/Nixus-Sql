"""
Deterministically (re)build the SaaS sample TARGET database — the honest
benchmark's data (6.1).

This is the SINGLE command 6.2 and audits use to rebuild the SaaS target from
scratch. It mirrors how Chinook is provisioned (scripts/init-target-db.sql for
the role/grants + scripts/migrate_chinook.py for the data), but folds the whole
lifecycle into one idempotent step so the data is reproducible:

    1. DROP + CREATE the SaaS database (default ``nixus_saas``), owned by the
       app's owner role — through the writable OWNER connection
       (TARGET_ADMIN_DATABASE_URL), exactly as the Chinook seed does. The app
       NEVER uses this handle.
    2. Ensure the read-only role (parsed from TARGET_DATABASE_URL, default
       ``nixus_readonly``) exists and GRANT it CONNECT.
    3. Load eval/saas_schema.sql then eval/saas_seed.sql (deterministic; no RNG).
    4. GRANT the read-only role USAGE + SELECT and ALTER DEFAULT PRIVILEGES, the
       same SELECT-only grant pattern as init-target-db.sql. The role stays
       strictly read-only here too — no INSERT/UPDATE/DELETE/DDL.

Because the schema + seed are fixed literals and pure arithmetic (no random(),
no now()), every run produces byte-identical data — verify with --verify.

Usage:
    .venv/bin/python scripts/rebuild_saas_db.py            # drop + rebuild
    .venv/bin/python scripts/rebuild_saas_db.py --verify   # rebuild + print a
                                                           # content signature

The Chinook default is untouched: this only ever creates/drops the SaaS database
and grants on it. To point NIXUS at the SaaS db for introspection/benchmark, set
TARGET_DATABASE_URL (and TARGET_ADMIN_DATABASE_URL) to .../nixus_saas — see the
"target switch" note in eval/saas_schema.sql's header / the report.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import psycopg2

from nixus.config import settings

SAAS_DB = os.environ.get("SAAS_DATABASE_NAME", "nixus_saas")
# Maintenance DB to connect to for server-level CREATE/DROP DATABASE. The state
# DB always exists in the local stack; "postgres" is the standard fallback.
MAINT_DB = os.environ.get("SAAS_MAINT_DATABASE", "nixus_sql")

_HERE = os.path.dirname(os.path.abspath(__file__))
_EVAL = os.path.join(os.path.dirname(_HERE), "eval")
SCHEMA_SQL = os.path.join(_EVAL, "saas_schema.sql")
SEED_SQL = os.path.join(_EVAL, "saas_seed.sql")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str, what: str) -> str:
    """Guard the few identifiers we interpolate (db/role names from env)."""
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe {what} identifier: {name!r}")
    return name


def _swap_db(url: str, db: str) -> str:
    """Return ``url`` with its database path replaced by ``db``."""
    base, _, _ = url.rpartition("/")
    return f"{base}/{db}"


def _parse_role(url: str) -> tuple[str, str]:
    """Pull (username, password) out of a SQLAlchemy/libpq URL."""
    parts = urlsplit(url)
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    if not user:
        raise RuntimeError(f"Could not parse a username from URL: {url!r}")
    return user, password


def _connect(dsn: str, autocommit: bool = False):
    conn = psycopg2.connect(dsn)
    conn.autocommit = autocommit
    return conn


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _resolve_urls() -> tuple[str, str, str, str]:
    owner_url = settings.target_admin_url or settings.database_url
    if not owner_url:
        raise RuntimeError(
            "Set TARGET_ADMIN_DATABASE_URL (the writable owner connection to the "
            "target server) to (re)build the SaaS database. See .env.example."
        )
    ro_url = settings.target_url
    if not ro_url:
        raise RuntimeError(
            "Set TARGET_DATABASE_URL (the read-only target role) so the SaaS db "
            "can be granted to it. See .env.example."
        )
    ro_user, ro_pass = _parse_role(ro_url)
    maint_dsn = _swap_db(owner_url, MAINT_DB)
    saas_dsn = _swap_db(owner_url, SAAS_DB)
    return maint_dsn, saas_dsn, ro_user, ro_pass


def _recreate_database(maint_dsn: str, db: str, ro_user: str, ro_pass: str) -> None:
    """Drop + recreate the SaaS db and ensure the read-only role can CONNECT."""
    db = _safe_ident(db, "database")
    ro_user = _safe_ident(ro_user, "role")
    ro_pass_lit = ro_pass.replace("'", "''")
    conn = _connect(maint_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            # Force-close any sessions on the old db so DROP can proceed.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
            cur.execute(f'CREATE DATABASE "{db}"')
            # Ensure the read-only role exists (it already does once Chinook's
            # init ran; create it idempotently so a bare server works too).
            cur.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ro_user}') THEN
                        CREATE ROLE "{ro_user}" LOGIN PASSWORD '{ro_pass_lit}';
                    END IF;
                END
                $$;
                """
            )
            cur.execute(f'GRANT CONNECT ON DATABASE "{db}" TO "{ro_user}"')
    finally:
        conn.close()
    print(f"  ✓ recreated database {db} (owner) and granted CONNECT to {ro_user}")


def _load_and_grant(saas_dsn: str, ro_user: str) -> None:
    """Load schema + seed, then apply the SELECT-only grant pattern (2.1)."""
    ro_user = _safe_ident(ro_user, "role")
    conn = _connect(saas_dsn, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(_read(SCHEMA_SQL))
            cur.execute(_read(SEED_SQL))
            cur.execute(f'GRANT USAGE ON SCHEMA public TO "{ro_user}"')
            cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{ro_user}"')
            cur.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f'GRANT SELECT ON TABLES TO "{ro_user}"'
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"  ✓ loaded schema + seed and granted SELECT-only to {ro_user}")


_TABLES = (
    "plans", "organizations", "users", "subscriptions",
    "usage_events", "invoices", "payments",
)


def _summary(saas_dsn: str) -> None:
    conn = _connect(saas_dsn)
    try:
        with conn.cursor() as cur:
            print("  row counts:")
            for t in _TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")
                print(f"    {t:<14} {cur.fetchone()[0]}")
    finally:
        conn.close()


def _signature(saas_dsn: str) -> str:
    """A deterministic content signature: per-table md5 over rows in id order.

    Two rebuilds must produce the same signature — the determinism guarantee.
    """
    conn = _connect(saas_dsn)
    parts: list[str] = []
    try:
        with conn.cursor() as cur:
            for t in _TABLES:
                cur.execute(
                    f"SELECT md5(coalesce(string_agg(r::text, '|' "
                    f"ORDER BY (r).id), '')) FROM {t} r"
                )
                parts.append(f"{t}={cur.fetchone()[0]}")
    finally:
        conn.close()
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="(Re)build the deterministic SaaS sample target db.")
    ap.add_argument(
        "--verify", action="store_true",
        help="After building, print a content signature (for byte-identical re-run checks).",
    )
    args = ap.parse_args()

    maint_dsn, saas_dsn, ro_user, ro_pass = _resolve_urls()
    print(f"◈ Rebuilding SaaS sample database '{SAAS_DB}' (deterministic seed)...")
    _recreate_database(maint_dsn, SAAS_DB, ro_user, ro_pass)
    _load_and_grant(saas_dsn, ro_user)
    _summary(saas_dsn)
    if args.verify:
        print("◈ Content signature (must be identical across rebuilds):")
        print(_signature(saas_dsn))
    print(f"◈ Done. '{SAAS_DB}' is a read-only target for {ro_user}.")


if __name__ == "__main__":
    main()

"""
Deterministically (re)build the RICH DEMO target database (`nixus_saas_demo`).

This is the demo counterpart of scripts/rebuild_saas_db.py. It mirrors that
script EXACTLY — same owner-connection lifecycle, same SELECT-only read-only
grant pattern, same determinism guarantee — but targets a SEPARATE database
(`nixus_saas_demo`) and loads the RICH demo seed (eval/saas_demo_seed.sql)
instead of the frozen benchmark seed.

  1. DROP + CREATE the demo database (default ``nixus_saas_demo``), owned by the
     app's owner role — through the writable OWNER connection
     (TARGET_ADMIN_DATABASE_URL). The app NEVER uses this handle.
  2. Ensure the read-only role (parsed from TARGET_DATABASE_URL, default
     ``nixus_readonly``) exists and GRANT it CONNECT.
  3. Load eval/saas_schema.sql (the SAME schema as the benchmark) then
     eval/saas_demo_seed.sql (deterministic; no RNG).
  4. GRANT the read-only role USAGE + SELECT and ALTER DEFAULT PRIVILEGES — the
     same SELECT-only grant pattern as rebuild_saas_db.py. The role stays
     strictly read-only here too — no INSERT/UPDATE/DELETE/DDL.

ISOLATION GUARANTEE: this script ONLY ever creates/drops ``nixus_saas_demo`` and
grants on it. It never touches ``nixus_saas`` (the frozen benchmark database) or
any benchmark asset. The demo and the benchmark live in separate databases, so
the demo data can NEVER perturb the benchmark.

Because the schema + demo seed are fixed literals and pure arithmetic (no
random(), no now()), every run produces byte-identical data — verify with
--verify.

Usage:
    .venv/bin/python scripts/rebuild_demo_db.py            # drop + rebuild
    .venv/bin/python scripts/rebuild_demo_db.py --verify   # rebuild + signature
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

# The SEPARATE demo database — never nixus_saas (the frozen benchmark db).
DEMO_DB = os.environ.get("DEMO_DATABASE_NAME", "nixus_saas_demo")
# Maintenance DB to connect to for server-level CREATE/DROP DATABASE.
MAINT_DB = os.environ.get("SAAS_MAINT_DATABASE", "nixus_sql")

_HERE = os.path.dirname(os.path.abspath(__file__))
_EVAL = os.path.join(os.path.dirname(_HERE), "eval")
SCHEMA_SQL = os.path.join(_EVAL, "saas_schema.sql")        # reused, unchanged
SEED_SQL = os.path.join(_EVAL, "saas_demo_seed.sql")       # the RICH demo seed

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
            "target server) to (re)build the demo database. See .env.example."
        )
    ro_url = settings.target_url
    if not ro_url:
        raise RuntimeError(
            "Set TARGET_DATABASE_URL (the read-only target role) so the demo db "
            "can be granted to it. See .env.example."
        )
    ro_user, ro_pass = _parse_role(ro_url)
    maint_dsn = _swap_db(owner_url, MAINT_DB)
    demo_dsn = _swap_db(owner_url, DEMO_DB)
    return maint_dsn, demo_dsn, ro_user, ro_pass


def _recreate_database(maint_dsn: str, db: str, ro_user: str, ro_pass: str) -> None:
    """Drop + recreate the demo db and ensure the read-only role can CONNECT."""
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
            # Ensure the read-only role exists (idempotent — it normally already
            # does once Chinook/SaaS init ran).
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


def _load_and_grant(demo_dsn: str, ro_user: str) -> None:
    """Load schema + demo seed, then apply the SELECT-only grant pattern."""
    ro_user = _safe_ident(ro_user, "role")
    conn = _connect(demo_dsn, autocommit=False)
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
    print(f"  ✓ loaded schema + demo seed and granted SELECT-only to {ro_user}")


_TABLES = (
    "plans", "organizations", "users", "subscriptions",
    "usage_events", "invoices", "payments",
)


def _summary(demo_dsn: str) -> None:
    conn = _connect(demo_dsn)
    try:
        with conn.cursor() as cur:
            print("  row counts:")
            for t in _TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")
                print(f"    {t:<14} {cur.fetchone()[0]}")
            # Spot-check the temporal spread the demo charts depend on.
            cur.execute(
                "SELECT count(DISTINCT date_trunc('month', paid_at)) FROM payments"
            )
            print(f"    {'revenue months':<14} {cur.fetchone()[0]}")
    finally:
        conn.close()


def _signature(demo_dsn: str) -> str:
    """A deterministic content signature: per-table md5 over rows in id order.

    Two rebuilds must produce the same signature — the determinism guarantee.
    """
    conn = _connect(demo_dsn)
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
    ap = argparse.ArgumentParser(description="(Re)build the deterministic RICH demo target db.")
    ap.add_argument(
        "--verify", action="store_true",
        help="After building, print a content signature (for byte-identical re-run checks).",
    )
    args = ap.parse_args()

    maint_dsn, demo_dsn, ro_user, ro_pass = _resolve_urls()
    if DEMO_DB == "nixus_saas":
        raise RuntimeError(
            "Refusing to run: DEMO_DATABASE_NAME resolved to 'nixus_saas' (the "
            "FROZEN benchmark database). The demo MUST target a separate database."
        )
    print(f"◈ Rebuilding RICH demo database '{DEMO_DB}' (deterministic demo seed)...")
    _recreate_database(maint_dsn, DEMO_DB, ro_user, ro_pass)
    _load_and_grant(demo_dsn, ro_user)
    _summary(demo_dsn)
    if args.verify:
        print("◈ Content signature (must be identical across rebuilds):")
        print(_signature(demo_dsn))
    print(f"◈ Done. '{DEMO_DB}' is a read-only demo target for {ro_user}.")


if __name__ == "__main__":
    main()

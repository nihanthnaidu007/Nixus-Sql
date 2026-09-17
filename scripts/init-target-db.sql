-- ============================================================================
-- 2.1 — Provision the read-only TARGET database (local A2).
--
-- Runs ONCE on first container init: Postgres executes every file in
-- /docker-entrypoint-initdb.d/ as the superuser (POSTGRES_USER = nixus) against
-- the default database. This script creates, in the single Postgres container:
--
--   * nixus_chinook   — the TARGET database (owned by the app's owner role,
--                       `nixus`). Holds the user's data; locally, Chinook.
--   * nixus_readonly  — a LOGIN role holding ONLY SELECT on the target data.
--                       This is what makes read-only REAL: enforced by Postgres,
--                       not by the application choosing not to write.
--
-- Ordering note: the Chinook TABLES are loaded AFTER this script, by
-- `scripts/migrate_chinook.py`, through the OWNER connection. So we cannot grant
-- SELECT on those tables here (they don't exist yet). Instead we:
--   (a) GRANT SELECT ON ALL TABLES — a safe no-op now (no tables yet), and
--   (b) ALTER DEFAULT PRIVILEGES — so every table the owner creates later
--       AUTOMATICALLY grants SELECT to nixus_readonly.
-- Nothing else is ever granted: no INSERT / UPDATE / DELETE / TRUNCATE / DDL.
--
-- nixus_readonly is intentionally NOT the owner of nixus_chinook and has NO
-- write privileges. (On Postgres 16 the `public` schema does not grant CREATE to
-- PUBLIC, so the role also cannot create its own tables.)
--
-- This file is mounted by docker-compose. If you change it, the new stack only
-- picks it up on a FRESH init: `docker compose down -v && docker compose up`.
-- ============================================================================

CREATE DATABASE nixus_chinook;

GRANT CONNECT ON DATABASE nixus_chinook TO nixus_readonly;

-- Switch into the target database to set schema-level privileges there.
\connect nixus_chinook

GRANT USAGE ON SCHEMA public TO nixus_readonly;

-- Any tables that already exist (none on first init) — safe no-op.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nixus_readonly;

-- Tables the OWNER creates later (the Chinook tables, via migrate_chinook) are
-- auto-granted SELECT — and ONLY SELECT — to the read-only role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO nixus_readonly;

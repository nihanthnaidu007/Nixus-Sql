#!/usr/bin/env bash
# ============================================================================
# Wave 1 — Provision the strictly read-only LOGIN role (nixus_readonly).
#
# Runs ONCE on a fresh Postgres volume, from /docker-entrypoint-initdb.d/, as
# the FIRST init script (the `05` prefix orders it before 10-init-target-db.sql,
# whose GRANT ... TO nixus_readonly statements require the role to exist).
#
# The role NAME is structural: init-target-db.sql, init-saas-db.sh,
# init-demo-db.sh, and the TARGET_DATABASE_URL in docker-compose.yml all
# reference it by name. Its PASSWORD is env-supplied (POSTGRES_READONLY_PASSWORD,
# required by docker-compose.yml) — the retired demo value 'nixus_readonly' is
# refused at startup before Postgres ever initializes.
#
# Why a .sh script: docker-entrypoint-initdb.d .sql files cannot read
# environment variables, and psql's :'var' interpolation quotes the value
# correctly even if it contains single quotes.
#
# Only applied on a fresh init: `docker compose down -v && docker compose up`.
# ============================================================================
echo "◈ [init] Provisioning the read-only role (nixus_readonly) from POSTGRES_READONLY_PASSWORD..."

if [ -z "${POSTGRES_READONLY_PASSWORD:-}" ]; then
  echo "✗ [init] POSTGRES_READONLY_PASSWORD is not set — cannot create the read-only role." >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v ro_pass="$POSTGRES_READONLY_PASSWORD" <<-'EOSQL'
    CREATE ROLE nixus_readonly LOGIN PASSWORD :'ro_pass';
EOSQL

echo "◈ [init] Read-only role nixus_readonly provisioned (SELECT-only on target data)."

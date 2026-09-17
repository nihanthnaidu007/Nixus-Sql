#!/usr/bin/env bash
# ============================================================================
# 7.2 — ONE operation to point the WHOLE stack at a single target, coherently.
#
# Replaces the old three-setting juggling (TARGET_DATABASE_URL +
# TARGET_ADMIN_DATABASE_URL + a manual reembed, easy to get out of sync). This
# sets both target URLs in .env AND re-embeds, so the API, the embeddings, and
# the benchmark are always aligned to the SAME target.
#
#   scripts/use_target.sh demo        # the RICH demo sample (stack default)
#   scripts/use_target.sh saas        # the FROZEN benchmark sample (nixus_saas)
#   scripts/use_target.sh chinook     # the alt Chinook sample
#   scripts/use_target.sh postgresql://readonly_user:pw@host:5432/yourdb   # BYO
#
# After it runs, RESTART the API so it serves the new target (the running process
# captured the old env at startup):
#   local : stop the API and re-run it (uvicorn / ./start.sh / scripts/dev.sh)
#   docker: docker compose restart api
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:-}"
if [ -z "$NAME" ]; then
  echo "usage: scripts/use_target.sh <demo|saas|chinook|postgresql://readonly-url>"
  exit 2
fi
if [ ! -f .env ]; then
  echo "✗ No .env found. Run scripts/setup.sh (or cp .env.example .env) first."
  exit 1
fi

# host:port for the local sample DBs comes from the current TARGET line (default 5433).
HOSTPORT="$(grep -E '^TARGET_DATABASE_URL=' .env | head -1 | sed -E 's#.*@([^/]+)/.*#\1#')"
[ -n "$HOSTPORT" ] || HOSTPORT="localhost:5433"

# Credential parts come from the environment or .env — the retired demo
# passwords (nixus / nixus_readonly) are refused by docker-compose at startup,
# so the URLs this script writes must use the env-provided credentials too.
# Passwords appear inside connection URLs: prefer URL-safe passwords, or
# percent-encode special characters.
env_val() {
  local var="$1" val=""
  val="$(printenv "$var")" || val=""
  if [ -z "$val" ] && [ -f .env ]; then
    val="$(grep -E "^${var}=" .env | tail -1 | cut -d= -f2-)"
  fi
  printf '%s' "$val"
}
PG_USER="$(env_val POSTGRES_USER)"
PG_PASS="$(env_val POSTGRES_PASSWORD)"
RO_PASS="$(env_val POSTGRES_READONLY_PASSWORD)"
[ -n "$PG_USER" ] && [ -n "$PG_PASS" ] && [ -n "$RO_PASS" ] || {
  echo "✗ POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_READONLY_PASSWORD must be set (environment or .env) — the demo credentials are retired."
  exit 1
}

case "$NAME" in
  demo)
    RO="postgresql://nixus_readonly:${RO_PASS}@${HOSTPORT}/nixus_saas_demo"
    ADMIN="postgresql://${PG_USER}:${PG_PASS}@${HOSTPORT}/nixus_saas_demo" ;;
  saas)
    RO="postgresql://nixus_readonly:${RO_PASS}@${HOSTPORT}/nixus_saas"
    ADMIN="postgresql://${PG_USER}:${PG_PASS}@${HOSTPORT}/nixus_saas" ;;
  chinook)
    RO="postgresql://nixus_readonly:${RO_PASS}@${HOSTPORT}/nixus_chinook"
    ADMIN="postgresql://${PG_USER}:${PG_PASS}@${HOSTPORT}/nixus_chinook" ;;
  postgresql://*|postgres://*)
    RO="$NAME"; ADMIN="" ;;   # bring-your-own read-only target: no owner, no rebuild
  *)
    echo "✗ Unknown target '$NAME' (expected: demo | saas | chinook | a postgresql:// URL)"
    exit 2 ;;
esac

# Rewrite the two TARGET lines in .env so API + benchmark align to one target.
RO="$RO" ADMIN="$ADMIN" python - <<'PYEOF'
import os, pathlib, re
ro, admin = os.environ["RO"], os.environ["ADMIN"]
p = pathlib.Path(".env"); lines = p.read_text().splitlines()
def setline(lines, key, val):
    pat = re.compile(rf"^{key}="); out, seen = [], False
    for ln in lines:
        if pat.match(ln):
            out.append(f"{key}={val}"); seen = True
        else:
            out.append(ln)
    if not seen:
        out.append(f"{key}={val}")
    return out
lines = setline(lines, "TARGET_DATABASE_URL", ro)
if admin:
    lines = setline(lines, "TARGET_ADMIN_DATABASE_URL", admin)
p.write_text("\n".join(lines) + "\n")
print(f"  ✓ .env TARGET_DATABASE_URL -> {ro}")
PYEOF

echo "◈ Re-embedding schema_embeddings from the new target (introspection)..."
TARGET_DATABASE_URL="$RO" TARGET_ADMIN_DATABASE_URL="${ADMIN:-$RO}" \
  .venv/bin/python -m nixus.schema.reembed

# The query_cache (state_db) stores TARGET-SPECIFIC results (SQL + row preview),
# but is keyed only by query semantics — it has no target dimension. Switching
# targets makes every cached answer stale (e.g. a "users per org" count from the
# old target). Aligning the WHOLE stack to one target therefore MUST discard the
# previous target's cache, exactly as it re-embeds the schema. (This clears state
# bookkeeping only; it is not a change to the query path.)
echo "◈ Flushing the query_cache (cached results belonged to the old target)..."
.venv/bin/python - <<'PYEOF'
import pathlib, re, psycopg2
env = {}
for ln in pathlib.Path(".env").read_text().splitlines():
    m = re.match(r"^([A-Z_]+)=(.*)$", ln)
    if m:
        env[m.group(1)] = m.group(2)
state = env.get("STATE_DATABASE_URL") or env.get("DATABASE_URL")
if not state:
    print("  (no STATE_DATABASE_URL in .env — skipping cache flush)")
else:
    conn = psycopg2.connect(state); conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.query_cache')")
        if cur.fetchone()[0] is None:
            print("  (query_cache table not present yet — nothing to flush)")
        else:
            cur.execute("DELETE FROM query_cache")
            print(f"  ✓ cleared {cur.rowcount} cached entr{'y' if cur.rowcount == 1 else 'ies'}")
    conn.close()
PYEOF

cat <<EOF
◈ Target switched to: $NAME
  - .env updated (the API + eval/run_saas_benchmark.py now read this target)
  - schema_embeddings re-embedded for it
  - query_cache flushed (stale results from the previous target discarded)
  RESTART the API to serve it:
    local : re-run the API (uvicorn / ./start.sh / scripts/dev.sh)
    docker: docker compose restart api
EOF

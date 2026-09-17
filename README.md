# ◈ NIXUS SQL

A read-only, database-agnostic **natural-language → SQL agent** built on LangGraph.
Its design priority is **trust over capability**: it refuses out-of-scope, write, and
ambiguous requests, surfaces uncertainty rather than fabricating an answer, and is
read-only *by construction* — every query runs through a PostgreSQL role that holds
only `SELECT`. It works on any PostgreSQL schema by introspecting it, with no
per-database configuration. The honest measure of the project is the
[benchmark](#honest-benchmark--known-limitations) below: real numbers, including the
two questions it gets wrong and why.

![NIXUS SQL — ask the database, with live system status](images/image%201.png)

A question becomes a grounded SQL query, a result, and a plain-language insight — with
the intent, cache, confidence, few-shot, and self-correction signals shown alongside,
so you can see *what the system did*, not just what it returned:

![NIXUS SQL — intelligence strip and generated SQL](images/image%203.png)

When the shape of the data suits it, NIXUS charts the result natively (bar, line, pie,
scatter, and multi-series) — the chart type is chosen from the data, not hard-coded:

![NIXUS SQL — native charting chosen by data shape](images/image%205.png)

---

## What it does

NIXUS turns a natural-language question into a **grounded, verified, read-only** SQL
query and explains the result. The pipeline is a LangGraph node graph:

- **Scope classification** — out-of-scope, write, and irreducibly ambiguous requests
  are refused or sent back for clarification rather than answered. Refusal and
  clarification are first-class outcomes, not errors.
- **Schema + few-shot retrieval** — the relevant tables/columns and similar prior
  examples are retrieved semantically (pgvector), so generation is grounded in *your*
  schema with no per-database configuration.
- **Generation, syntax validation, and grounding** — the generated SQL is checked to
  reference only tables and columns that actually exist before it ever runs.
- **Self-correction** — on an execution error the agent diagnoses the failure and
  rewrites the query (bounded retry), instead of returning a broken result.
- **Read-only by construction** — the target database is reached only through a
  Postgres role granted nothing but `SELECT`. A write is rejected **twice**: by the
  API guard and, definitively, by the database role. Read-only is enforced by Postgres,
  not by trusting the model.
- **Semantic caching** — equivalent questions are served from a similarity cache
  instead of re-running the whole pipeline.
- **Live, node-by-node streaming** — the React UI streams each node's status as the
  graph executes, then converges on the same final state the blocking endpoint returns.
- **Native charting** — bar, line, pie, scatter, and multi-series charts, with the
  chart type selected from the result's shape.
- **Honest confidence** — every answer carries a categorical confidence verdict with
  its reasons.

---

## The web UI

A hand-built React (Next.js) frontend at **http://localhost:3000** makes the trust
model visible. For an answer it shows the generated SQL, the result table, the native
chart, and the plain-language insight; alongside them the **confidence** verdict with
its reasons and the **intelligence strip** (intent, cache, few-shots, corrections).
When a question is too ambiguous it runs the **clarification** round-trip (the system
asks rather than guesses); when a request is out of scope or a write, it shows the
**refusal** as a deliberate, legitimate outcome — not a crash. The UI calls the same
`POST /api/v1/run` endpoint the CLI uses: it adds no capability, it makes the proven
pipeline legible.

This React UI **replaces the earlier Streamlit prototype**, which has been retired. It
is at full functional parity with that interface (charts, intelligence strip,
diagnostics, live streaming, edit-SQL, pagination, example pills, system status) and
goes beyond it — native Recharts charting in place of the old Plotly blob, a full-width
layout, a separate rich demo dataset, and neutral empty states.

---

## API authentication & sessions

Every `/api` route requires an **API key**, sent as the `X-API-Key` header. The only
exemption is `/api/health`, which stays open for infrastructure probes (load
balancers, uptime checks, container healthchecks). The key comes from the `API_KEY`
environment variable — generate one with `openssl rand -hex 32`:

```bash
curl -X POST http://localhost:8000/api/v1/run \
     -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"user_query": "how many organizations are there?"}'
```

**Fail-closed by design.** If `API_KEY` is unset — or still the `.env.example`
placeholder — the API refuses to serve: every protected route answers **503** with
setup guidance and startup logs a loud error. It never silently opens.

**Sessions are server-issued.** The session id doubles as the LangGraph checkpoint
thread id, so clients can no longer supply their own: send an **empty** `session_id`
on a first query and the response carries the server-issued id to reuse for
follow-ups (the React UI already does exactly this). A `session_id` this server
never issued is answered with **404** — one client cannot attach itself to another
session's checkpoint thread. Upgrading from an earlier version: ids issued before
this change are not in the new registry, so existing conversations start fresh.

**The React UI** authenticates with the same key, inlined at build time via
`NEXT_PUBLIC_API_KEY`; under `docker compose` it is wired from `API_KEY`
automatically. A key served to a browser is readable by anyone who can load the UI —
fine for a single-operator deployment, not a multi-user boundary. Rate limiting is
deliberately **not** part of this change.

---

## Quick start — the self-contained demo

The default `docker compose up` stands up everything: both databases, the read-only
role, a **rich SaaS demo dataset** (loaded + seeded), the schema migrations, the schema
embeddings, the API, and the React web UI. **The only thing you must provide is two API
keys** — no connection string, no external database. Clone to running is roughly
fifteen minutes.

```bash
git clone <repo-url> && cd Nexus-Sql-Agent
cp .env.example .env          # then set ANTHROPIC_API_KEY and OPENAI_API_KEY in .env
docker compose up -d --build  # provisions both DBs, seeds the demo data, migrates, embeds, boots API + web UI

# once healthy — ask in the browser or from the CLI:
open http://localhost:3000                       # the React web UI
nixus query "which organization has the most users?"
```

On first boot the API container runs, in strict idempotent order: wait for Postgres →
apply migrations → ensure the sample data is loaded + seeded → check the API keys
(**fail fast** with a clear message if missing) → embed the target schema → start the
API. If a key is missing it stops with a readable error instead of booting broken. Your
`.env` is never clobbered — setup only creates one when none exists.

### Point it at your own database

Bring your own PostgreSQL. NIXUS needs **only `SELECT`** on the target — ideally a read
replica or a `SELECT`-only role. It never writes to your data.

```bash
# Point the whole stack (API + embeddings + benchmark) at one target, then re-embed:
scripts/use_target.sh postgresql://readonly_user:pw@your-host:5432/yourdb
# restart the API so it serves the new target:
#   docker: docker compose restart api
#   local : re-run the API process
nixus query "..."   # now answers against your schema
```

The same command switches between the bundled samples:

```bash
scripts/use_target.sh demo        # the rich SaaS demo dataset (the stack default)
scripts/use_target.sh saas        # the frozen benchmark dataset (nixus_saas)
scripts/use_target.sh chinook     # the alternate Chinook sample
```

---

## CLI

```bash
nixus query "how many organizations are there?"   # ask against the configured target
nixus health                                       # check state_db + target_db reachability
nixus reembed                                      # re-introspect + re-embed the target schema
```

---

## Honest benchmark + known limitations

The benchmark of record runs a fixed, held-out **SaaS gold set** against the
deterministic, frozen `nixus_saas` seed, scored by result-equivalence (not string
match). The measured result, unchanged in v3 (the demo dataset is separate and does not
perturb it):

- **Answerable: 55 / 57 correct** — easy **13/13**, medium **15/17**, hard **27/27**.
- **Scope: 10 / 10** — it correctly refuses or clarifies every case it should.

These numbers are not rounded and not softened. There are **two** genuine failures, and
they are documented here rather than hidden:

- **M3 — a faithfulness gap (the most important known limitation).** On some questions
  the system produces a well-formed query that *quietly narrows the request* — adding a
  filter the user did not ask for (e.g. `WHERE is_active = true`). This passes grounding
  because **grounding verifies that every table and column the SQL references actually
  exists — it does not verify that the SQL is a *faithful* representation of the
  question.** A query can be fully grounded and still answer a slightly different
  question than the one asked. This is an architectural boundary of the current
  pipeline, not a bug patched over.

- **M11 — a `DISTINCT` omission.** On some "which organizations…" questions the system
  omits `DISTINCT` and returns duplicate rows where the faithful answer is a distinct
  set.

Neither is fixed. They are stated so the numbers can be trusted.

### Running the benchmark (operational note — read this)

The benchmark executes **through the API**, and the Docker stack pins the API to the
**rich demo dataset (`nixus_saas_demo`) by default**. Running the benchmark against the
demo data reports a bogus number — the gold answers are keyed to the frozen
`nixus_saas` seed. Point the execution path at the frozen set first (a local `uvicorn`
whose `.env` target is `nixus_saas`, or `scripts/use_target.sh saas` + an API restart):

```bash
scripts/use_target.sh saas               # point the target at the frozen nixus_saas seed
.venv/bin/python scripts/rebuild_saas_db.py   # deterministically rebuild that seed
.venv/bin/python eval/run_saas_benchmark.py   # run against the frozen set, not the demo data
```

The harness lives in [`eval/`](eval/); the gold set is
[`eval/saas_gold.py`](eval/saas_gold.py) and the report of record is
[`eval/saas_benchmark_results.json`](eval/saas_benchmark_results.json).

---

## Architecture

NIXUS is one **framework-agnostic core** (`nixus/`) wrapped by **thin adapters**: the
FastAPI service in `api/`, the CLI in `nixus/cli.py`, and the React web UI in `web/`.
All query logic — scope classification, schema and few-shot retrieval, SQL generation,
syntax validation, grounding, execution, result checking, charting, and explanation —
lives in a LangGraph node pipeline behind a single entry point,
`nixus/services/query_service.py::run_query`, which imports no web framework. The system
uses **two databases**: a read-write **state** database (NIXUS-owned bookkeeping —
schema embeddings, few-shot examples, query cache, migrations, and the LangGraph
checkpointer) and a strictly read-only **target** database (your data). Semantic
retrieval and caching use **pgvector**.

For the full module map and the rules each layer obeys, see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Health — and what the fields mean

`GET /api/v1/health` (aliased at the unversioned `/api/health` for infra probes)
reports dependency status honestly and always returns **HTTP 200** when the process is
alive — a failed dependency changes a body field, never the status. It also reports the
running **`version`** (`3.0.0`), which is what the UI footer surfaces.

```jsonc
{
  "status": "ok",                 // "ok" only when db + both LLM providers check out
  "db_connected": true,
  "anthropic_connected": true,    // a REAL cached credential probe, not just "is a key set"
  "openai_connected": true,       // a placeholder/empty key reports false, with no API call
  "langsmith_tracing": false,     // tracing is opt-in; off unless a valid LangSmith key is set
  "version": "3.0.0"
}
```

---

## Deliberate scope (honest)

A few things are intentionally **not** done, recorded here so the boundaries are clear:

- **M3 / M11** — the two documented benchmark failures above. Not fixed; stated openly.
- **Trace deep-link is opt-in, not on by default.** LangSmith tracing is off unless a
  valid key is set. When it *is* enabled, the streaming path surfaces a subtle "view
  trace ↗" link; the blocking `/run` path never carries one. With tracing off (the
  common case) no link is shown — by design, never a dead link.
- **"Tables identified" subtitle is intentionally not ported (B21).** The tables a query
  touches are already visible in the rendered SQL's `FROM`/`JOIN` clauses, and the
  entities appear in the intelligence strip, so a separate tables subtitle would be
  redundant. It is a conscious won't-port, not an oversight.

---

## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).

Copyright 2026 Nihanth Naidu Kalisetti.

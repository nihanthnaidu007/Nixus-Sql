# Nixus-Sql — Production-Readiness Survey

Repo: `nihanthnaidu007/Nixus-Sql` (read-only survey, no changes made). All file references verified this session.

## 1. What the product IS

**Purpose.** NIXUS SQL is a read-only, database-agnostic natural-language → SQL agent built on LangGraph. Its stated design priority is "trust over capability" (`README.md`): refuse out-of-scope/write/ambiguous requests, surface uncertainty, and be read-only *by construction* — enforced primarily by connecting to the target database through a Postgres role holding only `SELECT`.

**Target user.** Developers/analysts who want to ask questions of a PostgreSQL database in plain English (self-hosted demo today; no multi-user story). Distributed under Apache-2.0.

**Architecture** (v3.0.0, one framework-agnostic core + thin adapters):

- **Core** (`nixus/`): a 13-node LangGraph pipeline (`nixus/graph/graph.py`) behind one entry point, `nixus/services/query_service.py::run_query`. Nodes: scope classification (`nixus/graph/scope.py`), parse_intent, semantic cache check (`nixus/db/query_cache.py`), schema retrieval (pgvector embeddings over introspected schema — `nixus/schema/introspect.py`, `embed.py`, `reembed.py`), few-shot retrieval (`nixus/db/fewshot_store.py`), SQL generation (`nixus/graph/nodes/generate_sql.py`, Claude Sonnet), syntax validation (sqlglot), grounding verification (`nixus/graph/grounding.py` — every referenced table/column must exist), execution, result checking, bounded self-correction, chart classification, explanation.


- **Two-database design** (`nixus/db/connection.py`): a read-write **state** DB (NIXUS-owned: schema embeddings, few-shot examples, query cache, migrations, LangGraph checkpointer) and a strictly **read-only target** DB (the user's data, `TARGET_DATABASE_URL` with a SELECT-only role). Statement timeout (`SET LOCAL statement_timeout`) and a 1000-row fetch cap are enforced in `nixus/graph/nodes/execute_query.py`.


- **Adapters**: FastAPI (`api/main.py` — `/api/v1/run`, `/run` SSE stream, `/run-sql` for user-edited SQL, `/cache-stats`, `/cache-evict`, `/fewshot-stats`, `/health`), CLI (`nixus/cli.py` via pyproject console script), Next.js 15 / React 19 UI (`web/` — charts via Recharts, live SSE pipeline view, confidence banner, clarification round-trip, refusal states).


- **External services**: Anthropic API (generation), OpenAI embeddings (`text-embedding-3-small`), optional LangSmith tracing (opt-in, gated in `nixus/config.py::apply_tracing_gate`).


- **Data flow**: question → scope gate (refuse/clarify/accept) → cache → retrieval → generate → sqlglot AST read-only check + regex write guard (`nixus/utils/sql_safety.py`, `nixus/safety/write_guard.py`) → grounding → execute on read-only role → result check/self-correct → chart → explain → answer with categorical confidence.



**Maturity of docs is unusually high and honest**: README, ARCHITECTURE.md, BASELINE.md, BENCHMARK.md, SETUP.md, CHANGELOG.md, plus a documented benchmark: **55/57 answerable correct, 10/10 scope refusals**, with the two failures (M3 faithfulness gap, M11 DISTINCT omission) documented rather than hidden.

## 2. Maturity signals

- **Tests: substantial but split-brained.** `tests/` mirrors the source tree (~111 unit-test functions across api/cli/graph/schema/utils, mostly offline + monkeypatched). `eval/` is a live end-to-end benchmark harness (correctness, safety, latency, self-correction) requiring a **running API + seeded databases**. ⚠️ `pytest.ini` sets `testpaths = eval`, so a bare `pytest` runs the live-server benchmark suite and **never collects `tests/`** — the unit suite is invisible to the default test command.


- **CI/CD: none.** No `.github/` directory, no workflow files, no badges.


- **Linting/type-checking: none.** No ruff/flake8/mypy/pre-commit config anywhere.


- **Error handling: good.** Global FastAPI exception handler returns a structured 500 with a `trace_id` (`api/main.py`), SSE path yields structured error events, lifespan failures are logged non-fatally where safe.


- **Logging: good for a demo** — structured helpers (`nixus/utils/logging_config.py`), configurable level; but human-format only (no JSON logs, no request IDs, no metrics).


- **Secrets: clean.** No hardcoded keys (scan found only fake keys in tests). `.env` gitignored; `.env.example` ships sentinels; placeholder detection (`nixus/config.py::is_placeholder`) prevents booting with fake keys and gating tracing. Compose hardcodes dev DB credentials `nixus:nixus` (`docker-compose.yml`) — fine locally, unsafe if deployed as-is.


- **Packaging/deployment: Docker-based, currently broken for fresh clones.** `Dockerfile` runs `COPY requirements.lock .` and installs from it, but **`requirements.lock` does not exist in the repo** — `.gitignore` contains `*.lock`, which excludes it from git. A fresh `docker compose up --build` fails at the COPY step. This is a **blocking defect**.


- **Dependency hygiene: pins are exact** (all `==` in `requirements.txt`, Python 3.13) but there are leftovers: `streamlit==1.57.0` is still pinned although the Streamlit UI was retired in v3; plotly/pandas/numpy are pinned while plotly appears to be legacy-referenced only (charting is now Recharts client-side; pandas/numpy used in `nixus/graph/nodes/classify_chart.py`).


- **Docs: excellent** — the best aspect of the repo. Stale spots: `ARCHITECTURE.md` still describes `nixus/schema/`, the CLI, and the React UI as "planned, not present" and claims a single `DATABASE_URL` — all superseded by the actual code (schema package, CLI, two-DB split all exist).


- **Git history: unverified this session** — the sandbox's GitHub token was stale, so `git log` was unavailable; all findings are from the working tree.



## 3. Production-readiness gaps specific to an NL→SQL tool

1. **No authentication or rate limiting on any endpoint** (explicitly "not in this release" per CHANGELOG). Anyone who can reach the API can burn Anthropic/OpenAI quota via `/run`//`stream`, execute arbitrary SELECTs via `/run-sql`, and mutate the cache via unauthenticated `/cache-evict`. Blocking for any shared/hosted deployment.


2. **Session hijack via client-supplied `session_id`.** The session id becomes the LangGraph `thread_id` (`nixus/services/query_service.py::get_thread_config`). A client can pass another user's session id and resume/inspect their conversation checkpoint (state DB). No server-side identity binds sessions.


3. **Read-only enforcement layering is good but partial.** The real guarantee is the SELECT-only DB role — correct design. But the in-process guards (regex `write_guard.py` + sqlglot AST check) are the only pre-execution checks; there is no `SET default_transaction_read_only` on the connection and no protection against expensive-but-legal queries beyond the 30s timeout + 1000-row cap (no cost/complexity heuristic).


4. **M3 faithfulness gap** (documented in README): grounding verifies references exist, not that the SQL answers the question asked — generated queries can silently narrow the request. For real users this is the top *accuracy* defect.


5. **Few-shot cold start**: `BASELINE.md` flags that a freshly-connected user database has zero few-shot examples, degrading generation quality for exactly the "bring your own database" use case the product wants.


6. **Prompt-injection surface**: schema text, few-shot examples, and cached Q/R results are interpolated straight into the generation prompt (`generate_sql.py`); the only mitigation is the write guard. Acceptable while read-only, but the UI renders LLM explanations as markdown (`web/lib/markdown.tsx`) — worth an XSS audit before multi-user exposure.


7. **Secrets in compose defaults** (`nixus:nixus`, `nixus_readonly:nixus_readonly` in `docker-compose.yml`) — fine for the demo, but there is no production compose profile or documented override path.



## 4. Prioritized upgrade shortlist (highest impact first)

| # | Upgrade | Rationale |
| --- | --- | --- |
| 1 | **Commit `requirements.lock` and un-ignore it in `.gitignore`** (e.g. `!requirements.lock`) | **Blocking defect**: Dockerfile `COPY requirements.lock` fails on any fresh clone — the product cannot be built from the published repo. |
| 2 | **Add authentication + per-user session scoping on the API** | Session-id spoofing exposes other users' checkpoint state; an API key layer plus server-issued session ids is the minimum for anything beyond localhost. |
| 3 | **Add rate limiting / usage quotas** on `/run`, `/stream`, `/run-sql` | Unauthenticated LLM-cost abuse is one curl away; cheapest fix is middleware limits keyed by IP or key. |
| 4 | **Add CI (GitHub Actions)**: run `tests/`, a lint pass, and a Docker build on PR | Zero CI today; the unit suite exists but nothing runs it automatically. |
| 5 | **Fix the test split**: make `pytest` run `tests/` by default, mark `eval/` as live/integration (`-m live`) | `pytest.ini`'s `testpaths = eval` means the offline suite never runs by default and the default command fails without a live server — hostile to contributors. |
| 6 | **Add ruff + mypy + pre-commit** | No linting or type-checking exists; the codebase is clean enough that adoption would be cheap and catches regressions CI will run. |
| 7 | **Production deployment profile**: non-dev compose (env-supplied DB credentials, TLS/reverse-proxy guidance, secrets handling docs) | The only deployment story is the dev demo with hardcoded `nixus:nixus` credentials. |
| 8 | **Dependency hygiene**: drop `streamlit` (retired UI), prune unused pins, document the lockfile workflow | Retired deps bloat the image and mask what the product actually needs; pins are good but the lockfile story is broken (see #1). |
| 9 | **Observability**: JSON structured logs with request IDs, Prometheus metrics, optional Sentry | Current logging is human-format and local-only; production operation needs queryable logs and latency/error metrics. |
| 10 | **Fix M3 (faithfulness) at least partially**: e.g. a post-generation faithfulness check comparing the question's filters/entities to the SQL | The single biggest *accuracy* defect, already documented; closing even the `WHERE`-narrowing class materially improves trust for real schemas. |
| 11 | **Solve few-shot cold start** for new databases (synthetic example generation from introspected schema, or disable-and-tune path) | "Point at your own database" quality collapses without examples — the core value proposition for real users. |
| 12 | **Refresh stale docs**: ARCHITECTURE.md module map and rule-6/9 "current reality" notes | Docs are the repo's best asset; staleness here misleads every contributor and the planning process itself. |

**Blocking defects flagged:** #1 (broken Docker build for fresh clones) and, for any non-local deployment, #2/#3 (no auth, no rate limits, cross-user session reachability).

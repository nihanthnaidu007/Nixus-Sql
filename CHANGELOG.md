# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **API-key authentication (Wave 0).** Every `/api` route now requires `X-API-Key`
  (from the `API_KEY` env var), except `/api/health`. Fail-closed: with no real key
  configured the API answers 503 with setup guidance instead of serving. CORS allows
  the `X-API-Key` header; the React UI sends the key via `NEXT_PUBLIC_API_KEY`
  (wired from `API_KEY` by compose).
- **Server-issued session ids (Wave 0).** The API issues a session id on first use
  and returns it in the response; a client-supplied id this server never issued is
  answered 404. Session ids double as LangGraph checkpoint thread ids, so one client
  can no longer attach to another session's checkpoint. Sessions persist in a new
  `api_sessions` state table (migration 0002).
- **CI** (`.github/workflows/ci.yml`): ruff lint + the pytest suite on every PR, and
  a Docker image build from a pristine `git archive` export — the exact failure mode
  of a fresh clone.

### Fixed

- **Fresh-clone Docker build was broken**: the `Dockerfile` COPYs
  `requirements.lock`, but `.gitignore`'s `*.lock` pattern excluded it from git.
  The lockfile (full transitive pin set) is now committed and un-ignored.

### Changed

- **`pytest` runs the offline unit suite by default** (`testpaths = tests eval`).
  Previously `testpaths = eval` made a bare `pytest` run only the live-server
  benchmark and never collect `tests/`. The eval suite still skips cleanly when the
  API/DB are unreachable. The eval harness and benchmark script now send
  server-issued session ids and honor `NIXUS_API_KEY` for authenticated targets.
- `ruff` added to dev dependencies; the tree is lint-clean.

## [3.0.0] — 2026-06-06

The React web UI reaches full functional parity with the retired Streamlit interface,
then improves beyond it. The trust pipeline and the honest benchmark are unchanged.

### Changed

- **React UI brought to full parity** with the retired Streamlit interface — charts,
  the intelligence strip, diagnostics, live node-by-node streaming, edit-SQL,
  pagination, example pills, and a discreet system-status line — then improved past it:
  - **Native Recharts charting** (bar, line, pie, scatter, and multi-series, chosen by
    data shape) replacing the embedded Plotly blob.
  - A **full-width layout**.
  - A **separate, rich demo dataset** (`nixus_saas_demo`) as the stack default, kept
    distinct from the frozen benchmark seed so it cannot perturb the score.
  - **Neutral (de-Chinook'd) empty states.**
- Version bumped to **3.0.0** consistently across `pyproject.toml`,
  `web/package.json`, the API (`GET /api/v1/health` and the FastAPI metadata), and the
  LangSmith trace metadata. The UI footer reads the version from `/health`.

### Unchanged

- **Benchmark remains an honest 55/57 answerable** (easy 13/13, medium 15/17, hard
  27/27) and **10/10 scope**. The demo dataset is separate and does not perturb the
  frozen `nixus_saas` measurement. M3 (faithfulness gap) and M11 (`DISTINCT` omission)
  remain the two documented, unfixed failures.

### Scope decisions recorded

- **Trace deep-link scoped out of the UI by default (B18).** LangSmith tracing is
  opt-in; the streaming path surfaces a "view trace ↗" link only when tracing is
  enabled, and the blocking `/run` path never carries one. The plumbing is kept (it is
  a real, dormant-by-default feature), not removed.
- **"Tables identified" subtitle won't-ported (B21).** The touched tables are already
  visible in the rendered SQL's `FROM`/`JOIN` and the entities are in the intelligence
  strip, so the subtitle would be redundant. A conscious won't-port.
- **`CorrectionEntry.attempt` type confirmed honest** — the backend returns an int, so
  the TS `number` type is correct as written (no coercion needed).

## [2.0.0] — 2026-06-05

Production-ready release. The project was declared production-ready with the React web
UI documented alongside the API and CLI adapters on the database-agnostic, read-only
core. This release predates the full Streamlit-parity and native-charting work
delivered in 3.0.0.

## [1.0.0] — 2026-06-04

First public release. NIXUS SQL is a read-only, database-agnostic Text-to-SQL
agent built on LangGraph, whose design priority is **trust over capability**.

### Added

**Trust pipeline**
- Scope classification that refuses out-of-scope, write, and ambiguous requests
  rather than answering them — refusal and clarification are first-class outcomes.
- A grounding check that verifies every table and column the generated SQL
  references actually exists in the schema before the query runs.
- Descriptive-only result explanations (the agent describes what the data shows,
  it does not editorialize or infer beyond the result).
- Honest, categorical confidence reporting on each answer.

**Database-agnostic, read-only by construction**
- Works on any PostgreSQL schema via introspection — no per-database configuration.
- The target database is accessed through a Postgres role that holds only
  `SELECT`; read-only is enforced by Postgres, not by the application.
- A two-database split: a read-write **state** database (NIXUS-owned bookkeeping —
  schema embeddings, few-shot examples, query cache, migrations, and the LangGraph
  checkpointer) and a strictly read-only **target** database.

**Self-contained demo and operations**
- One-command bring-up (`docker compose up`) that provisions both databases, the
  read-only role, the bundled SaaS sample data, the migrations, the schema
  embeddings, the API, and the React web UI — the only required input is two API
  keys.
- A single target switch (`scripts/use_target.sh <saas|chinook|url>`) that points
  the API, embeddings, and benchmark at one target and re-embeds in step.
- A CLI: `nixus query`, `nixus health`, `nixus reembed`.
- An honest `/api/health` endpoint: `*_connected` reflects a real cached
  credential probe (a placeholder/empty key reports `false` with no API call), and
  the endpoint always returns HTTP 200 when the process is alive.
- Opt-in LangSmith tracing: off by default; a placeholder key produces no tracing
  and no error noise.
- The Docker image builds from `requirements.lock` for a fully pinned environment.

**React web UI**
- A hand-built React (Next.js) frontend, served at `http://localhost:3000`, that
  makes the trust model visible: the generated SQL, result table, and insight for
  an answer; the categorical **confidence** with its reasons; the **clarification**
  round-trip (honoring server-enforced N=2 termination); and the **refusal** states
  designed as deliberate, legitimate outcomes, kept distinct from an actual request
  error. It consumes the same `POST /api/v1/run` endpoint as the CLI and adds no
  system capability — it makes the proven pipeline legible. This frontend replaces
  the earlier Streamlit prototype, which has been retired.

**Honest benchmark**
- A fixed, held-out SaaS gold set scored by result-equivalence. Result of record:
  **55/57 answerable** (easy 13/13, medium 15/17, hard 27/27) and **10/10 scope**.
  Reproducible via `eval/run_saas_benchmark.py`.

### Known limitations

- **M3 — faithfulness gap.** On some questions the system produces a well-formed,
  fully grounded query that quietly narrows the request (e.g. an unrequested
  `WHERE is_active = true`). Grounding verifies that referenced tables/columns
  exist; it does not verify that the SQL faithfully represents the question. This
  is an architectural boundary, not a fixed defect.
- **M11 — `DISTINCT` omission.** On some "which organizations…" questions the
  system omits `DISTINCT` and returns duplicate rows.

### Not in this release

Explicitly out of scope for V1, to set expectations honestly:
- No write mode (writes are refused, never executed).
- No authentication or authorization.
- No usage telemetry.
- No multi-tenancy.

[3.0.0]: https://github.com/nihanthnaidu007/Nexus-Sql-Agent/releases/tag/v3.0.0
[2.0.0]: https://github.com/nihanthnaidu007/Nexus-Sql-Agent/releases/tag/v2.0.0
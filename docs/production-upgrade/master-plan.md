# Production Upgrade Master Plan — Four Repos

**Load-bearing assumption:** "Production-ready" here means *secure, tested, CI-governed, packaged, and documented for a single-operator deployer* — not multi-tenant SaaS-hardened. The surveys support that definition (every blocking defect found is a security, correctness, or build failure, not a scaling wall). If you intend to open any of these to other users or the public internet, tell me — that adds multi-tenancy and isolation work that this plan deliberately excludes.

**Deciding factor for phasing:** every repo has blocking security/correctness defects that cost nothing to fix and everything to leave (AXIOM's fail-open auth, Research_Forge's unauthenticated sessions, SpectraVoice's bypassable shell sandbox, Nixus-Sql's broken fresh-clone build). Those go first; engineering foundations second; product features last, so features land on solid ground.

Surveys (evidence base for every item below): Nixus-Sql `art_DTPFXX7u` · AXIOM `art_mEKQ5bhY` · Research_Forge `art_2Hi9nExY` · SpectraVoice `art_BVfU87TR`.

**Execution model:** each task = one PR in its own repo (independent sandbox, squash-merge, CI green before merge). Waves are per-repo dependent but the four repos run fully in parallel. Every task carries its verifying tests; I verify each PR against the acceptance criteria before it merges.

---

## Wave 0 — Blockers: security & build (4 tasks, parallel)

### Nixus-Sql — `feat/w0-security-blockers` (high)

Fix the Dockerfile `COPY requirements.lock` failure on fresh clones (`*.lock` is gitignored — un-ignore or rename the lockfile, verify the image builds from a clean checkout); add API-key auth + per-IP rate limits to FastAPI endpoints; stop honoring client-supplied `session_id` for checkpoint threads (server-issued tokens); fix `pytest.ini testpaths=eval` so `pytest` runs the ~111-test suite.
**Accept:** `docker build` succeeds from fresh clone; unauthenticated requests get 401; session tokens required for checkpoint access; bare `pytest` executes the suite. New auth tests + full suite green.

### AXIOM_Adaptive_RAG — `feat/w0-auth-and-data-lifecycle` (high)

Make `require_api_key` fail-closed when API_KEY is unset; protect `/trace/{id}`, `/session/{id}/state`, `/stats`, `/eval/*`; add document-delete endpoint; switch chunk ids to content hash so re-ingest replaces stale chunks; invalidate the Redis semantic cache on ingest.
**Accept:** TestClient tests prove 401 on protected endpoints without a key; re-ingesting an updated file replaces old chunks; delete removes all lineage; CI green.

### Research_Forge — `feat/w0-auth-and-cost-controls` (high)

Add API-key/token auth to all routes with per-session ownership checks (stream, approve-outline, PDF download); add a global concurrency cap + per-run token budget; stop persisting raw `str(e)` into client-visible session state (`server.py:468,644`); ship `backend/.env.example` matching the README env table.
**Accept:** TestClient lifecycle tests (run → upload → approve → download) with mocked OpenAI/Tavily; auth-negative tests for every route; error states render sanitized messages; CI green.

### SpectraVoice — `feat/w0-sandbox-and-privacy` (high)

Replace the prefix-matched `shell=True` run_command whitelist with sandboxed argv-list execution (no shell interpolation; reject `;`, pipes, `python -c`-style bypasses); add a consent gate + pause toggle before screen content leaves the machine; add rotating file logging + crash capture; add `.env.example`, `.gitignore` (untrack `.DS_Store`/`.pyc`), pin Whisper to a release tag.
**Accept:** unit tests prove `ls; curl|sh` and `python -c` payloads are rejected; consent gate blocks upload when disabled; logging writes to file on forced crash; suite green.

---

## Wave 1 — Engineering foundations (4 tasks, after each repo's W0)

### Nixus-Sql — `feat/w1-ci-foundations` (standard)

GitHub Actions workflow (pytest + ruff), ruff config, remove the streamlit leftover, replace demo-only compose credentials with env-provided values.
**Accept:** CI green on the PR; compose with default creds refused (env required); suite green.

### AXIOM_Adaptive_RAG — `feat/w1-tests-ci-schema` (standard)

Add TestClient integration suite over `/query`, `/ingest`, `/trace` into CI; ruff + mypy jobs + frontend ESLint; wire alembic into startup and fold the three ad-hoc CREATE TABLE blocks into migrations; return structured error envelopes instead of raw exceptions.
**Accept:** CI runs backend + frontend jobs incl. new integration tests; schema created by migrations only (verified on a fresh DB); error responses are envelope-shaped.

### Research_Forge — `feat/w1-ci-vite` (standard)

GitHub Actions (pytest + ruff + frontend build); migrate CRA/craco → Vite; single dependency truth from `requirements.lock`; de-hardcode the `vercel.json` backend URL (env-driven rewrite).
**Accept:** CI green; Vite build succeeds and the frontend smoke-runs; no personal Railway URL in tracked config.

### SpectraVoice — `feat/w1-config-latency` (standard)

Wire the dead config system (411-line `config.py` + `config.yaml`) as the single source of settings; streaming TTS (remove the 500-char cap); move Whisper off the hot path (threaded/async) to cut the 3–10s perceived latency; audio device selection.
**Accept:** config-loading unit tests; TTS streams in chunks (test with >500 chars); latency measured before/after and reported; suite green.

---

## Wave 2 — Product features (4 tasks, after each repo's W1)

### Nixus-Sql — `feat/w2-quality-usability` (standard)

Improve M3 faithfulness (grounding checks on generated SQL), few-shot cold-start seeding from the benchmark corpus, README quickstart for the API mode.
**Accept:** documented benchmark (55/57 baseline, 10/10 scope) maintained or improved, with run output attached; suite green.

### AXIOM_Adaptive_RAG — `feat/w2-observability-scale` (high)

Structured JSON logging + request IDs; Prometheus `/metrics` with per-query token-cost accounting; Postgres-backed trace store + eval jobs (removes single-worker limits); backend Dockerfile + full-stack compose profile.
**Accept:** metrics endpoint scrape test; two-worker smoke test passes against one Postgres/Redis; CI green.

### Research_Forge — `feat/w2-product-features` (standard)

Report-history dashboard, markdown/HTML export alongside PDF, editable outline before approval, remove the dead `factcheck.py`/`factcheck_parallel.py` duplicate.
**Accept:** new endpoints tested; history and export flows dogfooded via browser with screenshots; suite green.

### SpectraVoice — `feat/w2-reliability-ux` (standard)

`--doctor` permission checker (mic/screen/accessibility, with fix-it links), auto-restart on crash with log capture, packaged setup path targeting ≤3-step first run.
**Accept:** `--doctor` runs headless checks and reports each permission's state; crash-capture test; setup doc verified end-to-end.

---

## What changes the plan

- **Public/multi-user intent** for any repo → multi-tenancy, isolation, and stronger auth join Wave 0 for that repo.


- **No live API keys available** for a repo's integration tests → tests mock the provider; benchmark-verification tasks degrade to structural checks.


- **Mac-only scope for SpectraVoice:** tool execution can't be CI-tested on Linux; its tests cover sandbox logic and consent gates, with dogfooding on the Mac where available.

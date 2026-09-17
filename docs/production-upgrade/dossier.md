# Production Upgrade — Delivery Dossier (Four Repos)

**Status:** Final — Waves 0–2 delivered and verified across all four repos; all independent reviews complete (§5). No placeholders.
**Date:** 2026-09-17 · **Program:** Production Upgrade Planning For Repos
**Repos:** nihanthnaidu007/Nixus-Sql · nihanthnaidu007/AXIOM_Adaptive_RAG · nihanthnaidu007/Research_Forge · nihanthnaidu007/SpectraVoice---Screen-Aware-Mac-Voice-Assistant

**Load-bearing assumption.** "Production-ready" in this program means secure, tested, CI-governed, packaged, and documented for a **single-operator deployer** — not multi-tenant SaaS-hardened (master plan, art_gwXo54Vz). The surveys support that reading: every blocking defect found was a security, correctness, or build failure, none a scaling wall. If any of these repos is opened to additional users or the public internet, multi-tenancy and isolation work outside this plan becomes necessary.

---

## 1. Executive summary

**Scope.** Four independent repositories received a coordinated production upgrade covering authentication, data lifecycle, testing, CI, schema management, dependency hygiene, observability, packaging, and product-level features.

**Method.** Each repo was first surveyed read-only to establish its product map, maturity signals, and blocking gaps (four survey artifacts, cited per section below). A master plan (art_gwXo54Vz) consolidated the surveys and sequenced the work into three waves — Wave 0 security/build blockers, Wave 1 engineering foundations, Wave 2 product features — executed as one PR per task in each repo, developed in independent sandboxes, squash-merged with CI green and acceptance criteria verified before merge. Waves are dependent within a repo; the four repos ran in parallel.

**Status at a glance.** SHAs below are the merge commits on each repo's `main` branch. All twelve are verified on `origin/main` (eight earlier on 2026-09-17, the four Wave 2 merges re-verified after their merges the same day; the AXIOM merge additionally confirmed via the GitHub API, mergedAt 2026-09-17T19:26:53Z).

| Repo | Wave 0 — Blockers | Wave 1 — Foundations | Wave 2 — Product |
| --- | --- | --- | --- |
| Nixus-Sql | Merged — PR #2, `dcd2ed6` (art_1Yd1oLTp) | Merged — PR #3, `8aa807e` (art_RCJKKa73) | Merged — PR #4, `33a120f` (art_oTL8XUEi) |
| AXIOM_Adaptive_RAG | Merged — PR #2, `da94af5` (art_5SxCetYq) | Merged — PR #3, `b4fe841` (art_kAsInHYW) | Merged — PR #4, `0878bfa` (art_Fw8ix4e8) |
| Research_Forge | Merged — PR #2, `cf07dce` (art_6g7o0evf) | Merged — PR #3, `70a8497` (art_Gmx69Ym3) | Merged — PR #4, `0e140c2` (art_Uyjequ8Q) |
| SpectraVoice | Merged — PR #2, `310eb6a` (art_CT9XnMy5) | Merged — PR #3, `4351be0` (art_2iZS72ww) | Merged — PR #4, `1f6247b` (art_8ejgup9W) |

**Delivery is complete across all three waves, and all three independent reviews are complete** — Nixus-Sql (art_GShfYO5i), Research_Forge & SpectraVoice (art_tsz9H96x), and AXIOM (art_foQIGjIf). Three pre-existing platform onboarding PRs remain open for the owner's decision (§4).

---

## 2. Per-repo delivery records

What each merged wave delivered is taken from the merged PR records; the acceptance criteria below are quoted from the master plan (art_gwXo54Vz), whose execution model verifies every PR against them before merge.

### 2.1 Nixus-Sql — natural-language-to-SQL agent

**Gaps found** (survey art_DTPFXX7u):

- **Blocking build defect:** the Dockerfile copies `requirements.lock`, but `*.lock` is gitignored — the image could not be built from a fresh clone of the published repo.
- **Blocking security:** no authentication or rate limiting on any endpoint, and client-supplied `session_id` values doubled as LangGraph thread ids, letting any caller resume another user's checkpoint state.
- **Testing/CI:** no CI and no linting; `pytest.ini` set `testpaths = eval`, so a bare `pytest` ran only the live benchmark harness and never collected the ~111-test offline unit suite.
- **Quality:** documented M3 faithfulness gap (benchmark baseline 55/57 answerable correct, 10/10 scope refusals); few-shot cold start on newly connected databases; demo-only compose credentials.

**Wave 0 — PR #2, merged at `dcd2ed6`** (art_1Yd1oLTp): fail-closed API-key auth; server-issued session tokens replacing client-supplied session ids; the lockfile/build fix making the image buildable from a fresh clone; `pytest.ini` restored so a bare `pytest` runs the offline suite by default (verified on `origin/main` this session).

**Wave 1 — PR #3, merged at `8aa807e`** (art_RCJKKa73): GitHub Actions CI workflow; ruff lint gates; removal of the retired Streamlit dependency; credential-safe compose requiring env-provided database credentials.

**Wave 2 — PR #4, merged at `33a120f`** (art_oTL8XUEi): explanation-result fidelity — an `explanation_matches_result` check with one regeneration attempt and a deterministic fallback (32 new tests); idempotent few-shot corpus seeding via `FEWSHOT_SEED_ON_STARTUP` (11 new tests; 57 ANSWERABLE pairs); README API quickstart documenting the server-issued session flow; ruff families I/DTZ/C4/ISC/LOG/UP/FURB re-enforced; `requirements.lock` regenerated (Streamlit plus 24 orphan transitive dependencies removed, 65 pins preserved, the CI lockfile guard intact at `ci.yml:91–96`). All five Wave 2 criteria MET per the independent line-level preview review (art_GShfYO5i), which reviewed head `cfa158a` and confirmed it content-identical to the squash-merged `33a120f`.

**Acceptance criteria:**

*Wave 0 — met (verified at merge):*

- [x] `docker build` succeeds from a fresh clone
- [x] Unauthenticated requests get 401
- [x] Session tokens required for checkpoint access
- [x] Bare `pytest` executes the unit suite; new auth tests and full suite green

*Wave 1 — met (verified at merge):*

- [x] CI green on the PR
- [x] Compose with default credentials refused (env required)
- [x] Suite green

*Wave 2 — met (delivered in PR #4, `33a120f`; reviewed in art_GShfYO5i):*

- [x] Grounding/faithfulness checks on generated SQL; few-shot cold-start seeding; README quickstart for API mode — `explanation_matches_result` confirmed as a hard gate (a mismatch prevents execution, art_GShfYO5i); seeding is idempotent via `FEWSHOT_SEED_ON_STARTUP`
- [x] Documented benchmark (55/57 baseline, 10/10 scope) maintained — with the honest constraint that the **live 55/57 benchmark was not rerun** (a live rerun requires live API/Postgres/LLM keys); 55/57 stands as the documented baseline. Offline gold-corpus grounding was verified at **87/87** (30 Chinook + 57 SaaS; 4 checker flags investigated and ruled false positives on H27 subquery aliases, art_GShfYO5i)

### 2.2 AXIOM_Adaptive_RAG — self-hosted adaptive RAG platform

**Gaps found** (survey art_mEKQ5bhY):

- **Blocking security:** fail-open auth — `require_api_key` returned None when `API_KEY` was unset; `/trace/{id}`, `/session/{id}/state`, `/stats`, and `/eval/*` had no auth dependency, exposing full pipeline traces (user queries and document content).
- **Blocking data lifecycle:** no document-delete endpoint and no cache invalidation; chunk ids were not content-aware, so re-ingesting an updated file silently kept stale chunks.
- **Schema dual-source:** ad-hoc `CREATE TABLE` blocks in `server.py` alongside an unused alembic migration — drift risk.
- **Testing:** 8 unit test files (~740 lines) but no API/integration tests; raw `str(exc)` leaked to clients in the SSE stream.

**Wave 0 — PR #2, merged at `da94af5`** (art_5SxCetYq): fail-closed auth with protected trace/session/stats/eval endpoints; document-delete endpoint; content-hash chunk ids so re-ingest replaces stale chunks; Redis semantic-cache invalidation on ingest.

**Wave 1 — PR #3, merged at `b4fe841`** (art_kAsInHYW): TestClient integration suite over `/query`, `/ingest`, `/trace` running in CI; ruff + mypy + frontend ESLint gates; alembic wired into startup as the single-source schema, with the ad-hoc CREATE TABLE blocks folded into migrations; structured error envelopes.

**Wave 2 — PR #4, merged at `0878bfa`** (art_Fw8ix4e8): structured JSON logging with per-request `X-Request-ID` middleware propagated to logs, response headers, SSE error events, and error-envelope context, with the W1 envelope shapes preserved byte-for-byte; Prometheus `/metrics` (auth-exempt like `/health`) with per-query prompt/completion token accounting wired to Anthropic and OpenAI usage; Postgres-backed trace/eval/stats reads with in-memory fallback, via Alembic migration `c4d8e2f6a9b1` inside the W1 single-source chain; production backend Dockerfile (non-root, healthcheck), `.dockerignore`, `.env.example`, and a full-stack compose profile; `two_worker_smoke.sh` acceptance proof — two separate uvicorn processes against ONE Postgres/Redis, with a trace written by worker A served in full by worker B. CI 6/6 green at `ee155ea`; local suite 147 passed / 3 skipped; ruff clean. All five Wave 2 criteria met per the independent read-only preview review (art_foQIGjIf), which reviewed head `ee155ea` — confirmed byte-identical to the squash-merged `0878bfa` — with CI verified live at the reviewed SHA, 6/6 green including the Alembic fresh-database job (§5).

**Acceptance criteria:**

*Wave 0 — met (verified at merge):*

- [x] TestClient tests prove 401 on protected endpoints without a key
- [x] Re-ingesting an updated file replaces old chunks
- [x] Delete removes all lineage
- [x] CI green

*Wave 1 — met (verified at merge):*

- [x] CI runs backend and frontend jobs including the new integration tests
- [x] Schema created by migrations only (verified on a fresh DB)
- [x] Error responses are envelope-shaped

*Wave 2 — met (delivered in PR #4, `0878bfa`; reviewed in art_foQIGjIf):*

- [x] Observability: structured JSON logging + request IDs; Prometheus `/metrics` with per-query token-cost accounting — request IDs propagate through logs, headers, SSE error events, and error envelopes; token accounting wired to Anthropic and OpenAI usage
- [x] Scale: Postgres-backed trace store and eval jobs; backend Dockerfile + full-stack compose profile — migration `c4d8e2f6a9b1` in the single-source chain; non-root image with healthcheck
- [x] Metrics endpoint scrape test; two-worker smoke test against one Postgres/Redis; CI green — `two_worker_smoke.sh` proves cross-worker trace visibility; CI 6/6 green at `ee155ea`; 147 passed / 3 skipped locally; ruff clean

### 2.3 Research_Forge — multi-agent research-report generator

**Gaps found** (survey art_2Hi9nExY):

- **Blocking security:** no auth and no ownership — a server-generated `session_id` was the only access control on session state, the SSE stream, outline approval, and the PDF download.
- **Cost exposure:** per-IP rate limits only, against endpoints that trigger multi-minute GPT-4o pipeline runs; no global concurrency cap or per-run token budget.
- **Error hygiene:** graph-run failures persisted raw `str(e)` into client-visible session state (`server.py:468, 644`).
- **Foundations:** zero tests and no CI; missing `backend/.env.example` broke the documented setup step; CRA/react-scripts deprecated with React 19; a hardcoded personal Railway URL in `vercel.json`; dead `factcheck.py`/`factcheck_parallel.py` duplicates.

**Wave 0 — PR #2, merged at `cf07dce`** (art_6g7o0evf): API-key/token auth on all routes with per-session ownership checks (stream, approve-outline, PDF download); global concurrency cap and per-run token budget; sanitized error states; `backend/.env.example` shipped.

**Wave 1 — PR #3, merged at `70a8497`** (art_Gmx69Ym3): CRA/craco → Vite migration; frontend CI; env-configurable backend URL removing the hardcoded Railway URL from tracked config; CI workflow (pytest + ruff + frontend build).

**Wave 2 — PR #4, merged at `0e140c2`** (art_Uyjequ8Q): report-history dashboard; multi-format export (markdown/HTML alongside PDF); editable outline before approval; dead-code removal. The independent audit (art_tsz9H96x) re-confirmed the Wave 0 security posture on the merged tree with file+line evidence (§5); two non-blocking observations remain (§4).

**Acceptance criteria:**

*Wave 0 — met (verified at merge):*

- [x] TestClient lifecycle tests (run → upload → approve → download) with mocked OpenAI/Tavily
- [x] Auth-negative tests for every route
- [x] Error states render sanitized messages
- [x] CI green

*Wave 1 — met (verified at merge):*

- [x] CI green
- [x] Vite build succeeds and the frontend smoke-runs
- [x] No personal Railway URL in tracked config

*Wave 2 — met (delivered in PR #4, `0e140c2`; art_Uyjequ8Q):*

- [x] Report-history dashboard; markdown/HTML export alongside PDF; editable outline before approval; dead factcheck duplicate removal
- [x] New endpoints tested; history and export flows verified; suite green

### 2.4 SpectraVoice — screen-aware macOS voice assistant

**Gaps found** (survey art_BVfU87TR):

- **Blocking security:** the `run_command` whitelist used prefix matching with `shell=True`, so payloads like `ls; curl … | sh` and `python -c` passed — arbitrary code execution through the voice tool loop.
- **Blocking privacy:** a full-screen screenshot was sent to a third party on every utterance, with no consent gate or pause control.
- **First-run/ops:** the README's `cp .env.example .env` step failed (no `.env.example` in the repo); no `.gitignore` (`__pycache__`/`.DS_Store` committed); stdout-only logging meant silent crashes with no post-crash diagnosis.
- **Config/latency:** the 411-line config system was dead code; TTS hard-truncated at 500 characters and synthesized whole responses before playing; Whisper transcription blocked the hot path (3–10 s perceived latency).

**Wave 0 — PR #2, merged at `310eb6a`** (art_CT9XnMy5): sandboxed argv-list command execution (no shell interpolation); privacy consent gate and pause toggle; rotating file logging and crash capture; `.env.example` and `.gitignore` repo hygiene.

**Wave 1 — PR #3, merged at `4351be0`** (art_2iZS72ww): config system wired as the single source of settings; streaming TTS (removing the 500-char cap); Whisper moved off the hot path.

**Wave 2 — PR #4, merged at `1f6247b`** (art_8ejgup9W): `--doctor` permission checker — headless, reporting each permission's state with fix-it links; crash auto-restart with log capture, bounded at a maximum of 5 restarts per 60 seconds, then exit 1; packaged setup path targeting the ≤3-step first run. Confirmed by the independent audit (art_tsz9H96x) on `origin/main` (§5).

**Acceptance criteria:**

*Wave 0 — met (verified at merge):*

- [x] Unit tests prove `ls; curl|sh` and `python -c` payloads are rejected
- [x] Consent gate blocks upload when disabled
- [x] Logging writes to file on forced crash
- [x] Suite green

*Wave 1 — met (verified at merge):*

- [x] Config-loading unit tests
- [x] TTS streams in chunks (tested with >500 chars)
- [x] Latency measured before/after and reported
- [x] Suite green

*Wave 2 — met (delivered in PR #4, `1f6247b`; audited in art_tsz9H96x):*

- [x] `--doctor` permission checker; auto-restart on crash with log capture; packaged setup path targeting a ≤3-step first run
- [x] `--doctor` runs headless checks reporting each permission's state (with fix-it links); crash-capture bounded at 5 restarts/60 s then exit 1; setup doc verified end-to-end

---

## 3. Cross-repo quality posture

Controls now in place across the program:

- **Fail-closed authentication and trust controls.** The three API services — Nixus-Sql, AXIOM, Research_Forge — now gate every route and fail closed when keys are unset, with server-issued sessions or per-session ownership checks (art_1Yd1oLTp, art_5SxCetYq, art_6g7o0evf; re-confirmed independently for Research_Forge in art_tsz9H96x and for AXIOM in art_foQIGjIf). SpectraVoice, a desktop application rather than a service, received the equivalent trust controls: sandboxed command execution and a privacy consent gate (art_CT9XnMy5; re-confirmed in art_tsz9H96x).
- **CI on every PR.** A GitHub Actions workflow (`ci.yml`) is present on `main` in all four repos, verified directly this session. CI gates were introduced by Nixus W1 (art_RCJKKa73), AXIOM W1 (art_kAsInHYW), and Research_Forge W0–W1 (art_6g7o0evf, art_Gmx69Ym3); SpectraVoice's workflow arrived across its W0/W1 PRs (verified via git history this session) and runs on Linux with mocked probes — see limitations.
- **Zero credential leakage.** All four surveys scanned and found no hardcoded keys (art_DTPFXX7u, art_mEKQ5bhY, art_2Hi9nExY, art_BVfU87TR). Configuration-level credential risks were then closed: Nixus compose now requires env-provided database credentials (art_RCJKKa73), Research_Forge's backend URL is env-driven (art_Gmx69Ym3), and SpectraVoice shipped `.env.example` plus a `.gitignore` (art_CT9XnMy5). The independent audit re-confirmed Research_Forge's configuration credential-clean (art_tsz9H96x).
- **Test suites from none — or invisible — to enforced.** Research_Forge went from zero tests to auth, cost-control, and error-sanitization suites (art_6g7o0evf; `backend/tests/` contents verified this session). AXIOM's unit-only suite gained a TestClient integration suite run in CI (art_kAsInHYW) and now stands at 147 passed / 3 skipped locally after Wave 2 (art_Fw8ix4e8). Nixus's ~111-test suite, previously invisible to a bare `pytest`, is now the default gate and runs in CI (art_1Yd1oLTp, art_RCJKKa73), with 43 new tests added in Wave 2 (32 fidelity + 11 seeding; art_oTL8XUEi, art_GShfYO5i). SpectraVoice gained tests for the sandbox, consent, and config paths (art_CT9XnMy5, art_2iZS72ww).
- **Wave 2 extended the posture from controls to operations and product.** AXIOM gained Prometheus metrics, request-correlated JSON logs, and a two-worker deployment proof (`0878bfa`, art_Fw8ix4e8); Nixus gained result-faithful explanations, idempotent few-shot seeding, and a regenerated, guard-protected lockfile (`33a120f`, art_oTL8XUEi); Research_Forge gained report history, multi-format export, and an editable outline (`0e140c2`, art_Uyjequ8Q); SpectraVoice gained a `--doctor` diagnostic, bounded crash recovery, and a 3-step setup path (`1f6247b`, art_8ejgup9W).

| Control | Nixus-Sql | AXIOM | Research_Forge | SpectraVoice |
| --- | --- | --- | --- | --- |
| Fail-closed auth / trust | API-key auth, server-issued sessions (art_1Yd1oLTp) | Fail-closed `require_api_key`, protected endpoints (art_5SxCetYq) | Auth + ownership on all routes (art_6g7o0evf) | Sandboxed commands, consent gate (art_CT9XnMy5) |
| CI workflow on PRs | art_RCJKKa73 | art_kAsInHYW | art_6g7o0evf, art_Gmx69Ym3 | W0/W1 PRs (verified in git history) |
| Credential hygiene | Env-provided compose credentials (art_RCJKKa73) | Clean scan at baseline (art_mEKQ5bhY) | Env-driven backend URL (art_Gmx69Ym3); re-confirmed clean (art_tsz9H96x) | `.env.example` + `.gitignore` (art_CT9XnMy5) |
| Test suite | ~111 offline tests default + CI; +43 in W2 (art_1Yd1oLTp, art_RCJKKa73, art_oTL8XUEi) | Integration suite in CI; 147 passed / 3 skipped (art_kAsInHYW, art_Fw8ix4e8) | Zero → auth/cost/sanitization suites (art_6g7o0evf) | Sandbox/consent/config tests (art_CT9XnMy5, art_2iZS72ww) |
| Ops / packaging | Regenerated lockfile + lockfile guard (art_oTL8XUEi) | Production Dockerfile, full-stack compose, two-worker proof (art_Fw8ix4e8) | Vite frontend build + CI (art_Gmx69Ym3) | `--doctor`, bounded recovery, 3-step setup (art_8ejgup9W) |

---

## 4. Known remaining items and honest limitations

**Program-level limitations:**

1. **Nixus-Sql rate limiting is deferred.** The master plan's Wave 0 task listed per-IP rate limits (art_gwXo54Vz), but merged PR #2 delivered auth, sessions, and the build fix (art_1Yd1oLTp). Rate limiting remains a future recommendation, not a shipped control.
2. **Multi-tenancy is explicitly out of scope.** The program targets a single-operator deployment (art_gwXo54Vz). Opening any repo to additional users or the public internet requires multi-tenancy and isolation work not covered by this plan.
3. **The live Nixus benchmark was not rerun for Wave 2.** A live 55/57 benchmark rerun requires live API/Postgres/LLM keys; 55/57 stands as the documented baseline (art_DTPFXX7u). Offline gold-corpus grounding was verified at 87/87 (art_GShfYO5i). Scope-refusal behavior (10/10 at baseline) is unchanged by Wave 2.
4. **Three pre-existing platform onboarding PRs remain open** — one each on AXIOM_Adaptive_RAG, Research_Forge, and SpectraVoice (".obvious onboarding contract" branches; verified open on 2026-09-17). They predate this plan, are not part of it, and are flagged for the owner's decision.
5. **SpectraVoice is macOS-only; CI is not.** Tool-execution paths cannot be exercised on Linux runners; SpectraVoice's CI runs on Linux with mocked probes, covering sandbox logic and consent gates, with dogfooding on the Mac where available (art_gwXo54Vz). macOS-only runtime constraints are unchanged by Wave 2.
6. ~~**Temporarily relaxed ruff rule families.**~~ **Resolved.** The families relaxed at Nixus W1 (art_RCJKKa73) were re-enforced in Wave 2 — I/DTZ/C4/ISC/LOG/UP/FURB — with ruff clean and the CI lockfile guard intact (`ci.yml:91–96`) (`33a120f`, art_oTL8XUEi, art_GShfYO5i).

**Non-blocking observations (documented, not fixed in this program):**

- **Nixus:** cached explanations bypass the fidelity re-check — a cached result does not go through `explanation_matches_result` (minor).
- **Nixus:** pre-existing misleading "no rows matched" wording on the never-executed grounding-cap path.
- **Research_Forge:** three session endpoints (status, details, upload-into-existing) are API-key-only without per-session ownership — consistent with the documented single-operator scope.
- **Research_Forge:** factcheck duplicate removal was implemented at the sequential-implementation level; the parallel path is the live production code.
- **AXIOM (and other repos):** remote feature branches were left in place — no deletion instruction was given.
- **AXIOM:** `claude_evaluator`'s own AsyncAnthropic client never reports usage, so per-query token metrics undercount on the evaluation path — recommended follow-up PR wiring usage reporting into the evaluator client (art_foQIGjIf).
- **AXIOM:** the worker-count constraint is documented but not enforced (art_foQIGjIf).
- **AXIOM:** pre-existing `vector_store.connect()` DDL, inherited from W1, sits beside the Alembic chain (art_foQIGjIf).

---

## 5. Independent audits and reviews

- **art_tsz9H96x — Independent Audit, Research_Forge & SpectraVoice (read-only).** Both repos independently confirmed on `origin/main` with file+line evidence:
  - **Research_Forge (at `0e140c2`):** auth dependency present on all 13 routes (only `/health` open, by design); ownership dependencies fail closed with 401/403/404; `MAX_CONCURRENT_RUNS` semaphore returns 429 with Retry-After at both start points; token budget enforced via the shared LLM wrapper; sanitized errors test-asserted; configuration credential-clean.
  - **SpectraVoice (at `1f6247b`):** no `shell=True` anywhere in the execution path; strict allowlist argv with metacharacter rejection asserted at both the sandbox and executor level for `ls; curl|sh` and `python -c`; the consent gate blocks screenshot upload at the provider boundary (`vision_calls==0` asserted); the crash loop is bounded (max 5 restarts per 60 s, then exit 1); `--doctor` runs headless per-permission checks with fix-it links; Whisper pinned `==20250625`.
- **art_GShfYO5i — Nixus-Sql W2 preview review @ `cfa158a`.** Independent line-level review of the Wave 2 branch: all five criteria met, with file+line evidence; the grounding gate confirmed hard (a mismatch prevents execution); 43/43 new tests passing and the guard path genuinely executed; reviewed head `cfa158a` confirmed content-identical to the squash-merged `33a120f`.
- **art_foQIGjIf — AXIOM W2 preview review @ `ee155ea`.** Independent read-only line-level review of Wave 2 PR #4; reviewed head `ee155ea` confirmed byte-identical to the merged content (squash `0878bfa` on `main`); CI verified live at the reviewed SHA, 6/6 green including the Alembic fresh-database job. All five Wave 2 criteria met: 401 envelope byte-for-byte intact (enrichment gated on the pre-existing context key; W1 lock asserted at `test_auth_and_lifecycle.py:147`); strict request-ID validation rejects control characters; Prometheus metrics names/labels match the tests, with token accounting wired at the real LLM/embedding call sites; migration `c4d8e2f6a9b1` includes a downgrade and no `create_all` remains in app paths; non-root Dockerfile with healthcheck; the two-worker smoke script verified as genuinely cross-process and reproducible. Honesty note: the smoke script was verified statically by inspection, **not** executed in the review environment (no live PG/Redis available); the worker executed it successfully during development (art_Fw8ix4e8).

---

## Source register

| Source | Artifact |
| --- | --- |
| Master plan — waves, acceptance criteria, execution model, exclusions | art_gwXo54Vz |
| Nixus-Sql production-readiness survey | art_DTPFXX7u |
| AXIOM_Adaptive_RAG production-readiness survey | art_mEKQ5bhY |
| Research_Forge production-readiness survey | art_2Hi9nExY |
| SpectraVoice production-readiness survey | art_BVfU87TR |
| Nixus-Sql W0 — PR #2 (merged, `dcd2ed6`) | art_1Yd1oLTp |
| Nixus-Sql W1 — PR #3 (merged, `8aa807e`) | art_RCJKKa73 |
| Nixus-Sql W2 — PR #4 (merged, `33a120f`) | art_oTL8XUEi |
| AXIOM W0 — PR #2 (merged, `da94af5`) | art_5SxCetYq |
| AXIOM W1 — PR #3 (merged, `b4fe841`) | art_kAsInHYW |
| AXIOM W2 — PR #4 (merged, `0878bfa`) | art_Fw8ix4e8 |
| Research_Forge W0 — PR #2 (merged, `cf07dce`) | art_6g7o0evf |
| Research_Forge W1 — PR #3 (merged, `70a8497`) | art_Gmx69Ym3 |
| Research_Forge W2 — PR #4 (merged, `0e140c2`) | art_Uyjequ8Q |
| SpectraVoice W0 — PR #2 (merged, `310eb6a`) | art_CT9XnMy5 |
| SpectraVoice W1 — PR #3 (merged, `4351be0`) | art_2iZS72ww |
| SpectraVoice W2 — PR #4 (merged, `1f6247b`) | art_8ejgup9W |
| Nixus-Sql W2 preview review @ `cfa158a` | art_GShfYO5i |
| AXIOM W2 preview review @ `ee155ea` | art_foQIGjIf |
| Independent audit — Research_Forge & SpectraVoice | art_tsz9H96x |

*Verified against the four repositories on 2026-09-17: all twelve merge SHAs present on `origin/main` (Waves 0–2; the four Wave 2 SHAs re-verified after merge — Nixus `33a120f`, AXIOM `0878bfa`, Research_Forge `0e140c2`, SpectraVoice `1f6247b`; AXIOM additionally confirmed via the GitHub API with mergedAt 2026-09-17T19:26:53Z); `ci.yml` present in all four repos; Nixus `pytest.ini` set to `testpaths = tests eval`; Research_Forge `backend/tests/` containing auth, cost-control, and error-sanitization suites; and the three onboarding PRs open. The Nixus W2 review head (`cfa158a`) and AXIOM W2 review head (`ee155ea`, byte-identical to the merged `0878bfa`) are as recorded in art_GShfYO5i and art_foQIGjIf respectively.*

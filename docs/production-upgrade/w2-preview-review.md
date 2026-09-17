# Nixus-Sql W2 Preview Review — `feat/w2-quality-usability` @ `cfa158a`

**Scope:** Read-only preview review of `origin/feat/w2-quality-usability` as pushed (head `cfa158a`) vs `origin/main` (`8aa807e`). Four commits: `812ef95` (explanation-result fidelity), `e86ba97` (few-shot seeding), `ccbc620` (API quickstart docs), `cfa158a` (ruff families + lock regen). Merge-base diff: 11 files, +847/−75.

**Timing note (preview framing):** during the review window PR #4 was merged (squash `33a120f`, state MERGED, 4/4 CI checks green — run 35263438251, observed live). `git ls-remote` confirms the pushed branch head is still `cfa158a`, so everything below describes exactly what merged. These findings are **as of `cfa158a`** — evidence for the acceptance record, not a new review verdict, and no feedback was sent to the worker.

**Method:** fetch-only; the repo working tree was never written (it sat at the coder's live state and was re-verified clean). All file reads pinned to `cfa158a` via `git show`. Two checks were executed in an isolated `git archive` export in `/tmp` (no repo writes): `ruff check` and the two new test files. Criteria sourced from master plan `art_gwXo54Vz` (W2/Nixus-Sql section) plus the W2 dispatch brief.

---

## Verdict summary

| # | Criterion | Verdict |
|---|-----------|---------|
| 1 | M3 faithfulness — grounding on generated SQL + explanation matches executed result, in-graph, W0 plumbing respected | **Met** (grounding hard gate pre-exists and genuinely blocks execution; fidelity half new, wired, tested) |
| 2 | Few-shot cold-start seeding from eval corpus, with test | **Met** |
| 3 | README quickstart for API mode; compose/env credential consistency | **Met** |
| 4 | ruff.toml style families re-enabled where they pass cleanly | **Met** (verified by execution, not just config) |
| 5 | requirements.lock regenerated without streamlit; lockfile guard intact | **Met** (import-safety verified) |
| — | Master-plan accept line: benchmark (55/57 baseline, 10/10 scope) maintained/improved **with run output attached** | **Cannot tell from branch state — no run output attached** (see Risks #1) |

---

## Criterion 1 — M3 faithfulness: MET (with residual notes)

The criterion has two halves. All line numbers are at `cfa158a`.

### (a) Grounding on generated SQL — hard gate, pre-existing and unchanged

- `nixus/graph/graph.py:156-162` — conditional edge after `verify_grounding`: grounded → `execute_query`; **not grounded → `self_correct`** (while `correction_attempts < MAX_ATTEMPTS`), then `explain_result` at the cap. Ungrounded SQL can never reach `execute_query`. `graph.py` is unchanged on this branch (absent from the diffstat), so the gate is inherited from W0/W1 work — the branch builds on it rather than adding it.
- `nixus/graph/grounding.py:97-181` — pure `check_grounding` over a `SchemaView` built from the **live introspected catalog** (`:83-94`), not the top-k retrieval context (which would false-positive on omitted tables). Tables verified rigorously (`:128`); columns confidence-gated (`:155-173`) with escapes for CTEs, aliases, subqueries, stars, and ambiguous scope. Unparseable SQL returns `checked=False` (`:101-105`) and defers to `validate_syntax`.
- `nixus/graph/nodes/verify_grounding.py:53-67` — **fails open** when the schema is unavailable (`is_grounded=True, checked=False`), per the module's governing rule (`:10-17`: a false positive is worse than a false negative). `explain_result.py:196-197` caps confidence at MEDIUM when grounding was skipped rather than clean.

**Answer to the brief's key question (does the check prevent execution or just warn?):** it **prevents execution**. The routing is a hard gate in the graph wiring; on mismatch the node also clears stale results (`verify_grounding.py:96`, `execution_result=None`) so nothing ungrounded is ever explained as if it ran. The residual false-negative surface is deliberate and bounded: (i) fail-open on introspection outage, (ii) unqualified columns under ambiguous scope pass, (iii) the schema view is cached for process lifetime (`verify_grounding.py:32-44`), so mid-process drift relies on the advisory startup drift check (`api/main.py`, unchanged).

### (b) Explanation matches executed result — the branch's new work

- `nixus/graph/explanation_check.py` — ~180 new lines. `explanation_matches_result` (`:255-334`) deterministically verifies two claim types against the executed rows: **row-count claims** (`_ROW_COUNT_CLAIM` `:171-177`) must equal `row_count`, and **cited data values** (`_CITED_VALUE` `:186-188` — currency, thousands-grouped, decimals) must appear among the executed cells (`_cell_values` `:219-240`). Conservative escapes, all in the false-negative-safe direction: selector phrases ("top 3 rows") `:181`/`:293-294`; derived figures (average/difference/percent) window `:322-324`; values the user's own question echoes `:327-328`; empty results `:312`.
- Wiring inside the graph: `nixus/graph/nodes/explain_result.py:153-158` runs both checks (overstatement + fidelity) on every generation; `:158-172` regenerates **once** with `STRICT_SUFFIX` quoting the exact violations (`:59-65`); `:174-188` re-checks and falls back to the deterministic `describe_result_plainly` (`explanation_check.py:337-367`) — which by construction cannot hallucinate. Latency bounded at 2 LLM calls max.
- **W0 auth/session plumbing respected:** no client-supplied session usage anywhere new; `session_id` appears only truncated in log lines (`explain_result.py:169,184`); state flows through the existing checkpointer (unchanged).

### Residual correctness notes on the grounding/fidelity logic itself (all false-negative direction, none blocking)

1. **Abandoned-path wording (pre-existing shape, adjacent to M3):** when grounding or validation fails past the attempt cap, flow enters `explain_result` with no `execution_result` (`rows=[]`, `row_count=0`). The node has no branch for "query never executed" (`explain_result.py:97-107`), so the explanation/prompt frame yields "No rows matched the query." (`explanation_check.py:350-351`) — when the truth is "the SQL was rejected as ungrounded." The real cause does surface in `stream_updates` (`verify_grounding.py:98-102`), but the explanation itself is misleading. The branch's fidelity half explicitly promises "explanation matches executed result"; on this path there is no executed result, and the checker can't see the gap.
2. **Cached explanations bypass both checks** (`explain_result.py:109-130`). Sound for entries stored after this change (they pass the backstop before caching, `:222-234`), but cache rows written by the pre-fidelity build are served verbatim forever. Green-field installs are unaffected.
3. `_value_matches` substring acceptance (`explanation_check.py:252`) — a cited figure passes if it is a substring of a string cell ("42" inside "14200"). Conservative, per the governing rule, but it is a hole in the value check.
4. "Showing 12 rows" is selector-exempt (`:181`), so a wrong count under "showing" is never verified.

---

## Criterion 2 — Few-shot cold-start seeding: MET

- `nixus/db/fewshot_seeding.py` (new, 145 lines): loads answerable gold pairs from the eval corpus — `eval/saas_gold.ANSWERABLE` by default (matches the compose quickstart target), archived Chinook via `source="chinook"`, unknown source raises (`:53-75`). Answerable-only discipline is explicit: scope/refusal cases never become exemplars (`:10-12`).
- **Idempotency by identity, not embedding:** one `SELECT natural_language` read (`:90-93`) skips everything already stored with zero embedding API calls (`:106-116`); store-level near-duplicates count as skipped (`:126`). Per-item failures are counted, warned, and never raised (`:117-133`) — `SeedStats` reports the outcome (`:42-51`, `:135-138`).
- Startup wiring: `api/main.py:85-99` runs seeding in FastAPI lifespan inside its own try/except (never blocks boot), gated by `nixus/config.py:112` `fewshot_seed_on_startup: bool = True` (`FEWSHOT_SEED_ON_STARTUP=false` to disable). Seeded exemplars are stamped `auto_learned=False` (`:122`) so seeded vs auto-learned stay separable.
- Corpus reality check: `eval/saas_gold.py:283` `ANSWERABLE = _EASY + _MEDIUM + _HARD` — **57 pairs** (verified by executing the module). This matches the master plan's benchmark denominator (55/57 baseline).
- **Test** (`tests/db/test_fewshot_seeding.py`, 10 tests): real-corpus identity against `ANSWERABLE` (`:20-26`); chinook == 30 (`:29-30`); unknown source raises (`:33-39`); sqlglot table extraction incl. CTE and parse-failure-safe (`:45-57`); cold start stores all with `auto_learned=False` and extracted tables (`:86-98`); warm start makes zero store/embedding calls (`:101-111`); near-dup counted skipped (`:114-123`); store failures counted, not raised, log asserted (`:126-140`); existing-read failure degrades to attempt-all (`:143-155`). The store is monkeypatched by design (module docstring `:1-8`: runs in the W1 CI profile with no Postgres/no embedding provider) — appropriate; the store itself is pre-existing and separately covered.

---

## Criterion 3 — README quickstart for API mode: MET

- `README.md:108-129` — new "API quickstart (curl)": two-step flow (POST question → read answer), **server-issued session** semantics ("send an empty `session_id` on the first call, then echo back the id the response returns"), multi-turn/clarification note, and the 404 contract for unissued session ids (`:128`). `/api/v1/run-sql` noted as SELECT-only, enforced server-side (`:129-130`). Session semantics were already documented at `README.md:95-98`; the quickstart makes them actionable.
- Required-credentials list (`README.md:153+`): `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `API_KEY` (with `openssl rand -hex 32` hint), and the three bundled-Postgres credentials — **consistent with actual enforcement**: `docker-compose.yml:26-30` hard-requires `${POSTGRES_USER:?…}`, `${POSTGRES_PASSWORD:?…}`, `${POSTGRES_READONLY_PASSWORD:?…}` and refuses the retired demo values `nixus`/`nixus_readonly` (`:16-22`). `.env.example` documents the same three variables with a URL-encoding note. No drift between docs and enforcement found.
- Corroborating CI evidence: both docker-build jobs pass on the merged head (run 35263438251), so the documented compose path builds.

---

## Criterion 4 — ruff style families re-enabled: MET (verified by execution)

- `ruff.toml`: the Wave-1 temporary relaxations are removed — `I`, `UP`, `DTZ`, `C4`, `ISC`, `LOG`, `FURB` re-enforced — and the Wave-0 `per-file-ignores` section is deleted entirely. Remaining ignores are documented as deliberate, not temporary (`PLW`, `PLR`, `PERF`, `SIM`, `RUF`, plus the `BLE001`/`S110` resilience-pattern keeps).
- **Executed check:** `ruff 0.16.8` (the version pinned in `requirements-dev.txt`, per the file header) `check --no-cache .` on a pristine `git archive` export of `cfa158a` → **"All checks passed!" (exit 0)**. The commit's "pass cleanly" claim is verified, not asserted.

---

## Criterion 5 — requirements.lock without streamlit; guard intact: MET

- `requirements.lock`: −25 lines. `streamlit==1.57.0` removed together with its transitive chain (altair, pyarrow, pydeck, attrs, blinker, cachetools, gitdb/GitPython/smmap, itsdangerous, Jinja2/MarkupSafe, jsonschema + specs/referencing/rpds-py, pillow, protobuf, python-multipart, toml, watchdog, websockets, httptools). Core runtime deps retained (fastapi, langchain*, anthropic, openai, asyncpg, psycopg*, plotly, pandas, sqlglot, pgvector, …).
- **Import-safety verified:** `git grep` across the `cfa158a` tree for every removed package and its import name (streamlit, altair, GitPython/`import git`, jinja2, jsonschema, PIL, multipart/`UploadFile`, watchdog, websockets, toml, cachetools, itsdangerous, pyarrow, attr) → **zero hits** in tracked `*.py`/`*.txt`. The trim breaks no import as of this head.
- **Lockfile guard intact and untouched:** `.github/workflows/ci.yml:91-96` (export the committed tree; `::error` if `requirements.lock` is not in git — protects the Dockerfile COPY) and `ci.yml:82-83` (tests install from the lock, so CI validates it); `Dockerfile:10-11` unchanged. End-to-end confirmation: the CI `tests`, `lint`, `docker-build`, and `docker-build-web` jobs all pass on the merged head.

---

## Test-quality assessment

**Do the mismatch tests actually execute the guard path? Yes — for the fidelity half, at the node level.**

- `tests/graph/test_explanation_fidelity.py` (43-line parametrized must-not-flag table `:31-77`; 7 must-flag cases `:82-104`). The must-flag cases assert the **specific problem text**, not just the boolean (`:99-104`) — a wrong-count test cannot pass by accident.
- The three node-integration tests (`:176-222`) call the **real `explain_result_node`** with only the LLM scripted (`_ScriptedLLM`), asserting: the regenerate-once flow fires and the strict prompt quotes the exact violation ("returned 5 rows") (`:176-190`); persistent failure produces exactly the deterministic fallback (`:193-208`); a faithful explanation passes through with a single LLM call (`:211-222`). This is the real detect → regen → fallback sequence, not a mock of the guard.
- Grounding mismatch coverage pre-exists unchanged: `tests/graph/test_grounding.py` (on `main`).
- Local execution this session (isolated `/tmp` export of `cfa158a`): **43 passed** across the two new files, 1.74s.
- Gaps (minor): no test covers the cached-explanation bypass path; no test combines an overstatement and a fidelity failure in one explanation; the abandoned-path wording gap is untested because it is the gap itself (see Criterion 1, note 1).

---

## Top-3 risks

1. **Benchmark acceptance evidence is missing from the branch.** The master plan's W2 accept line for Nixus-Sql requires the documented benchmark (55/57 baseline, 10/10 scope) "maintained or improved, **with run output attached**." The diff touches no `eval/` files and attaches no new run output at `cfa158a`; suite-green is the only half satisfied on-branch. (Prior project-record verification — 248 passed / 109 skipped, 87/87 offline corpus grounding — is not a live benchmark re-run.) Cheap fix: run `eval/run_saas_benchmark.py` and attach the output to the delivery dossier.
2. **Misleading explanation on the never-executed path (pre-existing, adjacent).** A grounding/validation failure past the attempt cap ends in "No rows matched the query." instead of "the query could not be safely run." The branch's fidelity promise ("explanation matches executed result") makes this residual hole more conspicuous; stream_updates carry the real cause, the explanation does not. Natural follow-up: branch `explain_result` on a never-executed state.
3. **Cache-served explanations bypass fidelity re-checking.** Rows written by the pre-fidelity build are served verbatim (`explain_result.py:109-130`); only post-upgrade entries are guaranteed backstop-clean. Unaffected for fresh installs (the documented quickstart path); hardening options are re-check-on-serve or a cache schema version stamp.

---

## Review integrity

- Strictly read-only honored: no file edits, no commits, no pushes, no PR comments, no merges. Working tree verified clean before and after; all evidence read via `git show` at the pinned SHA `cfa158a`.
- Executed checks (ruff, pytest) ran in a `/tmp` `git archive` export, never in the repo checkout.

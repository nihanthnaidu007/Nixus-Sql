# Delivery Record — checkpoint assembly notes

This folder is the in-repository paper trail of the four-repo production upgrade
program (Nixus-Sql, AXIOM_Adaptive_RAG, Research_Forge, SpectraVoice). It was
assembled on 2026-09-17 from project artifacts in the Obvious project
"Production Upgrade Planning For Repos". This record documents how the folder was
assembled and which fetches were verbatim versus composed, so a reader can tell
retrieved content from record-keeping.

## Artifact fetch results

| File | Source artifact | Result |
|---|---|---|
| `dossier.md` | `art_jV2n9Tta` (Production Upgrade — Delivery Dossier, final, version 3) | Fetched verbatim |
| `master-plan.md` | `art_gwXo54Vz` (Production Upgrade Master Plan, version 1) | Fetched verbatim |
| `survey.md` | `art_DTPFXX7u` (Nixus-Sql Production-Readiness Survey, version 1) | Fetched verbatim |
| `w2-preview-review.md` | `art_GShfYO5i` (Nixus-Sql W2 Preview Review @ `cfa158a`, version 0) | Fetched verbatim |
| `w2-pr-record.md` | `art_oTL8XUEi` ([Merged] PR #4 card) | **Fetch exception** — see below |
| `README.md` | — | Composed index |

**Fetch exception:** `art_oTL8XUEi` is a pull-request card artifact
(`type: pull_request`), not a document — the fetch succeeded but the artifact
carries no markdown body to publish. `w2-pr-record.md` was therefore composed from
the card's metadata (reproduced verbatim inside that file) plus merge evidence
verified directly from `git log origin/main`. No content was invented to fill the gap.

## Verified checkpoint claims (with sources)

- **Wave merge chain on `origin/main`:** `dcd2ed6` (W0, PR #2) → `8aa807e` (W1, PR #3)
  → `33a120f` (W2, PR #4). Sources: `git log origin/main` at checkpoint assembly time;
  `dossier.md` per-repo delivery records and source register (W0 → `art_1Yd1oLTp`,
  W1 → `art_RCJKKa73`, W2 → `art_oTL8XUEi`).
- **Test suite:** 248 passed / 109 skipped. Source: `w2-preview-review.md`
  ("Prior project-record verification — 248 passed / 109 skipped, 87/87 offline
  corpus grounding — is not a live benchmark re-run").
- **Offline grounding:** 87/87 gold-corpus pairs grounded (30 Chinook + 57 SaaS;
  4 checker flags investigated and ruled false positives on H27 subquery aliases).
  Source: `dossier.md` §2.1 and §4.
- **Live 55/57 benchmark:** **not rerun** for Wave 2 — a live rerun requires live
  API/Postgres/LLM keys; 55/57 stands as the documented baseline (10/10 scope
  refusals unchanged). Source: `dossier.md` §2.1 and §4. Stated honestly here as
  the dossier states it.

## Scope of this commit

Docs-only: no runtime, CI, dependency, or test files are touched by this checkpoint.

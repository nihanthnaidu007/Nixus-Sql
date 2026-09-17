# Production Upgrade — Delivery Checkpoint

This folder is the **checkpoint paper trail** of the four-repo production upgrade
program (Nixus-Sql, AXIOM_Adaptive_RAG, Research_Forge, SpectraVoice) coordinated in
the Obvious project "Production Upgrade Planning For Repos". It freezes inside the
repository itself the planning, review, and delivery documents that governed the
program and the final delivery state of Nixus-Sql, so the record survives alongside
the code it describes.

Documents marked *verbatim* are byte-for-byte copies of the named project artifacts
as of 2026-09-17; composed records cite their sources inline. See
[`delivery-record.md`](./delivery-record.md) for full assembly provenance.

| File | Source | What it is |
|---|---|---|
| [`dossier.md`](./dossier.md) | `art_jV2n9Tta` (verbatim) | Consolidated delivery dossier for all four repos — per-repo delivery records, cross-repo quality posture, honest limitations, source register |
| [`master-plan.md`](./master-plan.md) | `art_gwXo54Vz` (verbatim) | Master plan — roadmap, acceptance criteria, CI-gated PR model, scope, single-operator constraint |
| [`survey.md`](./survey.md) | `art_DTPFXX7u` (verbatim) | Nixus-Sql production-readiness survey — the program's starting point |
| [`w2-preview-review.md`](./w2-preview-review.md) | `art_GShfYO5i` (verbatim) | Independent line-level review of the Nixus W2 branch @ `cfa158a` — five Wave 2 criteria MET |
| [`w2-pr-record.md`](./w2-pr-record.md) | `art_oTL8XUEi` (composed) | W2 PR #4 record — card metadata verbatim + git-verified merge evidence |
| [`delivery-record.md`](./delivery-record.md) | composed | How this folder was assembled — fetch results, verified claims, and the one fetch exception |

## Delivery state frozen here

Nixus-Sql production upgrade, Waves 0–2, merged to `main`:

- **Wave 0 — PR #2** (`dcd2ed6`): fail-closed API-key auth, server-issued sessions,
  buildable fresh clone, offline-suite `pytest.ini`.
- **Wave 1 — PR #3** (`8aa807e`): GitHub Actions CI workflow, ruff lint gates,
  Streamlit removal, credential-safe compose.
- **Wave 2 — PR #4** (`33a120f`): explanation-result fidelity, idempotent few-shot
  corpus seeding, ruff rule re-enforcement and lockfile regeneration.

Verification at delivery: 248 tests passed / 109 skipped; offline gold-corpus
grounding 87/87; the live 55/57 SaaS benchmark was **not** rerun (requires live
API/Postgres/LLM keys) and stands as the documented baseline — see
[`delivery-record.md`](./delivery-record.md).

The annotated tag **`production-upgrade/final`** marks the final delivery checkpoint
on `main` after this documentation PR.

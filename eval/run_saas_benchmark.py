"""SaaS honest benchmark — the BENCHMARK OF RECORD (6.2).

Scores the fixed SaaS gold set (eval/saas_gold.py) against the deterministic
nixus_saas seed using honest result-equivalence (eval/result_equivalence.py),
plus the scope/refusal cases on the scope gate's outcome. Emits a JSON report
(eval/saas_benchmark_results.json) with a summary (answerable pass/fail/total +
per-tier, scope pass/fail/total) and per-id results, and prints the honest
number with the failing ids.

This REPLACES the retired Chinook gold set as the project's benchmark. Run it
with the target pointed at nixus_saas and the schema re-embedded for SaaS:

    TARGET_DATABASE_URL=postgresql://nixus_readonly:nixus_readonly@localhost:5433/nixus_saas \
    TARGET_ADMIN_DATABASE_URL=postgresql://nixus:nixus@localhost:5433/nixus_saas \
      .venv/bin/python -m nixus.schema.reembed            # embeddings -> SaaS
    .venv/bin/python eval/run_saas_benchmark.py           # the benchmark of record

The API on :8000 must also be pointed at nixus_saas (it executes the generated
SQL there). Correctness is the pass/fail bar; latency is reported, not gated.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Allow `python eval/run_saas_benchmark.py` to import the eval package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx

from eval.conftest import BASE_URL, auth_headers, extract_rows, run_gold_sql
from eval.result_equivalence import results_equivalent
from eval.saas_gold import ANSWERABLE, SCOPE

RESULTS_PATH = Path("eval/saas_benchmark_results.json")

# Outcomes that must NOT have executed any SQL (refusals + clarification).
_NO_SQL_OUTCOMES = {"OUT_OF_SCOPE", "WRITE_REFUSAL", "NEEDS_CLARIFICATION"}


def _run_query_timed(client: httpx.Client, question: str) -> tuple[dict, float]:
    t0 = time.monotonic()
    resp = client.post(
        "/api/v1/run",
        json={"user_query": question, "session_id": ""},
    )
    ms = (time.monotonic() - t0) * 1000
    resp.raise_for_status()
    return resp.json(), ms


def score_answerable_case(client: httpx.Client, case: dict) -> dict:
    """Score one answerable case by result-equivalence to its gold SQL."""
    gold_rows = run_gold_sql(case["gold_sql"])
    state, latency_ms = _run_query_timed(client, case["question"])
    error = state.get("error")
    generated_sql = state.get("generated_sql")

    result = {
        "id": case["id"],
        "kind": "answerable",
        "tier": case["tier"],
        "ordered": case["ordered"],
        "question": case["question"],
        "generated_sql": generated_sql,
        "error": error,
        "gold_row_count": len(gold_rows),
        "latency_ms": round(latency_ms),
    }

    if error:
        result.update(passed=False, reason=f"API error: {error}", gen_row_count=None)
        return result

    api_rows = extract_rows(state)
    eq = results_equivalent(api_rows, gold_rows, ordered=case["ordered"])
    result.update(
        passed=bool(eq.equivalent),
        reason=eq.reason,
        gen_row_count=eq.gen_row_count,
        first_mismatch=eq.first_mismatch,
    )
    return result


def score_scope_case(client: httpx.Client, case: dict) -> dict:
    """Score one scope/refusal case on the scope gate outcome.

    Pass requires BOTH: scope_category == expected_outcome AND no SQL executed
    (refusals/clarifications must never reach generation/execution).
    """
    state, latency_ms = _run_query_timed(client, case["question"])
    scope_category = state.get("scope_category")
    generated_sql = state.get("generated_sql")
    sql_executed = bool(generated_sql)

    outcome_ok = scope_category == case["expected_outcome"]
    no_sql_ok = (case["expected_outcome"] not in _NO_SQL_OUTCOMES) or (not sql_executed)
    passed = outcome_ok and no_sql_ok

    reason = "ok"
    if not outcome_ok:
        reason = f"scope_category={scope_category!r} != expected {case['expected_outcome']!r}"
    elif not no_sql_ok:
        reason = "refusal/clarification but SQL was generated/executed"

    return {
        "id": case["id"],
        "kind": "scope",
        "expected_outcome": case["expected_outcome"],
        "scope_category": scope_category,
        "sql_executed": sql_executed,
        "passed": passed,
        "reason": reason,
        "latency_ms": round(latency_ms),
    }


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = (p / 100.0) * (n - 1)
    f = int(idx)
    c = min(f + 1, n - 1)
    return sorted_vals[f] + (idx - f) * (sorted_vals[c] - sorted_vals[f])


def run() -> dict:
    answerable_results: list[dict] = []
    scope_results: list[dict] = []
    latencies: list[float] = []

    with httpx.Client(base_url=BASE_URL, timeout=120.0, headers=auth_headers()) as client:
        for case in ANSWERABLE:
            r = score_answerable_case(client, case)
            answerable_results.append(r)
            latencies.append(r["latency_ms"])
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['id']:4} ({r['tier']}) — {r['reason']}")
        for case in SCOPE:
            r = score_scope_case(client, case)
            scope_results.append(r)
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['id']:4} (scope) — {r['reason']}")

    # ── summary ──
    tiers = ("easy", "medium", "hard")
    by_tier = {
        t: {
            "passed": sum(1 for r in answerable_results if r["tier"] == t and r["passed"]),
            "total": sum(1 for r in answerable_results if r["tier"] == t),
        }
        for t in tiers
    }
    a_pass = sum(1 for r in answerable_results if r["passed"])
    s_pass = sum(1 for r in scope_results if r["passed"])
    latencies.sort()

    summary = {
        "answerable": {
            "passed": a_pass,
            "failed": len(answerable_results) - a_pass,
            "total": len(answerable_results),
            "by_tier": by_tier,
        },
        "scope": {
            "passed": s_pass,
            "failed": len(scope_results) - s_pass,
            "total": len(scope_results),
        },
        # Reported metric only — never a pass/fail gate (Step 5).
        "latency_ms": {
            "p50": round(_percentile(latencies, 50)),
            "p95": round(_percentile(latencies, 95)),
            "samples": len(latencies),
            "note": "informational; latency is NOT a pass/fail gate",
        },
    }

    report = {"summary": summary, "results": answerable_results + scope_results}
    RESULTS_PATH.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    print("◈ SaaS honest benchmark (benchmark of record) — scoring gold set...\n")
    report = run()
    s = report["summary"]
    bt = s["answerable"]["by_tier"]
    print("\n" + "=" * 60)
    print("HONEST SaaS BASELINE")
    print("=" * 60)
    print(f"Answerable: {s['answerable']['passed']}/{s['answerable']['total']} "
          f"(easy {bt['easy']['passed']}/{bt['easy']['total']}, "
          f"medium {bt['medium']['passed']}/{bt['medium']['total']}, "
          f"hard {bt['hard']['passed']}/{bt['hard']['total']})")
    print(f"Scope:      {s['scope']['passed']}/{s['scope']['total']}")
    print(f"Latency (reported): p50={s['latency_ms']['p50']}ms p95={s['latency_ms']['p95']}ms")
    fail_with_tier = [
        f"{r['id']}({r.get('tier', 'scope')})"
        for r in report["results"] if not r["passed"]
    ]
    print(f"Failing ids: {fail_with_tier}")
    print(f"\nReport written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()

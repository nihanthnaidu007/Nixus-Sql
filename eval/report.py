"""
Benchmark report generator.

Reads a pytest-json-report JSON file and writes BENCHMARK.md to the repo root.

Usage:
    python eval/report.py [--input eval/benchmark_results.json] [--output BENCHMARK.md]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _rate(passed: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{passed}/{total} ({passed / total:.1%})"


def _status(passed: int, total: int, threshold: float) -> str:
    if total == 0:
        return "⚠ NO DATA"
    return "✅ PASS" if (passed / total) >= threshold else "❌ FAIL"


def _injection_status(passed: int, total: int) -> str:
    if total == 0:
        return "⚠ NO DATA"
    return "✅ PASS" if passed == total else "❌ FAIL"


def _load_metrics(metrics_path: Path) -> dict:
    if not metrics_path.exists():
        return {}
    try:
        return json.loads(metrics_path.read_text() or "{}")
    except json.JSONDecodeError:
        return {}


def _fmt_ms(metrics: dict, key: str) -> str:
    v = metrics.get(key)
    if v is None:
        return "—"
    return f"{round(float(v))} ms"


def _fmt_rate(metrics: dict, key: str) -> str:
    v = metrics.get(key)
    if v is None:
        return "—"
    return f"{float(v):.1%}"


def _fmt_pct(latencies: dict | None, p_key: str) -> str:
    """Render one percentile from a nested {p50, p95, p99, samples} dict."""
    if not latencies or p_key not in latencies or latencies[p_key] is None:
        return "—"
    return f"{round(float(latencies[p_key]))} ms"


def _fmt_samples(latencies: dict | None) -> str:
    if not latencies or latencies.get("samples") is None:
        return "—"
    return str(latencies["samples"])


def generate_report(results_path: Path, output_path: Path, metrics_path: Path | None = None) -> None:
    with results_path.open() as f:
        data = json.load(f)

    if metrics_path is None:
        metrics_path = Path("eval/benchmark_metrics.json")
    metrics = _load_metrics(metrics_path)

    tests = data.get("tests", [])
    run_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    duration_s = data.get("duration", 0)

    # Bucket tests by module
    categories: dict[str, dict] = {
        "sql_correctness": {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "cache_accuracy":  {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "self_correction": {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "safety_write":    {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "safety_read":     {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "safety_injection":{"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "chart":           {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "latency":         {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
        "other":           {"passed": 0, "failed": 0, "skipped": 0, "ids": []},
    }

    for t in tests:
        nodeid: str = t.get("nodeid", "")
        outcome: str = t.get("outcome", "unknown")

        # Match by file path first so tests like `test_cache_miss_latency`
        # in test_latency.py don't get mis-bucketed into cache_accuracy.
        if "test_sql_correctness.py" in nodeid:
            key = "sql_correctness"
        elif "test_latency.py" in nodeid:
            key = "latency"
        elif "test_cache_accuracy.py" in nodeid:
            key = "cache_accuracy"
        elif "test_self_correction.py" in nodeid:
            key = "self_correction"
        elif "test_safety.py" in nodeid and "injection" in nodeid:
            key = "safety_injection"
        elif "test_safety.py" in nodeid and "write" in nodeid:
            key = "safety_write"
        elif "test_safety.py" in nodeid and "read" in nodeid:
            key = "safety_read"
        elif "test_chart_classification.py" in nodeid:
            key = "chart"
        else:
            key = "other"

        bucket = categories[key]
        if outcome == "passed":
            bucket["passed"] += 1
        elif outcome == "failed":
            bucket["failed"] += 1
            bucket["ids"].append(nodeid.split("::")[-1])
        elif outcome == "skipped":
            bucket["skipped"] += 1

    # Compute totals
    def total(k: str) -> int:
        b = categories[k]
        return b["passed"] + b["failed"] + b["skipped"]

    sql_pass = categories["sql_correctness"]["passed"]
    sql_total = total("sql_correctness")
    inject_pass = categories["safety_injection"]["passed"]
    inject_total = total("safety_injection")

    # Non-negotiable bars
    sql_ok = sql_total > 0 and (sql_pass / sql_total) >= 0.80
    inject_ok = inject_total > 0 and inject_pass == inject_total
    overall_ok = sql_ok and inject_ok

    # Latency percentile dicts (nested schema written by test_latency.py).
    miss_lat = metrics.get("cache_miss_latencies") or {}
    hit_lat = metrics.get("cache_hit_latencies") or {}

    lines: list[str] = [
        "# NIXUS SQL — Benchmark Report",
        "",
        f"**Generated:** {run_date}  ",
        f"**Suite duration:** {duration_s:.1f}s  ",
        f"**Overall:** {'✅ ALL BARS MET' if overall_ok else '❌ ONE OR MORE BARS FAILED'}",
        "",
        "---",
        "",
        "## Non-Negotiable Bars",
        "",
        "| Metric | Result | Bar | Status |",
        "|--------|--------|-----|--------|",
        f"| SQL correctness rate | {_rate(sql_pass, sql_total)} | ≥ 80 % | {_status(sql_pass, sql_total, 0.80)} |",
        f"| SQL injection blocked | {_rate(inject_pass, inject_total)} | 5 / 5 | {_injection_status(inject_pass, inject_total)} |",
        "",
        "---",
        "",
        "## Results by Category",
        "",
        "| Category | Passed | Failed | Skipped | Rate | Bar |",
        "|----------|--------|--------|---------|------|-----|",
        f"| SQL correctness (30 queries) | {categories['sql_correctness']['passed']} | {categories['sql_correctness']['failed']} | {categories['sql_correctness']['skipped']} | {_rate(sql_pass, sql_total)} | ≥ 80 % |",
        f"| Cache paraphrase hit rate    | {categories['cache_accuracy']['passed']} | {categories['cache_accuracy']['failed']} | {categories['cache_accuracy']['skipped']} | {_fmt_rate(metrics, 'paraphrase_hit_rate')} | ≥ 60 % |",
        f"| Cache unrelated miss rate    | {categories['cache_accuracy']['passed']} | {categories['cache_accuracy']['failed']} | {categories['cache_accuracy']['skipped']} | {_fmt_rate(metrics, 'unrelated_miss_rate')} | ≥ 80 % |",
        f"| Self-correction resilience   | {categories['self_correction']['passed']} | {categories['self_correction']['failed']} | {categories['self_correction']['skipped']} | {_rate(categories['self_correction']['passed'], total('self_correction'))} | 5 / 5 |",
        f"| Safety — WRITE detection     | {categories['safety_write']['passed']} | {categories['safety_write']['failed']} | {categories['safety_write']['skipped']} | {_rate(categories['safety_write']['passed'], total('safety_write'))} | ≥ 80 % |",
        f"| Safety — READ pass-through   | {categories['safety_read']['passed']} | {categories['safety_read']['failed']} | {categories['safety_read']['skipped']} | {_rate(categories['safety_read']['passed'], total('safety_read'))} | ≥ 80 % |",
        f"| Safety — injection blocked   | {categories['safety_injection']['passed']} | {categories['safety_injection']['failed']} | {categories['safety_injection']['skipped']} | {_rate(inject_pass, inject_total)} | 5 / 5 |",
        f"| Chart classification         | {categories['chart']['passed']} | {categories['chart']['failed']} | {categories['chart']['skipped']} | {_rate(categories['chart']['passed'], total('chart'))} | 7 / 7 |",
        f"| Latency benchmarks           | {categories['latency']['passed']} | {categories['latency']['failed']} | {categories['latency']['skipped']} | {_rate(categories['latency']['passed'], total('latency'))} | see targets |",
        "",
        "---",
        "",
        "## Latency Percentiles",
        "",
        "> Cache-miss questions are generated fresh per run with a unique timestamp",
        "> token embedded in the natural-language text, so the semantic cache cannot",
        "> match them across runs. Cache-miss numbers reflect the full pipeline",
        "> including LLM calls; cache-hit numbers reflect served-from-cache",
        "> responses on a pre-warmed cache.",
        "",
        "| Path | p50 | p95 | p99 | Samples | Target |",
        "|------|-----|-----|-----|---------|--------|",
        f"| Cache miss (full pipeline) | {_fmt_pct(miss_lat, 'p50')} | {_fmt_pct(miss_lat, 'p95')} | {_fmt_pct(miss_lat, 'p99')} | {_fmt_samples(miss_lat)} | p50 < 30000 ms / p95 < 60000 ms / p99 < 90000 ms |",
        f"| Cache hit (vector lookup)  | {_fmt_pct(hit_lat, 'p50')} | {_fmt_pct(hit_lat, 'p95')} | {_fmt_pct(hit_lat, 'p99')} | {_fmt_samples(hit_lat)} | p50 < 3000 ms |",
        "",
    ]

    # Failed test details
    all_failed: list[str] = []
    for b in categories.values():
        all_failed.extend(b["ids"])

    if all_failed:
        lines += [
            "---",
            "",
            "## Failed Tests",
            "",
        ]
        for fid in all_failed:
            lines.append(f"- `{fid}`")
        lines.append("")

    lines += [
        "---",
        "",
        "_Report generated by `eval/report.py`. Re-run with `python eval/run_benchmark.py`._",
        "",
    ]

    output_path.write_text("\n".join(lines))
    print(f"Report written to {output_path}")

    if not overall_ok:
        print("❌ Non-negotiable bars not met — see report for details.")
        sys.exit(1)
    else:
        print("✅ All non-negotiable bars met.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate BENCHMARK.md from pytest-json-report output")
    parser.add_argument("--input",   default="eval/benchmark_results.json", help="Path to pytest-json-report JSON file")
    parser.add_argument("--metrics", default="eval/benchmark_metrics.json", help="Sidecar metrics JSON written by tests")
    parser.add_argument("--output",  default="BENCHMARK.md", help="Output markdown file path")
    args = parser.parse_args()

    results_path = Path(args.input)
    metrics_path = Path(args.metrics)
    output_path  = Path(args.output)

    if not results_path.exists():
        print(f"Error: results file not found: {results_path}", file=sys.stderr)
        print("Run `python eval/run_benchmark.py` first.", file=sys.stderr)
        sys.exit(1)

    generate_report(results_path, output_path, metrics_path=metrics_path)


if __name__ == "__main__":
    main()

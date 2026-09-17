"""
NIXUS SQL evaluation harness — LEGACY Chinook-demo CLI.

NOTE (6.2): the BENCHMARK OF RECORD is now the SaaS honest benchmark:

    .venv/bin/python eval/run_saas_benchmark.py        # benchmark of record

This legacy runner remains for the Chinook DEMO-target capability tests that
were not retired (safety, self_correction, chart_classification) plus the
recalibrated reported-metric latency. The Chinook SQL-correctness gold set
(E02) and the cache-accuracy gate (test_unrelated_miss_rate) were retired and
ARCHIVED under eval/archive_chinook/ (not collected) — so those categories are
no longer available here.

Usage:
    python eval/run_benchmark.py                            # run remaining categories
    python eval/run_benchmark.py --category safety          # run only the safety tests
    python eval/run_benchmark.py -k correctness             # pytest -k expression filter
    python eval/run_benchmark.py --no-report                # skip BENCHMARK.md generation
    python eval/run_benchmark.py --no-latency               # skip slow latency tests
    python eval/run_benchmark.py -v                         # verbose pytest output

Valid --category values:
    self_correction, safety, chart_classification, latency, all

Environment:
    NIXUS_API_URL   Override the API base URL (default: http://localhost:8000)

The script runs pytest with --json-report and writes results to
eval/benchmark_results.json, then calls eval/report.py to produce BENCHMARK.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Allow `python eval/run_benchmark.py` to import the eval package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# NOTE (6.2): sql_correctness + cache_accuracy were retired/archived (see
# eval/archive_chinook/). The SaaS benchmark of record is eval/run_saas_benchmark.py.
CATEGORY_TO_PATH: dict[str, str] = {
    "self_correction": "eval/test_self_correction.py",
    "safety": "eval/test_safety.py",
    "chart_classification": "eval/test_chart_classification.py",
    "latency": "eval/test_latency.py",
}
VALID_CATEGORIES = list(CATEGORY_TO_PATH.keys()) + ["all"]

# Sidecar metric keys that each category writes. Used to scope reset_metrics()
# so a per-category run only clears its own keys and preserves metrics recorded
# by an earlier full run. Keep this in sync with record_metric() calls in the
# corresponding test files.
CATEGORY_METRIC_KEYS: dict[str, list[str]] = {
    "self_correction": [],
    "safety": [],
    "chart_classification": [],
    "latency": ["cache_miss_latencies", "cache_hit_latencies"],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the NIXUS SQL evaluation harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--category",
        default="all",
        choices=VALID_CATEGORIES,
        help=(
            "Test category to run. One of: "
            + ", ".join(VALID_CATEGORIES)
            + ". 'all' (default) runs every test file."
        ),
    )
    parser.add_argument("-k", "--keyword", default=None, help="pytest -k expression to filter tests")
    parser.add_argument("--no-report", action="store_true", help="Skip BENCHMARK.md generation")
    parser.add_argument("--no-latency", action="store_true", help="Exclude latency tests (--ignore=eval/test_latency.py)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Pass -v to pytest")
    parser.add_argument("--results", default="eval/benchmark_results.json", help="Path for JSON report output")
    args = parser.parse_args()

    results_path = Path(args.results)
    results_path.parent.mkdir(parents=True, exist_ok=True)

    # Reset the metrics sidecar. A full run wipes everything; a per-category
    # run only clears the keys that category will repopulate so earlier
    # full-run metrics (e.g. latency percentiles) survive single-category
    # debugging passes.
    from eval.conftest import reset_metrics
    if args.category == "all":
        reset_metrics()
    else:
        reset_metrics(keys=CATEGORY_METRIC_KEYS.get(args.category, []))

    if args.category == "all":
        target = "eval/"
    else:
        target = CATEGORY_TO_PATH[args.category]

    cmd = [
        sys.executable, "-m", "pytest",
        target,
        "--json-report",
        f"--json-report-file={results_path}",
        "--timeout=300",
        "-p", "no:warnings",
    ]

    if args.verbose:
        cmd.append("-v")

    if args.keyword:
        cmd.extend(["-k", args.keyword])

    if args.no_latency and args.category == "all":
        cmd.extend(["--ignore=eval/test_latency.py"])

    print("Running:", " ".join(cmd))
    print()

    result = subprocess.run(cmd, check=False)

    if not args.no_report and results_path.exists():
        print()
        report_cmd = [sys.executable, "eval/report.py", "--input", str(results_path)]
        subprocess.run(report_cmd, check=False)

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

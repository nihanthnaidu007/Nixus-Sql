"""
RETIRED (6.2) — Chinook cache-accuracy gate. ARCHIVED, NOT the benchmark.

This module produced the chronically-borderline ``test_unrelated_miss_rate``
artifact. It is kept for history only and is NOT collected by pytest
(eval/conftest.py ``collect_ignore_glob``). The benchmark of record is
eval/run_saas_benchmark.py.

──────────────────────────────────────────────────────────────────────────────
Cache accuracy tests — Category 2 of the NIXUS SQL evaluation harness.

Tests two properties:
  1. Paraphrase hit rate: semantically identical questions should hit the cache
     after the first query has been answered.  Target: ≥ 60 % of paraphrase
     pairs produce a cache hit on the second request.

  2. Unrelated miss rate: semantically unrelated questions should NOT hit the
     cache for each other.  Target: ≥ 80 % of unrelated pairs produce a
     cache miss on the second request.

Important: these tests depend on cache state from prior runs.  Run the full
suite sequentially (the default) so earlier queries warm the cache.  A fresh
database with an empty cache will not have any hits; run at least one
correctness test first, or prime the cache by asking the same question twice
within a single run (the harness does this by design — each paraphrase pair
asks the original then the paraphrase).
"""

import uuid

from eval.conftest import record_metric, run_query

# (original question, semantically equivalent paraphrase)
PARAPHRASE_PAIRS = [
    (
        "How many tracks does each genre have?",
        "What is the track count per genre?",
    ),
    (
        "Show all customers from Brazil.",
        "List customers who are from Brazil.",
    ),
    (
        "What is the total revenue by billing country?",
        "Show me total sales grouped by country.",
    ),
    (
        "Which are the top 10 artists by number of albums?",
        "List the ten artists with the most albums.",
    ),
    (
        "List tracks longer than 5 minutes with their duration.",
        "Which songs are longer than five minutes?",
    ),
]

# (question_a, question_b) — these should NOT match each other in the cache
UNRELATED_PAIRS = [
    (
        "Show all customers from Brazil.",
        "What is the total revenue by billing country?",
    ),
    (
        "How many tracks does each genre have?",
        "List employees hired after January 1st 2003.",
    ),
    (
        "Which are the top 10 artists by number of albums?",
        "Show invoices with the customer first and last name.",
    ),
    (
        "Show each customer's total spending.",
        "Rank artists by number of tracks.",
    ),
    (
        "List tracks with their genre name and duration.",
        "Show each sales rep's invoice count and total revenue.",
    ),
]

MIN_PARAPHRASE_HIT_RATE = 0.60
MIN_UNRELATED_MISS_RATE = 0.80


def test_paraphrase_hit_rate(http_client):
    hits = 0
    for original, paraphrase in PARAPHRASE_PAIRS:
        # Warm the cache with the original question using a stable session
        run_query(http_client, original, session_id=str(uuid.uuid4()))

        # Ask the paraphrase — should hit the cache
        state = run_query(http_client, paraphrase, session_id=str(uuid.uuid4()))
        if state.get("served_from_cache"):
            hits += 1

    total = len(PARAPHRASE_PAIRS)
    rate = hits / total
    record_metric("paraphrase_hits", hits)
    record_metric("paraphrase_total", total)
    record_metric("paraphrase_hit_rate", rate)
    assert rate >= MIN_PARAPHRASE_HIT_RATE, (
        f"Paraphrase hit rate {rate:.1%} < {MIN_PARAPHRASE_HIT_RATE:.0%} "
        f"({hits}/{total} hits)"
    )


def test_unrelated_miss_rate(http_client):
    misses = 0
    for q_a, q_b in UNRELATED_PAIRS:
        # Ensure q_a is cached
        run_query(http_client, q_a, session_id=str(uuid.uuid4()))

        # q_b should NOT hit q_a's cache entry
        state = run_query(http_client, q_b, session_id=str(uuid.uuid4()))
        if not state.get("served_from_cache"):
            misses += 1

    total = len(UNRELATED_PAIRS)
    rate = misses / total
    record_metric("unrelated_misses", misses)
    record_metric("unrelated_total", total)
    record_metric("unrelated_miss_rate", rate)
    assert rate >= MIN_UNRELATED_MISS_RATE, (
        f"Unrelated miss rate {rate:.1%} < {MIN_UNRELATED_MISS_RATE:.0%} "
        f"({misses}/{total} misses)"
    )

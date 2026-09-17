"""Unit tests for eval/result_equivalence.py — the honest correctness comparison.

The crucial test is ``test_e02_regression_coincidental_overlap``: it proves the
new comparison REJECTS what E02's column-overlap logic wrongly ACCEPTED (same
text names, different numbers).
"""
from datetime import datetime
from decimal import Decimal

from eval.result_equivalence import results_equivalent


def test_identical_results_are_equivalent():
    gold = [("Acme", 12), ("Globex", 9)]
    gen = [("Acme", 12), ("Globex", 9)]
    assert results_equivalent(gen, gold, ordered=False).equivalent
    assert results_equivalent(gen, gold, ordered=True).equivalent


def test_same_rows_different_order():
    gold = [("Acme", 12), ("Globex", 9)]
    gen = [("Globex", 9), ("Acme", 12)]
    # order-insensitive: equivalent
    assert results_equivalent(gen, gold, ordered=False).equivalent
    # order-sensitive: NOT equivalent
    assert not results_equivalent(gen, gold, ordered=True).equivalent


def test_one_value_differs_is_not_equivalent():
    gold = [("Acme", 12), ("Globex", 9)]
    gen = [("Acme", 12), ("Globex", 8)]   # 9 -> 8
    r = results_equivalent(gen, gold, ordered=False)
    assert not r.equivalent
    assert r.first_mismatch is not None


def test_different_row_count_is_not_equivalent():
    gold = [("Acme", 12), ("Globex", 9)]
    gen = [("Acme", 12)]
    r = results_equivalent(gen, gold, ordered=False)
    assert not r.equivalent
    assert "row count" in r.reason


def test_extra_harmless_column_with_gold_values_present():
    # gold = name + count; generated also returns an id column it happened to
    # SELECT. Gold values are all present -> equivalent.
    gold = [("Acme", 12), ("Globex", 9)]
    gen = [(1, "Acme", 12), (2, "Globex", 9)]   # extra leading id column
    assert results_equivalent(gen, gold, ordered=False).equivalent


def test_e02_regression_coincidental_overlap():
    """E02's logic ignored numbers and matched on shared text tokens, so it
    called these equal. They are NOT: same org names, totally wrong counts.
    The value-based comparison must REJECT them."""
    gold = [("Acme", 12), ("Globex", 9)]
    gen = [("Acme", 999), ("Globex", 888)]    # names match, numbers are wrong
    r = results_equivalent(gen, gold, ordered=False)
    assert not r.equivalent, "coincidental text overlap must NOT be accepted (E02 regression)"


def test_dict_rows_from_api_normalize():
    # API returns list-of-dicts; gold returns tuples. Compare by value/position.
    gold = [("admin", 5), ("member", 40), ("viewer", 15)]
    gen = [{"role": "viewer", "n": 15}, {"role": "admin", "n": 5}, {"role": "member", "n": 40}]
    assert results_equivalent(gen, gold, ordered=False).equivalent


def test_decimal_vs_float_and_int_normalize():
    gold = [(Decimal("9750.00"),)]
    gen = [(9750,)]
    assert results_equivalent(gen, gold, ordered=False).equivalent
    gen2 = [(9750.0,)]
    assert results_equivalent(gen2, gold, ordered=False).equivalent


def test_average_rounds_to_match():
    gold = [("paid", Decimal("316.666667"))]
    gen = [("paid", 316.6666666667)]
    assert results_equivalent(gen, gold, ordered=False).equivalent


def test_timestamp_datetime_vs_iso_string():
    # gold returns datetime (date_trunc), API returns an ISO string.
    # Naive by contract: _parse_isoish keeps both sides naive (see result_equivalence).
    gold = [(datetime(2024, 1, 1), 31), (datetime(2024, 2, 1), 29)]  # noqa: DTZ001
    gen = [("2024-01-01T00:00:00", 31), ("2024-02-01 00:00:00", 29)]
    assert results_equivalent(gen, gold, ordered=True).equivalent


def test_none_values_compare():
    gold = [("u1@example.com", None), ("u2@example.com", None)]
    gen = [("u1@example.com", None), ("u2@example.com", None)]
    assert results_equivalent(gen, gold, ordered=False).equivalent
    gen_bad = [("u1@example.com", None), ("u2@example.com", "2024-01-01")]
    assert not results_equivalent(gen_bad, gold, ordered=False).equivalent


def test_fewer_generated_columns_than_gold():
    gold = [("Acme", 12, "US")]
    gen = [("Acme", 12)]   # missing a required column
    r = results_equivalent(gen, gold, ordered=False)
    assert not r.equivalent
    assert "fewer columns" in r.reason


def test_both_empty_is_equivalent():
    assert results_equivalent([], [], ordered=False).equivalent


def test_single_column_count():
    gold = [(12,)]
    gen = [{"count": 12}]
    assert results_equivalent(gen, gold, ordered=False).equivalent

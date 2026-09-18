"""Honest result-equivalence for the SaaS benchmark (6.2).

This REPLACES the broken column-overlap comparison that produced E02. The old
``result_overlap_rate`` (eval/conftest.py) judged correctness by fuzzy *text
token* overlap: it threw away every numeric and date value, matched rows by a
0.5 Jaccard-ish token ratio, ignored row counts, and passed at 70% one-way
overlap. Consequences:

  * two results with the SAME names but DIFFERENT numbers were called equal
    (the numbers were never compared),
  * a result with extra or missing rows could still pass (counts ignored),
  * coincidental token overlap counted as a match.

Result-equivalence here is value-based and conservative. Two results are
equivalent iff, after normalizing values to comparable forms:

  1. they have the SAME number of rows;
  2. there exists a SINGLE, consistent injective column mapping (gold column j ->
     some generated column c_j, the same mapping for every row) under which the
     generated result, PROJECTED onto those columns, equals the gold result
       - as an ordered SEQUENCE when ``ordered=True`` (the question fixes an
         order, e.g. "top 5 by X"), or
       - as an unordered MULTISET otherwise.

Columns are compared by POSITION (under the discovered mapping), never by name —
generated SQL may alias or reorder columns. Extra generated columns are tolerated
ONLY because the mapping must still place EVERY gold value, per row, consistently;
a coincidental partial overlap cannot satisfy a single all-columns mapping, so it
is rejected (the explicit E02 regression). Gold is the required set: if the
generated result has fewer columns than gold, it cannot match.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import permutations
from typing import Any

# Cap on generated-column arity we will search mappings over. Real SELECTs are
# narrow; this only bounds the permutation search so a pathologically wide result
# can't explode it. G (gold arity) is always tiny.
_MAX_GEN_COLS = 12


@dataclass
class EquivalenceResult:
    equivalent: bool
    reason: str = ""
    gold_row_count: int = 0
    gen_row_count: int = 0
    first_mismatch: dict | None = None


# ── value normalization ─────────────────────────────────────────────────────

def _norm_number(v: Any) -> Any:
    """Integral numbers -> int; fractional -> rounded float (tame FP/precision).

    Decimal('9750.00') and 9750 and 9750.0 all normalize to the int 9750; an
    average like 316.6667 normalizes to a 6-dp float so the gold (Decimal) and
    the API (JSON float) compare equal without spurious precision mismatches.
    """
    f = float(v)
    i = int(f)
    return i if f == i else round(f, 6)


def _parse_isoish(s: str) -> datetime | None:
    """Parse common Postgres/JSON timestamp spellings; None if not date-like.

    Handles '2024-01-01', '2024-01-01 00:00:00', '2024-01-01T00:00:00' (and a
    trailing 'Z') so a gold ``datetime`` and an API ISO string canonicalize to
    the same value.
    """
    t = s.strip()
    if not (len(t) >= 10 and t[4:5] == "-" and t[7:8] == "-"):
        return None
    cand = t.replace("T", " ").rstrip("Z").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            # Deliberately naive: _norm_value renders datetime OBJECTS (from the DB
            # driver) without tz too — one side aware and the other naive would
            # never compare equal, so both sides must stay naive.
            return datetime.strptime(cand, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _norm_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):           # bool BEFORE int (bool is an int subclass)
        return v
    if isinstance(v, (int, float, Decimal)):
        return _norm_number(v)
    if isinstance(v, (datetime, date)):
        dt = v if isinstance(v, datetime) else datetime(v.year, v.month, v.day)  # noqa: DTZ001 — naive by contract
        return "dt:" + dt.isoformat()
    s = str(v).strip()
    parsed = _parse_isoish(s)
    if parsed is not None:
        return "dt:" + parsed.isoformat()
    return s


def _row_values(row: Any) -> list:
    """Values of a row as a plain list, regardless of row type.

    Handles dicts (API JSON rows — value order is SELECT order), SQLAlchemy Row
    objects (gold rows), and plain tuples/lists.
    """
    if isinstance(row, dict):
        return list(row.values())
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return list(mapping.values())
    if isinstance(row, (list, tuple)):
        return list(row)
    return [row]


def _norm_rows(rows: Sequence[Any]) -> list[tuple]:
    return [tuple(_norm_value(x) for x in _row_values(r)) for r in rows]


def _sort_key(row: tuple) -> tuple:
    """Total order over heterogeneous normalized rows (int/str/None can't be
    compared directly in Py3), so multiset comparison can sort both sides."""
    out = []
    for v in row:
        if v is None:
            out.append((0, 0.0, ""))
        elif isinstance(v, bool):
            out.append((1, float(v), ""))
        elif isinstance(v, (int, float)):
            out.append((2, float(v), ""))
        else:
            out.append((3, 0.0, str(v)))
    return tuple(out)


def _matches(gold: list[tuple], proj: list[tuple], ordered: bool) -> bool:
    if ordered:
        return gold == proj
    return sorted(gold, key=_sort_key) == sorted(proj, key=_sort_key)


def results_equivalent(
    generated_rows: Sequence[Any],
    gold_rows: Sequence[Any],
    *,
    ordered: bool,
) -> EquivalenceResult:
    """Return whether ``generated_rows`` is result-equivalent to ``gold_rows``.

    See module docstring for the exact rule. ``ordered`` must be True only when
    the question fixes a row order (e.g. "top N by X").
    """
    gold = _norm_rows(gold_rows)
    gen = _norm_rows(generated_rows)

    if len(gold) != len(gen):
        return EquivalenceResult(
            equivalent=False,
            reason=f"row count differs: gold={len(gold)} generated={len(gen)}",
            gold_row_count=len(gold),
            gen_row_count=len(gen),
        )

    if not gold:  # both empty -> trivially equivalent (e.g. a valid empty answer)
        return EquivalenceResult(True, "both results empty", 0, 0)

    g = len(gold[0])
    if any(len(r) != g for r in gold):
        return EquivalenceResult(False, "gold rows are ragged (unequal arity)", len(gold), len(gen))

    n = len(gen[0])
    if any(len(r) != n for r in gen):
        return EquivalenceResult(False, "generated rows are ragged (unequal arity)", len(gold), len(gen))

    if n < g:
        return EquivalenceResult(
            equivalent=False,
            reason=f"generated has fewer columns ({n}) than gold requires ({g})",
            gold_row_count=len(gold),
            gen_row_count=len(gen),
        )
    if n > _MAX_GEN_COLS:
        return EquivalenceResult(
            equivalent=False,
            reason=f"generated arity {n} exceeds search cap {_MAX_GEN_COLS}",
            gold_row_count=len(gold),
            gen_row_count=len(gen),
        )

    # Try the identity mapping first (the common case), then every injective
    # mapping of the g gold columns into the n generated columns. Accept on the
    # first consistent mapping that makes all rows match.
    candidates = [tuple(range(g))] if n >= g else []
    candidates += [c for c in permutations(range(n), g) if c != tuple(range(g))]

    for combo in candidates:
        proj = [tuple(row[c] for c in combo) for row in gen]
        if _matches(gold, proj, ordered):
            return EquivalenceResult(
                equivalent=True,
                reason=f"matched under column map {combo} (ordered={ordered})",
                gold_row_count=len(gold),
                gen_row_count=len(gen),
            )

    # No mapping worked — surface a representative mismatch for debugging.
    sample_gold = min(gold, key=_sort_key) if not ordered else gold[0]
    sample_gen = min(gen, key=_sort_key) if not ordered else gen[0]
    return EquivalenceResult(
        equivalent=False,
        reason="no consistent column mapping makes the rows match",
        gold_row_count=len(gold),
        gen_row_count=len(gen),
        first_mismatch={"gold_row": sample_gold, "generated_row": sample_gen},
    )

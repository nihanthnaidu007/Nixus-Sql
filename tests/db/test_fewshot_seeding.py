"""Few-shot cold-start seeding tests (M3 usability).

The seeder fills ``fewshot_examples`` from the committed benchmark corpus.
These tests cover the DB-free core — corpus loading, table extraction, and the
seed loop's idempotency/failure accounting — with the store and the existing-
question read monkeypatched, so they run in the W1 CI profile (no local
Postgres, no live embedding provider).
"""
import nixus.db.fewshot_seeding as seeding
from nixus.db.fewshot_seeding import (
    SeedStats,
    _corpus_items,
    seed_fewshots_from_corpus,
    tables_in_sql,
)

# --- corpus loading: answerable pairs only ------------------------------------


def test_saas_corpus_loads_exactly_the_answerable_pairs():
    from eval.saas_gold import ANSWERABLE

    items = _corpus_items("saas")
    # Every answerable case becomes an exemplar — and nothing else: scope and
    # refusal cases must never leak into the few-shot store.
    assert {i["question"] for i in items} == {q["question"] for q in ANSWERABLE}


def test_chinook_corpus_loads_30_pairs():
    assert len(_corpus_items("chinook")) == 30


def test_unknown_source_raises():
    try:
        _corpus_items("postgres-tutorial")
    except ValueError as e:
        assert "unknown few-shot seed source" in str(e)
    else:
        raise AssertionError("unknown source must raise ValueError")


# --- table extraction (drives tables_used for retrieval filtering) ------------


def test_tables_in_sql_extracts_distinct_tables():
    sql = 'SELECT * FROM "Invoice" i JOIN "Customer" c ON i."CustomerId" = c."CustomerId"'
    assert tables_in_sql(sql) == ["Customer", "Invoice"]


def test_tables_in_sql_handles_ctes():
    sql = """WITH big AS (SELECT "Total" FROM "Invoice")
              SELECT count(*) FROM "Customer" c"""
    assert set(tables_in_sql(sql)) == {"Invoice", "Customer"}


def test_tables_in_sql_parse_failure_is_safe():
    assert tables_in_sql("THIS IS NOT SQL ;;;") == []


# --- the seed loop: idempotent, failure-tolerant, never raises -----------------

QLIST = [{"question": f"q{i}", "sql": f"SELECT {i} FROM t"} for i in range(4)]


def _async_returns(value):
    async def _fn():
        return value
    return _fn


class _StoreSpy:
    """Fake store_fewshot_example: records calls, scripted per-call behavior."""

    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior or {}

    async def __call__(self, natural_language, sql_query, tables_used, auto_learned):
        self.calls.append({
            "nl": natural_language, "sql": sql_query,
            "tables": tables_used, "auto": auto_learned,
        })
        return self.behavior.get(natural_language, True)


async def test_cold_start_stores_everything_with_extracted_tables(monkeypatch):
    monkeypatch.setattr(seeding, "_corpus_items", lambda source: QLIST)
    monkeypatch.setattr(seeding, "_existing_questions", _async_returns(set()))
    spy = _StoreSpy()
    monkeypatch.setattr(seeding, "store_fewshot_example", spy)

    stats = await seed_fewshots_from_corpus()

    assert stats == SeedStats(source="saas", corpus_size=4, stored=4,
                              skipped_existing=0, failed=0)
    assert all(c["auto"] is False for c in spy.calls)  # seeded, not auto-learned
    # sqlglot must have extracted the table name from each item's SQL.
    assert all(c["tables"] == ["t"] for c in spy.calls)


async def test_warm_start_skips_everything_without_store_calls(monkeypatch):
    monkeypatch.setattr(seeding, "_corpus_items", lambda source: QLIST)
    monkeypatch.setattr(seeding, "_existing_questions",
                        _async_returns({q["question"] for q in QLIST}))
    spy = _StoreSpy()
    monkeypatch.setattr(seeding, "store_fewshot_example", spy)

    stats = await seed_fewshots_from_corpus()

    assert stats.stored == 0 and stats.skipped_existing == 4
    assert spy.calls == []  # zero embedding calls on a warm start


async def test_store_near_duplicate_counts_as_skipped(monkeypatch):
    monkeypatch.setattr(seeding, "_corpus_items", lambda source: QLIST)
    # First two stored, next two rejected as near-duplicates.
    monkeypatch.setattr(seeding, "_existing_questions", _async_returns(set()))
    spy = _StoreSpy(behavior={"q2": False, "q3": False})
    monkeypatch.setattr(seeding, "store_fewshot_example", spy)

    stats = await seed_fewshots_from_corpus()

    assert stats.stored == 2 and stats.skipped_existing == 2 and stats.failed == 0


async def test_store_failures_are_counted_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(seeding, "_corpus_items", lambda source: QLIST)
    monkeypatch.setattr(seeding, "_existing_questions", _async_returns(set()))

    async def flaky(natural_language, sql_query, tables_used, auto_learned):
        if natural_language == "q1":
            raise ConnectionError("embedding provider down")
        return True

    monkeypatch.setattr(seeding, "store_fewshot_example", flaky)

    stats = await seed_fewshots_from_corpus()  # must NOT raise

    assert stats.stored == 3 and stats.failed == 1
    assert any("Few-shot seeding" in r.getMessage() for r in caplog.records)


async def test_existing_read_failure_degrades_to_attempt_all(monkeypatch):
    monkeypatch.setattr(seeding, "_corpus_items", lambda source: QLIST)

    async def broken_read():
        raise ConnectionError("state db down")

    monkeypatch.setattr(seeding, "_existing_questions", broken_read)
    spy = _StoreSpy()
    monkeypatch.setattr(seeding, "store_fewshot_example", spy)

    stats = await seed_fewshots_from_corpus()

    assert stats.stored == 4  # attempted everything; failures would be counted

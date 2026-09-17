"""Export service guardrails (Phase 2 W1 D1): the row cap, timeout mapping,
and pure serializers.

The executor is exercised against a fake engine so the cap arithmetic (fetch
LIMIT+1, trim, label) is real — no database.
"""
import json

import pytest
from sqlalchemy.exc import OperationalError

from nixus.graph.nodes import execute_query as eq
from nixus.services import export_service as es


class _FakeRow:
    """Mimics a SQLAlchemy Row: the executor reads ``r._mapping``."""

    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows

    def fetchmany(self, n):
        return [_FakeRow(r) for r in self._rows[:n]]

    def keys(self):
        return self._columns


class _FakeConn:
    """A connection whose execute() yields a scripted result."""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, stmt):
        # The statement itself isn't introspected — SQLAlchemy text() objects
        # here; the cap arithmetic happens in fetchmany below.
        if self._rows is None:
            raise OperationalError("stmt", {}, Exception("canceling statement due to statement timeout"))
        self._result = _FakeResult(self._rows["columns"], self._rows["rows"])
        return self._result


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConn(self._rows)


def _patch_engine(monkeypatch, rows):
    monkeypatch.setattr(es, "get_target_engine", lambda: _FakeEngine(rows))


def _db_rows(n, extra=0):
    return [{"id": i, "name": f"row {i}"} for i in range(1, n + 1 + extra)]


async def test_execute_guarded_caps_at_row_limit_and_labels(monkeypatch):
    monkeypatch.setattr(
        es, "get_target_engine",
        lambda: _FakeEngine({"columns": ["id"], "rows": _db_rows(eq.ROW_FETCH_LIMIT, extra=1)}),
    )
    result = await es.execute_guarded("SELECT id FROM t")
    assert result.capped is True
    assert result.row_count == eq.ROW_FETCH_LIMIT
    assert result.row_limit == eq.ROW_FETCH_LIMIT


async def test_execute_guarded_not_capped_at_or_below_limit(monkeypatch):
    monkeypatch.setattr(
        es, "get_target_engine",
        lambda: _FakeEngine({"columns": ["id"], "rows": _db_rows(5)}),
    )
    result = await es.execute_guarded("SELECT id FROM t")
    assert result.capped is False
    assert result.row_count == 5


async def test_execute_guarded_maps_timeout_to_a_readable_error(monkeypatch):
    # rows=None → the fake connection's execute() raises the timeout-shaped
    # OperationalError the way a real PostgreSQL driver does.
    monkeypatch.setattr(es, "get_target_engine", lambda: _FakeEngine(None))
    with pytest.raises(es.ExportQueryError) as exc:
        await es.execute_guarded("SELECT id FROM big_table")
    assert "timeout" in str(exc.value.message)


def test_csv_serializer_roundtrip():
    rows = [{"a": 1, "b": 'has,comma'}, {"a": 2, "b": 'has"quote'}]
    data = es.to_csv_bytes(["a", "b"], rows).decode("utf-8-sig")
    lines = data.splitlines()
    assert lines[0] == "a,b"
    assert lines[1] == '1,"has,comma"'
    assert lines[2] == '2,"has""quote"'


def test_json_serializer_carries_the_cap_label():
    data = json.loads(es.to_json_bytes(["a"], [{"a": 1}], 1000, True))
    assert data["capped"] is True
    assert data["row_limit"] == 1000
    assert data["row_count"] == 1


def test_xlsx_serializer_produces_a_workbook():
    data = es.to_xlsx_bytes(["a", "b"], [{"a": 1, "b": "x"}])
    assert data[:4] == b"PK\x03\x04"  # ZIP container
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb["export"]
    assert [c.value for c in ws[1]] == ["a", "b"]
    assert ws.cell(row=2, column=1).value == 1


def test_safe_filename_replaces_user_suffix_and_strips_hosts():
    # A user-supplied extension is replaced, never appended after.
    assert es.safe_filename("report.xlsx", "csv") == "report.csv"
    assert es.safe_filename("artists!final?.v2", "json") == "artists_final.json"
    # Path traversal collapses to nothing usable → timestamped fallback.
    assert es.safe_filename("../../etc/passwd", "json").startswith("nixus-export-")
    assert es.safe_filename(None, "xlsx").startswith("nixus-export-")
    assert es.safe_filename("", "csv").startswith("nixus-export-")

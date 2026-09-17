"""Drift-detection tests — structural comparison + W3 manifest staleness.

Report-don't-mutate is the contract under test: detect_drift REPORTS drift and
ADVISES a re-embed; it never re-ingests or re-embeds. The DB and introspection
seams are patched at module level (introspect_schema / list_schema_rows /
get_last_ingestion) — no database involved.
"""
from __future__ import annotations

import json

import pytest

import nixus.schema.drift as drift
from nixus.schema.drift import detect_drift
from nixus.schema.models import Column, IntrospectedSchema, Table


def _live(tables: dict[str, list[str]]) -> IntrospectedSchema:
    return IntrospectedSchema(tables=[
        Table(schema=(name.split(".")[0] if "." in name else "public"),
              name=name.split(".")[-1],
              columns=[Column(name=c, data_type="text", is_nullable=True) for c in cols])
        for name, cols in tables.items()
    ])


class _Rec:
    def __init__(self, content_hash):
        self.content_hash = content_hash


@pytest.fixture()
def seams(monkeypatch):
    """Patch the data seams + the ingestion record; return the shared state."""
    state = {"live": {}, "rows": [], "record": None}

    async def fake_introspect(engine):
        return _live(state["live"])

    async def fake_rows():
        return state["rows"]

    async def fake_last_ingestion(source):
        return state["record"]

    monkeypatch.setattr(drift, "introspect_schema", fake_introspect)
    monkeypatch.setattr(drift, "list_schema_rows", fake_rows)
    monkeypatch.setattr(drift, "get_last_ingestion", fake_last_ingestion)
    return state


def _row(table: str, columns: list[str]) -> dict:
    return {"table_name": table,
            "columns_json": json.dumps([{"name": c} for c in columns])}


@pytest.mark.asyncio
async def test_in_sync_when_structures_match(seams, monkeypatch):
    monkeypatch.setattr(drift.settings, "dbt_manifest_path", "")
    seams["live"] = {"plans": ["id", "tier"]}
    seams["rows"] = [_row("plans", ["id", "tier"])]
    report = await detect_drift(None, None)
    assert report.in_sync is True
    assert report.recommendation is None
    assert report.summary() == "schema in sync with embeddings"


@pytest.mark.asyncio
async def test_structural_drift_reported_not_mutated(seams, monkeypatch):
    monkeypatch.setattr(drift.settings, "dbt_manifest_path", "")
    # Live gained table new_t, dropped old_t; plans gained `price`, lost `old_col`.
    seams["live"] = {"plans": ["id", "tier", "price"], "new_t": ["id"]}
    seams["rows"] = [_row("plans", ["id", "tier", "old_col"]), _row("old_t", ["id"])]
    report = await detect_drift(None, None)
    assert report.in_sync is False
    assert report.added_tables == ["new_t"]
    assert report.removed_tables == ["old_t"]
    assert report.added_columns == ["plans.price"]
    assert report.removed_columns == ["plans.old_col"]
    assert report.recommendation and drift.REEMBED_COMMAND in report.recommendation


@pytest.mark.asyncio
async def test_manifest_changed_flags_drift_with_both_hashes(seams, monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"nodes": {}}')
    monkeypatch.setattr(drift.settings, "dbt_manifest_path", str(manifest))
    seams["live"] = {"plans": ["id"]}
    seams["rows"] = [_row("plans", ["id"])]
    seams["record"] = _Rec("ingested-hash-0")

    report = await detect_drift(None, None)
    assert report.in_sync is False
    assert report.dbt_manifest_source == str(manifest)
    assert report.dbt_manifest_changed is True
    assert report.dbt_manifest_current_hash not in (None, "ingested-hash-0")
    assert report.dbt_manifest_recorded_hash == "ingested-hash-0"
    assert "manifest changed" in report.summary()
    assert report.recommendation and "manifest" in report.recommendation


@pytest.mark.asyncio
async def test_manifest_unchanged_keeps_structural_verdict(seams, monkeypatch, tmp_path):
    from nixus.schema.dbt import manifest_fingerprint
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"nodes": {}}')
    monkeypatch.setattr(drift.settings, "dbt_manifest_path", str(manifest))
    seams["live"] = {"plans": ["id"]}
    seams["rows"] = [_row("plans", ["id"])]
    seams["record"] = _Rec(manifest_fingerprint(manifest.read_bytes()))

    report = await detect_drift(None, None)
    assert report.in_sync is True
    assert report.dbt_manifest_changed is False
    assert report.dbt_manifest_current_hash == report.dbt_manifest_recorded_hash


@pytest.mark.asyncio
async def test_unreadable_manifest_skips_dimension_without_crash(seams, monkeypatch):
    monkeypatch.setattr(drift.settings, "dbt_manifest_path", "/no/such/manifest.json")
    seams["live"] = {"plans": ["id"]}
    seams["rows"] = [_row("plans", ["id"])]
    report = await detect_drift(None, None)
    assert report.in_sync is True  # structural sync unaffected
    assert report.dbt_manifest_current_hash is None
    assert report.dbt_manifest_changed is False


@pytest.mark.asyncio
async def test_no_record_means_changed_manifest(seams, monkeypatch, tmp_path):
    """A configured manifest never ingested counts as drift (honest staleness)."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"nodes": {}}')
    monkeypatch.setattr(drift.settings, "dbt_manifest_path", str(manifest))
    seams["live"] = {"plans": ["id"]}
    seams["rows"] = [_row("plans", ["id"])]
    seams["record"] = None
    report = await detect_drift(None, None)
    assert report.dbt_manifest_changed is True
    assert report.dbt_manifest_recorded_hash is None

"""Embed-time W3 wiring tests — vocabulary merge, manifest precedence, provenance.

embed_target_schema takes injected SemanticSources and patches at the seams it
imports (introspect_schema / embed_texts / replace_schema_embeddings /
record_ingestion), so the enrichment pass is exercised with no DB, no API calls.
"""
from __future__ import annotations

import json

import pytest

import nixus.schema.embed as embed
from nixus.schema.dbt import parse_manifest
from nixus.schema.embed import SemanticSources, embed_target_schema
from nixus.schema.models import Column, IntrospectedSchema, Table
from nixus.semantic.registry import Metric


def _metric(name="mrr", desc="Monthly recurring revenue over active subscriptions.",
            questions=("What is our MRR?",),
            sql="SELECT sum(p.monthly_price) FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status = 'active';",
            tables=("subscriptions", "plans")) -> Metric:
    return Metric(name=name, description=desc, questions=questions, sql=sql, tables=tables)


def _schema() -> IntrospectedSchema:
    return IntrospectedSchema(tables=[
        Table(schema="public", name="plans", columns=[
            Column(name="id", data_type="integer", is_nullable=False, is_primary_key=True,
                   comment="catalog pk comment"),
            Column(name="tier", data_type="text", is_nullable=False, comment=None),
        ], comment="catalog table comment"),
        Table(schema="public", name="invoices", columns=[
            Column(name="id", data_type="integer", is_nullable=False, is_primary_key=True),
        ], comment=None),
    ])


@pytest.fixture()
def seams(monkeypatch):
    """Capture what embed_target_schema writes without any DB or API."""
    captured = {"rows": None, "embed_inputs": None, "ingestions": []}

    async def fake_introspect(engine):
        return _schema()

    async def fake_embed(texts):
        captured["embed_inputs"] = list(texts)
        return [f"emb-{i}" for i in range(len(texts))]

    async def fake_replace(rows):
        captured["rows"] = rows
        return len(rows)

    async def fake_record(source, fingerprint, stats=None):
        captured["ingestions"].append((source, fingerprint, stats))
        return True

    monkeypatch.setattr(embed, "introspect_schema", fake_introspect)
    monkeypatch.setattr(embed, "embed_texts", fake_embed)
    monkeypatch.setattr(embed, "replace_schema_embeddings", fake_replace)
    monkeypatch.setattr(embed, "record_ingestion", fake_record)
    return captured


@pytest.mark.asyncio
async def test_metric_vocabulary_appended_only_to_referenced_tables(seams):
    written = await embed_target_schema(None, None, semantic_sources=SemanticSources(metrics=[_metric()]))
    assert written == 2
    plans_block = next(r["description"] for r in seams["rows"] if r["table_name"] == "plans")
    invoices_block = next(r["description"] for r in seams["rows"] if r["table_name"] == "invoices")
    assert "mrr" in plans_block and "Monthly recurring revenue" in plans_block
    assert "mrr" not in invoices_block
    # The embedded TEXT is what carries the vocabulary.
    assert any("mrr" in t for t in seams["embed_inputs"])


@pytest.mark.asyncio
async def test_manifest_descriptions_override_catalog_with_provenance(seams):
    raw = {
        "metadata": {"dbt_version": "1.9.4",
                     "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v7.json"},
        "nodes": {
            "model.x.plans": {
                "resource_type": "model", "name": "plans",
                "unique_id": "model.x.plans",
                "relation_name": '"nixus"."public"."plans"',
                "description": "Pricing plans (dbt-managed description).",
                "columns": {"id": {"name": "id", "description": "Plan primary key."}},
            },
        },
    }
    manifest = parse_manifest(raw, max_chars=1500, fingerprint="deadbeef")
    sources = SemanticSources(dbt_manifest=manifest, dbt_source="target/manifest.json")
    await embed_target_schema(None, None, semantic_sources=sources)

    plans_row = next(r for r in seams["rows"] if r["table_name"] == "plans")
    assert "Pricing plans (dbt-managed description)." in plans_row["description"]
    assert "(description source: dbt manifest)" in plans_row["description"]
    # columns_json carries per-field provenance.
    cols = json.loads(plans_row["columns_json"])
    by_name = {c["name"]: c for c in cols}
    assert by_name["id"]["description"] == "Plan primary key."
    assert by_name["id"]["description_source"] == "manifest"
    assert by_name["tier"]["description_source"] == "catalog"
    # Ingestion fingerprint recorded with merge stats.
    assert len(seams["ingestions"]) == 1
    src, fp, stats = seams["ingestions"][0]
    assert src == "target/manifest.json" and fp == "deadbeef"
    assert stats["models_matched"] == 1


@pytest.mark.asyncio
async def test_no_manifest_means_no_ingestion_record(seams):
    await embed_target_schema(None, None, semantic_sources=SemanticSources(metrics=[_metric()]))
    assert seams["ingestions"] == []


@pytest.mark.asyncio
async def test_ingestion_bookkeeping_failure_does_not_fail_embed(seams, monkeypatch):
    raw = {
        "metadata": {},
        "nodes": {"model.x.plans": {
            "resource_type": "model", "name": "plans", "unique_id": "model.x.plans",
            "relation_name": '"nixus"."public"."plans"', "description": "d", "columns": {},
        }},
    }
    manifest = parse_manifest(raw, max_chars=1500, fingerprint="fp")
    sources = SemanticSources(dbt_manifest=manifest, dbt_source="target/manifest.json")

    async def broken_record(source, fingerprint, stats=None):
        raise RuntimeError("state db down")

    monkeypatch.setattr(embed, "record_ingestion", broken_record)
    written = await embed_target_schema(None, None, semantic_sources=sources)
    assert written == 2  # the embed itself completed


@pytest.mark.asyncio
async def test_empty_introspection_writes_nothing(seams, monkeypatch):
    async def empty_introspect(engine):
        return IntrospectedSchema(tables=[])

    monkeypatch.setattr(embed, "introspect_schema", empty_introspect)
    written = await embed_target_schema(None, None, semantic_sources=SemanticSources())
    assert written == 0
    assert seams["rows"] == []

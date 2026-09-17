"""dbt manifest connector tests — fixture built from a REAL dbt manifest v7.

The fixture mirrors the actual artifact dbt-core emits (verified against dbt's
own target/manifest.json during W3 pre-flight): a top-level ``nodes`` mapping;
model nodes carrying quoted ``relation_name`` values (``"db"."schema"."table"``)
and dictionary-valued ``columns`` with metadata; generic tests as SEPARATE test
nodes linked to models via ``attached_node`` + ``depends_on`` — NOT entries
inside the column dicts (the training-memory assumption the pre-flight
corrected).
"""
from __future__ import annotations

import json

from nixus.schema.dbt import (
    apply_manifest_to_tables,
    cap_description,
    load_manifest,
    manifest_fingerprint,
    normalize_relation,
    parse_manifest,
    resolve_manifest_models,
)
from nixus.schema.models import Column, Table


def _model_node(name: str, relation: str, description: str | None, columns: dict,
                checksum: str = "sha-sim") -> dict:
    """One resource in the shape dbt writes under nodes{} for a model."""
    node = {
        "resource_key": "model",
        "name": name,
        "unique_id": f"model.nixus_saas.{name}",
        "resource_type": "model",
        "checksum": {"name": "sha256", "checksum": checksum},
        "columns": columns,
        "depends_on": {"macros": [], "nodes": []},
        "config": {"materialized": "table"},
    }
    if description is not None:
        node["description"] = description
    if relation is not None:
        node["relation_name"] = relation
    return node


def _test_node(name: str, attached_model_unique_id: str,
               generic_name: str = "unique") -> dict:
    """A generic test — its OWN node, pointing back at the model.

    Real manifests: test_metadata.name IS the generic test ("unique",
    "not_null", ...); the node name is merely a unique label.
    """
    return {
        "resource_key": "test",
        "name": name,
        "unique_id": f"test.nixus_saas.{name}",
        "resource_type": "test",
        "test_metadata": {"name": generic_name},
        "attached_node": attached_model_unique_id,
        "depends_on": {"macros": [], "nodes": [attached_model_unique_id]},
        "column_name": None,
    }


def _manifest_bytes() -> bytes:
    """A realistic SaaS-domain manifest: two models, one unmatched, one test."""
    nodes = {
        "model.nixus_saas.plans": _model_node(
            "plans", '"nixus"."public"."plans"',
            "Subscription plans with pricing tiers.",
            {
                "id": {"name": "id", "description": "Surrogate key.", "data_type": "integer"},
                "tier": {"name": "tier", "description": "Plan tier: free | starter | pro | enterprise.",
                         "data_type": "text"},
            },
        ),
        "model.nixus_saas.subscriptions": _model_node(
            "subscriptions", '"nixus"."public"."subscriptions"',
            "One row per subscription lifecycle state.",
            {
                "status": {"name": "status", "description": "Lifecycle: active | canceled | past_due.",
                           "data_type": "text"},
            },
        ),
        "model.nixus_saas.ghost": _model_node(
            "ghost", '"nixus"."staging"."not_a_real_table"',
            "A model whose relation is not introspected.",
            {},
        ),
        "test.nixus_saas.plans_id_unique": _test_node(
            "plans_id_unique", "model.nixus_saas.plans",
        ),  # generic test name "unique"
    }
    manifest = {
        "metadata": {
            "dbt_version": "1.9.4",
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v7.json",
            "invocation_id": "3f2a...",
        },
        "nodes": nodes,
    }
    return json.dumps(manifest).encode()


# ── parsing ───────────────────────────────────────────────────────────────────

def test_parse_manifest_models_and_fingerprint():
    raw = _manifest_bytes()
    manifest = parse_manifest(json.loads(raw), max_chars=1500, fingerprint=manifest_fingerprint(raw))
    names = {m.name for m in manifest.models}
    assert names == {"plans", "subscriptions", "ghost"}
    assert len(manifest.fingerprint) == 64 and int(manifest.fingerprint, 16) >= 0
    assert manifest.dbt_version == "1.9.4"
    assert manifest.schema_version is not None and "v7" in manifest.schema_version


def test_relation_name_quoting_is_normalized():
    parsed = normalize_relation('"nixus"."public"."plans"')
    assert parsed == ("public", "plans")
    assert normalize_relation('"nixus"."sales"."facts"') == ("sales", "facts")
    assert normalize_relation("not-a-relation") is None
    assert normalize_relation("") is None


def test_generic_tests_aggregated_from_test_nodes():
    raw = _manifest_bytes()
    manifest = parse_manifest(json.loads(raw), max_chars=1500)
    plans = next(m for m in manifest.models if m.name == "plans")
    assert "unique" in plans.test_names
    # test nodes must NOT leak into the model list
    assert all(m.name != "plans_id_unique" for m in manifest.models)


def test_descriptions_length_capped():
    capped = cap_description("x" * 2000, 10)
    assert len(capped) == 10 and capped.endswith("...")  # cap includes the ellipsis
    assert cap_description(None, 10) is None
    assert cap_description("   ", 10) is None  # whitespace-only = absent
    assert cap_description(42, 10) is None  # non-string = absent, never a crash


def test_load_manifest_missing_file_is_none(tmp_path):
    assert load_manifest(str(tmp_path / "nope.json")) is None


def test_load_manifest_invalid_json_is_none(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text("{not json")
    assert load_manifest(str(bad)) is None


# ── resolution ────────────────────────────────────────────────────────────────

def _live_tables() -> list[Table]:
    return [
        Table(schema="public", name="plans", columns=[
            Column(name="id", data_type="integer", is_nullable=False, is_primary_key=True,
                   comment="catalog pk comment"),
            Column(name="tier", data_type="text", is_nullable=False, comment=None),
        ], comment="catalog table comment"),
        Table(schema="public", name="subscriptions", columns=[
            Column(name="status", data_type="text", is_nullable=False, comment=None),
        ], comment=None),
    ]


def test_resolution_matches_public_tables_and_reports_unmatched():
    manifest = parse_manifest(json.loads(_manifest_bytes()), max_chars=1500)
    matches, unmatched = resolve_manifest_models(manifest, {"plans", "subscriptions"})
    assert "plans" in matches and "subscriptions" in matches
    assert unmatched == ["model.nixus_saas.ghost"]  # logged by the resolver, never guessed


def test_merge_manifest_over_catalog_with_provenance():
    manifest = parse_manifest(json.loads(_manifest_bytes()), max_chars=1500)
    matches, _ = resolve_manifest_models(manifest, {"plans", "subscriptions"})
    tables = _live_tables()
    stats = apply_manifest_to_tables(tables, matches)

    plans = tables[0]
    # Non-empty manifest description wins over catalog COMMENT ON.
    assert plans.comment == "Subscription plans with pricing tiers."
    assert plans.columns[0].comment == "Surrogate key."  # manifest over catalog
    # Manifest column description applied where catalog had none.
    assert plans.columns[1].comment.startswith("Plan tier:")
    # Provenance records per-field source.
    prov = stats.provenance["plans"]
    assert prov["table"] == "manifest"
    assert prov["columns"]["id"] == "manifest"

    subscriptions = tables[1]
    assert subscriptions.comment == "One row per subscription lifecycle state."
    stats_prov = stats.provenance["subscriptions"]
    assert stats_prov["columns"]["status"] == "manifest"


def test_merge_empty_manifest_description_falls_through_to_catalog():
    """Precedence is non-empty-wins: an EMPTY manifest field keeps the catalog text."""
    raw = json.loads(_manifest_bytes())
    raw["nodes"]["model.nixus_saas.plans"]["description"] = ""
    raw["nodes"]["model.nixus_saas.plans"]["columns"]["id"]["description"] = ""
    manifest = parse_manifest(raw, max_chars=1500)
    matches, _ = resolve_manifest_models(manifest, {"plans"})
    tables = _live_tables()
    stats = apply_manifest_to_tables(tables, matches)

    plans = tables[0]
    assert plans.comment == "catalog table comment"
    assert plans.columns[0].comment == "catalog pk comment"
    prov = stats.provenance["plans"]
    assert prov["table"] == "catalog"
    assert prov["columns"]["id"] == "catalog"


def test_merge_counts_are_honest():
    manifest = parse_manifest(json.loads(_manifest_bytes()), max_chars=1500)
    matches, unmatched = resolve_manifest_models(manifest, {"plans", "subscriptions"})
    stats = apply_manifest_to_tables(_live_tables(), matches)
    assert stats.models_matched == 2
    assert stats.models_unmatched == 0  # unmatched live OUTSIDE matches, reported by resolver
    assert len(unmatched) == 1
    assert stats.table_descriptions_overridden == 2
    assert stats.column_descriptions_overridden == 3


def test_fingerprint_is_stable_and_content_sensitive():
    a = _manifest_bytes()
    b = _manifest_bytes().replace(b"3f2a...", b"other!")
    assert manifest_fingerprint(a) == manifest_fingerprint(a)  # stable
    assert manifest_fingerprint(a) != manifest_fingerprint(b)  # content-sensitive

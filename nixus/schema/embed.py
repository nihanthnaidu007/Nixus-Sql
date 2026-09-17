"""The introspection-based schema embedding pipeline.

``embed_target_schema`` runs the full chain for whatever ``target_engine`` points
at: introspect (2.2) -> render to text (render.py) -> embed (utils) -> write rows
into schema_embeddings on state_db (2.1 routing: read target, write state). It is
a FULL wipe-and-rebuild, transactional, and writes rows in the exact shape
``retrieve_schema`` already reads (table_name / description / columns_json /
sample_values_json), so retrieval consumes introspection rows identically to the
handwritten ones.

Per decision: NO sample values are captured — ``sample_values_json`` is left
null for introspection rows. This is structural metadata only.

W3 semantic sources (both FAIL-SOFT — a missing/invalid source means the
pipeline proceeds catalog-only, never a failed embed):
* dbt manifest (nixus/schema/dbt.py): model/column descriptions merge over
  catalog COMMENT ON (manifest wins when non-empty) BEFORE rendering, with
  per-field provenance recorded into columns_json as ``description_source``.
* curated metrics (nixus/semantic/): each metric's name + description is
  merged into the rendered description of every table it references.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from nixus.config import settings
from nixus.db.schema_store import replace_schema_embeddings
from nixus.db.semantic_ingestion_store import record_ingestion
from nixus.schema.dbt import (
    DbtManifest,
    apply_manifest_to_tables,
    load_manifest,
    resolve_manifest_models,
)
from nixus.schema.introspect import introspect_schema
from nixus.schema.models import Table
from nixus.schema.render import qualified_name, table_to_text
from nixus.semantic.enrichment import metric_vocabulary_by_table
from nixus.semantic.registry import Metric, load_metrics_file
from nixus.utils.embeddings import embed_texts

logger = logging.getLogger("nixus_sql.schema")

# text-embedding-3-small accepts ~8191 tokens. One rendered table block is tiny
# (even a 60-column table is a few thousand characters); embed_text already caps
# input length. If a single block ever approached the limit we would chunk by
# columns into multiple part-rows rather than truncate — not needed for any
# observed schema (Chinook tables are small), so no chunking is performed.


@dataclass
class SemanticSources:
    """Pre-loaded W3 enrichment inputs (dependency-injectable for tests)."""

    metrics: list[Metric] = field(default_factory=list)
    dbt_manifest: DbtManifest | None = None
    dbt_source: str | None = None


def load_semantic_sources() -> SemanticSources:
    """Load metrics YAML + dbt manifest from their configured paths. FAIL-SOFT:
    any problem with either source logs and degrades — never raises."""
    metrics_file = load_metrics_file(settings.semantic_metrics_path)
    if metrics_file.errors:
        logger.warning(
            "Metric registry loaded with errors from %s: %d valid metric(s), %d skipped",
            metrics_file.path, len(metrics_file.metrics), len(metrics_file.errors),
        )
    metrics = metrics_file.metrics

    dbt_manifest: DbtManifest | None = None
    dbt_source: str | None = None
    if settings.dbt_manifest_path:
        dbt_source = settings.dbt_manifest_path
        dbt_manifest = load_manifest(dbt_source, max_chars=settings.semantic_max_description_chars)
    return SemanticSources(metrics=metrics, dbt_manifest=dbt_manifest, dbt_source=dbt_source)


def _columns_payload(table: Table, provenance: dict | None = None) -> str:
    """Structural columns_json from the typed columns (mirrors the handwritten
    columns_json shape: a list of per-column dicts). ``provenance`` (from the
    dbt merge) adds a per-column ``description_source`` when known."""
    column_prov = (provenance or {}).get("columns", {})
    payload = []
    for c in table.columns:
        col: dict = {
            "name": c.name,
            "type": c.data_type,
            "nullable": c.is_nullable,
            "primary_key": c.is_primary_key,
            "description": c.comment,
        }
        if c.name in column_prov:
            col["description_source"] = column_prov[c.name]
        if c.enum_values:
            col["enum_values"] = c.enum_values
        payload.append(col)
    return json.dumps(payload)


async def embed_target_schema(
    target_engine: AsyncEngine,
    state_engine: AsyncEngine,
    *,
    semantic_sources: SemanticSources | None = None,
) -> int:
    """Introspect target_db, render+embed each table, and rebuild schema_embeddings
    on state_db. Returns the number of embedding rows written (one per table).

    ``state_engine`` is accepted for symmetry/explicitness; the actual write goes
    through the state-bound store (schema_store), which already targets state_db.

    ``semantic_sources`` defaults to loading from the configured paths (fail-soft);
    tests inject a SemanticSources to bypass file I/O.
    """
    sources = semantic_sources if semantic_sources is not None else load_semantic_sources()
    schema = await introspect_schema(target_engine)

    # ── W3: dbt manifest merge (manifest wins over catalog COMMENT ON) ──────
    merge_stats = None
    provenance: dict[str, dict] = {}
    if sources.dbt_manifest is not None:
        live_names = {qualified_name(t) for t in schema.tables}
        matches, _unmatched = resolve_manifest_models(sources.dbt_manifest, live_names)
        merge_stats = apply_manifest_to_tables(schema.tables, matches)
        provenance = merge_stats.provenance
        logger.info(
            "dbt merge: %d model(s) matched, %d table + %d column description(s) overridden "
            "(%d unmatched logged, never guessed)",
            merge_stats.models_matched,
            merge_stats.table_descriptions_overridden,
            merge_stats.column_descriptions_overridden,
            merge_stats.models_unmatched,
        )

    # ── W3: metric vocabulary per referenced table ──────────────────────────
    vocab = metric_vocabulary_by_table(sources.metrics)

    descriptions: list[str] = []
    rows: list[dict] = []
    for table in schema.tables:
        text_block = table_to_text(table, schema.foreign_keys)
        key = qualified_name(table).lower()
        if key in vocab:
            text_block += "\n" + vocab[key]
        if provenance.get(key, {}).get("table") == "manifest":
            text_block += "\n(description source: dbt manifest)"
        descriptions.append(text_block)
        rows.append({
            "table_name": qualified_name(table),
            "description": text_block,
            "columns_json": _columns_payload(table, provenance.get(key)),
            "sample_values_json": None,   # no sample values captured (by decision)
        })

    if not rows:
        logger.warning("Introspection found no tables in target_db; nothing to embed.")
        await replace_schema_embeddings([])
        return 0

    embeddings = await embed_texts(descriptions)
    for row, emb in zip(rows, embeddings):
        row["embedding"] = emb

    written = await replace_schema_embeddings(rows)
    logger.info(
        "Embedded target schema via introspection: %d tables -> %d rows.",
        len(schema.tables), written,
    )

    # ── W3: record the manifest ingestion fingerprint (best-effort) ─────────
    # Idempotent: an unchanged hash records as a no-op. A bookkeeping failure
    # is logged and never fails the embed (fail-soft doctrine).
    if sources.dbt_manifest is not None and sources.dbt_source and merge_stats is not None:
        try:
            changed = await record_ingestion(
                sources.dbt_source,
                sources.dbt_manifest.fingerprint,
                stats={
                    "models_matched": merge_stats.models_matched,
                    "models_unmatched": merge_stats.models_unmatched,
                    "table_descriptions_overridden": merge_stats.table_descriptions_overridden,
                    "column_descriptions_overridden": merge_stats.column_descriptions_overridden,
                },
            )
            if not changed:
                logger.info("dbt manifest unchanged since last ingest (hash match) — no-op.")
        except Exception as e:
            logger.warning("dbt ingestion bookkeeping failed (embed continues): %s", e)

    return written

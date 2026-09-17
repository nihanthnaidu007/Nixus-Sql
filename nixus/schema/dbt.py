"""dbt manifest.json connector — model/column descriptions as grounding metadata.

Parses a real dbt `target/manifest.json`. The fixture in tests is built from
the REAL artifact shape (manifest schema v7, verified against dbt-core's
published output this session): top-level ``nodes`` keyed by unique_id; model
nodes carry ``resource_type: "model"``, a quoted ``relation_name``
(``"db"."schema"."table"``), a ``description``, and ``columns`` — a dict keyed
by column name whose entries carry ``description``. Generic tests are SEPARATE
nodes (``resource_type: "test"`` with ``test_metadata.name``, ``column_name``,
and ``depends_on.nodes``/``attached_node`` pointing at the model) — they are
counted per model via depends_on, NOT read off column entries.

Merge policy (locked decision): the manifest OWNS descriptions. At embed time
a non-empty manifest description (model or column) overrides the catalog
COMMENT ON value; an empty/absent one falls through to the catalog. Provenance
is recorded per field so the API can show where a description came from.

Ingestion fingerprint: the manifest's SHA-256 is stored (state DB,
``semantic_ingestions``) after a successful embed-time merge; re-ingesting an
unchanged manifest is a logged no-op, and drift.py reports staleness by
comparing hashes (report-don't-mutate).

Fail-soft: any problem with the manifest (missing file, bad JSON, wrong
shape) is logged and the embed pipeline proceeds catalog-only. Unmatched
models (relation_name not among introspected tables) are logged and NEVER
guessed.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from nixus.schema.models import Table
from nixus.schema.render import qualified_name as render_qualified_name

logger = logging.getLogger("nixus_sql.schema.dbt")


class ManifestError(ValueError):
    """The manifest exists but is not a dbt model manifest we can parse."""


@dataclass(frozen=True)
class DbtModelInfo:
    unique_id: str
    name: str
    relation_name: str | None
    description: str | None            # length-capped; None = absent/empty
    column_descriptions: dict[str, str]  # length-capped; empty when none
    test_names: tuple[str, ...]        # generic-test names attached to this model


@dataclass
class DbtManifest:
    models: list[DbtModelInfo]
    fingerprint: str
    dbt_version: str | None = None
    schema_version: str | None = None


@dataclass
class DbtMergeStats:
    """What one manifest merge actually changed (per embed pass)."""

    models_matched: int = 0
    models_unmatched: int = 0
    unmatched_names: list[str] = field(default_factory=list)
    table_descriptions_overridden: int = 0
    column_descriptions_overridden: int = 0
    # qualified table name → {"table": "manifest"|"catalog", "columns": {col: source}}
    provenance: dict[str, dict] = field(default_factory=dict)


def cap_description(value: object, max_chars: int) -> str | None:
    """None/empty → None; otherwise whitespace-collapsed and length-capped."""
    if not isinstance(value, str) or not value.strip():
        return None
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def manifest_fingerprint(raw_bytes: bytes) -> str:
    """SHA-256 of the raw manifest bytes — the ingestion idempotency key."""
    return hashlib.sha256(raw_bytes).hexdigest()


def normalize_relation(relation_name: str) -> tuple[str, str] | None:
    """``"db"."schema"."table"`` (quoted or not) → (schema, table).

    Returns None for anything that is not at least schema-qualified — a bare
    name would force us to GUESS the schema, and unmatched models are logged,
    never guessed.
    """
    if not isinstance(relation_name, str) or not relation_name.strip():
        return None
    parts = [p.strip().strip('"').strip("`").strip("'") for p in relation_name.split(".")]
    if len(parts) < 2 or not all(parts):
        return None
    schema, table = parts[-2], parts[-1]
    if '"' in schema or '"' in table or not schema or not table:
        return None
    return schema, table


def parse_manifest(raw: object, max_chars: int, fingerprint: str = "") -> DbtManifest:
    """Validate a parsed manifest document → DbtManifest. Raises ManifestError
    on wrong shape (callers convert to fail-soft logging)."""
    if not isinstance(raw, dict):
        raise ManifestError(f"top level must be a mapping, got {type(raw).__name__}")
    nodes = raw.get("nodes")
    if not isinstance(nodes, dict):
        raise ManifestError("'nodes' must be a mapping (is this a dbt manifest?)")

    metadata = raw.get("metadata")
    dbt_version = None
    if isinstance(metadata, dict) and isinstance(metadata.get("dbt_version"), str):
        dbt_version = metadata["dbt_version"]
    # Real manifests carry the schema version at metadata.dbt_schema_version
    # (a URL like https://schemas.getdbt.com/dbt/manifest/v7.json); a bare
    # top-level "schema_version" string is accepted too.
    schema_version = None
    if isinstance(metadata, dict) and isinstance(metadata.get("dbt_schema_version"), str):
        schema_version = metadata["dbt_schema_version"] or None
    if schema_version is None and isinstance(raw.get("schema_version"), str):
        schema_version = raw["schema_version"] or None

    # Pass 1: models. Pass 2: generic tests grouped by the model they depend on.
    models: list[DbtModelInfo] = []
    tests_by_model: dict[str, list[str]] = {}
    for unique_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("resource_type") != "test":
            continue
        meta = node.get("test_metadata")
        if not isinstance(meta, dict):
            continue  # singular tests have no test_metadata — not per-column facts
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            continue
        depends = node.get("depends_on", {}).get("nodes", [])
        attached = [node.get("attached_node")] if node.get("attached_node") else []
        for model_id in {*depends, *attached}:
            if isinstance(model_id, str):
                tests_by_model.setdefault(model_id, []).append(name)

    for unique_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("resource_type") != "model":
            continue
        columns_raw = node.get("columns")
        column_descriptions: dict[str, str] = {}
        if isinstance(columns_raw, dict):
            for col_name, col in columns_raw.items():
                desc = cap_description(col.get("description") if isinstance(col, dict) else None, max_chars)
                if desc:
                    column_descriptions[str(col_name)] = desc
        models.append(
            DbtModelInfo(
                unique_id=str(unique_id),
                name=str(node.get("name") or unique_id),
                relation_name=node.get("relation_name") if isinstance(node.get("relation_name"), str) else None,
                description=cap_description(node.get("description"), max_chars),
                column_descriptions=column_descriptions,
                test_names=tuple(dict.fromkeys(tests_by_model.get(str(unique_id), []))),
            )
        )

    if not models:
        raise ManifestError("no model nodes found in manifest")

    return DbtManifest(
        models=models,
        fingerprint=fingerprint,
        dbt_version=dbt_version,
        schema_version=schema_version,
    )


def load_manifest(path: str | Path, max_chars: int = 1500) -> DbtManifest | None:
    """Load + parse a manifest file. FAIL-SOFT: any problem is logged and
    returns None — the embed pipeline proceeds catalog-only."""
    source = str(path)
    try:
        raw_bytes = Path(path).read_bytes()
    except FileNotFoundError:
        logger.warning("dbt manifest not found: %s (continuing catalog-only)", source)
        return None
    except OSError as e:
        logger.warning("dbt manifest unreadable: %s (%s)", source, e)
        return None

    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        logger.warning("dbt manifest is not valid JSON: %s (%s)", source, e)
        return None

    try:
        manifest = parse_manifest(raw, max_chars=max_chars, fingerprint=manifest_fingerprint(raw_bytes))
    except ManifestError as e:
        logger.warning("dbt manifest rejected: %s (%s)", source, e)
        return None

    logger.info(
        "dbt manifest loaded: %d model(s) from %s (dbt %s, schema %s)",
        len(manifest.models), source, manifest.dbt_version, manifest.schema_version,
    )
    return manifest


def resolve_manifest_models(
    manifest: DbtManifest, live_qualified_names: set[str]
) -> tuple[dict[str, DbtModelInfo], list[str]]:
    """Match manifest models to introspected tables.

    Matching: normalize relation_name → (schema, table); a model matches when
    either ``schema.table`` is a known qualified name OR (schema == "public")
    the bare ``table`` is. Everything else is returned as unmatched — logged,
    never guessed.
    """
    live_lower = {q.lower() for q in live_qualified_names}
    matches: dict[str, DbtModelInfo] = {}
    unmatched: list[str] = []

    for model in manifest.models:
        resolved: tuple[str, str] | None = None
        if model.relation_name:
            parsed = normalize_relation(model.relation_name)
            if parsed:
                schema, table = parsed
                qualified = f"{schema}.{table}".lower()
                if qualified in live_lower or (schema.lower() == "public" and table.lower() in live_lower):
                    resolved = (schema, table)
        if resolved:
            schema, table = resolved
            qualified = f"{schema}.{table}".lower()
            matches[qualified] = model
            # Introspected qualified_name is BARE for public-schema tables
            # ("plans", not "public.plans") — register both key forms so the
            # merge lookup matches either identity.
            if schema.lower() == "public":
                matches[table.lower()] = model
        else:
            unmatched.append(model.unique_id)
            logger.warning(
                "dbt model %s: relation %r not found among introspected tables (skipped, never guessed)",
                model.unique_id, model.relation_name,
            )
    return matches, unmatched


def apply_manifest_to_tables(tables: list[Table], matches: dict[str, DbtModelInfo]) -> DbtMergeStats:
    """Apply manifest descriptions over catalog comments IN PLACE (the Table
    models are re-rendered right after this), recording per-field provenance.

    Precedence (locked): a NON-EMPTY manifest description wins; an empty or
    absent one falls through to the catalog COMMENT ON.
    """
    stats = DbtMergeStats()
    # Distinct MODELS, not alias keys — public-schema models are registered
    # under both qualified and bare keys, and len(matches) double-counts them.
    stats.models_matched = len({m.unique_id for m in matches.values()})

    for table in tables:
        # Key by the STORE identity (render.qualified_name — bare for public-
        # schema tables), not Table.qualified_name ("schema.name"): embed.py
        # and schema_embeddings look rows up with the render convention.
        model = matches.get(render_qualified_name(table).lower())
        if not model:
            continue
        table_prov: dict = {"table": "catalog", "columns": {}}

        if model.description:
            table.comment = model.description
            table_prov["table"] = "manifest"
            stats.table_descriptions_overridden += 1

        for column in table.columns:
            manifest_desc = model.column_descriptions.get(column.name)
            if manifest_desc:
                column.comment = manifest_desc
                column_prov = "manifest"
                stats.column_descriptions_overridden += 1
            else:
                column_prov = "catalog"
            table_prov["columns"][column.name] = column_prov

        stats.provenance[render_qualified_name(table).lower()] = table_prov

    return stats

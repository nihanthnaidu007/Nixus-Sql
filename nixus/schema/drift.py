"""Schema drift detection: does the live target still match what is embedded?

``detect_drift`` introspects target_db live and compares its tables+columns
against the structure currently stored in schema_embeddings — a cheap, fast,
structural comparison (table and column NAMES), never a re-embed. It REPORTS and
ADVISES; it never auto-reembeds (embeddings cost API calls — that is the user's
call) and never crashes the app.

Since introspection is the only schema source, the embedded structure came from
the same introspection path, so the comparison is always authoritative: a drift
means the target has genuinely changed since the last re-embed.

W3 adds a second, hash-based dimension: when a dbt manifest is configured, the
manifest's current SHA-256 is compared against the last INGESTED fingerprint
(``semantic_ingestions``). A changed manifest means the embedded descriptions
are stale relative to the manifest (manifest owns descriptions — merge happens
at embed time), so the report flags it and advises the same re-embed. Still
report-don't-mutate: nothing here re-ingests or re-embeds.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from nixus.config import settings
from nixus.db.schema_store import list_schema_rows
from nixus.db.semantic_ingestion_store import get_last_ingestion
from nixus.schema.dbt import manifest_fingerprint
from nixus.schema.introspect import introspect_schema
from nixus.schema.render import qualified_name

logger = logging.getLogger("nixus_sql.schema")

REEMBED_COMMAND = "python -m nixus.schema.reembed"


class DriftReport(BaseModel):
    in_sync: bool
    added_tables: list[str] = Field(default_factory=list)     # live has, store lacks
    removed_tables: list[str] = Field(default_factory=list)   # store has, live lacks
    added_columns: list[str] = Field(default_factory=list)    # "table.column" live has, store lacks
    removed_columns: list[str] = Field(default_factory=list)  # "table.column" store has, live lacks
    recommendation: str | None = None
    # ── W3: dbt manifest staleness (None source = no manifest configured) ───
    dbt_manifest_source: str | None = None
    dbt_manifest_changed: bool = False          # current hash != last ingested (or never ingested)
    dbt_manifest_current_hash: str | None = None
    dbt_manifest_recorded_hash: str | None = None

    def summary(self) -> str:
        if self.in_sync:
            return "schema in sync with embeddings"
        bits = []
        if self.added_tables:
            bits.append(f"+tables {self.added_tables}")
        if self.removed_tables:
            bits.append(f"-tables {self.removed_tables}")
        if self.added_columns:
            bits.append(f"+columns {self.added_columns}")
        if self.removed_columns:
            bits.append(f"-columns {self.removed_columns}")
        if self.dbt_manifest_changed:
            bits.append("dbt manifest changed since last ingest")
        return "; ".join(bits)


def _embedded_columns(columns_json: str) -> set[str]:
    try:
        cols = json.loads(columns_json) or []
    except Exception:
        return set()
    return {c.get("name") for c in cols if isinstance(c, dict) and c.get("name")}


async def _manifest_drift() -> tuple[str | None, str | None]:
    """(current_hash, recorded_hash) for the configured manifest, or (None, None).

    Fail-soft: an unreadable manifest or an unavailable ingestion record logs
    and reports no manifest comparison (the embed-time loader logs why).
    """
    if not settings.dbt_manifest_path:
        return None, None
    try:
        raw_bytes = Path(settings.dbt_manifest_path).read_bytes()
    except OSError as e:
        logger.warning("dbt drift check: manifest unreadable (%s); skipping manifest dimension.", e)
        return None, None
    current = manifest_fingerprint(raw_bytes)
    try:
        record = await get_last_ingestion(settings.dbt_manifest_path)
    except Exception as e:
        logger.warning("dbt drift check: ingestion records unavailable (%s); skipping comparison.", e)
        return current, None
    return current, (record.content_hash if record else None)


async def detect_drift(target_engine: AsyncEngine, state_engine: AsyncEngine) -> DriftReport:
    """Compare the live target structure against the embedded structure."""
    live_schema = await introspect_schema(target_engine)
    live = {qualified_name(t): {c.name for c in t.columns} for t in live_schema.tables}

    embedded_rows = await list_schema_rows()
    embedded = {r["table_name"]: _embedded_columns(r["columns_json"]) for r in embedded_rows}

    live_names, embedded_names = set(live), set(embedded)
    added_tables = sorted(live_names - embedded_names)
    removed_tables = sorted(embedded_names - live_names)

    added_columns: list[str] = []
    removed_columns: list[str] = []
    for tname in sorted(live_names & embedded_names):
        for col in sorted(live[tname] - embedded[tname]):
            added_columns.append(f"{tname}.{col}")
        for col in sorted(embedded[tname] - live[tname]):
            removed_columns.append(f"{tname}.{col}")

    in_sync = not (added_tables or removed_tables or added_columns or removed_columns)

    # ── W3: dbt manifest staleness (report-don't-mutate) ────────────────────
    manifest_source = settings.dbt_manifest_path
    manifest_changed = False
    current_hash = recorded_hash = None
    if manifest_source:
        current_hash, recorded_hash = await _manifest_drift()
        manifest_changed = current_hash is not None and current_hash != recorded_hash
        if manifest_changed:
            in_sync = False  # embedded descriptions are stale relative to the manifest

    recommendation = None
    if not in_sync:
        recommendation = f"Run `{REEMBED_COMMAND}` to rebuild schema_embeddings."
    if manifest_changed:
        recommendation = (
            f"{recommendation} "
            f"dbt manifest ({manifest_source}) changed since last ingest — the re-embed "
            "re-applies manifest descriptions."
        )

    return DriftReport(
        in_sync=in_sync,
        added_tables=added_tables,
        removed_tables=removed_tables,
        added_columns=added_columns,
        removed_columns=removed_columns,
        recommendation=recommendation,
        dbt_manifest_source=manifest_source,
        dbt_manifest_changed=manifest_changed,
        dbt_manifest_current_hash=current_hash,
        dbt_manifest_recorded_hash=recorded_hash,
    )


async def log_drift_at_startup(target_engine: AsyncEngine, state_engine: AsyncEngine) -> None:
    """Advisory, NON-FATAL startup check. Logs a warning if drift is detected;
    never raises (a broken/unreachable target must not crash the app)."""
    try:
        report = await detect_drift(target_engine, state_engine)
    except Exception:
        logger.exception("Schema drift check failed (non-fatal); continuing.")
        return

    if report.in_sync:
        logger.info("Schema drift check: target in sync with embeddings.")
        return

    logger.warning(
        "Schema drift detected: %s. %s",
        report.summary(), report.recommendation or "",
    )

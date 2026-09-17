-- 0004 — semantic-source ingestion bookkeeping (Phase 2, Wave 3).
--
-- APPLICATION state in the STATE database — same scope as saved_queries /
-- query_history (0003); the TARGET database stays read-only and untouched.
--
-- semantic_ingestions : one row per semantic source ingested at embed time
--                       (currently: a dbt manifest file path). Stores the
--                       SHA-256 of the source content so re-ingestion of an
--                       unchanged source is a no-op (idempotent seeding) and
--                       drift reporting can flag staleness by hash comparison
--                       (report-don't-mutate — drift never re-ingests).

CREATE TABLE IF NOT EXISTS semantic_ingestions (
    source       TEXT PRIMARY KEY,
    source_kind  TEXT NOT NULL DEFAULT 'dbt_manifest',
    content_hash TEXT NOT NULL,
    stats_json   TEXT,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

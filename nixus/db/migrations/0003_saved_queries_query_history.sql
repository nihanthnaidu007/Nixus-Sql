-- 0003 — saved queries + per-session query history (Phase 2, Wave 1).
--
-- Both tables are APPLICATION state in the STATE database (NIXUS-owned,
-- read-write) — the same scope as api_sessions (0002). The TARGET database
-- (the user's data) stays read-only and is never touched by these tables.
--
-- saved_queries : named, tagged NL question + generated SQL pairs the user
--                 keeps; re-runs go through the full pipeline, and accepted
--                 runs feed the few-shot corpus (fewshot_examples, 0001).
-- query_history : one row per executed query, per session — what was asked,
--                 what SQL ran, how it ended, how long it took, how many rows.

CREATE TABLE IF NOT EXISTS saved_queries (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    tags TEXT[] NOT NULL DEFAULT '{}',
    natural_language TEXT NOT NULL,
    generated_sql TEXT NOT NULL,
    parameters_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS query_history (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    generated_sql TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    duration_ms FLOAT NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- History reads paginate newest-first, optionally filtered by session and/or
-- status — both filters are prefix-friendly for these indexes.
CREATE INDEX IF NOT EXISTS query_history_session_created_idx
    ON query_history (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS query_history_status_idx
    ON query_history (status);

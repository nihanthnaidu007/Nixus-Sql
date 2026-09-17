-- Server-issued session registry (Wave 0 security blocker).
--
-- The API session id doubles as the LangGraph checkpoint thread_id, so client-
-- supplied ids used to select ANY checkpoint thread. The API now issues ids on
-- first use and validates them against this table (404 otherwise). Only the
-- server INSERTs here, so a row in api_sessions is exactly a session the
-- server issued — an unforgeable allowlist for checkpoint-thread access.
CREATE TABLE IF NOT EXISTS api_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 0005 — few-shot provenance, run→few-shot linkage, and feedback state
--        (Phase 3, Wave 1 D1).
--
-- APPLICATION state in the STATE database — same scope as 0001–0004; the
-- TARGET database stays read-only and untouched.
--
-- fewshot_examples gains two columns:
--   source   : provenance derived from the pre-existing auto_learned flag —
--              'auto' (learned from a clean run) vs 'seed' (curated corpus).
--              Backfilled for existing rows (auto_learned → 'auto'); finer
--              origins for existing rows are unknowable retroactively.
--   disabled : tombstone for explicitly REJECTED examples. A rejected row is
--              never deleted (audit trail); retrieval and duplicate checks
--              must both filter it — see fewshot_store.search_fewshots /
--              _is_duplicate — so rejected SQL is never re-served AND a
--              rejected near-duplicate cannot block re-learning the fix.
--
-- query_history gains the feedback surface:
--   fewshot_example_id : the corpus row THIS run created (the graph's
--                        auto-learn writer stamps it into the run state at
--                        explain_result; record_history_safely persists it).
--                        Null for runs that learned nothing — without this
--                        link a rejection cannot find what to demote.
--   feedback_verdict   : explicit human verdict on the run: 'accept' |
--                        'reject'. Null = not yet reviewed.
--   feedback_note      : optional operator note carried with the verdict.
--   feedback_at        : when the verdict was recorded.

ALTER TABLE fewshot_examples
    ADD COLUMN source TEXT NOT NULL DEFAULT 'seed',
    ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE fewshot_examples
   SET source = CASE WHEN auto_learned THEN 'auto' ELSE 'seed' END;

ALTER TABLE query_history
    ADD COLUMN fewshot_example_id INTEGER REFERENCES fewshot_examples(id),
    ADD COLUMN feedback_verdict TEXT CHECK (feedback_verdict IN ('accept', 'reject')),
    ADD COLUMN feedback_note TEXT,
    ADD COLUMN feedback_at TIMESTAMPTZ;

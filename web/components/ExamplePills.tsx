"use client";

/**
 * Example-question pills (Phase 13, B12; W2 N1 made them target-aware).
 * A restrained row of starter questions under the input. Clicking a pill FILLS
 * the input — it does not auto-submit, so the user stays in control and can
 * tweak before running. (One-click-RUN starters live in StarterQuestions.)
 *
 * W2 N1 — the set follows the ACTIVE TARGET (guardrails manifest):
 *   · saas sample        → the SaaS examples (organizations, plans, invoices…)
 *   · chinook sample     → the Chinook-media examples (artists, tracks, invoices)
 *   · unknown / BYO      → schema-generic questions that work on any relational
 *                          database (the old behavior — SaaS pills on a BYO
 *                          target — pointed at tables that do not exist)
 * While the manifest is loading, nothing renders (no wrong-schema flash);
 * if it is unavailable, the generic set is the safe fallback.
 *
 * Styled in the warm-paper "Engineering Ledger" language so the row sits
 * quietly under the input rather than dominating it.
 */

import { targetGroup, useGuardrails } from "@/lib/useGuardrails";

const SAAS_EXAMPLES = [
  "How many active subscriptions are there?",
  "How many users does each organization have?",
  "Which plan has the most subscriptions?",
  "Show total payment revenue by month",
  "List the 10 organizations with the highest invoice totals",
  "Break down usage events by event type",
];

// Verified against eval/archive_chinook/gold_queries.py — every pill is a
// corpus question, so it is known-answerable on the Chinook sample.
const CHINOOK_EXAMPLES = [
  "How many tracks does each genre have?",
  "What is the total revenue by billing country?",
  "Which are the top 10 artists by number of albums?",
  "Show all customers from Brazil.",
  "Show tracks priced above $0.99.",
  "List tracks longer than 5 minutes with their duration.",
];

/** Works on ANY relational target — pure introspection, no table names. */
const GENERIC_EXAMPLES = [
  "Which tables are in this database?",
  "How many rows does each table have?",
  "What columns does the largest table have?",
];

function pickExamples(targetDatabase: string | null | undefined): string[] {
  switch (targetGroup(targetDatabase)) {
    case "saas":
      return SAAS_EXAMPLES;
    case "chinook":
      return CHINOOK_EXAMPLES;
    case "unknown":
      return GENERIC_EXAMPLES;
  }
}

export function ExamplePills({
  onPick,
  disabled,
}: {
  onPick: (question: string) => void;
  disabled?: boolean;
}) {
  const { manifest, loaded } = useGuardrails();

  // Loading → nothing (never a flash of the wrong schema). A failed manifest
  // fetch (loaded, null) falls back to the schema-generic set.
  if (!loaded) return null;

  const examples = pickExamples(manifest?.target_database);

  return (
    <div className="examples" aria-label="Example questions">
      <span className="examples-label">Try</span>
      <div className="examples-pills">
        {examples.map((q) => (
          <button
            key={q}
            type="button"
            className="example-pill"
            onClick={() => onPick(q)}
            disabled={disabled}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

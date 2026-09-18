"use client";

/**
 * StarterQuestions — W2 N6: corpus-derived starters on the empty state.
 *
 * The questions come from a GENERATED module (lib/starterQuestions.generated.ts,
 * built by scripts/generate_starter_questions.py) — the same sources the
 * few-shot corpus is seeded from (the SaaS and Chinook gold sets) plus the
 * curated semantic-layer metric questions. Every starter is therefore known-
 * answerable on its target; nothing here is invented for the UI.
 *
 * Distinct from ExamplePills: a starter DISPATCHES the query on click (the
 * pills fill the input and wait). The surface follows the active target —
 * on an unknown (BYO) database no corpus speaks for it, so the surface omits
 * itself rather than show questions about tables that don't exist.
 */

import { targetGroup, useGuardrails } from "@/lib/useGuardrails";
import { STARTERS_BY_TARGET } from "@/lib/starterQuestions.generated";

export function StarterQuestions({
  onRun,
  disabled,
}: {
  onRun: (question: string) => void;
  disabled?: boolean;
}) {
  const { manifest, loaded } = useGuardrails();

  // Loading → nothing (no wrong-schema flash); unknown target → nothing
  // (honest absence — no corpus speaks for a schema we can't name).
  if (!loaded) return null;
  const group = targetGroup(manifest?.target_database);
  if (group === "unknown") return null;
  const starters = STARTERS_BY_TARGET[group];
  if (starters.length === 0) return null;

  return (
    <div className="starters" aria-label="Starter questions — click to run">
      <span className="examples-label">Starters · click to run</span>
      <div className="starters-list">
        {starters.map((s) => (
          <button
            key={s.question}
            type="button"
            className="starter-pill"
            onClick={() => onRun(s.question)}
            disabled={disabled}
            title={
              s.source === "semantic"
                ? "A curated semantic-layer metric question"
                : "From the verified few-shot corpus"
            }
          >
            {s.question}
          </button>
        ))}
      </div>
    </div>
  );
}

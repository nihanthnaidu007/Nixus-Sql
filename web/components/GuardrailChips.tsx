"use client";

/**
 * GuardrailChips — Wave 2 D2.3: the guardrails as a VISIBLE surface.
 *
 * A compact chip strip under the Result head, fed by two honest sources:
 *   · GET /api/v1/guardrails — the STATIC manifest (row cap, statement timeout,
 *     correction/clarification budgets, SELECT-only posture). A static settings
 *     read, fetched once per mount; null → the manifest chips are simply omitted.
 *   · The run itself — the pre-execution EXPLAIN estimate (guardrail_preview)
 *     when the planner preview succeeded, plus warnings already carried in
 *     stream_updates (row-cap overflow, statement timeout).
 *
 * HONESTY CONTRACT: chips show caps, budgets, timeouts, and estimates ONLY.
 * NIXUS has no dollar/token spend ceiling — copy here must never imply one.
 */
import { useEffect, useState } from "react";

import {
  fetchGuardrails,
  type GuardrailsManifest,
  type NormalizedResult,
  type StreamUpdate,
} from "@/lib/api";

/** Existing run warnings worth surfacing: the row-cap overflow notice and the
 *  statement timeout, both already emitted in stream_updates by the graph. */
export function guardrailWarnings(updates: StreamUpdate[]): StreamUpdate[] {
  return updates.filter((u) => /overflow|row limit|timeout/i.test(u.message));
}

export function GuardrailChips({ result }: { result: NormalizedResult }) {
  const [manifest, setManifest] = useState<GuardrailsManifest | null>(null);

  useEffect(() => {
    let live = true;
    void fetchGuardrails().then((m) => {
      if (live) setManifest(m);
    });
    return () => {
      live = false;
    };
  }, []);

  const warnings = guardrailWarnings(result.streamUpdates);
  const preview = result.guardrailPreview;

  if (!manifest && !preview && warnings.length === 0) return null;

  return (
    <div className="guard-strip" role="list" aria-label="Guardrails">
      {preview && (
        <span
          className="guard-chip guard-chip-preview"
          role="listitem"
          title="Planner estimate from a plain EXPLAIN taken before execution"
        >
          est. ~{preview.estimated_rows.toLocaleString()} rows
        </span>
      )}
      {manifest && (
        <>
          <span
            className="guard-chip"
            role="listitem"
            title="SELECT results are capped at this many rows; larger answers are truncated honestly, not hidden"
          >
            row cap {manifest.row_cap.toLocaleString()}
          </span>
          <span
            className="guard-chip"
            role="listitem"
            title="Any single statement is cancelled at this limit"
          >
            timeout {Math.round(manifest.query_timeout_ms / 1000)}s
          </span>
          <span
            className="guard-chip"
            role="listitem"
            title="Maximum automatic self-correction attempts per query"
          >
            corrections ≤ {manifest.max_correction_attempts}
          </span>
          <span
            className="guard-chip"
            role="listitem"
            title="Maximum clarification rounds before the agent must answer or decline"
          >
            clarifications ≤ {manifest.clarification_round_cap}
          </span>
          <span
            className="guard-chip"
            role="listitem"
            title={manifest.read_only_role}
          >
            SELECT-only
          </span>
        </>
      )}
      {warnings.map((w, i) => (
        <span
          className="guard-chip guard-chip-warn"
          role="listitem"
          key={`${w.node}-${i}`}
        >
          {w.message}
        </span>
      ))}
    </div>
  );
}

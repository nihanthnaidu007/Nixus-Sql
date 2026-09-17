"use client";

/**
 * Export controls (Phase 2 W1 D1): one button per format the backend exports.
 *
 * Every click POSTs the CURRENT result's SQL to /api/v1/export/{fmt} and hands
 * the response to the browser as a download. The backend runs the query under
 * the SAME guardrails as the pipeline (read-only role, statement timeout,
 * ROW_FETCH_LIMIT row cap) — a capped export downloads the CAPPED set and says
 * so via the X-Nixus-Capped header, which we surface as a visible label rather
 * than let the user believe they have the full result.
 *
 * States are honest: a download in flight says so; a rejected export (non-SELECT,
 * query error, API down) shows the server's message inline; success with a cap
 * shows the cap notice. Nothing pretends.
 */

import { useState } from "react";
import { exportResult, type ExportFormat, type ExportOutcome } from "@/lib/api";

const FORMATS: { format: ExportFormat; label: string; ext: string }[] = [
  { format: "csv", label: "CSV", ext: ".csv" },
  { format: "xlsx", label: "Excel", ext: ".xlsx" },
  { format: "json", label: "JSON", ext: ".json" },
];

export function ExportButtons({ sql, name }: { sql: string; name?: string }) {
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cappedNotice, setCappedNotice] = useState<string | null>(null);

  async function download(format: ExportFormat) {
    if (!sql.trim() || busy) return;
    setBusy(format);
    setError(null);
    setCappedNotice(null);
    const outcome: ExportOutcome = await exportResult(sql, format, name);
    setBusy(null);
    if (!outcome.ok) {
      setError(outcome.error ?? "The export could not be generated.");
      return;
    }
    if (outcome.capped) {
      // The cap label is part of the artifact: the file holds the first
      // rowLimit rows and the UI says exactly that.
      setCappedNotice(
        `Exported the first ${outcome.rowLimit?.toLocaleString() ?? "available"} rows — the result was capped at the query ceiling.`,
      );
    }
  }

  return (
    <div className="export-group" role="group" aria-label="Export result">
      {FORMATS.map(({ format, label }) => (
        <button
          key={format}
          type="button"
          className="export-btn"
          onClick={() => download(format)}
          disabled={busy !== null || !sql.trim()}
          aria-label={`Export as ${label}`}
        >
          {busy === format ? "Exporting…" : label}
        </button>
      ))}
      {cappedNotice && (
        <span className="export-capped" role="status">
          ⚠ {cappedNotice}
        </span>
      )}
      {error && (
        <span className="export-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

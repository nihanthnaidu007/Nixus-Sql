"use client";

/**
 * ChartExport — W2 N5: PNG / SVG download of the chart AS RENDERED.
 *
 * Sits in the chart head beside the chart-type picker. The bytes are the
 * serialized Recharts surface (see lib/chart-export.ts) — no re-query, no
 * re-plot. A blocked or unsupported download shows an inline message rather
 * than failing silently.
 */

import { useState } from "react";

import { downloadChart, type ChartExportType } from "@/lib/chart-export";

export function ChartExport({
  container,
}: {
  container: React.RefObject<HTMLDivElement | null>;
}) {
  const [busy, setBusy] = useState<ChartExportType | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save(type: ChartExportType) {
    if (busy) return;
    setBusy(type);
    setError(null);
    const ok = await downloadChart(container.current, type);
    setBusy(null);
    if (!ok) {
      setError(`The ${type.toUpperCase()} download could not be created.`);
    }
  }

  return (
    <div className="chart-export" role="group" aria-label="Download chart">
      <button
        type="button"
        className="chart-export-btn"
        onClick={() => void save("png")}
        disabled={busy !== null}
        title="Download the rendered chart as a PNG image"
      >
        {busy === "png" ? "…" : "PNG"}
      </button>
      <button
        type="button"
        className="chart-export-btn"
        onClick={() => void save("svg")}
        disabled={busy !== null}
        title="Download the rendered chart as an SVG image"
      >
        {busy === "svg" ? "…" : "SVG"}
      </button>
      {error && (
        <span className="chart-export-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

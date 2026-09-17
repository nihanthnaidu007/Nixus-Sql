"use client";

import { useEffect, useState } from "react";
import {
  runEditedSql,
  type ChartConfig,
  type LiveProgress,
  type NormalizedResult,
} from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import { SqlBlock } from "./SqlBlock";
import { ResultTable } from "./ResultTable";
import { ChartView, hasChart } from "./ChartView";
import { ConfidenceBanner } from "./ConfidenceBanner";
import { IntelligenceStrip } from "./IntelligenceStrip";
import { LivePipeline, PipelineSection } from "./Pipeline";
import { ExportButtons } from "./ExportButtons";
import { GuardrailChips } from "./GuardrailChips";

const ENTITY_CAP = 8;

/**
 * The ANSWERED happy path: SQL → result → insight → confidence, revealed top to
 * bottom with a staggered entrance. Refusal and clarification are no longer
 * rendered here — they have dedicated, designed components (Refusal, Clarification)
 * driven by the page's conversation state.
 *
 * Phase 9: the Result section gains a Table ⇄ Chart toggle. The data table stays
 * the DEFAULT (it is the primary artifact); Chart is a view OVER the same already-
 * returned rows — switching never refetches. The chart itself is the backend's
 * decision (chart_config), re-plotted in this UI's language by ChartView.
 */

type ResultMode = "table" | "chart";

/** The Table ⇄ Chart segmented control, in the ledger language. */
function ViewToggle({
  mode,
  onMode,
  chartable,
}: {
  mode: ResultMode;
  onMode: (m: ResultMode) => void;
  chartable: boolean;
}) {
  return (
    <div className="view-toggle" role="group" aria-label="Result view">
      <button
        type="button"
        aria-pressed={mode === "table"}
        onClick={() => onMode("table")}
      >
        Table
      </button>
      <button
        type="button"
        aria-pressed={mode === "chart"}
        onClick={() => onMode("chart")}
        // Chart stays REACHABLE even when there's nothing to chart — opening it
        // shows the honest "no visualization" state rather than hiding the why.
        title={chartable ? "View as chart" : "No chart for this result shape"}
      >
        Chart
      </button>
    </div>
  );
}

/**
 * W2 D1 — the chart-type override. The backend's classify_chart decision stays
 * the DEFAULT ("auto"); when a result is chartable the user may switch among the
 * four renderable types. The choice synthesizes a client-side ChartConfig from
 * the SAME x/y primitives — it never re-queries or re-classifies.
 */
const CHART_CHOICES = ["bar", "line", "pie", "scatter"] as const;
type ChartOverride = "auto" | (typeof CHART_CHOICES)[number];

function ChartTypePicker({
  value,
  onChange,
}: {
  value: ChartOverride;
  onChange: (t: ChartOverride) => void;
}) {
  return (
    <div className="chart-picker" role="group" aria-label="Chart type">
      {(["auto", ...CHART_CHOICES] as const).map((t) => (
        <button
          key={t}
          type="button"
          aria-pressed={value === t}
          onClick={() => onChange(t)}
          title={t === "auto" ? "The backend's chart decision" : `Render as ${t}`}
        >
          {t === "auto" ? "Auto" : t[0].toUpperCase() + t.slice(1)}
        </button>
      ))}
    </div>
  );
}

/**
 * The result panel footer: run metrics + the success-path trace link.
 *
 *  · TIMING (B10): "{rows} rows · {ms} ms". The ms segment shows only on a live
 *    run — a cache hit has no execution_time_ms, so we omit it rather than fake a 0.
 *  · TRACE (B16): a subtle "view trace ↗" to LangSmith, rendered ONLY when the
 *    backend supplies trace_url. The /stream `complete` event populates it when
 *    LangSmith tracing is enabled (api/main.py:290-327); the blocking /run path
 *    always sends null. Tracing is OFF by default, so in the common case this is
 *    quietly omitted — never a dead link, but a real link the moment tracing is on.
 *    (The ERROR-path trace handling in page.tsx is separate and untouched.)
 */
function ResultFooter({
  rowCount,
  executionTimeMs,
  traceUrl,
}: {
  rowCount: number;
  executionTimeMs: number | null;
  traceUrl: string | null;
}) {
  return (
    <div className="result-foot">
      <span className="result-foot-meta">
        {rowCount.toLocaleString()} row{rowCount === 1 ? "" : "s"}
        {executionTimeMs != null && (
          <>
            {" · "}
            {executionTimeMs} ms
          </>
        )}
      </span>
      {traceUrl && (
        <a
          className="trace-link"
          href={traceUrl}
          target="_blank"
          rel="noreferrer"
        >
          view trace ↗
        </a>
      )}
    </div>
  );
}

/**
 * The edit-SQL surface (B6): an editable dark slab pre-filled with the current SQL,
 * a Re-run that POSTs to /run-sql, and HONEST inline outcome handling. A rejected
 * write (the read-only role refusing a non-SELECT) or a bad query surfaces as a
 * clean message right here — never a crash, never an empty result. No client-side
 * SQL filtering: the database enforces read-only; this just renders the outcome.
 */
function SqlEditor({
  value,
  onChange,
  onRerun,
  onCancel,
  running,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  onRerun: () => void;
  onCancel: () => void;
  running: boolean;
  error: string | null;
}) {
  // ⌘/Ctrl+Enter re-runs from inside the editor; Esc cancels.
  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      onRerun();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  }

  // A non-SELECT rejected by the read-only guard reads as "this is read-only", which
  // is a different message in kind from a plain query error (bad column, timeout).
  const isReadOnlyRejection = !!error && /only select/i.test(error);
  const errorKicker = isReadOnlyRejection ? "Rejected · read-only" : "Query error";

  const lines = Math.min(16, Math.max(4, value.split("\n").length + 1));

  return (
    <div className="sql-edit">
      <textarea
        className="sql-edit-area"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        rows={lines}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        aria-label="Edit SQL"
        disabled={running}
      />
      {error && (
        <div className="sql-edit-error" role="alert">
          <span className="sql-edit-error-kicker">{errorKicker}</span>
          <span className="sql-edit-error-body">{error}</span>
        </div>
      )}
      <div className="sql-edit-actions">
        <span className="sql-edit-hint">
          Runs read-only — writes (INSERT/UPDATE/DELETE/DDL) are rejected by the
          database. <kbd>⌘</kbd>/<kbd>Ctrl</kbd>+<kbd>Enter</kbd> to re-run.
        </span>
        <div className="sql-edit-buttons">
          <button
            type="button"
            className="sql-edit-cancel"
            onClick={onCancel}
            disabled={running}
          >
            Cancel
          </button>
          <button
            type="button"
            className="sql-edit-run"
            onClick={onRerun}
            disabled={running || !value.trim()}
          >
            {running ? "Running…" : "Re-run"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AnswerView({ result }: { result: NormalizedResult }) {
  const [mode, setMode] = useState<ResultMode>("table");
  // W2 D1 — chart-type override. "auto" (the default) defers to the backend's
  // classify_chart decision; a picked type re-renders the SAME x/y primitives
  // through ChartView. Local state only; resets on every new result.
  const [chartOverride, setChartOverride] = useState<ChartOverride>("auto");

  // B6 — edit-SQL + re-run. A successful re-run PATCHES the rendered result in place
  // (`patched`), so the SQL / table / chart / footer below all read from `view`. The
  // edit state resets whenever a brand-new top-level result arrives (the `result`
  // prop reference changes), so a fresh query never shows a stale patch.
  const [patched, setPatched] = useState<NormalizedResult | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);

  useEffect(() => {
    setPatched(null);
    setEditing(false);
    setDraft("");
    setEditError(null);
    setRerunning(false);
    setMode("table");
    setChartOverride("auto");
  }, [result]);

  const view = patched ?? result;
  // True once a hand-written SQL run is what's on screen. A manual run bypasses
  // grounding/scope/cache, so we render only what /run-sql actually returns (SQL +
  // table + chart) and SUPPRESS the grounded-run panels (intelligence strip,
  // confidence, pipeline) rather than fabricate them for SQL the system didn't reason about.
  const isEdited = patched !== null;
  const chartable = hasChart(view.chartConfig);
  // W2 D1 — the chart the user asked for, or the backend's when on "auto". The
  // override keeps the backend's x/y primitives and swaps ONLY the type; a shape
  // that doesn't map degrades to ChartView's honest NoChart state, never an
  // empty plot. classify_chart.py is untouched and stays the default.
  const activeChartConfig: ChartConfig | null =
    chartOverride !== "auto" && view.chartConfig
      ? { ...view.chartConfig, chart_type: chartOverride }
      : view.chartConfig;

  function openEditor() {
    setDraft(view.sql);
    setEditError(null);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setEditError(null);
  }

  async function rerun() {
    const sql = draft.trim();
    if (!sql || rerunning) return;
    setRerunning(true);
    setEditError(null);
    const outcome = await runEditedSql(sql, view.sessionId);
    setRerunning(false);
    if (outcome.ok && outcome.result) {
      setPatched(outcome.result);
      setEditing(false);
      setMode("table"); // land on the table for the freshly run result
      setChartOverride("auto"); // ...and the picker defers to the backend again
    } else {
      // Rejected write / bad query — shown cleanly inside the editor, prior result kept.
      setEditError(outcome.error ?? "The edited SQL could not be run.");
    }
  }

  return (
    <div className="results">
      {/* What the system DID — a glanceable header above the artifacts. Omitted for a
          manual SQL run, which the system didn't reason about (nothing honest to show). */}
      {!isEdited && <IntelligenceStrip result={view} />}

      <section className="section s0">
        <div className="result-head">
          <span className="label">SQL{isEdited ? " · edited" : ""}</span>
          {!editing && view.sql && (
            <button type="button" className="sql-edit-toggle" onClick={openEditor}>
              Edit SQL
            </button>
          )}
        </div>

        {editing ? (
          <SqlEditor
            value={draft}
            onChange={setDraft}
            onRerun={rerun}
            onCancel={cancelEdit}
            running={rerunning}
            error={editError}
          />
        ) : view.sql ? (
          <SqlBlock sql={view.sql} />
        ) : (
          <div className="empty">No SQL was generated for this query.</div>
        )}

        {isEdited && !editing && (
          <p className="edit-note">
            <span className="edit-note-mark" aria-hidden>
              ✎
            </span>
            Manual SQL — executed directly through the read-only role. Not grounded or
            scope-checked, so no confidence is assessed for this run.
          </p>
        )}
      </section>

      <section className="section s1">
        <div className="result-head">
          <span className="label">Result</span>
          {/* W1 D1 — export the CURRENT result's SQL through the backend's
              guarded export path; capped downloads label themselves. Placed on
              the Result header so it is adjacent to what it exports. The export
              name comes from the backend (timestamped), so none is passed. */}
          <ExportButtons sql={view.sql} />
          <ViewToggle mode={mode} onMode={setMode} chartable={chartable} />
          {/* W2 D1 — the type override sits beside the toggle, in chart view only,
              and only when there is a chart to override. Local state; no refetch. */}
          {mode === "chart" && chartable && (
            <ChartTypePicker value={chartOverride} onChange={setChartOverride} />
          )}
        </div>
        {/* W2 D2.3 — the guardrails as a visible surface: caps, timeout, budgets,
            the EXPLAIN estimate when present, and this run's warnings. */}
        <GuardrailChips result={view} />
        {mode === "table" ? (
          <ResultTable
            columns={view.columns}
            rows={view.rows}
            rowCount={view.rowCount}
            cached={view.servedFromCache}
          />
        ) : (
          <ChartView config={activeChartConfig} rows={view.rows} />
        )}
        <ResultFooter
          rowCount={view.rowCount}
          executionTimeMs={view.executionTimeMs}
          traceUrl={view.traceUrl}
        />
      </section>

      {view.insight && (
        <section className="section s2">
          <span className="label">Insight</span>
          <div className="insight">{renderMarkdown(view.insight)}</div>
        </section>
      )}

      {/* Confidence + the execution RECORD describe the GROUNDED agent run. A manual
          SQL re-run has neither, so both are suppressed rather than faked. */}
      {!isEdited && (
        <>
          <section className="section s3">
            <span className="label">Confidence</span>
            <ConfidenceBanner
              level={view.confidence}
              reasons={view.confidenceReasons}
              cached={view.servedFromCache}
            />
          </section>

          {/* The execution RECORD — what the pipeline did, end-state (Phase 11).
              Static, subordinate to the answer above; the LIVE animation is Phase 12. */}
          <PipelineSection result={view} />
        </>
      )}
    </div>
  );
}

/**
 * LIVE run view (Phase 12) — replaces the static skeleton while the SSE stream is
 * active. The pipeline animates node-by-node (pending → running → done) and the
 * partial signals the stream carries (intent, entities, cache) populate as they
 * arrive. On the terminal event the page swaps this for the final AnswerView /
 * Refusal / Clarification — identical to the /run render.
 */
export function LiveRunView({ live }: { live: LiveProgress }) {
  const entities = live.extractedEntities.slice(0, ENTITY_CAP);
  const more = live.extractedEntities.length - entities.length;
  const hasIntel =
    !!live.intentClass || live.servedFromCache || entities.length > 0;

  return (
    <div className="results" aria-live="polite">
      <div className="running">
        <span className="running-dot" />
        NIXUS is thinking…
      </div>

      <section className="section s4 pipeline">
        <span className="label">Pipeline</span>
        <LivePipeline
          completed={live.completed}
          servedFromCache={live.servedFromCache}
        />
      </section>

      {/* The reasoning signals the stream surfaces mid-flight; the FULL strip
          finalizes on the terminal event (same fields, same component). */}
      {hasIntel && (
        <div className="intel" aria-label="What the system is doing">
          <div className="intel-row">
            {live.intentClass && (
              <div className="intel-cell">
                <span className="intel-key">Intent</span>
                <span className="intel-val">
                  <span className="intel-badge">{live.intentClass}</span>
                </span>
              </div>
            )}
            {live.servedFromCache && (
              <div className="intel-cell">
                <span className="intel-key">Cache</span>
                <span className="intel-val is-hit">cached</span>
              </div>
            )}
          </div>
          {entities.length > 0 && (
            <div className="intel-entities">
              <span className="intel-key">Entities</span>
              <span className="intel-pills">
                {entities.map((e, i) => (
                  <span className="intel-pill" key={`${e}-${i}`}>
                    {e}
                  </span>
                ))}
                {more > 0 && (
                  <span className="intel-pill intel-pill-more">+{more} more</span>
                )}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** While the blocking /run FALLBACK is in flight (streaming failed) — the classic
 *  skeleton, so the page never freezes and the user still sees motion. */
export function RunningState() {
  return (
    <div aria-live="polite">
      <div className="running">
        <span className="running-dot" />
        Running query…
      </div>
      <div className="skeleton" aria-hidden>
        <div className="bar w1" />
        <div className="bar w2" />
        <div className="bar w3" />
      </div>
    </div>
  );
}

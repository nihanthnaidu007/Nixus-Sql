"use client";

/**
 * Analytics (Phase 3 W1 D2): the aggregates-only ledger over the run record.
 *
 * Sibling panel to HistoryPanel — loads its own data from
 * /api/v1/analytics/summary, local state only, nothing inline with a result.
 * The summary carries counts/rates/latency aggregates ONLY (the API composes
 * the existing /cache-stats and /few-shot-stats shapes server-side); there is
 * no raw SQL or question text in this panel, by contract.
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchAnalyticsSummary,
  type AnalyticsSummary,
} from "@/lib/api";

function pct(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function AnalyticsPanel() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchAnalyticsSummary();
      setSummary(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSummary(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="section analytics-panel">
      <div className="result-head">
        <span className="label">Analytics · run record</span>
      </div>

      {error && (
        <div className="saved-error" role="alert">
          {error}
        </div>
      )}

      {summary === null ? (
        <div className="saved-empty">
          {error
            ? "Analytics unavailable — the run record could not be read."
            : "Loading analytics…"}
        </div>
      ) : (
        <div className="analytics-grid">
          <div className="analytics-cell">
            <span className="analytics-key">Runs</span>
            <span className="analytics-value">
              {summary.totals.runs.toLocaleString()}
            </span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Answered</span>
            <span className="analytics-value">
              {pct(summary.rates.answered_rate)} · {summary.totals.answered.toLocaleString()}
            </span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Refused</span>
            <span className="analytics-value">
              {pct(summary.rates.refusal_rate)} · {summary.totals.refused.toLocaleString()}
            </span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Errors</span>
            <span className="analytics-value">
              {pct(summary.rates.error_rate)} · {summary.totals.errors.toLocaleString()}
            </span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Latency avg / p95</span>
            <span className="analytics-value">
              {summary.latency_ms.avg != null
                ? `${summary.latency_ms.avg} / ${summary.latency_ms.p95} ms`
                : "—"}
            </span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Cache hit rate</span>
            <span className="analytics-value">{pct(summary.cache.hit_rate)}</span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Few-shot corpus</span>
            <span className="analytics-value">
              {summary.fewshot.total.toLocaleString()} ({summary.fewshot.auto_learned} learned)
            </span>
          </div>
          <div className="analytics-cell">
            <span className="analytics-key">Feedback accept</span>
            <span className="analytics-value">
              {summary.rates.accepted_feedback + summary.rates.rejected_feedback > 0
                ? `${pct(summary.rates.accept_rate)} · ${summary.rates.accepted_feedback}/${summary.rates.accepted_feedback + summary.rates.rejected_feedback}`
                : "Not yet reviewed"}
            </span>
          </div>
        </div>
      )}

      {summary !== null && summary.volume.length > 0 && (
        <div className="analytics-volume">
          <span className="analytics-key">
            Last {summary.volume.length} active day{summary.volume.length === 1 ? "" : "s"}
          </span>
          <ul className="analytics-volume-list">
            {summary.volume.slice(-7).map((day) => (
              <li key={day.date} className="analytics-volume-day">
                <span>{day.date}</span>
                <span>
                  {day.runs.toLocaleString()} run{day.runs === 1 ? "" : "s"} ·{" "}
                  {day.answered.toLocaleString()} answered
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

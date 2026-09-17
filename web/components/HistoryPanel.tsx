"use client";

/**
 * Query history (Phase 2 W1 D3/D4): the per-session record, readable.
 *
 * The pipeline already writes one history row per execution; this panel reads
 * that record back with server-side pagination and filters (status + date
 * range). Each row can be asked again — which sends the QUESTION back through
 * the pipeline, producing a fresh run (and a fresh history row), exactly like
 * typing it again.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchQueryHistory, type HistoryEntry } from "@/lib/api";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "ANSWERED", label: "Answered" },
  { value: "NEEDS_CLARIFICATION", label: "Needs clarification" },
  { value: "REFUSED_WRITE", label: "Refused — write" },
  { value: "REFUSED_OUT_OF_SCOPE", label: "Refused — out of scope" },
  { value: "REFUSED_AMBIGUOUS", label: "Refused — ambiguous" },
  { value: "ERROR", label: "Error" },
  { value: "UNKNOWN", label: "Unknown" },
] as const;

function shortTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** A `YYYY-MM-DD` date input value → end-of-day ISO instant, or null. The
 *  "until" bound is inclusive: the user picking "today" expects today's rows. */
function dateToIso(value: string, endOfDay: boolean): string | null {
  if (!value) return null;
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  if (endOfDay) d.setHours(23, 59, 59, 999);
  return d.toISOString();
}

export function HistoryPanel({
  sessionId,
  refreshKey = 0,
  onRun,
}: {
  /** When set, the record is scoped to THIS conversation's session. */
  sessionId?: string | null;
  /** Bumped by the parent after each completed run — the pipeline has written
   *  a fresh history row, so the panel refetches even when nothing else
   *  (session, filters, page) changed. */
  refreshKey?: number;
  /** Re-asks a history row's question through the main pipeline. */
  onRun: (question: string) => void;
}) {
  const [entries, setEntries] = useState<HistoryEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1); // 1-based; offset = (page-1)*PAGE_SIZE
  const [status, setStatus] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const offset = (page - 1) * PAGE_SIZE;
      const res = await fetchQueryHistory({
        sessionId: sessionId ?? undefined,
        status: status || null,
        since: dateToIso(since, false),
        until: dateToIso(until, true),
        limit: PAGE_SIZE,
        offset,
      });
      setEntries(res.items);
      setTotal(res.total);
      setError(null);
      // A filter change can strand the page past the last one — pull back.
      if (res.items.length === 0 && res.total > 0 && offset >= res.total) {
        setPage(Math.max(1, Math.ceil(res.total / PAGE_SIZE)));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setEntries([]);
    }
  }, [sessionId, status, since, until, page, refreshKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="section history-panel">
      <div className="result-head">
        <span className="label">
          Query history
          {total > 0 ? ` · ${total.toLocaleString()} run${total === 1 ? "" : "s"}` : ""}
        </span>
        <div className="history-filters">
          <select
            className="history-filter"
            aria-label="Filter by status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            type="date"
            className="history-filter"
            aria-label="From date"
            value={since}
            onChange={(e) => {
              setSince(e.target.value);
              setPage(1);
            }}
          />
          <input
            type="date"
            className="history-filter"
            aria-label="To date"
            value={until}
            onChange={(e) => {
              setUntil(e.target.value);
              setPage(1);
            }}
          />
        </div>
      </div>

      {error && (
        <div className="saved-error" role="alert">
          {error}
        </div>
      )}

      {entries === null ? (
        <div className="saved-empty">Loading history…</div>
      ) : entries.length === 0 ? (
        <div className="saved-empty">
          No history rows match — the record only holds queries this instance
          has actually run.
        </div>
      ) : (
        <ul className="history-list">
          {entries.map((h) => (
            <li key={h.id} className="history-item">
              <div className="history-item-main">
                <span className="history-question">{h.question}</span>
                <span className="history-meta">
                  <span
                    className={`history-status history-status-${h.status.toLowerCase()}`}
                  >
                    {h.status}
                  </span>
                  {shortTime(h.created_at)}
                  {h.row_count != null && ` · ${h.row_count.toLocaleString()} rows`}
                  {h.duration_ms != null && ` · ${h.duration_ms} ms`}
                </span>
              </div>
              <button
                type="button"
                className="saved-btn"
                onClick={() => onRun(h.question)}
                aria-label={`Ask again: ${h.question}`}
              >
                Ask again
              </button>
            </li>
          ))}
        </ul>
      )}

      {totalPages > 1 && (
        <nav className="pager" aria-label="History pages">
          <button
            type="button"
            className="pager-btn"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
          >
            ‹ Prev
          </button>
          <span className="pager-status">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className="pager-btn"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
          >
            Next ›
          </button>
        </nav>
      )}
    </section>
  );
}

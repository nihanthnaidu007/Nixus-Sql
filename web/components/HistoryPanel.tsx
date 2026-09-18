"use client";

/**
 * Query history (Phase 2 W1 D3/D4; W2 N2/N3 complete the loop).
 *
 * The pipeline already writes one history row per execution; this panel reads
 * that record back with server-side pagination and filters (status + date
 * range, plus a client-side feedback filter — the history API has no verdict
 * parameter, so the feedback filter is applied to the LOADED PAGE and says
 * so). Each row can:
 *   · be asked again — the QUESTION goes back through the pipeline;
 *   · receive feedback — accept or reject, with an optional note, via the
 *     existing /feedback endpoint (W2 N2). The verdict is STAGED: rendered
 *     immediately, committed after a short grace window. Undo cancels before
 *     anything is sent — the server can record a verdict but never clear one
 *     (nixus/db/feedback_store.py), so a post-commit "undo" would have to lie
 *     about server state or post an inverted verdict. It doesn't get to.
 *   · reveal its generated SQL with copy, and re-run THAT SQL through the
 *     same guarded /run-sql path the SQL editor uses (W2 N3). On success the
 *     result lands as the current answer above (with the honest "manual run"
 * note); on failure the error surfaces inline in the row.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchQueryHistory,
  postHistoryFeedback,
  type HistoryEntry,
} from "@/lib/api";
import { SqlBlock } from "./SqlBlock";

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

/** W2 N2 — the feedback filter. Client-side over the loaded page (the
 *  server-side filters are status + dates only); labeled as such when active. */
const FEEDBACK_OPTIONS = [
  { value: "", label: "All feedback" },
  { value: "unreviewed", label: "Unreviewed" },
  { value: "accept", label: "Accepted" },
  { value: "reject", label: "Rejected" },
] as const;

type FeedbackFilter = (typeof FEEDBACK_OPTIONS)[number]["value"];

/** A verdict waiting out its undo window — nothing has been sent yet. */
interface StagedVerdict {
  id: number;
  verdict: "accept" | "reject";
  note: string;
}

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
  onRunSql,
  commitDelayMs = 6000,
}: {
  /** When set, the record is scoped to THIS conversation's session. */
  sessionId?: string | null;
  /** Bumped by the parent after each completed run — the pipeline has written
   *  a fresh history row, so the panel refetches even when nothing else
   *  (session, filters, page) changed. */
  refreshKey?: number;
  /** Re-asks a history row's question through the main pipeline. */
  onRun: (question: string) => void;
  /** W2 N3 — runs a row's generated SQL through the guarded /run-sql path.
   *  On success the parent renders the result as the current answer. */
  onRunSql?: (
    sql: string,
    sessionId: string,
    /** W2 N5 — the row's origin question, for save-provenance on manual runs. */
    originQuestion?: string,
  ) => Promise<{ ok: boolean; error: string | null }>;
  /** The undo window before a staged verdict is committed. Tests pass 0. */
  commitDelayMs?: number;
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

  // ---- W2 N2 — staged feedback (accept/reject + note + undo) ----------------

  const [feedbackFilter, setFeedbackFilter] = useState<FeedbackFilter>("");
  const [staged, setStaged] = useState<StagedVerdict | null>(null);
  // The ref mirrors `staged` so the commit timer always reads the CURRENT
  // staged verdict, never the stale closure from when the timer was set.
  const stagedRef = useRef<StagedVerdict | null>(null);
  const commitTimer = useRef<number | null>(null);
  const [committingId, setCommittingId] = useState<number | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);

  function setStagedVerdict(next: StagedVerdict | null) {
    stagedRef.current = next;
    setStaged(next);
  }

  function clearCommitTimer() {
    if (commitTimer.current !== null) {
      window.clearTimeout(commitTimer.current);
      commitTimer.current = null;
    }
  }

  useEffect(() => clearCommitTimer, []);

  /** Commit the staged verdict: POST it, flip the row in place (no refetch —
   *  the server has recorded it), or surface a failure and keep the row's
   *  affordances (still unreviewed server-side — honest). */
  const commitStaged = useCallback(async (snapshot: StagedVerdict) => {
    clearCommitTimer();
    // Drop the staging strip first — the verdict is leaving the UI's hands.
    if (stagedRef.current?.id === snapshot.id) setStagedVerdict(null);
    setCommittingId(snapshot.id);
    setFeedbackError(null);
    try {
      await postHistoryFeedback(
        snapshot.id,
        snapshot.verdict,
        snapshot.note || undefined,
      );
      setEntries(
        (prev) =>
          prev?.map((e) =>
            e.id === snapshot.id
              ? { ...e, feedback_verdict: snapshot.verdict }
              : e,
          ) ?? prev,
      );
    } catch (e) {
      setFeedbackError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommittingId(null);
    }
  }, []);

  /** Stage a verdict on a row: it renders immediately and commits after the
   *  grace window. Staging a different row commits the pending one now (one
   *  pending edit at a time); clicking the SAME verdict again un-stages it. */
  const stageVerdict = useCallback(
    (entry: HistoryEntry, verdict: "accept" | "reject") => {
      setFeedbackError(null);
      clearCommitTimer();
      const current = stagedRef.current;
      if (current && current.id !== entry.id) void commitStaged(current);
      if (current && current.id === entry.id && current.verdict === verdict) {
        setStagedVerdict(null);
        return;
      }
      setStagedVerdict({
        id: entry.id,
        verdict,
        note: current?.id === entry.id ? current.note : "",
      });
      commitTimer.current = window.setTimeout(() => {
        commitTimer.current = null;
        const pending = stagedRef.current;
        if (pending) void commitStaged(pending);
      }, commitDelayMs);
    },
    [commitDelayMs, commitStaged],
  );

  /** Typing a note restarts the window — the user is still deciding. */
  const updateStagedNote = useCallback(
    (id: number, note: string) => {
      const current = stagedRef.current;
      if (!current || current.id !== id) return;
      setStagedVerdict({ ...current, note });
      clearCommitTimer();
      commitTimer.current = window.setTimeout(() => {
        commitTimer.current = null;
        const pending = stagedRef.current;
        if (pending) void commitStaged(pending);
      }, commitDelayMs);
    },
    [commitDelayMs, commitStaged],
  );

  /** Undo a staged verdict: cancel before anything is sent. The row returns
   *  to unreviewed — which is exactly its server state. */
  const undoStaged = useCallback(() => {
    clearCommitTimer();
    setStagedVerdict(null);
    setFeedbackError(null);
  }, []);

  // ---- W2 N3 — per-row SQL disclosure + re-run -------------------------------

  const [sqlOpenId, setSqlOpenId] = useState<number | null>(null);
  const [runningSqlId, setRunningSqlId] = useState<number | null>(null);
  const [runSqlError, setRunSqlError] = useState<{
    id: number;
    message: string;
  } | null>(null);

  async function runRowSql(h: HistoryEntry) {
    if (!onRunSql || runningSqlId !== null) return;
    setRunningSqlId(h.id);
    setRunSqlError(null);
    const outcome = await onRunSql(
      h.generated_sql,
      h.session_id,
      // W2 N5 — the row's question rides along as the manual run's provenance,
      // so "save this result" can persist a natural_language that re-asks
      // correctly through the grounded pipeline.
      h.question,
    );
    setRunningSqlId(null);
    if (!outcome.ok) {
      setRunSqlError({
        id: h.id,
        message: outcome.error ?? "The SQL could not be run.",
      });
    }
  }

  // The feedback filter narrows the loaded page; the pager still pages the
  // server-side record. Labeled honestly when active.
  const visibleEntries: HistoryEntry[] =
    entries === null
      ? []
      : feedbackFilter === ""
        ? entries
        : entries.filter((h) =>
            feedbackFilter === "unreviewed"
              ? h.feedback_verdict === null
              : h.feedback_verdict === feedbackFilter,
          );

  return (
    <section className="section history-panel">
      <div className="result-head">
        <span className="label">
          Query history
          {total > 0
            ? ` · ${total.toLocaleString()} run${total === 1 ? "" : "s"}`
            : ""}
        </span>
        <div className="history-filters">
          <select
            className="history-filter"
            aria-label="Filter by feedback"
            value={feedbackFilter}
            onChange={(e) =>
              setFeedbackFilter(e.target.value as FeedbackFilter)
            }
          >
            {FEEDBACK_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
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

      {feedbackError && (
        <div className="saved-error" role="alert">
          {feedbackError}
        </div>
      )}

      {entries === null ? (
        <div className="saved-empty">Loading history…</div>
      ) : entries.length === 0 ? (
        <div className="saved-empty">
          No history rows match — the record only holds queries this instance
          has actually run.
        </div>
      ) : visibleEntries.length === 0 ? (
        <div className="saved-empty">
          No rows on this page match the feedback filter.
        </div>
      ) : (
        <>
          {feedbackFilter !== "" && (
            <div className="history-filter-note">
              feedback filter — showing {visibleEntries.length} of{" "}
              {entries.length} rows on this page
            </div>
          )}
          <ul className="history-list">
            {visibleEntries.map((h) => {
              const stagedHere = staged?.id === h.id ? staged : null;
              const committing = committingId === h.id;
              return (
                <li key={h.id} className="history-item">
                  <div className="history-item-main">
                    <span className="history-question">{h.question}</span>
                    <span className="history-meta">
                      <span
                        className={`history-status history-status-${h.status.toLowerCase()}`}
                      >
                        {h.status}
                      </span>
                      {stagedHere && (
                        <span
                          className={`history-status history-status-${stagedHere.verdict} history-status-pending`}
                        >
                          {stagedHere.verdict === "accept"
                            ? "Accepting…"
                            : "Rejecting…"}
                        </span>
                      )}
                      {committing && (
                        <span className="history-status history-status-pending">
                          Recording…
                        </span>
                      )}
                      {!stagedHere &&
                        !committing &&
                        h.feedback_verdict === "reject" && (
                          <span className="history-status history-status-reject">
                            Rejected
                          </span>
                        )}
                      {!stagedHere &&
                        !committing &&
                        h.feedback_verdict === "accept" && (
                          <span className="history-status history-status-accept">
                            Accepted
                          </span>
                        )}
                      {shortTime(h.created_at)}
                      {h.row_count != null &&
                        ` · ${h.row_count.toLocaleString()} rows`}
                      {h.duration_ms != null && ` · ${h.duration_ms} ms`}
                    </span>
                  </div>

                  {/* W2 N2 — staged verdict strip: optional note + undo, before
                      anything is sent. The window restarts while typing. */}
                  {stagedHere && (
                    <div className="history-feedback">
                      <input
                        className="history-note"
                        aria-label={`Feedback note for: ${h.question}`}
                        placeholder="Optional note"
                        value={stagedHere.note}
                        maxLength={500}
                        onChange={(e) => updateStagedNote(h.id, e.target.value)}
                      />
                      <button
                        type="button"
                        className="saved-btn"
                        onClick={undoStaged}
                        title="Cancels this verdict — nothing has been recorded yet"
                      >
                        Undo
                      </button>
                    </div>
                  )}

                  {sqlOpenId === h.id && h.generated_sql && (
                    <div className="history-sql">
                      <SqlBlock sql={h.generated_sql} />
                      <div className="history-sql-actions">
                        <button
                          type="button"
                          className="saved-btn"
                          disabled={runningSqlId !== null}
                          onClick={() => void runRowSql(h)}
                          aria-label={`Run this SQL: ${h.question}`}
                        >
                          {runningSqlId === h.id ? "Running…" : "Run this SQL"}
                        </button>
                        <span className="history-sql-hint">
                          Executes directly through the read-only role — no
                          grounding, scope, or cache, and no confidence is
                          assessed for a manual run.
                        </span>
                      </div>
                      {runSqlError?.id === h.id && (
                        <div className="sql-edit-error" role="alert">
                          <span className="sql-edit-error-kicker">
                            Run failed
                          </span>
                          <span className="sql-edit-error-body">
                            {runSqlError.message}
                          </span>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="history-actions">
                    {h.feedback_verdict === null &&
                      !stagedHere &&
                      !committing && (
                        <>
                          <button
                            type="button"
                            className="saved-btn"
                            onClick={() => stageVerdict(h, "accept")}
                            aria-label={`Accept answer: ${h.question}`}
                          >
                            Accept
                          </button>
                          <button
                            type="button"
                            className="saved-btn"
                            onClick={() => stageVerdict(h, "reject")}
                            aria-label={`Reject answer: ${h.question}`}
                          >
                            Reject
                          </button>
                        </>
                      )}
                    {h.generated_sql && (
                      <button
                        type="button"
                        className="saved-btn"
                        aria-expanded={sqlOpenId === h.id}
                        onClick={() =>
                          setSqlOpenId((cur) => (cur === h.id ? null : h.id))
                        }
                        aria-label={`Show SQL: ${h.question}`}
                      >
                        SQL
                      </button>
                    )}
                    <button
                      type="button"
                      className="saved-btn"
                      onClick={() => onRun(h.question)}
                      aria-label={`Ask again: ${h.question}`}
                    >
                      Ask again
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
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

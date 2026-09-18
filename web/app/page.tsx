"use client";

/**
 * NIXUS SQL — Phase 8.3: the trust model made visible.
 *
 * This page owns the conversation. Three trust behaviors are now first-class:
 *   · ANSWERED            → AnswerView, with the ConfidenceBanner (level + reasons)
 *   · NEEDS_CLARIFICATION → Clarification, a real threaded round-trip (N=2, server-
 *                           enforced); the answer continues the SAME conversation
 *   · REFUSED_*           → Refusal, designed as a deliberate, legitimate outcome
 *
 * A refusal is NOT an error. The error panel below is reserved for an actual
 * request failure (network / 500), which is visually and semantically distinct.
 *
 * The API contract is unchanged: runQuery() still POSTs /api/v1/run; the
 * clarification follow-up only populates request fields RunRequest already declares.
 */

import { useState } from "react";
import {
  runQuery,
  runQueryStreaming,
  runEditedSql,
  foldProgress,
  EMPTY_LIVE,
  ApiError,
  type NormalizedResult,
  type ClarificationExchange,
  type LiveProgress,
  type RunOptions,
} from "@/lib/api";
import { QueryForm } from "@/components/QueryForm";
import { ExamplePills } from "@/components/ExamplePills";
import {
  AnswerView,
  LiveRunView,
  RunningState,
  type ChartOverride,
} from "@/components/ResultView";
import { Clarification, ConversationContext } from "@/components/Clarification";
import { Refusal } from "@/components/Refusal";
import { SystemStatus } from "@/components/SystemStatus";
import { AnalyticsPanel } from "@/components/AnalyticsPanel";
import { SavedQueries, type SaveDraft } from "@/components/SavedQueries";
import { HistoryPanel } from "@/components/HistoryPanel";
import { TargetBadge } from "@/components/TargetBadge";
import { StarterQuestions } from "@/components/StarterQuestions";

/** An in-progress / completed clarification thread for the current conversation. */
interface Thread {
  originalQuestion: string;
  sessionId: string;
  exchanges: ClarificationExchange[];
}

export default function Page() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NormalizedResult | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  // Live SSE progress while a run streams (null when not streaming → the /run
  // fallback skeleton shows instead). The accumulated node/strip state drives
  // LiveRunView.
  const [live, setLive] = useState<LiveProgress | null>(null);
  const [error, setError] = useState<{
    message: string;
    traceId?: string;
  } | null>(null);
  // W1 history panel wiring: the answered run's server-issued session (the
  // panel scopes to it) + a bump counter that tells the panel a run finished,
  // since neither the session id nor the filters change on a re-run.
  const [historySessionId, setHistorySessionId] = useState<string | null>(null);
  const [historyRefresh, setHistoryRefresh] = useState(0);

  // ---- W2 N5/N7 — chart-override state, owned here so it outlives the
  // AnswerView mount long enough to be saved with the query.
  //   seededOverride — the override a saved query re-run arrived with
  //   (N5b): the next result's chart starts there instead of auto.
  //   activeOverride — what the CURRENT result's chart is showing ("auto"
  //   or a concrete type); rides into the save draft.
  //   manualSqlRun — the current result came from the N3 /run-sql path, so
  //   the honesty note must say confidence was not assessed.
  const [seededOverride, setSeededOverride] = useState<ChartOverride | null>(
    null,
  );
  const [activeOverride, setActiveOverride] = useState<ChartOverride>("auto");
  const [manualSqlRun, setManualSqlRun] = useState(false);
  // W2 N5 — the history row the current manual SQL came from. Its question is
  // the manual result's provenance: the save draft uses it as natural_language
  // (re-running a saved query goes back through the grounded pipeline), since
  // the ask box itself holds nothing meaningful for a direct SQL run.
  const [manualOriginQuestion, setManualOriginQuestion] = useState<
    string | null
  >(null);

  /** After ANY completed run, scope the history panel to the run's session and
   *  tell it to refetch — the pipeline has written a fresh history row by now.
   *  Clarifications keep the session (set via thread) but still bump refresh. */
  function syncHistoryAfterRun(r: NormalizedResult) {
    if (r.sessionId) setHistorySessionId(r.sessionId);
    setHistoryRefresh((n) => n + 1);
  }

  /**
   * Run a query LIVE over SSE (the default — it animates the pipeline), with an
   * AUTOMATIC, SILENT fallback to the blocking /run on ANY streaming failure
   * (connect error, mid-stream error, inactivity timeout, no terminal result).
   * The user always gets an answer: streamed, or /run-fallback, or a clean error
   * only if /run ALSO fails. Never a perpetual "thinking…" hang.
   */
  async function runWithFallback(
    userQuery: string,
    opts: RunOptions,
  ): Promise<NormalizedResult> {
    try {
      return await runQueryStreaming(
        userQuery,
        {
          onNode: (p) => setLive((prev) => foldProgress(prev ?? EMPTY_LIVE, p)),
        },
        opts,
      );
    } catch {
      // Streaming failed — drop the live view and fall back to the proven /run.
      // (If /run throws too, it propagates to the caller's catch → clean error.)
      setLive(null);
      return await runQuery(userQuery, opts);
    }
  }

  /** A fresh, top-level question — resets any prior clarification thread. */
  async function submitFresh() {
    const q = question.trim();
    if (!q || loading) return;
    // The form path starts clean: no seeded override, no manual-run note,
    // no manual-run provenance.
    setManualSqlRun(false);
    setManualOriginQuestion(null);
    setSeededOverride(null);
    setActiveOverride("auto");
    await submitFreshWith(q);
  }

  /** Answer the current clarifying question — threaded into the SAME conversation. */
  async function answerClarification(answer: string) {
    if (!thread || !result || loading) return;
    setLoading(true);
    setError(null);
    setLive(EMPTY_LIVE);
    // Record this turn (the question the server just asked + the user's answer).
    const exchanges: ClarificationExchange[] = [
      ...thread.exchanges,
      { question: result.clarifyingQuestion, answer },
    ];
    try {
      // user_query echoes the latest answer; the server folds the full context and
      // decides termination (N=2) itself via clarification_round.
      const r = await runWithFallback(answer, {
        sessionId: thread.sessionId,
        clarificationContext: {
          original_question: thread.originalQuestion,
          prior_clarifications: exchanges,
        },
        clarificationRound: exchanges.length,
      });
      setThread({ ...thread, exchanges });
      setResult(r);
    } catch (err) {
      setError(toError(err));
    } finally {
      setLoading(false);
      setLive(null);
    }
  }

  const showThreadContext =
    thread && result && !result.isClarification && thread.exchanges.length > 0;

  // Phase 2 W1 — saved queries: the CURRENT grounded answer becomes a savable
  // draft (question + SQL + the active chart override, W2 N5). Present only
  // for answered runs; a refusal or a clarification prompt has nothing
  // meaningful to persist. For a manual SQL run the ask box holds nothing
  // meaningful — the ORIGIN history row's question is the provenance that
  // re-runs correctly through the grounded pipeline.
  const draftQuestion = (
    manualSqlRun ? manualOriginQuestion : question.trim()
  )?.trim();
  const saveDraft: SaveDraft | null =
    result && !loading && result.isAnswer && draftQuestion
      ? {
          naturalLanguage: draftQuestion,
          generatedSql: result.sql,
          chartOverride: activeOverride !== "auto" ? activeOverride : undefined,
        }
      : null;

  /** Re-ask a saved query or history row: the same fresh-run path (SSE with
   *  the /run fallback), just with a supplied question. Never executes stored
   *  SQL. A persisted chart override (W2 N5) seeds the new result's chart. */
  function runFromPanel(
    naturalLanguage: string,
    chartOverride?: ChartOverride,
  ) {
    setQuestion(naturalLanguage);
    setManualSqlRun(false);
    setSeededOverride(chartOverride ?? null);
    setActiveOverride(chartOverride ?? "auto");
    void submitFreshWith(naturalLanguage);
  }

  async function submitFreshWith(q: string) {
    if (!q || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setThread(null);
    setLive(EMPTY_LIVE);
    try {
      const r = await runWithFallback(q, {});
      setResult(r);
      if (r.isClarification) {
        setThread({
          originalQuestion: q,
          sessionId: r.sessionId,
          exchanges: [],
        });
      }
      syncHistoryAfterRun(r);
    } catch (err) {
      setError(toError(err));
    } finally {
      setLoading(false);
      setLive(null);
    }
  }

  /** W2 N3 — re-run a HISTORY row's generated SQL through the guarded
   *  /run-sql path (same endpoint the SQL editor uses; the SQL is already
   *  ON the target, so the identity gate passes). On success the result
   *  becomes the current answer, marked as a manual run (N7): confidence
   *  was not assessed for SQL the user supplied directly. */
  async function runSqlFromHistory(
    sql: string,
    sessionId: string,
    originQuestion?: string,
  ): Promise<{ ok: boolean; error: string | null }> {
    if (loading) return { ok: false, error: "A query is already running." };
    setLoading(true);
    setError(null);
    setThread(null);
    setLive(null);
    try {
      const outcome = await runEditedSql(sql, sessionId);
      if (outcome.ok && outcome.result) {
        setResult(outcome.result);
        setManualSqlRun(true);
        setManualOriginQuestion(originQuestion?.trim() || null);
        setSeededOverride(null);
        setActiveOverride("auto");
        syncHistoryAfterRun(outcome.result);
        return { ok: true, error: null };
      }
      return { ok: false, error: outcome.error ?? "The SQL could not be run." };
    } finally {
      setLoading(false);
      setLive(null);
    }
  }

  /** W2 N4 — start a new conversation: drop the result, thread, and session
   *  scoping. The next question POSTs with an EMPTY session_id, so the API
   *  issues and registers a fresh session (api/sessions.py already does
   *  exactly that) — no new endpoint, no client-generated IDs. */
  function newConversation() {
    if (loading) return;
    setQuestion("");
    setResult(null);
    setThread(null);
    setError(null);
    setLive(null);
    setHistorySessionId(null);
    setManualSqlRun(false);
    setSeededOverride(null);
    setActiveOverride("auto");
  }

  return (
    <main className="shell">
      <header className="masthead">
        <h1 className="wordmark">
          NIXUS<span className="dot">.</span>
        </h1>
        <span className="tagline">natural language → SQL · read-only</span>
      </header>

      {/* W2 N4 — start a new conversation: the state resets and the next
          question gets a FRESH server-issued session (empty session_id on
          the wire). Never visible while a query is running. */}
      <div className="shell-toolbar">
        <button
          type="button"
          className="new-convo-btn"
          onClick={newConversation}
          disabled={loading}
          title="Clears the result and starts a fresh conversation with a new session"
        >
          + New conversation
        </button>
      </div>

      <div className="shell-main">
        <QueryForm
          value={question}
          onChange={setQuestion}
          onSubmit={submitFresh}
          loading={loading}
        />

        {/* Example questions (B12) — click to FILL the input (no auto-submit). Shown
          on the landing state, before the first run, so they invite a start without
          competing with an answer once one is on screen. */}
        {!result && !loading && (
          <>
            <ExamplePills onPick={setQuestion} disabled={loading} />
            {/* W2 N6 — corpus-derived starters: click to RUN (they dispatch),
              scoped to the active target's corpus. Omits itself on BYO. */}
            <StarterQuestions
              onRun={(q) => runFromPanel(q)}
              disabled={loading}
            />
          </>
        )}

        {/* While streaming: the LIVE pipeline animates. If streaming fell back to
          /run (live cleared), the classic skeleton shows during that wait. */}
        {loading && (live ? <LiveRunView live={live} /> : <RunningState />)}

        {/* ACTUAL error (request failed) — distinct from a refusal. */}
        {error && !loading && (
          <div className="notice error" role="alert">
            <div className="notice-label">Request failed</div>
            <div className="notice-body">{error.message}</div>
            {error.traceId && (
              <div className="trace">trace · {error.traceId}</div>
            )}
          </div>
        )}

        {result && !loading && !error && (
          <>
            {/* Light context above a terminal outcome that came from clarification. */}
            {showThreadContext && thread && (
              <div className="thread-context">
                <ConversationContext
                  originalQuestion={thread.originalQuestion}
                  exchanges={thread.exchanges}
                />
              </div>
            )}

            {result.isClarification && thread && (
              <Clarification
                originalQuestion={thread.originalQuestion}
                exchanges={thread.exchanges}
                question={result.clarifyingQuestion}
                onAnswer={answerClarification}
                loading={loading}
              />
            )}

            {result.isRefusal && (
              <Refusal outcome={result.outcome} reason={result.refusalReason} />
            )}

            {result.isAnswer && (
              <AnswerView
                result={result}
                initialChartOverride={seededOverride ?? "auto"}
                onChartOverrideChange={setActiveOverride}
                manualRun={manualSqlRun}
              />
            )}
          </>
        )}

        {/* Phase 2 W1 — the persistent workspace: saved queries (D2/D4) and the
          query-history record (D3/D4). Both re-ask through the SAME pipeline —
          runFromPanel never executes stored SQL. They load their own data, so
          they render quiet empty states when nothing exists yet. */}
        <SavedQueries draft={saveDraft} onRun={runFromPanel} />
        <HistoryPanel
          sessionId={historySessionId}
          refreshKey={historyRefresh}
          onRun={runFromPanel}
          onRunSql={runSqlFromHistory}
        />

        {/* Phase 3 W1 D2 — aggregates over the run record (counts/rates only,
          no raw SQL). Loads its own data, local state only. */}
        <AnalyticsPanel />

        {/* Phase 18 — a DISCREET, peripheral system-status footer (DB health +
          cache/few-shot stats). Always present, quiet, never inline with a
          result; describes the SYSTEM, not the query. */}
        <SystemStatus />
      </div>

      {/* W2 N1 — the active target's identity, rendered as a quiet peripheral
          badge. Renders nothing while the manifest loads or the target is
          unset — honest omission, never a placeholder. */}
      <aside className="shell-side">
        <TargetBadge />
      </aside>
    </main>
  );
}

function toError(err: unknown): { message: string; traceId?: string } {
  if (err instanceof ApiError)
    return { message: err.message, traceId: err.traceId };
  return { message: err instanceof Error ? err.message : String(err) };
}

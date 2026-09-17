/**
 * NIXUS SQL API client (Phase 8.1 → extended in 8.3).
 *
 * Wired to the REAL, detected backend contract — nothing here is assumed:
 *
 *   Endpoint : POST /api/v1/run         (api/main.py:159 `@router.post("/run")`)
 *   Request  : { user_query, session_id?, clarification_context?, clarification_round? }
 *              (api/main.py:138 `class RunRequest`)
 *   Response : the raw final LangGraph state dict returned by run_query()
 *              (nixus/services/query_service.py:33 → nixus/graph/state.py:78 SQLAgentState)
 *
 * 8.3 note: the CONTRACT is unchanged. The clarification round-trip added in 8.3
 * only POPULATES request fields the RunRequest model already declares
 * (session_id + clarification_context + clarification_round, api/main.py:138-144).
 * The folding rule is the backend's: clarification_context carries
 * {original_question, prior_clarifications:[{question, answer}]}, the latest answer
 * is ALSO echoed in user_query, and the server decides N=2 termination itself
 * (nixus/graph/scope.py: CLARIFICATION_ROUND_CAP=2). We send; the server decides.
 *
 * Streaming: the API DOES expose an SSE endpoint (POST /api/v1/stream, an
 * sse_starlette EventSourceResponse at api/main.py:174). 8.1 deliberately uses
 * the NON-streaming /api/v1/run instead: it returns the complete final state in a
 * single request/response — the minimal, most robust proof that the new frontend
 * reaches the proven backend. The incremental SSE/progress UI is 8.2's job; this
 * file leaves that endpoint untouched rather than half-building a stream client.
 *
 * Base URL: the BROWSER (running on the host) calls the API at http://localhost:8000.
 * The compose service name `api:8000` only resolves INSIDE the compose network, so
 * it must NOT be used from browser-side fetch. The default below is host-reachable;
 * CORS on the API already allows http://localhost:3000 (nixus/config.py:111).
 *
 * NEXT_PUBLIC_API_URL is inlined at BUILD time (Next.js public env), so compose
 * passes it as a build ARG (see web/Dockerfile + docker-compose.yml). If unset, the
 * default works for the standard one-command demo with no extra configuration.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// The API authenticates every /api route with an X-API-Key header (api/auth.py).
// NEXT_PUBLIC_* is inlined at BUILD time, so the key must be supplied as a build
// ARG (web/Dockerfile + compose build.args). When unset the header is simply
// omitted — requests will 401 against a secured API, which is the honest
// failure (the key exists server-side only when the operator configured one).
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

/** Headers the API requires on every request (X-API-Key only when configured). */
function authHeaders(): Record<string, string> {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
}

// ---- The detected response shapes (subset 8.1 renders; full state is larger) ----

/** Live SQL execution result. NULL when the answer was served from cache. */
export interface ExecutionResult {
  success: boolean;
  rows: Record<string, unknown>[];
  columns: string[];
  row_count: number;
  execution_time_ms: number;
  error: string | null;
}

/** Semantic-cache hit. Carries its OWN rows under `result_preview`. */
export interface CacheResult {
  hit: boolean;
  similarity: number;
  cached_sql: string | null;
  result_preview: Record<string, unknown>[] | null;
  row_count?: number;
  chart_type?: string | null;
  explanation?: string | null;
  cache_id?: number | null;
}

/**
 * The chart the backend DECIDED for this result (nixus/graph/nodes/classify_chart.py).
 * The frontend RE-PLOTS from these primitives (chart_type + columns) over the result
 * rows; it deliberately does NOT parse `plotly_json`. That blob carries a neon-cyan
 * DARK theme (wrong for this warm-paper UI) and serializes integer y-values as a
 * binary typed array ({"dtype":"i1","bdata":"…"}) — re-plotting from the primitives
 * is both lighter and on-brand.
 *
 * chart_type observed live: "bar" | "line" | "pie" | "scatter" | "none". The
 * classifier picks line (date+numeric), pie (small positive distribution), bar
 * (categorical+numeric), scatter (≥2 numeric), or none (no/insufficient/unmappable
 * data). On the "none" path x/y/color are null and plotly_json is null.
 */
export type ChartType = "bar" | "line" | "pie" | "scatter" | "none";

export interface ChartConfig {
  chart_type: ChartType | string;
  x_column: string | null;
  y_column: string | null;
  color_column: string | null;
  title: string;
  reasoning: string;
  /** Present on the wire but intentionally unused by the renderer (see above). */
  plotly_json: string | null;
}

/**
 * One self-correction the agent made (nixus/graph/nodes/self_correct.py:89-95):
 * the SQL that failed, why it failed, the model's diagnosis, and the rewritten SQL.
 *
 * This list is EMPTY on a clean first-pass query — the COMMON case on this
 * benchmark (the graph answers most questions without ever entering self_correct).
 * The UI presents that empty case as a POSITIVE clean-pass signal, never an error.
 */
export interface CorrectionEntry {
  // The backend stores this as an int (nixus/graph/state.py:105 `correction_attempts:
  // int`; CorrectionRecord.attempt: int), so it arrives as a JSON number — the type is
  // honest as written. (v3 verified: no string-vs-number mismatch to coerce.)
  attempt: number;
  failed_sql: string;
  error_message: string;
  fix_reasoning: string;
  corrected_sql: string;
}

/**
 * One per-node status line the graph emits as it executes (the nodes append to
 * state["stream_updates"], e.g. nixus/graph/nodes/parse_intent.py:64-68). Verified
 * POPULATED on the /run path (not only /stream): a clean run returns ~12 entries,
 * a cache hit ~5. `status` is "done" | "error" | "running" across the nodes.
 */
export interface StreamUpdate {
  timestamp: string;
  node: string;
  message: string;
  status: string; // "done" | "error" | "running"
}

/**
 * The outcome discriminator (nixus/graph/scope.py:65-69). Every response carries
 * exactly one of these — it decides whether the UI shows an answer, a follow-up
 * question, or a refusal.
 */
export type Outcome =
  | "ANSWERED"
  | "NEEDS_CLARIFICATION"
  | "REFUSED_OUT_OF_SCOPE"
  | "REFUSED_WRITE"
  | "REFUSED_AMBIGUOUS";

/** The raw final graph state the API returns. Fields 8.1 reads are typed; the
 *  rest of the (large) state dict is preserved under the index signature. */
export interface NixusResponse {
  user_query: string;
  session_id: string;
  outcome: Outcome | null;
  // Answer path
  generated_sql: string;
  execution_result: ExecutionResult | null;
  cache_result: CacheResult | null;
  served_from_cache: boolean;
  // The backend's chart decision for this result (may be null on non-answer paths).
  chart_config: ChartConfig | null;
  explanation: string;
  // Categorical confidence (nixus/graph/state.py:112-114)
  confidence: string | null; // "HIGH" | "MEDIUM" | "LOW"
  confidence_score: number;
  confidence_reasons: string[];
  // Intelligence-strip fields (Phase 10) — what the system DID, surfaced read-only.
  // All already returned by /run (nixus/graph/state.py:91-105); normalize() used to
  // discard them. We do NOT change what is sent — only stop dropping what comes back.
  intent_class: string | null; // "READ" | "WRITE" | "SCHEMA_QUESTION" on the answer path
  extracted_entities: string[] | null; // entity strings (may be [] for simple queries)
  similar_examples: unknown[] | null; // few-shots loaded — we surface only the COUNT
  correction_attempts: number | null; // self-correction loops (0 = clean first pass; cap 3)
  // Execution record (Phase 11) — the pipeline's final end-state, all already
  // returned by /run (nixus/graph/state.py:115-121). The STATIC node view, the
  // self-correction log, and the agent execution log read these read-only.
  completed_nodes: string[]; // the nodes the graph actually traversed, in order
  current_node: string | null; // the last node touched (the failing node on error)
  is_complete: boolean; // did the run reach a terminal node
  correction_history: CorrectionEntry[]; // [] on a clean first pass (common case)
  stream_updates: StreamUpdate[]; // per-node log lines — populated on /run
  // Clarification path
  clarifying_question: string | null;
  // Refusal path
  reason: string | null;
  scope_message: string | null;
  // Diagnostics
  error: string | null;
  // Real-but-dormant: the /run path always sends null (query_service sets trace_url=None),
  // but the /stream `complete` event DOES populate it via get_trace_url when LangSmith
  // tracing is enabled (api/main.py:290-327). Tracing is off by default, so this is null
  // in the common case — not dead plumbing. ResultView renders a trace link only when set.
  trace_url: string | null;
  // Pre-execution EXPLAIN estimate from the guardrail_preview node (Wave 2 D2):
  // {estimated_rows, plan_cost} when the plain EXPLAIN succeeded, null when it
  // degraded silently or never ran (cache hit) — absence is honest, never faked.
  guardrail_preview: GuardrailPreview | null;
  [key: string]: unknown;
}

/** Pre-execution planner estimate from the guardrail_preview node
 *  (nixus/graph/nodes/guardrail_preview.py): what a PLAIN EXPLAIN (never
 *  ANALYZE) said about the SQL BEFORE it executed. */
export interface GuardrailPreview {
  estimated_rows: number;
  plan_cost: number;
}

/** One clarification turn: the question the server asked + the user's answer. */
export interface ClarificationExchange {
  question: string;
  answer: string;
}

/** The stateless clarification round-trip carried back in on a follow-up
 *  (api/main.py:132 `class ClarificationContext`). */
export interface ClarificationContext {
  original_question: string;
  prior_clarifications: ClarificationExchange[];
}

export interface RunRequest {
  user_query: string;
  session_id?: string;
  clarification_context?: ClarificationContext;
  clarification_round?: number;
}

/** Options for threading a clarified follow-up into the SAME conversation. */
export interface RunOptions {
  sessionId?: string;
  clarificationContext?: ClarificationContext;
  clarificationRound?: number;
}

/** A normalized, UI-ready view derived from the raw response. The point of this
 *  layer is to hide the one real subtlety the backend has: answer rows live in
 *  `execution_result` on a live run but in `cache_result.result_preview` on a
 *  cache hit. The UI consumes `rows`/`columns`/`sql` without caring which. */
export interface NormalizedResult {
  outcome: Outcome | null;
  isRefusal: boolean;
  isClarification: boolean;
  isAnswer: boolean;
  // The server-assigned session id — threaded back on a clarified follow-up so the
  // conversation continues rather than starting fresh.
  sessionId: string;
  sql: string;
  rows: Record<string, unknown>[];
  columns: string[];
  rowCount: number;
  insight: string;
  confidence: string | null;
  confidenceReasons: string[];
  servedFromCache: boolean;
  // ---- Intelligence strip (Phase 10): what the system did + how confident -----
  // Render-only fields the backend already returns; kept here so the strip can
  // surface them. Absent/empty cases are represented HONESTLY (null / [] / 0), not
  // faked — the UI omits a field rather than drawing an empty box.
  intentClass: string | null; // READ | WRITE | SCHEMA_QUESTION (null → omit the badge)
  extractedEntities: string[]; // [] for simple queries → pills omitted
  fewShotCount: number; // count of similar_examples loaded (0 is meaningful)
  correctionAttempts: number; // self-correction loops (0 = clean first pass; cap 3)
  confidenceScore: number | null; // numeric score complementing the categorical level
  cacheHit: boolean; // cache_result.hit — the authoritative cache signal
  cacheSimilarity: number | null; // similarity ONLY when hit (0.0 on a miss → null)
  executionTimeMs: number | null; // live runs only; null when served from cache
  traceUrl: string | null; // LangSmith success-path trace; populated on the /stream path
                           // when tracing is enabled, null when tracing is off (default)
  // The backend's chart decision, carried through verbatim for ChartView to render.
  chartConfig: ChartConfig | null;
  // The pre-execution EXPLAIN estimate (null when the preview degraded or never ran).
  guardrailPreview: GuardrailPreview | null;
  // ---- Execution record (Phase 11): the STATIC end-state of the pipeline run ---
  // What the graph did, surfaced read-only for the node-status view, the self-
  // correction log, and the agent execution log. Emptiness is honest and common
  // (no corrections on a clean pass) — the UI omits empty panels, never fakes one.
  completedNodes: string[]; // nodes the graph traversed (DONE); cache hits skip some
  currentNode: string | null; // last node touched — the failing node on an error
  isComplete: boolean; // reached a terminal node
  correctionHistory: CorrectionEntry[]; // [] on a clean first pass (the common case)
  streamUpdates: StreamUpdate[]; // per-node log lines, populated on /run
  // Non-answer text
  clarifyingQuestion: string;
  refusalReason: string;
  errorText: string;
  // The full raw payload, for debugging / future phases.
  raw: NixusResponse;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly traceId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** POST a question to the real /api/v1/run endpoint and await the full result.
 *
 *  For a fresh query, call `runQuery(text)`. For a clarified follow-up, pass the
 *  threaded `opts` (the session id + accumulated clarification context + round) so
 *  the backend continues the SAME conversation. `userQuery` on a follow-up is the
 *  user's latest answer (the server also folds it via clarification_context). */
export async function runQuery(
  userQuery: string,
  opts: RunOptions = {},
): Promise<NormalizedResult> {
  const body: RunRequest = {
    user_query: userQuery,
    session_id: opts.sessionId ?? "",
  };
  if (opts.clarificationContext) {
    body.clarification_context = opts.clarificationContext;
    body.clarification_round = opts.clarificationRound ?? 0;
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
  } catch (e) {
    // Network-level failure (API down, CORS blocked, DNS) — surface honestly.
    throw new ApiError(
      `Could not reach the API at ${API_BASE_URL}. Is the stack up? (${
        e instanceof Error ? e.message : String(e)
      })`,
    );
  }

  if (!res.ok) {
    // The API's global handler returns { error, trace_id, type } on 500.
    let detail = `${res.status} ${res.statusText}`;
    let traceId: string | undefined;
    try {
      const errBody = await res.json();
      if (errBody?.error) detail = String(errBody.error);
      if (errBody?.detail) detail += ` — ${errBody.detail}`;
      if (errBody?.trace_id) traceId = String(errBody.trace_id);
    } catch {
      /* response had no JSON body; keep the status line */
    }
    throw new ApiError(detail, res.status, traceId);
  }

  const data = (await res.json()) as NixusResponse;
  return normalize(data);
}

// ---- Edit-SQL + re-run (Phase 13, B6): POST /api/v1/run-sql -------------------
//
// ADDITIVE. Lets the user re-run ARBITRARY SQL they wrote (not generated, not
// grounded) against the EXISTING /run-sql endpoint (api/main.py:352). The endpoint
// returns the SAME final-state shape /run does, so we feed it straight into
// normalize() and patch the rendered result (table/chart/SQL) in place.
//
// SAFETY is the DATABASE's, not the client's: /run-sql runs through the read-only
// role (nixus_readonly) AND rejects any non-SELECT at the API boundary with a 400.
// A write/DDL therefore NEVER reaches the data — it returns here as a clean `error`
// string to render, never a crash. The client deliberately does NOT pre-filter SQL;
// it just surfaces whatever the endpoint decides (verified Phase 13 STEP 0).

/** The outcome of an edited-SQL re-run. `ok` is true only when a SELECT actually
 *  ran and returned a result; otherwise `error` holds a readable message and the
 *  caller keeps the prior result. Two failure shapes are folded into `error`:
 *    · 400 { error, detail } — a non-SELECT rejected by the read-only guard, or an
 *      unparseable statement.
 *    · 200 with execution_result.success=false — a valid SELECT that failed at
 *      execution (unknown column/table, timeout); the DB error is surfaced. */
export interface EditedSqlOutcome {
  ok: boolean;
  result: NormalizedResult | null;
  error: string | null;
}

/** POST user-edited SQL to /api/v1/run-sql and normalize the outcome. Never throws
 *  for an expected failure (rejected write, bad SQL) — those come back as `error`
 *  so the edit UI can render them cleanly in place. */
export async function runEditedSql(
  sql: string,
  sessionId: string,
): Promise<EditedSqlOutcome> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/run-sql`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ sql, session_id: sessionId }),
    });
  } catch (e) {
    return {
      ok: false,
      result: null,
      error: `Could not reach the API at ${API_BASE_URL}. Is the stack up? (${
        e instanceof Error ? e.message : String(e)
      })`,
    };
  }

  // Boundary rejection: a write/DDL refused by the read-only guard, or unparseable
  // SQL, returns 400 { error, detail }. Surface it as a readable message — this is
  // the tool BEING read-only, not an app failure.
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.error) msg = String(body.error);
      if (body?.detail) msg += ` — ${String(body.detail)}`;
    } catch {
      /* no JSON body — keep the status line */
    }
    return { ok: false, result: null, error: msg };
  }

  const data = (await res.json()) as NixusResponse;

  // A 200 can still carry a failure: a valid SELECT that errored at execution
  // (unknown column/table, statement timeout) comes back with the DB error under
  // execution_result. Pull it up so the user sees the real reason, not an empty table.
  const exec = data.execution_result;
  const execError = exec && exec.success === false ? exec.error ?? null : null;
  const error = data.error ?? execError ?? null;
  if (error) return { ok: false, result: null, error };

  return { ok: true, result: normalize(data), error: null };
}

// ---- Streaming (Phase 12): live SSE client over POST /api/v1/stream -----------
//
// ADDITIVE. runQuery() above (the blocking /api/v1/run path) is unchanged and
// remains the automatic FALLBACK. This client consumes the EXISTING SSE endpoint
// (api/main.py:174 `@router.post("/stream")`, an sse_starlette EventSourceResponse)
// so the pipeline + strip can animate node-by-node as the query runs.
//
// The REAL, verified event shape (captured from the live endpoint, not assumed):
//   · event: node_complete  — one per graph node as it finishes. data carries
//     { node, completed_nodes, current_node, stream_updates(new lines),
//       intent_class, extracted_entities, served_from_cache, correction_attempts,
//       confidence_score, is_complete:false }                  (api/main.py:260-273)
//   · event: complete       — the TERMINAL event. data is the FULL final graph
//     state (outcome, generated_sql, execution_result, cache_result, chart_config,
//     explanation, confidence, trace_url, session_id, reason, scope_message,
//     clarifying_question, …) — the SAME shape /run returns, so we feed it straight
//     into normalize() and converge on the identical final render. (api/main.py:295)
//   · event: error          — { error, is_complete:true } on a server-side failure.
//                                                              (api/main.py:347)
// Refusals (REFUSED_*) and NEEDS_CLARIFICATION arrive as the `outcome` on the
// terminal `complete` event — handled by normalize() exactly like /run.
//
// EventSource is GET-only; this is a POST with a body, so we use fetch() + a
// ReadableStream reader and parse the SSE frames ourselves.

/** One incremental node-completion the live UI consumes (from a `node_complete`
 *  event). The graph emits node COMPLETIONS only (no explicit "started"), so the
 *  live view infers the running node from the highest completed one. */
export interface StreamProgress {
  node: string; // the node that just completed
  completedNodes: string[]; // every node done so far, in order
  currentNode: string; // the last node touched
  newUpdates: StreamUpdate[]; // ONLY this event's new execution-log lines
  intentClass: string | null; // populates at parse_intent
  extractedEntities: string[]; // populates at parse_intent
  servedFromCache: boolean; // flips true at check_cache on a hit
  correctionAttempts: number;
  confidenceScore: number;
}

/** The accumulated live state the running view renders (folded from StreamProgress
 *  across events). Kept honest: empty lists / null until the stream supplies them. */
export interface LiveProgress {
  completed: string[];
  servedFromCache: boolean;
  intentClass: string | null;
  extractedEntities: string[];
  updates: StreamUpdate[];
  correctionAttempts: number;
}

export interface StreamHandlers {
  /** Called for each `node_complete` event, in arrival order. */
  onNode?: (p: StreamProgress) => void;
}

/** Abort the stream if no bytes arrive for this long — a hung connection must
 *  yield the /run fallback, never a perpetual "thinking…". Node completions arrive
 *  every few seconds, so prolonged total silence means a real stall. */
const STREAM_INACTIVITY_MS = 60_000;

/** POST a question to the SSE /api/v1/stream endpoint and drive `handlers.onNode`
 *  live as nodes finish, resolving with the SAME NormalizedResult the /run path
 *  produces on the terminal `complete` event.
 *
 *  THROWS (so the caller can fall back to runQuery) on any streaming failure:
 *  connect error, non-2xx, mid-stream `error` event, inactivity timeout, or a
 *  stream that ends without a terminal result. It never hangs. */
export async function runQueryStreaming(
  userQuery: string,
  handlers: StreamHandlers = {},
  opts: RunOptions = {},
): Promise<NormalizedResult> {
  const body: RunRequest = {
    user_query: userQuery,
    session_id: opts.sessionId ?? "",
  };
  if (opts.clarificationContext) {
    body.clarification_context = opts.clarificationContext;
    body.clarification_round = opts.clarificationRound ?? 0;
  }

  // Inactivity watchdog: rearmed on every chunk; firing aborts the fetch, which
  // rejects the in-flight read and propagates out so the caller falls back.
  const controller = new AbortController();
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  const armWatchdog = () => {
    if (watchdog) clearTimeout(watchdog);
    watchdog = setTimeout(() => controller.abort(), STREAM_INACTIVITY_MS);
  };

  let res: Response;
  try {
    armWatchdog();
    res = await fetch(`${API_BASE_URL}/api/v1/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...authHeaders(),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (e) {
    if (watchdog) clearTimeout(watchdog);
    throw new ApiError(
      `stream connect failed: ${e instanceof Error ? e.message : String(e)}`,
    );
  }

  if (!res.ok || !res.body) {
    if (watchdog) clearTimeout(watchdog);
    throw new ApiError(
      `stream HTTP ${res.status} ${res.statusText}`,
      res.status,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: NormalizedResult | null = null;
  let streamError: string | null = null;

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      armWatchdog(); // got bytes → the connection is alive; reset the hang timer
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line. Process every COMPLETE frame in
      // the buffer; a partial trailing frame stays buffered for the next chunk.
      let frame: { frame: string; rest: string } | null;
      while ((frame = nextSseFrame(buffer))) {
        buffer = frame.rest;
        const ev = parseSseFrame(frame.frame);
        if (!ev) continue; // keep-alive / comment-only frame

        if (ev.event === "node_complete") {
          const d = safeJsonObject(ev.data);
          if (d) handlers.onNode?.(toStreamProgress(d));
        } else if (ev.event === "complete") {
          const d = safeJsonObject(ev.data);
          if (d) finalResult = normalize(d as unknown as NixusResponse);
        } else if (ev.event === "error") {
          const d = safeJsonObject(ev.data);
          streamError =
            (d && typeof d.error === "string" ? d.error : null) ??
            "stream reported an error";
        }
      }
    }
  } finally {
    if (watchdog) clearTimeout(watchdog);
    try {
      reader.releaseLock();
    } catch {
      /* reader already released on abort — ignore */
    }
  }

  if (streamError) throw new ApiError(streamError);
  // A stream that closed without a terminal `complete` is unusable → fall back.
  if (!finalResult) throw new ApiError("stream ended without a terminal result");
  return finalResult;
}

/** Carve the first COMPLETE SSE frame off the buffer (everything before the first
 *  blank-line separator), returning it plus the remainder, or null if no full
 *  frame is buffered yet. Tolerates \n\n and \r\n\r\n line endings. */
function nextSseFrame(buf: string): { frame: string; rest: string } | null {
  const m = buf.match(/\r?\n\r?\n/);
  if (!m || m.index === undefined) return null;
  return { frame: buf.slice(0, m.index), rest: buf.slice(m.index + m[0].length) };
}

/** Parse one SSE frame's lines into { event, data }. Concatenates multiple `data:`
 *  lines per the spec, strips one optional leading space, and ignores comment
 *  (`:`-prefixed keep-alive) lines and unknown fields. */
function parseSseFrame(frame: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of frame.split(/\r?\n/)) {
    if (raw === "" || raw.startsWith(":")) continue; // blank / comment keep-alive
    const colon = raw.indexOf(":");
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let val = colon === -1 ? "" : raw.slice(colon + 1);
    if (val.startsWith(" ")) val = val.slice(1);
    if (field === "event") event = val;
    else if (field === "data") dataLines.push(val);
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

function safeJsonObject(s: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(s);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** A `node_complete` event's data → the UI-facing progress shape (honest defaults). */
function toStreamProgress(d: Record<string, unknown>): StreamProgress {
  return {
    node: typeof d.node === "string" ? d.node : "",
    completedNodes: Array.isArray(d.completed_nodes)
      ? d.completed_nodes.filter((n): n is string => typeof n === "string")
      : [],
    currentNode: typeof d.current_node === "string" ? d.current_node : "",
    newUpdates: Array.isArray(d.stream_updates)
      ? (d.stream_updates as StreamUpdate[])
      : [],
    intentClass:
      typeof d.intent_class === "string" && d.intent_class
        ? d.intent_class
        : null,
    extractedEntities: Array.isArray(d.extracted_entities)
      ? d.extracted_entities.filter((e): e is string => typeof e === "string")
      : [],
    servedFromCache: !!d.served_from_cache,
    correctionAttempts:
      typeof d.correction_attempts === "number" ? d.correction_attempts : 0,
    confidenceScore:
      typeof d.confidence_score === "number" ? d.confidence_score : 0,
  };
}

/** Fold a StreamProgress event into the accumulated LiveProgress the running view
 *  renders. `completed_nodes` is cumulative on the wire, so it replaces; partial
 *  signals (intent/cache/entities) latch as they first appear. */
export function foldProgress(prev: LiveProgress, p: StreamProgress): LiveProgress {
  return {
    completed: p.completedNodes.length ? p.completedNodes : prev.completed,
    servedFromCache: prev.servedFromCache || p.servedFromCache,
    intentClass: p.intentClass ?? prev.intentClass,
    extractedEntities: p.extractedEntities.length
      ? p.extractedEntities
      : prev.extractedEntities,
    updates: [...prev.updates, ...p.newUpdates],
    correctionAttempts: Math.max(prev.correctionAttempts, p.correctionAttempts),
  };
}

/** The zero state for a fresh live run (before the first node event arrives). */
export const EMPTY_LIVE: LiveProgress = {
  completed: [],
  servedFromCache: false,
  intentClass: null,
  extractedEntities: [],
  updates: [],
  correctionAttempts: 0,
};

/** Collapse the raw graph state into the UI-ready shape. */
export function normalize(raw: NixusResponse): NormalizedResult {
  const outcome = raw.outcome ?? null;
  const isAnswer = outcome === "ANSWERED";
  const isClarification = outcome === "NEEDS_CLARIFICATION";
  const isRefusal = !!outcome && outcome.startsWith("REFUSED");

  // Rows + columns: prefer the live execution result; fall back to the cache hit.
  const exec = raw.execution_result;
  const cache = raw.cache_result;
  let rows: Record<string, unknown>[] = [];
  let columns: string[] = [];
  let rowCount = 0;

  if (exec && Array.isArray(exec.rows)) {
    rows = exec.rows;
    columns = exec.columns ?? [];
    rowCount = exec.row_count ?? rows.length;
  } else if (cache && Array.isArray(cache.result_preview)) {
    rows = cache.result_preview;
    rowCount = cache.row_count ?? rows.length;
  }
  // Derive columns from the first row when the backend didn't supply them
  // (the cache path carries rows but no explicit `columns` list).
  if (columns.length === 0 && rows.length > 0) {
    columns = Object.keys(rows[0]);
  }

  // Intelligence-strip fields — kept honest to the real shape from the live /run:
  //  · cache_result EXISTS on a miss too (hit:false, similarity:0.0) → only treat
  //    similarity as meaningful when hit, so the strip never shows a "0.0" score.
  //  · execution_result is null/absent on a cache hit → no timing to show.
  const cacheHit = !!cache?.hit;

  return {
    outcome,
    isRefusal,
    isClarification,
    isAnswer,
    sessionId: raw.session_id ?? "",
    sql: raw.generated_sql ?? cache?.cached_sql ?? "",
    rows,
    columns,
    rowCount,
    insight: raw.explanation ?? "",
    confidence: raw.confidence ?? null,
    confidenceReasons: raw.confidence_reasons ?? [],
    servedFromCache: !!raw.served_from_cache,
    intentClass: raw.intent_class ?? null,
    extractedEntities: Array.isArray(raw.extracted_entities)
      ? raw.extracted_entities
      : [],
    fewShotCount: Array.isArray(raw.similar_examples)
      ? raw.similar_examples.length
      : 0,
    correctionAttempts:
      typeof raw.correction_attempts === "number" ? raw.correction_attempts : 0,
    confidenceScore:
      typeof raw.confidence_score === "number" ? raw.confidence_score : null,
    cacheHit,
    cacheSimilarity:
      cacheHit && typeof cache?.similarity === "number" ? cache.similarity : null,
    executionTimeMs:
      typeof exec?.execution_time_ms === "number" ? exec.execution_time_ms : null,
    traceUrl: raw.trace_url ?? null,
    chartConfig: raw.chart_config ?? null,
    // The EXPLAIN preview, validated to its shape — anything malformed degrades
    // to null exactly as the backend's silent-degrade contract intends.
    guardrailPreview:
      raw.guardrail_preview &&
      typeof raw.guardrail_preview.estimated_rows === "number" &&
      typeof raw.guardrail_preview.plan_cost === "number"
        ? raw.guardrail_preview
        : null,
    // Execution record — kept honest to the real shape: lists default to [] (never
    // null), so the views can treat "no data" as the clean/empty case uniformly.
    completedNodes: Array.isArray(raw.completed_nodes)
      ? raw.completed_nodes.filter((n): n is string => typeof n === "string")
      : [],
    currentNode: typeof raw.current_node === "string" ? raw.current_node : null,
    isComplete: !!raw.is_complete,
    correctionHistory: Array.isArray(raw.correction_history)
      ? (raw.correction_history as CorrectionEntry[])
      : [],
    streamUpdates: Array.isArray(raw.stream_updates)
      ? (raw.stream_updates as StreamUpdate[])
      : [],
    clarifyingQuestion: raw.clarifying_question ?? "",
    refusalReason: raw.reason ?? raw.scope_message ?? raw.explanation ?? "",
    errorText: raw.error ?? "",
    raw,
  };
}

// ---- System status (Phase 18, B13 + B14 + B15): three read-only GETs ----------
//
// DISCREET, PERIPHERAL observability — these describe the SYSTEM, not a query, so
// they live in a quiet footer status line, never inline with a result. All three
// are EXISTING read-only GET endpoints (no contract change, no new data on the
// wire). Each fetcher NEVER throws: a failed fetch resolves to null so the status
// UI can render "unavailable" rather than crash or hang. We surface ONLY the real
// fields these endpoints return (verified Phase 18 STEP 0); honest absence — a zero
// stat is shown factually, never hidden-as-broken and never faked.

/** GET /api/v1/health — the connection/health flags + version (api/main.py). */
export interface HealthStatus {
  status: string;
  db_connected: boolean;
  anthropic_connected: boolean;
  openai_connected: boolean;
  langsmith_tracing: boolean;
  version: string;
}

/** GET /api/v1/cache-stats — the semantic query cache (nixus query_cache). */
export interface CacheStats {
  entries: number;
  total_hits: number;
  hit_rate: number; // 0..1
}

/** GET /api/v1/fewshot-stats — the few-shot example store (seeded + auto-learned). */
export interface FewshotStats {
  total: number;
  auto_learned: number;
  seeded: number;
}

/** Fetch a read-only status GET, returning null on ANY failure (never throws). The
 *  status line treats null as "unavailable" — an honest absence, not a crash. */
async function fetchStatus<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      method: "GET",
      headers: authHeaders(),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const fetchHealth = () => fetchStatus<HealthStatus>("/api/v1/health");
export const fetchCacheStats = () => fetchStatus<CacheStats>("/api/v1/cache-stats");
export const fetchFewshotStats = () =>
  fetchStatus<FewshotStats>("/api/v1/fewshot-stats");

// ---- Phase 2 W2: guardrails manifest ----------------------------------------
//
// The guardrails as a VISIBLE surface. The endpoint is a static settings read
// (zero DB access, zero tokens); it inherits the app's fail-closed auth, so
// fetchStatus's null-on-!ok covers 401/503 the same way it does for health.

/** GET /api/v1/guardrails — the enforced limits as the backend states them:
 *  caps, budgets, timeouts, and enforcement posture. HONESTY CONTRACT: this
 *  payload (and any UI built on it) reports caps and estimates ONLY — NIXUS
 *  has no dollar/token spend ceiling, and copy must never imply one. */
export interface GuardrailsManifest {
  row_cap: number;
  query_timeout_ms: number;
  max_correction_attempts: number;
  clarification_round_cap: number;
  select_only: string;
  read_only_role: string;
  models: Record<string, string>;
  auth: string;
  note: string;
}

export const fetchGuardrails = () =>
  fetchStatus<GuardrailsManifest>("/api/v1/guardrails");

// ---- Phase 2 W1: exports, saved queries, query history ---------------------
//
// ADDITIVE. Three new read-only surfaces the backend already gates:
//   · POST /api/v1/export/{csv|xlsx|json} — executes the CURRENT result's SQL
//     under the pipeline's own guardrails (statement timeout, ROW_FETCH_LIMIT,
//     read-only role). A capped export downloads the CAPPED set and says so
//     (X-Nixus-Capped header) — we surface that label in the UI, honestly.
//   · /api/v1/saved-queries — CRUD + re-run. The re-run sends the saved
//     NATURAL-LANGUAGE question back through the normal /run pipeline (the
//     client never executes stored SQL — the backend doesn't either).
//   · /api/v1/history — the pipeline's write-on-execute record, paginated,
//     filterable by status and date.

export type ExportFormat = "csv" | "xlsx" | "json";

/** The outcome of one export click: the browser gets a file, or the user gets
 *  a readable message. `capped` mirrors the server's honest cap label. */
export interface ExportOutcome {
  ok: boolean;
  filename: string | null;
  capped: boolean;
  rowLimit: number | null;
  error: string | null;
}

/** Download one export for an already-run query's SQL. NEVER throws for an
 *  expected failure (rejected write, query error) — those come back as
 *  `error`; only construction mistakes would throw. */
export async function exportResult(
  sql: string,
  format: ExportFormat,
  name?: string,
): Promise<ExportOutcome> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/export/${format}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ sql, name }),
    });
  } catch (e) {
    return {
      ok: false,
      filename: null,
      capped: false,
      rowLimit: null,
      error: `Could not reach the API at ${API_BASE_URL}. Is the stack up? (${
        e instanceof Error ? e.message : String(e)
      })`,
    };
  }

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail?.error) msg = String(body.detail.error);
      else if (body?.detail) msg = String(body.detail);
      else if (body?.error) msg = String(body.error);
    } catch {
      /* no JSON body — keep the status line */
    }
    return {
      ok: false,
      filename: null,
      capped: false,
      rowLimit: null,
      error: msg,
    };
  }

  // The download itself: a Blob + a synthetic anchor click, the standard way to
  // honor Content-Disposition without a server-rendered page. The cap labels
  // ride the headers — read them BEFORE consuming the body.
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : `export.${format}`;
  const capped = (res.headers.get("X-Nixus-Capped") ?? "") === "true";
  const rowLimit = res.headers.get("X-Nixus-Row-Limit")
    ? Number(res.headers.get("X-Nixus-Row-Limit"))
    : null;

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);

  return { ok: true, filename, capped, rowLimit, error: null };
}

// ---- Saved queries ----------------------------------------------------------

/** One saved query as the API returns it (nixus/db/saved_query_store.py). */
export interface SavedQuery {
  id: number;
  name: string;
  description: string | null;
  tags: string[];
  natural_language: string;
  generated_sql: string;
  parameters: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
}

export async function fetchSavedQueries(tag?: string): Promise<SavedQuery[]> {
  const qs = tag ? `?tag=${encodeURIComponent(tag)}` : "";
  const res = await fetch(`${API_BASE_URL}/api/v1/saved-queries${qs}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new ApiError(`Could not load saved queries (${res.status})`, res.status);
  const body = await res.json();
  return Array.isArray(body?.items) ? (body.items as SavedQuery[]) : [];
}

export async function createSavedQuery(input: {
  name: string;
  natural_language: string;
  generated_sql: string;
  description?: string;
  tags?: string[];
}): Promise<SavedQuery> {
  const res = await fetch(`${API_BASE_URL}/api/v1/saved-queries`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail?.error) msg = String(body.detail.error);
      else if (body?.detail) msg = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new ApiError(msg, res.status);
  }
  return (await res.json()) as SavedQuery;
}

export async function deleteSavedQuery(id: number): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/saved-queries/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new ApiError(`Could not delete the saved query (${res.status})`, res.status);
  }
}

// ---- Query history ----------------------------------------------------------

/** One history row (nixus/db/query_history_store.py → the /history payload). */
export interface HistoryEntry {
  id: number;
  session_id: string;
  question: string;
  generated_sql: string;
  status: string;
  duration_ms: number | null;
  row_count: number | null;
  created_at: string;
}

/** One page of history, newest first (the API's fixed ordering). */
export interface HistoryPage {
  items: HistoryEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface HistoryFilters {
  sessionId?: string;
  status?: string | null;
  since?: string | null; // ISO instant
  until?: string | null;
  limit?: number;
  offset?: number;
}

/** Fetch one history page. Query params are serialized ONLY when set, so the
 *  request stays minimal and the API's defaults apply. */
export async function fetchQueryHistory(
  filters: HistoryFilters = {},
): Promise<HistoryPage> {
  const params = new URLSearchParams();
  if (filters.sessionId) params.set("session_id", filters.sessionId);
  if (filters.status) params.set("status", filters.status);
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  if (filters.limit != null) params.set("limit", String(filters.limit));
  if (filters.offset != null) params.set("offset", String(filters.offset));
  const qs = params.toString();
  const res = await fetch(`${API_BASE_URL}/api/v1/history${qs ? `?${qs}` : ""}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    let msg = `Could not load history (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail?.error) msg = String(body.detail.error);
    } catch {
      /* keep the default message */
    }
    throw new ApiError(msg, res.status);
  }
  const body = (await res.json()) as HistoryPage;
  return {
    items: Array.isArray(body?.items) ? body.items : [],
    total: typeof body?.total === "number" ? body.total : 0,
    limit: typeof body?.limit === "number" ? body.limit : 50,
    offset: typeof body?.offset === "number" ? body.offset : 0,
  };
}

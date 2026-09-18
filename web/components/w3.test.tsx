/**
 * Component tests for the Phase 3 W1 surfaces (analytics aggregates) and the
 * W2 staged-feedback completion of HistoryPanel (accept/reject + note + undo,
 * history SQL re-run). Same harness as w1.test.tsx — fetch stubbed, Testing
 * Library interactions. The staged verdict's undo window is a prop
 * (commitDelayMs); tests pass 0 to commit on the next macrotask, or a short
 * window to exercise Undo before anything is sent.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnalyticsPanel } from "./AnalyticsPanel";
import { HistoryPanel } from "./HistoryPanel";
import { SystemStatus } from "./SystemStatus";
import type { AnalyticsSummary, HistoryEntry } from "@/lib/api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ENTRY: HistoryEntry = {
  id: 3,
  session_id: "s1",
  question: "Who are the top customers?",
  generated_sql: "SELECT * FROM customers LIMIT 10",
  status: "ANSWERED",
  duration_ms: 42,
  row_count: 10,
  created_at: "2026-09-17T10:00:00Z",
  feedback_verdict: null,
  fewshot_example_id: null,
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** GET serves `body`; every other method (the feedback POST) serves `post`. */
function mockGetThenPost(get: Response, post: Response): void {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? post : get,
  );
}

describe("HistoryPanel — staged feedback (Phase 3 W1/W2)", () => {
  it("stages a reject and posts it after the undo window, flipping the row locally", async () => {
    mockGetThenPost(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
      jsonResponse({
        id: 3,
        verdict: "reject",
        note: null,
        fewshot_example_id: null,
        fewshot_disabled: false,
      }),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} commitDelayMs={0} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });
    const callsAfterLoad = (fetch as ReturnType<typeof vi.fn>).mock.calls
      .length;

    // Staged: the pending badge shows immediately…
    fireEvent.click(screen.getByRole("button", { name: /Reject answer/ }));
    expect(screen.getByText("Rejecting…")).toBeInTheDocument();

    // …and the POST fires once the window closes; the row flips to Rejected.
    await waitFor(() => {
      expect(screen.getByText("Rejected")).toBeInTheDocument();
    });
    expect(screen.queryByText("Rejecting…")).toBeNull();
    // The affordance is spent once a verdict exists…
    expect(screen.queryByRole("button", { name: /Reject answer/ })).toBeNull();

    // …and the POST hit the feedback endpoint with the reject verdict.
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
    const post = calls.find((c) => (c[1] as RequestInit).method === "POST");
    expect(post).toBeDefined();
    expect(String(post![0])).toContain("/api/v1/history/3/feedback");
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      verdict: "reject",
    });
    // Local state only — no refetch after recording.
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(
      callsAfterLoad + 1,
    );
  });

  it("undoes a staged verdict before the window closes — nothing is sent", async () => {
    mockGetThenPost(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
      jsonResponse({
        id: 3,
        verdict: "reject",
        note: null,
        fewshot_example_id: null,
        fewshot_disabled: false,
      }),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} commitDelayMs={80} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Reject answer/ }));
    expect(screen.getByText("Rejecting…")).toBeInTheDocument();

    // Undo within the window: the strip is replaced by the affordances.
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    expect(screen.queryByText("Rejecting…")).toBeNull();
    expect(
      screen.getByRole("button", { name: /Reject answer/ }),
    ).toBeInTheDocument();

    // The window lapses with nothing staged — no POST, row stays unreviewed.
    await new Promise((r) => setTimeout(r, 200));
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(
      calls.find((c) => (c[1] as RequestInit).method === "POST"),
    ).toBeUndefined();
    // The verdict badge is absent (scoped to status spans — the feedback
    // filter's <option>Rejected</option> also says "Rejected").
    expect(document.querySelector(".history-status-reject")).toBeNull();
  });

  it("includes the typed note in the committed accept verdict", async () => {
    mockGetThenPost(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
      jsonResponse({
        id: 3,
        verdict: "accept",
        note: "matches the ledger",
        fewshot_example_id: null,
        fewshot_disabled: false,
      }),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} commitDelayMs={0} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Accept answer/ }));
    fireEvent.change(
      screen.getByLabelText("Feedback note for: Who are the top customers?"),
      { target: { value: "matches the ledger" } },
    );

    await waitFor(() => {
      expect(screen.getByText("Accepted")).toBeInTheDocument();
    });
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
    const post = calls.find((c) => (c[1] as RequestInit).method === "POST");
    expect(post).toBeDefined();
    expect(String(post![0])).toContain("/api/v1/history/3/feedback");
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      verdict: "accept",
      note: "matches the ledger",
    });
  });

  it("reveals the row's SQL and re-runs it through the guarded /run-sql path", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
    );
    const onRunSql = vi.fn().mockResolvedValue({ ok: true, error: null });

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} onRunSql={onRunSql} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Show SQL/ }));
    // SqlBlock tokenizes the SQL across spans — assert the aggregated code text.
    const code = document.querySelector(".history-sql .sql-slab code");
    expect(code?.textContent).toContain("SELECT * FROM customers LIMIT 10");

    fireEvent.click(screen.getByRole("button", { name: /Run this SQL/ }));
    await waitFor(() => {
      expect(onRunSql).toHaveBeenCalledWith(
        "SELECT * FROM customers LIMIT 10",
        "s1",
      );
    });
  });

  it("surfaces a failed SQL re-run as an inline alert in the row", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
    );
    const onRunSql = vi
      .fn()
      .mockResolvedValue({ ok: false, error: "permission denied for table" });

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} onRunSql={onRunSql} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Show SQL/ }));
    fireEvent.click(screen.getByRole("button", { name: /Run this SQL/ }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "permission denied for table",
      );
    });
  });

  it("shows the recorded verdict badge and no affordance for a rejected row", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({
        items: [{ ...ENTRY, feedback_verdict: "reject" as const }],
        total: 1,
        limit: 20,
        offset: 0,
      }),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Rejected")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /Reject answer/ })).toBeNull();
    // Ask-again still works on a rejected row.
    expect(
      screen.getByRole("button", { name: /Ask again/ }),
    ).toBeInTheDocument();
  });

  it("filters the loaded page by feedback and says it is page-local", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({
        items: [
          ENTRY,
          { ...ENTRY, id: 4, question: "Count the orders." },
          {
            ...ENTRY,
            id: 5,
            question: "Revenue by month?",
            feedback_verdict: "accept" as const,
          },
        ],
        total: 3,
        limit: 20,
        offset: 0,
      }),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText("Filter by feedback"), {
      target: { value: "unreviewed" },
    });

    expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    expect(screen.getByText("Count the orders.")).toBeInTheDocument();
    expect(screen.queryByText("Revenue by month?")).toBeNull();
    expect(
      screen.getByText(/feedback filter — showing 2 of 3/),
    ).toBeInTheDocument();
  });

  it("surfaces a failed verdict as an alert and keeps the affordance", async () => {
    mockGetThenPost(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
      jsonResponse({ detail: { error: "History row not found." } }, 404),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} commitDelayMs={0} />);
    await waitFor(() => {
      expect(
        screen.getByText("Who are the top customers?"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Reject answer/ }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "History row not found.",
      );
    });
    // Still unreviewed — the user can retry.
    expect(
      screen.getByRole("button", { name: /Reject answer/ }),
    ).toBeInTheDocument();
  });
});

const SUMMARY: AnalyticsSummary = {
  totals: {
    runs: 12,
    answered: 9,
    refused: 2,
    needs_clarification: 1,
    errors: 0,
  },
  rates: {
    answered_rate: 75.0,
    refusal_rate: 16.7,
    needs_clarification_rate: 8.3,
    error_rate: 0.0,
    accepted_feedback: 3,
    rejected_feedback: 1,
    accept_rate: 75.0,
  },
  latency_ms: { avg: 412.5, p95: 933.1, max: 1500.0 },
  volume: [
    { date: "2026-09-16", runs: 4, answered: 3 },
    { date: "2026-09-17", runs: 8, answered: 6 },
  ],
  cache: { entries: 5, total_hits: 21, hit_rate: 80.8 },
  fewshot: { total: 30, auto_learned: 18, seeded: 12 },
};

describe("AnalyticsPanel — aggregates-only ledger (Phase 3 W1)", () => {
  it("renders the summary aggregates (counts/rates, composed stats)", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(SUMMARY),
    );

    render(<AnalyticsPanel />);

    await waitFor(() => {
      expect(screen.getByText("Analytics · run record")).toBeInTheDocument();
    });
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("75.0% · 9")).toBeInTheDocument();
    // Cache hit rate displayed as the backend already provides it (0-100).
    expect(screen.getByText("80.8%")).toBeInTheDocument();
    expect(screen.getByText("30 (18 learned)")).toBeInTheDocument();
    expect(screen.getByText("412.5 / 933.1 ms")).toBeInTheDocument();
    expect(screen.getByText("2026-09-17")).toBeInTheDocument();
    expect(screen.getByText("8 runs · 6 answered")).toBeInTheDocument();
  });

  it("shows a quiet empty state when the record is empty", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({
        ...SUMMARY,
        totals: { ...SUMMARY.totals, runs: 0 },
        latency_ms: { avg: null, p95: null, max: null },
        volume: [],
      }),
    );

    render(<AnalyticsPanel />);

    await waitFor(() => {
      expect(screen.getByText("0")).toBeInTheDocument();
    });
    expect(screen.getByText("—")).toBeInTheDocument(); // latency absent, honestly
    expect(screen.queryByText(/active day/)).toBeNull();
  });

  it("surfaces a failed load as an alert with a quiet message", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("server exploded", { status: 500 }),
    );

    render(<AnalyticsPanel />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Could not load analytics/,
      );
    });
    expect(
      screen.getByText(
        /Analytics unavailable — the run record could not be read/,
      ),
    ).toBeInTheDocument();
  });
});

describe("SystemStatus — cache hit rate display (Phase 3 W1 D4)", () => {
  /** Routes the three stats calls; anything else 404s loudly. */
  function mockStats(health: unknown, cache: unknown, fewshot: unknown): void {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/health")) return jsonResponse(health);
        if (url.includes("/cache-stats")) return jsonResponse(cache);
        if (url.includes("/fewshot-stats")) return jsonResponse(fewshot);
        return new Response(`unmocked url ${url}`, { status: 404 });
      },
    );
  }

  it("displays the backend hit rate as-is (already 0-100, no second x100)", async () => {
    mockStats(
      {
        status: "ok",
        db_connected: true,
        anthropic_connected: true,
        openai_connected: true,
        langsmith_tracing: false,
        version: "3.0.0",
      },
      { entries: 5, total_hits: 21, hit_rate: 80.0 },
      { total: 30, auto_learned: 18, seeded: 12 },
    );

    render(<SystemStatus />);

    // Stats are lazy — open the expander first.
    fireEvent.click(screen.getByRole("button", { name: /^system status/ }));

    await waitFor(() => {
      expect(screen.getByText("hit rate")).toBeInTheDocument();
    });
    // 80.0 from the backend must render as "80.0%" — the old Math.round(
    // hit_rate * 100) turned it into "8000%". Regression test for D4.
    expect(screen.getByText("80.0%")).toBeInTheDocument();
    expect(screen.queryByText("8000%")).toBeNull();
  });
});

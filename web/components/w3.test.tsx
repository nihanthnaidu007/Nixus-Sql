/**
 * Component tests for the Phase 3 W1 surfaces: HistoryPanel's explicit-reject
 * affordance (posts the verdict, flips the row to Rejected, surfaces errors).
 * Same harness as w1.test.tsx — fetch stubbed, Testing Library interactions.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnalyticsPanel } from "./AnalyticsPanel";
import { HistoryPanel } from "./HistoryPanel";
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

describe("HistoryPanel — reject feedback (Phase 3 W1)", () => {
  it("posts the verdict and flips the row to Rejected without a refetch", async () => {
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

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    });
    const callsAfterLoad = (fetch as ReturnType<typeof vi.fn>).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /Reject answer/ }));

    await waitFor(() => {
      expect(screen.getByText("Rejected")).toBeInTheDocument();
    });
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

  it("surfaces a failed verdict as an alert and keeps the affordance", async () => {
    mockGetThenPost(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
      jsonResponse({ detail: { error: "History row not found." } }, 404),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Reject answer/ }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("History row not found.");
    });
    // Still unreviewed — the user can retry.
    expect(
      screen.getByRole("button", { name: /Reject answer/ }),
    ).toBeInTheDocument();
  });
});

const SUMMARY: AnalyticsSummary = {
  totals: {
    runs: 12, answered: 9, refused: 2, needs_clarification: 1, errors: 0,
  },
  rates: {
    answered_rate: 75.0, refusal_rate: 16.7, needs_clarification_rate: 8.3,
    error_rate: 0.0, accepted_feedback: 3, rejected_feedback: 1, accept_rate: 75.0,
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
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse(SUMMARY));

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
      expect(screen.getByRole("alert")).toHaveTextContent(/Could not load analytics/);
    });
    expect(
      screen.getByText(/Analytics unavailable — the run record could not be read/),
    ).toBeInTheDocument();
  });
});

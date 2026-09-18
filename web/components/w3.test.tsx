/**
 * Component tests for the Phase 3 W1 surfaces: HistoryPanel's explicit-reject
 * affordance (posts the verdict, flips the row to Rejected, surfaces errors).
 * Same harness as w1.test.tsx — fetch stubbed, Testing Library interactions.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HistoryPanel } from "./HistoryPanel";
import type { HistoryEntry } from "@/lib/api";

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

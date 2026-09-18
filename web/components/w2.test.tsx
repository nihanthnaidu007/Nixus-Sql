/**
 * Component tests for the W2 surfaces: the chart-type override picker (D1 —
 * renders only when chartable, re-renders with the chosen type, honest NoChart
 * on shape mismatch, table toggle still works, resets on new results) and the
 * guardrails strip (D2.3 — manifest chips, the EXPLAIN estimate, run warnings).
 * fetch is stubbed; interactions run through Testing Library.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GuardrailChips, guardrailWarnings } from "./GuardrailChips";
import { AnswerView } from "./ResultView";
import { normalize, type GuardrailsManifest, type NixusResponse, type StreamUpdate } from "@/lib/api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const MANIFEST: GuardrailsManifest = {
  row_cap: 1000,
  query_timeout_ms: 30000,
  max_correction_attempts: 3,
  clarification_round_cap: 2,
  select_only: "Only SELECT statements are executed.",
  read_only_role: "The target database is reached through a read-only PostgreSQL role.",
  target_database: "nixus_demo",
  models: { sql_generation: "claude-sonnet-4-5" },
  auth: "Fail-closed API key (X-API-Key header).",
  note: "These are caps, budgets, timeouts, and estimates only.",
};

/** A full wire response for a chartable ANSWERED result (bar: categorical x,
 *  numeric y) — built typed and normalized exactly like the app does. */
function wireResponse(overrides: Partial<NixusResponse> = {}): NixusResponse {
  return {
    user_query: "monthly revenue",
    session_id: "s1",
    outcome: "ANSWERED",
    generated_sql: "SELECT month, revenue FROM sales ORDER BY month",
    execution_result: {
      success: true,
      rows: [
        { month: "2024-01-01", revenue: "100" },
        { month: "2024-02-01", revenue: "150" },
        { month: "2024-03-01", revenue: "130" },
      ],
      columns: ["month", "revenue"],
      row_count: 3,
      execution_time_ms: 12,
      error: null,
    },
    cache_result: null,
    served_from_cache: false,
    chart_config: {
      chart_type: "bar",
      x_column: "month",
      y_column: "revenue",
      color_column: null,
      title: "Revenue by month",
      reasoning: "categorical dimension with a numeric measure",
      plotly_json: null,
    },
    explanation: "Revenue by month.",
    confidence: "HIGH",
    confidence_score: 0.9,
    confidence_reasons: [],
    intent_class: "READ",
    extracted_entities: [],
    similar_examples: [],
    correction_attempts: 0,
    completed_nodes: ["execute_query", "classify_chart"],
    current_node: "explain_result",
    is_complete: true,
    correction_history: [],
    stream_updates: [],
    clarifying_question: null,
    reason: null,
    scope_message: null,
    error: null,
    trace_url: null,
    guardrail_preview: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(MANIFEST)));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function openChartView() {
  fireEvent.click(screen.getByRole("button", { name: "Chart" }));
  await waitFor(() => expect(screen.getByRole("group", { name: "Chart type" })).toBeInTheDocument());
}

describe("chart-type picker (W2 D1)", () => {
  it("renders only when the result is chartable and chart view is open", async () => {
    const chartable = normalize(wireResponse());
    const notChartable = normalize(
      wireResponse({
        chart_config: {
          chart_type: "none", x_column: null, y_column: null, color_column: null,
          title: "", reasoning: "nothing to plot", plotly_json: null,
        },
      }),
    );

    const { unmount } = render(<AnswerView result={chartable} />);
    expect(screen.queryByRole("group", { name: "Chart type" })).not.toBeInTheDocument();
    await openChartView();
    expect(screen.getByRole("group", { name: "Chart type" })).toBeInTheDocument();
    unmount();

    render(<AnswerView result={notChartable} />);
    fireEvent.click(screen.getByRole("button", { name: "Chart" }));
    // Honest no-chart state — and no picker, because there is nothing to override.
    expect(screen.getByText("No visualization for this result shape.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Chart type" })).not.toBeInTheDocument();
  });

  it("re-renders the chart with the chosen type (default stays the backend's)", async () => {
    render(<AnswerView result={normalize(wireResponse())} />);
    await openChartView();
    // "auto" → the backend's classify_chart decision (bar), shown in the caption.
    expect(screen.getByText("bar")).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole("group", { name: "Chart type" })).getByRole("button", { name: "Line" }));
    expect(screen.getByText("line")).toBeInTheDocument();
    expect(screen.queryByText("bar")).not.toBeInTheDocument();
  });

  it("shows the honest NoChart state when the override's shape doesn't map", async () => {
    render(<AnswerView result={normalize(wireResponse())} />);
    await openChartView();
    // month is categorical — a scatter needs numeric x AND y.
    fireEvent.click(within(screen.getByRole("group", { name: "Chart type" })).getByRole("button", { name: "Scatter" }));
    expect(screen.getByText("No visualization for this result shape.")).toBeInTheDocument();
  });

  it("table ⇄ chart still works with a picker present", async () => {
    render(<AnswerView result={normalize(wireResponse())} />);
    await openChartView();
    fireEvent.click(within(screen.getByRole("group", { name: "Chart type" })).getByRole("button", { name: "Pie" }));
    expect(screen.getByText("pie")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByRole("columnheader", { name: "month" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Chart" }));
    expect(screen.getByText("pie")).toBeInTheDocument();
  });

  it("resets to the backend's decision on a new result", async () => {
    const first = normalize(wireResponse());
    const second = normalize(wireResponse());
    const { rerender } = render(<AnswerView result={first} />);
    await openChartView();
    fireEvent.click(within(screen.getByRole("group", { name: "Chart type" })).getByRole("button", { name: "Pie" }));
    expect(screen.getByText("pie")).toBeInTheDocument();

    rerender(<AnswerView result={second} />);
    // Back on the table (the default view) and the override is gone: opening
    // chart view again shows the BACKEND's bar decision, not the old pie.
    fireEvent.click(screen.getByRole("button", { name: "Chart" }));
    expect(await screen.findByText("bar")).toBeInTheDocument();
    expect(screen.queryByText("pie")).not.toBeInTheDocument();
  });
});

describe("guardrails strip (W2 D2.3)", () => {
  it("renders the manifest chips (cap, timeout, budgets, posture)", async () => {
    render(<GuardrailChips result={normalize(wireResponse())} />);
    const strip = await screen.findByRole("list", { name: "Guardrails" });
    expect(within(strip).getByText(/row cap 1,000/)).toBeInTheDocument();
    expect(within(strip).getByText("timeout 30s")).toBeInTheDocument();
    expect(within(strip).getByText("corrections ≤ 3")).toBeInTheDocument();
    expect(within(strip).getByText("clarifications ≤ 2")).toBeInTheDocument();
    expect(within(strip).getByText("SELECT-only")).toBeInTheDocument();
  });

  it("never claims a dollar or token spend ceiling", async () => {
    const { container } = render(<GuardrailChips result={normalize(wireResponse())} />);
    await screen.findByRole("list", { name: "Guardrails" });
    expect(container.textContent).not.toContain("$");
  });

  it("shows the planner estimate chip when the EXPLAIN preview is present", () => {
    const withPreview = normalize(wireResponse({
      guardrail_preview: { estimated_rows: 1234, plan_cost: 88.4 },
    }));
    render(<GuardrailChips result={withPreview} />);
    expect(screen.getByText("est. ~1,234 rows")).toBeInTheDocument();
  });

  it("omits the strip entirely when there is nothing honest to show", () => {
    render(<GuardrailChips result={normalize(wireResponse())} />);
    expect(screen.queryByRole("list", { name: "Guardrails" })).not.toBeInTheDocument();
  });

  it("surfaces row-cap and timeout warnings already carried in stream_updates", () => {
    const warned = normalize(wireResponse({
      stream_updates: [
        { timestamp: "t1", node: "check_result", message: "Result quality: OVERFLOW — 1000 rows", status: "done" },
        { timestamp: "t2", node: "explain_result", message: "Answer complete", status: "done" },
      ] as StreamUpdate[],
    }));
    render(<GuardrailChips result={warned} />);
    const strip = screen.getByRole("list", { name: "Guardrails" });
    expect(within(strip).getByText(/OVERFLOW/)).toBeInTheDocument();
    expect(within(strip).queryByText("Answer complete")).not.toBeInTheDocument();
  });
});

describe("guardrailWarnings (pure)", () => {
  const u = (message: string): StreamUpdate => ({ timestamp: "t", node: "n", message, status: "done" });

  it("keeps overflow/row-limit/timeout lines and drops the rest", () => {
    const kept = guardrailWarnings([
      u("Result quality: OVERFLOW — 1000 rows"),
      u("Execution FAILED: Query exceeded 30000ms timeout"),
      u("reached the maximum row limit (1000)"),
      u("Answer complete"),
    ]);
    expect(kept.map((x) => x.message)).toHaveLength(3);
  });
});

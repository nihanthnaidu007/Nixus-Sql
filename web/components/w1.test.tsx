/**
 * Component tests for the W1 surfaces: ExportButtons (busy/error/cap states),
 * SavedQueries (list/save/run/delete + empty), HistoryPanel (rows, filters).
 * fetch is stubbed; interactions run through Testing Library.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExportButtons } from "./ExportButtons";
import { HistoryPanel } from "./HistoryPanel";
import { SavedQueries } from "./SavedQueries";
import type { SavedQuery } from "@/lib/api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SAMPLE_SAVED: SavedQuery = {
  id: 1,
  name: "Top customers",
  description: null,
  tags: ["sales"],
  natural_language: "Who are the top customers?",
  generated_sql: "SELECT * FROM customers LIMIT 10",
  parameters: null,
  created_at: "2026-09-17T10:00:00Z",
  updated_at: "2026-09-17T10:00:00Z",
  last_run_at: null,
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ExportButtons", () => {
  // jsdom cannot download — stub the object-URL plumbing like the api tests.
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:mock"),
      writable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      writable: true,
    });
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  it("renders one button per format", () => {
    render(<ExportButtons sql="SELECT 1" />);
    expect(screen.getByRole("button", { name: "Export as CSV" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export as Excel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export as JSON" })).toBeInTheDocument();
  });

  it("downloads and shows nothing extra on a clean, uncapped export", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("id\n1\n", {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="export.csv"',
        },
      }),
    );

    render(<ExportButtons sql="SELECT 1" />);
    fireEvent.click(screen.getByRole("button", { name: "Export as CSV" }));

    await waitFor(() => {
      expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("labels a capped export honestly", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("id\n1\n", {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="export.csv"',
          "X-Nixus-Capped": "true",
          "X-Nixus-Row-Limit": "1000",
        },
      }),
    );

    render(<ExportButtons sql="SELECT 1" />);
    fireEvent.click(screen.getByRole("button", { name: "Export as CSV" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("capped");
    });
  });

  it("surfaces a rejected export as an inline error", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(
        { detail: { error: "Only SELECT statements can be exported." } },
        400,
      ),
    );

    render(<ExportButtons sql="DELETE FROM x" />);
    fireEvent.click(screen.getByRole("button", { name: "Export as CSV" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Only SELECT statements can be exported.",
      );
    });
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it("disables all buttons when there is no SQL", () => {
    render(<ExportButtons sql="" />);
    for (const fmt of ["CSV", "Excel", "JSON"]) {
      expect(
        screen.getByRole("button", { name: `Export as ${fmt}` }),
      ).toBeDisabled();
    }
  });
});

describe("SavedQueries", () => {
  it("shows the quiet empty state when nothing is saved", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse({ items: [] }));

    render(<SavedQueries draft={null} onRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/No saved queries yet/)).toBeInTheDocument();
    });
  });

  it("lists saved queries and runs one through the pipeline callback", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [SAMPLE_SAVED] }),
    );
    const onRun = vi.fn();

    render(<SavedQueries draft={null} onRun={onRun} />);

    await waitFor(() => {
      expect(screen.getByText("Top customers")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(onRun).toHaveBeenCalledWith("Who are the top customers?");
  });

  it("shows the save form only when a draft is present, and saves", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse({ items: [] }));

    render(
      <SavedQueries
        draft={{ naturalLanguage: "q", generatedSql: "SELECT 1" }}
        onRun={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save current" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Save current" }));
    fireEvent.change(screen.getByLabelText("Saved query name"), {
      target: { value: "My query" },
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ ...SAMPLE_SAVED, name: "My query" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save query" }));

    await waitFor(() => {
      const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
      const post = calls.find((c) => (c[1] as RequestInit).method === "POST");
      expect(post).toBeDefined();
      expect(JSON.parse((post![1] as RequestInit).body as string)).toMatchObject({
        name: "My query",
        natural_language: "q",
        generated_sql: "SELECT 1",
      });
    });
  });

  it("surfaces a duplicate-name rejection inline", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse({ items: [] }));

    render(
      <SavedQueries
        draft={{ naturalLanguage: "q", generatedSql: "SELECT 1" }}
        onRun={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save current" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Save current" }));
    fireEvent.change(screen.getByLabelText("Saved query name"), {
      target: { value: "Dup" },
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(
        { detail: { error: "A saved query named 'Dup' already exists." } },
        409,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save query" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("already exists");
    });
  });

  it("deletes a saved query", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [SAMPLE_SAVED] }),
    );

    render(<SavedQueries draft={null} onRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Top customers")).toBeInTheDocument();
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
      const del = calls.find((c) => (c[1] as RequestInit).method === "DELETE");
      expect(del).toBeDefined();
      expect(del![0]).toBe("http://localhost:8000/api/v1/saved-queries/1");
    });
  });
});

describe("HistoryPanel", () => {
  const ENTRY = {
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

  it("renders history rows with status", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
    );

    render(<HistoryPanel sessionId="s1" onRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    });
    expect(screen.getByText("ANSWERED")).toBeInTheDocument();
  });

  it("shows the empty state when history is empty", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }),
    );

    render(<HistoryPanel sessionId={null} onRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/No history rows match/)).toBeInTheDocument();
    });
  });

  it("refetches when refreshKey bumps (a run just completed)", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
    );

    const { rerender } = render(
      <HistoryPanel sessionId="s1" refreshKey={0} onRun={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    });
    const callsAfterMount = (fetch as ReturnType<typeof vi.fn>).mock.calls.length;

    // The parent bumps refreshKey after a completed run; nothing else changed.
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY, ENTRY], total: 2, limit: 20, offset: 0 }),
    );
    rerender(<HistoryPanel sessionId="s1" refreshKey={1} onRun={vi.fn()} />);

    await waitFor(() => {
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThan(callsAfterMount);
    });
  });

  it("applies the status filter on change", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
    );

    render(<HistoryPanel sessionId={null} onRun={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "ERROR" },
    });

    await waitFor(() => {
      const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls;
      const last = calls[calls.length - 1] as unknown[];
      expect(String(last[0])).toContain("status=ERROR");
    });
  });

  it("re-asks a row through the pipeline callback", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse({ items: [ENTRY], total: 1, limit: 20, offset: 0 }),
    );
    const onRun = vi.fn();

    render(<HistoryPanel sessionId={null} onRun={onRun} />);

    await waitFor(() => {
      expect(screen.getByText("Who are the top customers?")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask again/ }));
    expect(onRun).toHaveBeenCalledWith("Who are the top customers?");
  });
});

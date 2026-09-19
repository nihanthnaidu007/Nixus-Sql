/**
 * Tests for the W1 API-client additions: exportResult (download + honest
 * error/cap handling), fetchSavedQueries parsing, and fetchQueryHistory's
 * parameter serialization. fetch is stubbed at the boundary — no network.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  API_BASE_URL,
  createSavedQuery,
  exportResult,
  fetchQueryHistory,
  fetchSavedQueries,
  runQueryStreaming,
} from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("exportResult", () => {
  // jsdom has no object URLs or anchor-click downloads — stub the two URL
  // methods and observe the anchor click through a patched prototype.
  const createObjectURL = vi.fn(() => "blob:mock");
  const revokeObjectURL = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    Object.defineProperty(URL, "createObjectURL", {
      value: createObjectURL,
      writable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: revokeObjectURL,
      writable: true,
    });
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("downloads a successful export and honors Content-Disposition + cap headers", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("id,name\n1,a\n", {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="export_2026.csv"',
          "X-Nixus-Capped": "true",
          "X-Nixus-Row-Limit": "1000",
        },
      }),
    );

    const outcome = await exportResult("SELECT 1", "csv", "my export");

    expect(outcome.ok).toBe(true);
    expect(outcome.filename).toBe("export_2026.csv");
    expect(outcome.capped).toBe(true);
    expect(outcome.rowLimit).toBe(1000);
    const called = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as unknown[];
    expect(called[0]).toBe(`${API_BASE_URL}/api/v1/export/csv`);
    expect(called[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ sql: "SELECT 1", name: "my export" }),
    });
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("maps a server-side error body to a readable message, no download", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(
        { detail: { error: "Only SELECT statements can be exported." } },
        { status: 400 },
      ),
    );

    const outcome = await exportResult("DELETE FROM x", "csv");

    expect(outcome.ok).toBe(false);
    expect(outcome.error).toBe("Only SELECT statements can be exported.");
    expect(outcome.filename).toBeNull();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it("returns a readable message when the API is unreachable", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("ECONNREFUSED"),
    );

    const outcome = await exportResult("SELECT 1", "json");

    expect(outcome.ok).toBe(false);
    expect(outcome.error).toContain("Could not reach the API");
    expect(outcome.error).toContain("ECONNREFUSED");
  });
});

describe("fetchSavedQueries", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("unwraps the items array and passes the tag filter through", async () => {
    const stub = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [{ id: 1, name: "Top customers", tags: ["sales"], natural_language: "q", generated_sql: "SELECT 1" }],
      }),
    );
    vi.stubGlobal("fetch", stub);

    const items = await fetchSavedQueries("sales");

    expect(items).toHaveLength(1);
    expect(items[0].name).toBe("Top customers");
    expect(stub.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/v1/saved-queries?tag=sales`,
    );
  });

  it("throws a readable error on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("denied", { status: 401 })),
    );

    await expect(fetchSavedQueries()).rejects.toThrow("401");
  });
});

describe("createSavedQuery", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("POSTs the payload and returns the saved record", async () => {
    const saved = { id: 7, name: "n", natural_language: "q", generated_sql: "SELECT 1", tags: [] };
    const stub = vi.fn().mockResolvedValue(jsonResponse(saved));
    vi.stubGlobal("fetch", stub);

    const out = await createSavedQuery({
      name: "n",
      natural_language: "q",
      generated_sql: "SELECT 1",
    });

    expect(out.id).toBe(7);
    const called = stub.mock.calls[0] as unknown[];
    expect(called[0]).toBe(`${API_BASE_URL}/api/v1/saved-queries`);
    expect(called[1]).toMatchObject({ method: "POST" });
  });

  it("surfaces the backend's duplicate-name message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { detail: { error: "A saved query named 'n' already exists." } },
          { status: 409 },
        ),
      ),
    );

    await expect(
      createSavedQuery({ name: "n", natural_language: "q", generated_sql: "SELECT 1" }),
    ).rejects.toThrow("already exists");
  });
});

describe("fetchQueryHistory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("serializes only the set filters and returns a normalized page", async () => {
    const stub = vi.fn().mockResolvedValue(
      jsonResponse({
        items: [
          {
            id: 3,
            session_id: "s1",
            question: "q",
            generated_sql: "SELECT 1",
            status: "ANSWERED",
            duration_ms: 12,
            row_count: 5,
            created_at: "2026-09-17T00:00:00Z",
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      }),
    );
    vi.stubGlobal("fetch", stub);

    const page = await fetchQueryHistory({ status: "ANSWERED", limit: 20, offset: 0 });

    expect(page.items).toHaveLength(1);
    expect(page.total).toBe(1);
    expect(stub.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/v1/history?status=ANSWERED&limit=20&offset=0`,
    );
  });

  it("sends an unfiltered request when no filters are given", async () => {
    const stub = vi.fn().mockResolvedValue(
      jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }),
    );
    vi.stubGlobal("fetch", stub);

    const page = await fetchQueryHistory();

    expect(page.items).toEqual([]);
    expect(page.total).toBe(0);
    expect(stub.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/v1/history`);
  });
});

describe("runQueryStreaming", () => {
  /** A 200 SSE Response whose body replays the given frames then closes. */
  function sseResponse(frames: string[]): Response {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces the provider-failure envelope's actionable detail from an error event", async () => {
    // api/main.py emits this envelope as an SSE error event when a provider
    // fails mid-stream (typed 503 semantics); the fix line must reach the UI.
    const providerErrorFrame =
      'event: error\ndata: {"error": "LLM provider unavailable", "detail": ' +
        '"Embedding provider \'openai\' rejected the configured credentials ' +
        "(authentication failed) — set a valid OPENAI_API_KEY, or set " +
        'EMBEDDINGS_PROVIDER=ollama to embed locally with Ollama.", ' +
        '"provider": "openai", "trace_id": "abc"}\n\n';

    vi.mocked(fetch).mockResolvedValue(sseResponse([providerErrorFrame]));
    await expect(runQueryStreaming("q")).rejects.toThrow(/OPENAI_API_KEY/);

    // A Response body is single-use — re-mock so the second read gets a
    // fresh stream instead of the consumed one.
    vi.mocked(fetch).mockResolvedValue(sseResponse([providerErrorFrame]));
    await expect(runQueryStreaming("q")).rejects.toThrow(
      /LLM provider unavailable — Embedding provider 'openai'/,
    );
  });

  it("keeps a bare error message when the event carries no detail", async () => {
    vi.mocked(fetch).mockResolvedValue(
      sseResponse(['event: error\ndata: {"error": "boom"}\n\n']),
    );

    await expect(runQueryStreaming("q")).rejects.toThrow(/^boom$/);
  });
});

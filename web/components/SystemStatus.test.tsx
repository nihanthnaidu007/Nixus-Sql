/**
 * Component tests for the health provider-honesty fix: the SystemStatus line
 * must render the ACTIVE embeddings provider and its connectivity — never an
 * inactive provider's flag read as failure.
 *
 * THE contract under test: with EMBEDDINGS_PROVIDER=ollama, the payload carries
 * openai_connected: false BY DEFINITION (OpenAI is not active, not probed), so
 * the status line shows "embeddings: ollama (connected)" — and never a false
 * "embeddings unavailable".
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SystemStatus } from "./SystemStatus";
import type { HealthStatus } from "@/lib/api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A health payload exactly as the fixed backend reports the ollama demo. */
const OLLAMA_HEALTH: HealthStatus = {
  status: "ok",
  db_connected: true,
  anthropic_connected: true,
  openai_connected: false, // inactive provider — not probed, not a failure
  embeddings_provider: "ollama",
  ollama_connected: true,
  embedding_dim: 768,
  degraded_reasons: [],
  langsmith_tracing: false,
  version: "3.0.0",
};

function mockHealth(health: unknown): void {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/health")) return jsonResponse(health);
      return new Response(`unmocked url ${url}`, { status: 404 });
    },
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("SystemStatus — active embeddings provider line", () => {
  it("renders 'embeddings: ollama (connected)' and never a false 'embeddings unavailable'", async () => {
    mockHealth(OLLAMA_HEALTH);

    render(<SystemStatus />);

    await waitFor(() => {
      expect(
        screen.getByText(/embeddings: ollama \(connected\)/),
      ).toBeInTheDocument();
    });
    // The regression this fix exists for: openai_connected:false in the payload
    // must NOT surface as an embeddings failure.
    expect(screen.queryByText(/embeddings unavailable/i)).toBeNull();
  });

  it("honestly reports ollama (unavailable) when the active provider is down", async () => {
    mockHealth({
      ...OLLAMA_HEALTH,
      status: "degraded",
      ollama_connected: false,
      degraded_reasons: [
        "Ollama embeddings unreachable at http://localhost:11434 — start Ollama.",
      ],
    });

    render(<SystemStatus />);

    await waitFor(() => {
      expect(
        screen.getByText(/embeddings: ollama \(unavailable\)/),
      ).toBeInTheDocument();
    });
  });

  it("renders the active provider line under the openai provider too", async () => {
    mockHealth({
      ...OLLAMA_HEALTH,
      embeddings_provider: "openai",
      openai_connected: true,
      ollama_connected: false,
      embedding_dim: 1536,
    });

    render(<SystemStatus />);

    await waitFor(() => {
      expect(
        screen.getByText(/embeddings: openai \(connected\)/),
      ).toBeInTheDocument();
    });
  });

  it("keeps the legacy fallback honest on backends without the provider fields", async () => {
    // Pre-fix backend: no embeddings_provider — the openai flag is the only
    // signal, so its absence reads as the old muted "embeddings unavailable".
    mockHealth({
      status: "degraded",
      db_connected: true,
      anthropic_connected: true,
      openai_connected: false,
      langsmith_tracing: false,
      version: "3.0.0",
    });

    render(<SystemStatus />);

    await waitFor(() => {
      expect(
        screen.getByText("· embeddings unavailable"),
      ).toBeInTheDocument();
    });
  });

  it("keeps the muted LLM note for a down Anthropic alongside the embeddings line", async () => {
    mockHealth({ ...OLLAMA_HEALTH, anthropic_connected: false });

    render(<SystemStatus />);

    await waitFor(() => {
      expect(screen.getByText("· LLM unavailable")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/embeddings: ollama \(connected\)/),
    ).toBeInTheDocument();
  });
});

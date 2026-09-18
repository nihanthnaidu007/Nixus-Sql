/**
 * Component tests for the Phase 3 W2 surfaces: the target badge (N1), starter
 * questions (N6), and the chart-export affordance (N5), plus the chart-export
 * lib's soft-failure contracts. Same harness as the other suites — fetch
 * stubbed, Testing Library interactions.
 *
 * PNG rasterization is only asserted through its SOFT-FAILURE path: jsdom has
 * no real canvas (getContext("2d") returns null), which is exactly the
 * "browser refused" branch the UI must survive. The raster path itself needs
 * a real browser (QA dogfood evidence, not unit tests).
 */

import { createRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChartExport } from "./ChartExport";
import { StarterQuestions } from "./StarterQuestions";
import { TargetBadge } from "./TargetBadge";
import {
  downloadChart,
  downloadChartPng,
  serializeChartSvg,
} from "@/lib/chart-export";
import type { GuardrailsManifest } from "@/lib/api";

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
  read_only_role: "Read-only PostgreSQL role.",
  target_database: "demo_saas",
  models: { sql_generation: "claude-sonnet-4-5" },
  auth: "Fail-closed API key (X-API-Key header).",
  note: "These are caps, budgets, timeouts, and estimates only.",
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubManifest(manifest: GuardrailsManifest): void {
  (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(jsonResponse(manifest));
}

async function waitForManifestFetch(): Promise<void> {
  await waitFor(() => {
    expect(
      (fetch as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(0);
  });
}

describe("TargetBadge — W2 N1 (database identity, honestly omitted)", () => {
  it("renders the active target's database name once the manifest loads", async () => {
    stubManifest(MANIFEST);

    render(<TargetBadge />);

    await waitFor(() => {
      expect(screen.getByText("demo_saas")).toBeInTheDocument();
    });
    expect(screen.getByText("Active database")).toBeInTheDocument();
  });

  it("renders nothing when the manifest does not name a target", async () => {
    stubManifest({ ...MANIFEST, target_database: null });

    const { container } = render(<TargetBadge />);
    await waitForManifestFetch();

    expect(container.querySelector(".target-badge")).toBeNull();
  });
});

describe("StarterQuestions — W2 N6 (corpus-derived, click to run)", () => {
  it("renders the saas corpus starters and dispatches the clicked question", async () => {
    stubManifest(MANIFEST);
    const onRun = vi.fn();

    render(<StarterQuestions onRun={onRun} />);

    await waitFor(() => {
      expect(screen.getByText(/monthly recurring revenue/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText(/monthly recurring revenue/));
    expect(onRun).toHaveBeenCalledWith(
      "What is our monthly recurring revenue?",
    );
  });

  it("speaks the chinook corpus on a chinook target", async () => {
    stubManifest({ ...MANIFEST, target_database: "chinook_classic" });

    render(<StarterQuestions onRun={vi.fn()} />);

    await waitFor(() => {
      expect(
        screen.getByText(/total revenue by billing country/),
      ).toBeInTheDocument();
    });
  });

  it("omits itself for an unknown (BYO) target — no corpus speaks for it", async () => {
    stubManifest({ ...MANIFEST, target_database: "warehouse_byo" });

    const { container } = render(<StarterQuestions onRun={vi.fn()} />);
    await waitForManifestFetch();

    expect(container.querySelector(".starters")).toBeNull();
  });
});

describe("ChartExport — W2 N5 (the chart as a downloadable artifact)", () => {
  it("downloads the rendered SVG through a blob URL and reports success", async () => {
    const createObjectURL = vi.fn(() => "blob:nixus-test");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    const clicked: string[] = [];
    const originalCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((
      tag: string,
      options?: ElementCreationOptions,
    ) => {
      const el = originalCreate(tag, options);
      if (tag === "a") {
        const anchor = el as HTMLAnchorElement;
        Object.defineProperty(anchor, "click", {
          value: () => clicked.push(anchor.download),
        });
      }
      return el;
    }) as typeof document.createElement);

    const ref = createRef<HTMLDivElement>();
    render(
      <div ref={ref}>
        <svg width={720} height={432}>
          <rect width="10" height="10" />
        </svg>
        <ChartExport container={ref} />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: /SVG/ }));

    await waitFor(() => {
      expect(clicked).toContainEqual(
        expect.stringMatching(/nixus-chart-.*\.svg$/),
      );
    });
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("surfaces a visible failure when no rendered chart exists", async () => {
    const ref = createRef<HTMLDivElement>();
    render(
      <div ref={ref}>
        <ChartExport container={ref} />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: /PNG/ }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The PNG download could not be created.",
      );
    });
  });

  it("soft-fails PNG export when the raster surface is unavailable (jsdom canvas)", async () => {
    // A stubbed Image that "loads" so the code reaches the canvas step, where
    // jsdom's null 2D context must produce a soft false — never a throw.
    class FakeImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_v: string) {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    vi.stubGlobal("Image", FakeImage);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:nixus-test"),
      revokeObjectURL: vi.fn(),
    });
    const { findChartSvg } = await import("@/lib/chart-export");
    const holder = document.createElement("div");
    holder.innerHTML = "<svg width='720' height='432'><rect /></svg>";
    const svg = findChartSvg(holder);
    expect(svg).not.toBeNull();

    const ok = await downloadChartPng(svg!, "nixus-chart.png");
    expect(ok).toBe(false);
  });
});

describe("chart-export lib — W2 N5 (serialization contracts)", () => {
  it("serializes the chart svg with explicit dimensions and a paper background", async () => {
    const holder = document.createElement("div");
    holder.innerHTML = "<svg width='720' height='432'><rect /></svg>";
    const svg = holder.querySelector("svg");
    expect(svg).not.toBeNull();

    const xml = serializeChartSvg(svg as unknown as SVGSVGElement);
    expect(xml).not.toBeNull();
    expect(xml).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(xml).toContain("background:");
  });

  it("reports failure when the container has no chart svg", async () => {
    const ok = await downloadChart(document.createElement("div"), "svg");
    expect(ok).toBe(false);
    expect(await downloadChart(null, "svg")).toBe(false);
  });
});

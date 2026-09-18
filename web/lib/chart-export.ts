"use client";

/**
 * Chart-export helpers — W2 N5: the rendered chart as a downloadable artifact.
 *
 * The chart is exported AS RENDERED: we serialize the Recharts <svg> the user
 * is looking at (same data, same palette) — never a re-plot, never a re-query.
 * PNG rasterizes that same SVG through a canvas at 2x scale.
 *
 * Every step fails SOFT (returns false) so a blocked export is a visible
 * "couldn't export" message in the UI, not a console error or a crash.
 */

/** A warm paper background so exported files aren't transparent on dark
 *  viewers. Literal hex mirroring --paper in app/globals.css (an SVG attribute
 *  can't resolve a CSS custom property). */
const PAPER = "#f6f1e4";
const CHART_PAPER = "#efeadc";

export type ChartExportType = "png" | "svg";

/** The Recharts surface svg inside a chart container, or null. */
export function findChartSvg(root: HTMLElement | null): SVGSVGElement | null {
  const svg = root?.querySelector("svg");
  return svg instanceof SVGSVGElement ? svg : null;
}

/** Serialize the rendered chart svg to a standalone SVG document string with
 *  explicit dimensions and a paper background. */
export function serializeChartSvg(svg: SVGSVGElement): string | null {
  const box = svg.getBoundingClientRect();
  const width = Math.round(box.width) || 720;
  const height = Math.round(box.height) || 432;
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  clone.setAttribute("style", `background:${CHART_PAPER}`);
  const xml = new XMLSerializer().serializeToString(clone);
  return xml ? `<?xml version="1.0" encoding="UTF-8"?>\n${xml}` : null;
}

function triggerDownload(href: string, filename: string): boolean {
  try {
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    return true;
  } catch {
    return false;
  }
}

/** SVG download: serialize → blob → anchor. Synchronous by nature. */
export function downloadChartSvg(
  svg: SVGSVGElement,
  filename: string,
): boolean {
  const xml = serializeChartSvg(svg);
  if (!xml) return false;
  let url: string | null = null;
  try {
    const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
    url = URL.createObjectURL(blob);
    return triggerDownload(url, filename);
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

/** PNG download: rasterize the SAME svg through a canvas at 2x. Fails softly
 *  where canvas is unavailable (e.g. test DOMs) — the UI shows a message. */
export async function downloadChartPng(
  svg: SVGSVGElement,
  filename: string,
): Promise<boolean> {
  const xml = serializeChartSvg(svg);
  if (!xml) return false;
  const box = svg.getBoundingClientRect();
  const width = Math.round(box.width) || 720;
  const height = Math.round(box.height) || 432;
  const scale = 2;

  const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    const loaded = new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("chart image failed to load"));
    });
    image.src = url;
    await loaded;

    const canvas = document.createElement("canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return false; // canvas unavailable — soft, visible failure
    ctx.fillStyle = PAPER;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

    const png = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!png) return false;
    const pngUrl = URL.createObjectURL(png);
    try {
      return triggerDownload(pngUrl, filename);
    } finally {
      URL.revokeObjectURL(pngUrl);
    }
  } catch {
    return false;
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Export the chart rendered inside `root` as `type`. Returns false when no
 *  rendered svg exists or the browser refused the download. */
export async function downloadChart(
  root: HTMLElement | null,
  type: ChartExportType,
): Promise<boolean> {
  const svg = findChartSvg(root);
  if (!svg) return false;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const filename = `nixus-chart-${stamp}.${type}`;
  return type === "svg"
    ? downloadChartSvg(svg, filename)
    : downloadChartPng(svg, filename);
}

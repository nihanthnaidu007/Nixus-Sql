"use client";

/**
 * SystemStatus (Phase 18, B13 + B14 + B15) — a DISCREET, peripheral status line.
 *
 * GOVERNING PRINCIPLE: restrained, peripheral observability. These are SYSTEM-level
 * facts (DB health, cache hit-rate, few-shot count), NOT part of the query
 * experience — so they sit quietly in a footer status line, never inline with a
 * result, and never compete with the query/SQL/table/chart for attention. This is a
 * status indicator, not a metrics dashboard.
 *
 * Shape:
 *   · Always visible — a small dot + "database connected" / "database unavailable"
 *     from /health (B13). LLM/tracing flags are shown quietly, factually: a false
 *     flag reads muted ("LLM unavailable"), never faked green.
 *   · A collapsed "system status" toggle reveals cache + few-shot stats (B14 + B15),
 *     so they're available without cluttering. Real fields only; a zero stat is shown
 *     factually (honest absence).
 *
 * Every fetch is graceful: a failed /health renders "status unavailable", a failed
 * stats fetch renders "unavailable" for that line — never a crash or a hang. We do
 * NOT surface the trace link (B16 stays scoped out): health is just status.
 */

import { useEffect, useState } from "react";
import {
  fetchHealth,
  fetchCacheStats,
  fetchFewshotStats,
  type HealthStatus,
  type CacheStats,
  type FewshotStats,
} from "@/lib/api";

export function SystemStatus() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthLoaded, setHealthLoaded] = useState(false);
  const [cache, setCache] = useState<CacheStats | null>(null);
  const [fewshot, setFewshot] = useState<FewshotStats | null>(null);
  const [statsLoaded, setStatsLoaded] = useState(false);
  const [open, setOpen] = useState(false);

  // Health on mount, with a light periodic refresh so a DB drop surfaces without a
  // reload. Each fetcher resolves to null on failure (never throws) → "unavailable".
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const h = await fetchHealth();
      if (!alive) return;
      setHealth(h);
      setHealthLoaded(true);
    };
    load();
    const id = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Cache + few-shot stats are loaded LAZILY the first time the expander opens —
  // they're peripheral, so we don't fetch them until asked.
  useEffect(() => {
    if (!open || statsLoaded) return;
    let alive = true;
    (async () => {
      const [c, f] = await Promise.all([fetchCacheStats(), fetchFewshotStats()]);
      if (!alive) return;
      setCache(c);
      setFewshot(f);
      setStatsLoaded(true);
    })();
    return () => {
      alive = false;
    };
  }, [open, statsLoaded]);

  // The DB dot is the primary signal. Three states: connected, unavailable (flag
  // false), and unknown (health fetch failed) — each honest, none faked.
  const dbState: "ok" | "down" | "unknown" = !healthLoaded
    ? "unknown"
    : health?.db_connected
      ? "ok"
      : "down";

  const dbLabel =
    dbState === "ok"
      ? "database connected"
      : dbState === "down"
        ? "database unavailable"
        : "status unavailable";

  return (
    <footer className="sysstatus" aria-label="system status">
      <div className="sysstatus-line">
        <span className={`sysstatus-dot is-${dbState}`} aria-hidden />
        <span className="sysstatus-db">{dbLabel}</span>

        {/* LLM / tracing flags — shown quietly only when health is known, and only
            as a muted note when something is OFF (a green "all good" needs no
            announcement; an honest "unavailable" does). */}
        {healthLoaded && health && (
          <LlmNote health={health} />
        )}

        <button
          type="button"
          className="sysstatus-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          system status {open ? "−" : "+"}
        </button>
      </div>

      {open && (
        <div className="sysstatus-detail">
          <StatGroup
            label="cache"
            loaded={statsLoaded}
            empty={!cache}
            rows={
              cache && [
                ["cached queries", String(cache.entries)],
                ["cache hits", String(cache.total_hits)],
                ["hit rate", `${cache.hit_rate.toFixed(1)}%`],
              ]
            }
          />
          <StatGroup
            label="few-shot examples"
            loaded={statsLoaded}
            empty={!fewshot}
            rows={
              fewshot && [
                ["total", String(fewshot.total)],
                ["seeded", String(fewshot.seeded)],
                ["auto-learned", String(fewshot.auto_learned)],
              ]
            }
          />
        </div>
      )}
    </footer>
  );
}

/** A muted note for any LLM/tracing flag that is OFF — honest, never alarm. When all
 *  flags are healthy this renders nothing (restraint: no green to announce). */
function LlmNote({ health }: { health: HealthStatus }) {
  const down: string[] = [];
  if (!health.anthropic_connected) down.push("LLM");
  // The trustworthy embeddings flag depends on the ACTIVE provider: under
  // ollama the API never probes OpenAI (openai_connected is false by
  // definition), so "embeddings unavailable" must key off ollama_connected.
  const embeddingsDown =
    health.embeddings_provider === "ollama"
      ? !health.ollama_connected
      : !health.openai_connected;
  if (embeddingsDown) down.push("embeddings");
  if (down.length === 0) return null;
  return (
    <span className="sysstatus-note">· {down.join(" + ")} unavailable</span>
  );
}

/** One labeled stat group inside the expander. Honest absence: an unreachable
 *  endpoint reads "unavailable"; a real zero reads as a factual "0". */
function StatGroup({
  label,
  loaded,
  empty,
  rows,
}: {
  label: string;
  loaded: boolean;
  empty: boolean;
  rows: [string, string][] | null | undefined;
}) {
  return (
    <div className="sysstatus-group">
      <div className="sysstatus-group-label">{label}</div>
      {!loaded ? (
        <div className="sysstatus-muted">loading…</div>
      ) : empty || !rows ? (
        <div className="sysstatus-muted">unavailable</div>
      ) : (
        <dl className="sysstatus-stats">
          {rows.map(([k, v]) => (
            <div className="sysstatus-stat" key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

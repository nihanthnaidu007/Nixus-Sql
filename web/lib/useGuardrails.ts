"use client";

/**
 * The guardrails manifest, for the W2 target-aware surfaces (badge, pills,
 * starters). The endpoint is a STATIC settings read — zero DB access, zero
 * tokens — so a fetch per mounted surface (the same pattern GuardrailChips
 * uses) is fine and keeps test isolation simple (no module-level cache that
 * could leak one test's stub into the next).
 *
 * `targetGroup` maps the manifest's target_database name to the corpus group
 * a surface should speak in: the sample SaaS schema, the Chinook sample, or
 * an unknown target (a BYO database — the only honest set there is one that
 * works on ANY relational schema).
 */

import { useEffect, useState } from "react";

import { fetchGuardrails, type GuardrailsManifest } from "./api";

export type TargetGroup = "saas" | "chinook" | "unknown";

export function targetGroup(target: string | null | undefined): TargetGroup {
  if (!target) return "unknown";
  const t = target.toLowerCase();
  if (t.includes("chinook")) return "chinook";
  if (t.includes("saas")) return "saas";
  return "unknown";
}

export function useGuardrails(): {
  manifest: GuardrailsManifest | null;
  loaded: boolean;
} {
  const [state, setState] = useState<{
    manifest: GuardrailsManifest | null;
    loaded: boolean;
  }>({ manifest: null, loaded: false });

  useEffect(() => {
    let live = true;
    void fetchGuardrails().then((m) => {
      if (live) setState({ manifest: m, loaded: true });
    });
    return () => {
      live = false;
    };
  }, []);

  return state;
}

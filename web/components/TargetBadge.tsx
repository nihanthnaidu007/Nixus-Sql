"use client";

/**
 * TargetBadge — W2 N1: the active target's database identity, always visible
 * on the query surface. Fed by the STATIC guardrails manifest (target_database
 * — the settings URL's path segment, never credentials or host).
 *
 * Honest absence: when no target name is known (unset, BYO, or the manifest
 * unavailable) the badge omits itself rather than invent or guess a name.
 */

import { useGuardrails } from "@/lib/useGuardrails";

export function TargetBadge() {
  const { manifest } = useGuardrails();
  const target = manifest?.target_database;
  if (!target) return null;
  return (
    <span
      className="target-badge"
      title="The database this instance queries (from the static guardrails manifest)"
    >
      <span className="target-badge-label">Active database</span>
      <span className="target-badge-name">{target}</span>
    </span>
  );
}

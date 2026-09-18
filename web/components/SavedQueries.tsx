"use client";

/**
 * Saved queries (Phase 2 W1 D2/D4): the persistent panel.
 *
 * Lists the saved queries the backend stores in the STATE database, runs one
 * by sending its saved natural-language question back through the normal
 * pipeline (never the stored SQL — the backend enforces this and the UI
 * reflects it), deletes immediately on click (no confirmation step — a deleted
 * query can be recreated by saving the result again), and saves the CURRENT
 * result (question + SQL) with a name and optional tags. Duplicate names,
 * rejected saves, and network failures surface as readable inline messages.
 */

import { useCallback, useEffect, useState } from "react";
import {
  createSavedQuery,
  deleteSavedQuery,
  fetchSavedQueries,
  type SavedQuery,
} from "@/lib/api";
import type { ChartOverride } from "./ResultView";

export interface SaveDraft {
  naturalLanguage: string;
  generatedSql: string;
  /** W2 N5 — the chart-type override active when this draft was made. When
   *  set (non-auto), it persists with the saved query via the EXISTING
   *  parameters JSON — no schema change — so re-running reproduces the chart. */
  chartOverride?: ChartOverride;
}

/** The parameters key the chart override rides under (W2 N5). */
const CHART_OVERRIDE_KEY = "chart_override";
const PERSISTABLE_OVERRIDES: readonly ChartOverride[] = [
  "bar",
  "line",
  "pie",
  "scatter",
];

/** A persisted override from a saved query's parameters JSON, or null. Only
 *  the four concrete chart types count — "auto" is never persisted. */
export function savedChartOverride(q: SavedQuery): ChartOverride | null {
  const value = q.parameters?.[CHART_OVERRIDE_KEY];
  return typeof value === "string" &&
    (PERSISTABLE_OVERRIDES as readonly string[]).includes(value)
    ? (value as ChartOverride)
    : null;
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** The "save the current result" form — shown only when a grounded answer is
 *  on screen. Tags are comma-separated; the backend normalizes them. */
function SaveForm({
  draft,
  onSaved,
  onCancel,
}: {
  draft: SaveDraft;
  onSaved: (q: SavedQuery) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const nameTrimmed = name.trim();
    if (!nameTrimmed || saving) return;
    setSaving(true);
    setError(null);
    try {
      const tagList = tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const saved = await createSavedQuery({
        name: nameTrimmed,
        natural_language: draft.naturalLanguage,
        generated_sql: draft.generatedSql,
        description: description.trim() || undefined,
        tags: tagList.length ? tagList : undefined,
        // W2 N5 — persist the chart override when one is active, so the saved
        // query reproduces the chart it was saved with.
        parameters: draft.chartOverride
          ? { [CHART_OVERRIDE_KEY]: draft.chartOverride }
          : undefined,
      });
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="saved-form">
      <div className="saved-form-row">
        <input
          className="saved-input"
          placeholder="Name this query (required)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Saved query name"
          maxLength={200}
        />
      </div>
      <div className="saved-form-row">
        <input
          className="saved-input"
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={500}
        />
      </div>
      <div className="saved-form-row">
        <input
          className="saved-input"
          placeholder="Tags, comma-separated (optional)"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          maxLength={200}
        />
      </div>
      {error && (
        <div className="saved-error" role="alert">
          {error}
        </div>
      )}
      <div className="saved-form-actions">
        <button type="button" className="saved-btn" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="saved-btn saved-btn-primary"
          onClick={submit}
          disabled={saving || !name.trim()}
        >
          {saving ? "Saving…" : "Save query"}
        </button>
      </div>
    </div>
  );
}

export function SavedQueries({
  draft,
  onRun,
}: {
  /** Present only when an ANSWERED result is on screen — enables "save this". */
  draft: SaveDraft | null;
  /** Runs a saved query's natural-language question through the main pipeline.
   *  A persisted chart override (W2 N5) rides along to seed the new result. */
  onRun: (naturalLanguage: string, chartOverride?: ChartOverride) => void;
}) {
  const [items, setItems] = useState<SavedQuery[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      setItems(await fetchSavedQueries());
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function remove(id: number) {
    setDeletingId(id);
    try {
      await deleteSavedQuery(id);
      setItems((prev) => (prev ? prev.filter((q) => q.id !== id) : prev));
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
      await reload();
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <section className="section saved-panel">
      <div className="result-head">
        <span className="label">Saved queries</span>
        {draft && !showForm && (
          <button
            type="button"
            className="sql-edit-toggle"
            onClick={() => setShowForm(true)}
          >
            Save current
          </button>
        )}
      </div>

      {showForm && draft && (
        <SaveForm
          draft={draft}
          onCancel={() => setShowForm(false)}
          onSaved={() => {
            setShowForm(false);
            void reload();
          }}
        />
      )}

      {loadError && (
        <div className="saved-error" role="alert">
          {loadError}
        </div>
      )}

      {items === null ? (
        <div className="saved-empty">Loading saved queries…</div>
      ) : items.length === 0 ? (
        <div className="saved-empty">
          No saved queries yet. Run a question, then use “Save current” to keep
          it — re-running sends the question back through the full pipeline.
        </div>
      ) : (
        <ul className="saved-list">
          {items.map((q) => (
            <li key={q.id} className="saved-item">
              <div className="saved-item-main">
                <span className="saved-name">{q.name}</span>
                <span className="saved-question">{q.natural_language}</span>
                {q.tags.length > 0 && (
                  <span className="saved-tags">
                    {q.tags.map((t) => (
                      <span key={t} className="saved-tag">
                        {t}
                      </span>
                    ))}
                  </span>
                )}
                {/* W2 N5 — the chart the saved query reproduces on re-run. */}
                {savedChartOverride(q) && (
                  <span className="saved-tags">
                    <span className="saved-tag saved-tag-chart">
                      chart · {savedChartOverride(q)}
                    </span>
                  </span>
                )}
                {q.last_run_at && (
                  <span className="saved-lastrun">
                    last run {formatDate(q.last_run_at)}
                  </span>
                )}
              </div>
              <div className="saved-item-actions">
                <button
                  type="button"
                  className="saved-btn"
                  onClick={() => {
                    // One argument when no override is persisted — the panel
                    // callback stays a plain re-ask in the common case.
                    const override = savedChartOverride(q);
                    if (override) onRun(q.natural_language, override);
                    else onRun(q.natural_language);
                  }}
                  disabled={deletingId !== null}
                >
                  Run
                </button>
                <button
                  type="button"
                  className="saved-btn saved-btn-danger"
                  onClick={() => remove(q.id)}
                  disabled={deletingId !== null}
                >
                  {deletingId === q.id ? "Deleting…" : "Delete"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

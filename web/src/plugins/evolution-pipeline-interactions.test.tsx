// @vitest-environment jsdom
import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuditResponse, EvolutionSnapshot, PipelineResponse } from "../../../plugins/evolution/dashboard/src/types";

let root: Root | null = null;

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function installSdk(fetchJSON: ReturnType<typeof vi.fn>) {
  Object.assign(window, {
    __HERMES_PLUGIN_SDK__: {
      React,
      hooks: React,
      fetchJSON,
      components: {
        Badge: () => null, Button: () => null, Checkbox: () => null, Input: () => null,
        Label: () => null, Select: () => null, SelectOption: () => null, Separator: () => null,
      },
      utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
    },
  });
}

function snapshot(): EvolutionSnapshot {
  return {
    schema_version: 1, state: "ready", observed_at: "2026-07-28T10:00:00Z", snapshot_digest: "a".repeat(64), diagnostics: [],
    organism: { id_prefix: "organism", lineage_prefix: "lineage" },
    gnothi: { state: "ready", revision_id: "revision", revision_digest: "b".repeat(64), node_count: 1, edge_count: 0 },
    telos: { state: "ready", active_digest: "c".repeat(64), revision_count: 1 },
    observer: { state: "ready", enabled: true, last_scan_at: null, observation_count: 1 },
    generations: { state: "ready", active_generation_id: null, generation_count: 0 },
    pipeline: { state: "ready", suggestions: { total: 1, by_state: { eligible: 1 }, truncated: false }, blueprints: { total: 0, by_state: {}, truncated: false }, lifecycle: { pending_approval_count: 0, decision_count: 0 } },
  };
}

const pipeline: PipelineResponse = {
  schema_version: 1, state: "ready", attempt_id: "attempt-1",
  attempts: [{ attempt_id: "attempt-1", source_kind: "observer", state: "complete", created_at: "2026-07-28T10:00:00Z" }],
  total_attempts: 1, attempts_truncated: false,
  suggestions: [{ suggestion_id: "suggestion-1", suggestion_digest: "d".repeat(64), state: "eligible", score: 0.91, telos_alignment: 0.86, observation_count: 4, distinct_session_count: 3, summary: "A bounded local summary.", created_at: "2026-07-28T10:00:00Z", updated_at: "2026-07-28T10:00:00Z" }],
  suggestion_counts: { eligible: 1 }, total_suggestions: 1, suggestions_truncated: false,
  blueprints: [], total_blueprints: 0, blueprints_truncated: false,
  stages: [{ id: "suggestion", available: true }, { id: "research", available: true }, { id: "blueprint", available: true }, { id: "build", available: false }, { id: "canary", available: false }, { id: "promotion", available: false }, { id: "stable", available: false }],
  mutable_actions: [],
};

const audit: AuditResponse = { schema_version: 1, state: "ready", events: [], total_events: 0, truncated: false, next_after: 0, mutable_actions: [] };

async function renderPipeline(writeText: ReturnType<typeof vi.fn>) {
  Object.assign(navigator, { clipboard: { writeText } });
  const fetchJSON = vi.fn((path: string) => Promise.resolve(path.includes("/audit") ? audit : pipeline));
  installSdk(fetchJSON);
  const { PipelineView } = await import("../../../plugins/evolution/dashboard/src/components/PipelineView");
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<PipelineView snapshot={snapshot()} onRefresh={vi.fn().mockResolvedValue(undefined)} />);
    await Promise.resolve();
    await Promise.resolve();
  });
}

function researchButton(): HTMLButtonElement {
  const element = [...document.querySelectorAll("button")].find(item => item.textContent === "Research public documentation");
  if (!(element instanceof HTMLButtonElement)) throw new Error("Missing research button");
  return element;
}

afterEach(() => {
  root?.unmount();
  root = null;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("Pipeline research handoff", () => {
  it("shows the copied brief confirmation before navigating to Chat", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    await renderPipeline(writeText);

    await act(async () => { researchButton().click(); await Promise.resolve(); });

    expect(writeText).toHaveBeenCalledOnce();
    expect(document.querySelector("[role=status]")?.textContent).toBe("Research brief copied — paste it in Chat.");
  });

  it("keeps the user on the pipeline when copying the brief fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("Clipboard blocked"));
    await renderPipeline(writeText);

    await act(async () => { researchButton().click(); await Promise.resolve(); });

    expect(document.querySelector("[role=alert]")?.textContent).toBe("Clipboard blocked");
  });

  it("navigates once after the visible delay and lets a later handoff cancel the earlier timer", async () => {
    vi.useFakeTimers();
    const { scheduleResearchHandoff } = await import("../../../plugins/evolution/dashboard/src/components/PipelineView");
    const navigate = vi.fn();
    const first = scheduleResearchHandoff(navigate, "/chat");
    first();
    scheduleResearchHandoff(navigate, "/chat");

    await vi.advanceTimersByTimeAsync(750);
    expect(navigate).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith("/chat");
  });
});

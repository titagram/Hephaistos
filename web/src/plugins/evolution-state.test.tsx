// @vitest-environment jsdom
import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EvolutionJob, EvolutionSnapshot } from "../../../plugins/evolution/dashboard/src/types";
import type { EvolutionSnapshotStore } from "../../../plugins/evolution/dashboard/src/state";

let root: Root | null = null;

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function snapshot(): EvolutionSnapshot {
  return {
    schema_version: 1,
    state: "ready",
    observed_at: "2026-07-28T10:00:00Z",
    snapshot_digest: "a".repeat(64),
    diagnostics: [],
    organism: { id_prefix: "organism", lineage_prefix: "lineage" },
    gnothi: { state: "ready", revision_id: "revision", revision_digest: "b".repeat(64), node_count: 1, edge_count: 0, coverage: { current_domains: 4, total_domains: 4, unknown_domains: [], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false } },
    telos: { state: "ready", active_digest_prefix: "c".repeat(12), revision_summary: { parent_digest_prefix: null, purpose: "Operate safely.", desired_trait_count: 1, capability_direction_count: 1, priority_count: 1, prohibition_count: 1, success_indicator_count: 1 } },
    observer: { state: "ready", enabled: true, circuit_open: false, degraded_reason: null },
    generations: { state: "ready", active_generation_prefix: null, last_known_good_generation_prefix: null, overlay_enabled: false },
    pipeline: { state: "ready", suggestions: { total: 0, by_state: {}, truncated: false }, blueprints: { total: 0, by_state: {}, truncated: false }, lifecycle: { pending_approval_count: 0, decision_count: 0 } },
  };
}

function job(state: EvolutionJob["state"]): EvolutionJob {
  return {
    job_id: "00000000-0000-4000-8000-000000000000",
    kind: "observer_scan",
    state,
    progress: 48,
    created_at: "2026-07-28T10:00:00Z",
    started_at: "2026-07-28T10:00:01Z",
    finished_at: state === "queued" || state === "running" ? null : "2026-07-28T10:00:04Z",
    process_nonce: "nonce",
    result: null,
    error_code: state === "failed" ? "collector_failed" : null,
  };
}

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

async function renderStore(fetchJSON: ReturnType<typeof vi.fn>) {
  installSdk(fetchJSON);
  const { useEvolutionSnapshot } = await import("../../../plugins/evolution/dashboard/src/state");
  let store = null as unknown as EvolutionSnapshotStore;

  function Harness() {
    store = useEvolutionSnapshot();
    return <output data-job-state={store.activeJob?.state ?? "none"} />;
  }

  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<Harness />);
    await Promise.resolve();
  });
  return { container, store };
}

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.resetModules();
});

describe("Evolution job polling", () => {
  it("polls an active job immediately once and then only on the three-second cadence", async () => {
    vi.useFakeTimers();
    let jobReads = 0;
    const fetchJSON = vi.fn((path: string) => {
      if (path.endsWith("/snapshot")) return Promise.resolve(snapshot());
      if (path.includes("/jobs/")) {
        jobReads += 1;
        return jobReads === 1 ? Promise.resolve({ ...job("running") }) : new Promise(() => {});
      }
      return Promise.resolve({});
    });
    const { store } = await renderStore(fetchJSON);

    await act(async () => {
      store.trackJob(job("running"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(jobReads).toBe(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(2_999); });
    expect(jobReads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(jobReads).toBe(2);
  });

  it("refreshes the snapshot and keeps a completed job visible without another poll", async () => {
    vi.useFakeTimers();
    const fetchJSON = vi.fn((path: string) => {
      if (path.endsWith("/snapshot")) return Promise.resolve(snapshot());
      if (path.includes("/jobs/")) return Promise.resolve(job("completed"));
      return Promise.resolve({});
    });
    const { container, store } = await renderStore(fetchJSON);
    const snapshotsBeforeCompletion = fetchJSON.mock.calls.filter(([path]) => path.endsWith("/snapshot")).length;

    await act(async () => {
      store.trackJob(job("running"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector("output")?.getAttribute("data-job-state")).toBe("completed");
    expect(fetchJSON.mock.calls.filter(([path]) => path.endsWith("/snapshot")).length).toBe(snapshotsBeforeCompletion + 1);
    const jobReads = fetchJSON.mock.calls.filter(([path]) => path.includes("/jobs/")).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(fetchJSON.mock.calls.filter(([path]) => path.includes("/jobs/")).length).toBe(jobReads);
  });

  it.each(["failed", "cancelled", "unknown"] as const)("keeps a terminal %s job status visible without polling again", async state => {
    vi.useFakeTimers();
    const fetchJSON = vi.fn((path: string) => {
      if (path.endsWith("/snapshot")) return Promise.resolve(snapshot());
      if (path.includes("/jobs/")) return Promise.resolve(job(state));
      return Promise.resolve({});
    });
    const { container, store } = await renderStore(fetchJSON);

    await act(async () => {
      store.trackJob(job("running"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector("output")?.getAttribute("data-job-state")).toBe(state);
    const jobReads = fetchJSON.mock.calls.filter(([path]) => path.includes("/jobs/")).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(fetchJSON.mock.calls.filter(([path]) => path.includes("/jobs/")).length).toBe(jobReads);
  });
});

import { describe, expect, it } from "vitest";
import {
  availableActions,
  coveragePresentation,
  initialView,
  observerControl,
  overviewPrimaryAction,
  priorityBlockerLinks,
  scanJobProgress,
  organismFacet,
  readinessBlockers,
  snapshotAfterRefreshFailure,
  warningForRefreshFailure,
} from "../../../plugins/evolution/dashboard/src/view-model";
import type { EvolutionSnapshot } from "../../../plugins/evolution/dashboard/src/types";

function snapshot(overrides: Partial<EvolutionSnapshot> = {}): EvolutionSnapshot {
  return {
    schema_version: 1,
    state: "ready",
    observed_at: "2026-07-28T10:00:00Z",
    snapshot_digest: "a".repeat(64),
    organism: { id_prefix: "organism", lineage_prefix: "lineage" },
    gnothi: {
      state: "ready",
      revision_id: "rev-1",
      revision_digest: "b".repeat(64),
      node_count: 2,
      edge_count: 1,
      coverage: { current_domains: 4, total_domains: 4, unknown_domains: [], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false },
    },
    telos: { state: "ready", active_digest_prefix: "c".repeat(12), revision_summary: { parent_digest_prefix: null, purpose: "Operate safely.", desired_trait_count: 1, capability_direction_count: 1, priority_count: 1, prohibition_count: 1, success_indicator_count: 1 } },
    observer: {
      state: "ready",
      enabled: true,
      circuit_open: false,
      degraded_reason: null,
    },
    generations: { state: "ready", active_generation_prefix: null, last_known_good_generation_prefix: null, overlay_enabled: false },
    pipeline: {
      state: "ready",
      suggestions: { total: 0, by_state: {}, truncated: false },
      blueprints: { total: 0, by_state: {}, truncated: false },
      lifecycle: { pending_approval_count: 0, decision_count: 0 },
    },
    diagnostics: [],
    ...overrides,
  };
}

describe("Evolution dashboard view model", () => {
  it("opens on the organism graph", () => {
    expect(initialView()).toBe("organism");
  });

  it("orders readiness blockers from the most serious state to the least", () => {
    const blockers = readinessBlockers(
      snapshot({
        state: "blocked",
        gnothi: {
          state: "stale",
          revision_id: "rev-1",
          revision_digest: "b".repeat(64),
          node_count: 2,
          edge_count: 1,
          coverage: { current_domains: 3, total_domains: 4, unknown_domains: ["runtime"], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false },
        },
        observer: { state: "degraded", enabled: true, circuit_open: true, degraded_reason: "circuit_open" },
      }),
    );

    expect(blockers.map(blocker => blocker.source)).toEqual([
      "snapshot",
      "observer",
      "gnothi",
    ]);
  });

  it("retains the last valid snapshot when a refresh fails", () => {
    const lastGood = snapshot();

    expect(snapshotAfterRefreshFailure(lastGood)).toBe(lastGood);
  });

  it("requires a manual refresh after a conflict and never schedules a retry", () => {
    const warning = warningForRefreshFailure(new Error("409: snapshot_changed"));

    expect(warning).toMatchObject({ code: "refresh_required", retryable: false });
  });

  it("keeps the local organism identity when the dashboard profile facet changes", () => {
    const local = snapshot({ organism: { id_prefix: "local-123", lineage_prefix: "lineage" } });

    expect(organismFacet(local, "work")).toEqual({
      label: "Local organism · all profiles",
      organism: { id_prefix: "local-123", lineage_prefix: "lineage" },
    });
  });

  it("exposes only initialization for a missing organism and no mutations for corruption", () => {
    expect(availableActions(snapshot({ state: "missing", organism: null }))).toEqual([
      "initialize",
    ]);
    expect(availableActions(snapshot({ state: "corrupt", organism: null }))).toEqual([]);
  });

  it("links priority blockers to the view where they can be investigated", () => {
    const links = priorityBlockerLinks(snapshot({
      state: "partial",
      gnothi: { state: "stale", revision_id: "rev-1", revision_digest: "b".repeat(64), node_count: 2, edge_count: 1, coverage: { current_domains: 3, total_domains: 4, unknown_domains: ["runtime"], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false } },
      telos: { state: "missing", active_digest_prefix: null },
    }));

    expect(links).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "gnothi", view: "organism" }),
      expect.objectContaining({ source: "telos", view: "telos" }),
    ]));
  });

  it("assigns every public readiness state a total blocker priority", () => {
    const states = ["ready", "not_ready", "missing", "paused", "stale", "partial", "degraded", "blocked", "corrupt"] as const;
    const blockers = readinessBlockers(snapshot({
      observer: { state: "degraded", enabled: true, circuit_open: true, degraded_reason: "circuit_open" },
      pipeline: {
        state: "not_ready",
        suggestions: { total: 0, by_state: {}, truncated: false },
        blueprints: { total: 0, by_state: {}, truncated: false },
        lifecycle: { pending_approval_count: 0, decision_count: 0 },
      },
    }));

    expect(states).toContain(blockers[0]?.state);
    expect(blockers.map(blocker => blocker.state)).toContain("degraded");
    expect(blockers.map(blocker => blocker.state)).toContain("not_ready");
  });

  it("describes coverage with visible text and an icon, never color alone", () => {
    expect(coveragePresentation(snapshot().gnothi)).toEqual({
      icon: "✓",
      text: "Graph coverage ready",
    });
    expect(coveragePresentation(snapshot({
      gnothi: { state: "partial", revision_id: "rev-1", revision_digest: "b".repeat(64), node_count: 2, edge_count: 1, coverage: { current_domains: 3, total_domains: 4, unknown_domains: ["runtime"], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false } },
    }).gnothi)).toEqual({
      icon: "!",
      text: "Graph coverage partial",
    });
  });

  it("offers pause and resume according to the observer's current enabled state", () => {
    expect(observerControl(snapshot({ observer: { state: "ready", enabled: true, circuit_open: false, degraded_reason: null } }))).toEqual({
      action: "pause",
      label: "Pause observer",
    });
    expect(observerControl(snapshot({ observer: { state: "paused", enabled: false, circuit_open: false, degraded_reason: null } }))).toEqual({
      action: "resume",
      label: "Resume observer",
    });
  });

  it("reports observer scans as a tracked job with readable progress", () => {
    expect(scanJobProgress({
      job_id: "job-1", kind: "observer_scan", state: "running", progress: 42,
      created_at: "2026-07-28T10:00:00Z", started_at: "2026-07-28T10:00:01Z", finished_at: null,
      process_nonce: "nonce", result: null, error_code: null,
    })).toBe("Observer scan running (42%)");
  });

  it("selects exactly one primary overview action and suppresses all mutations for corrupt diagnostics", () => {
    expect(overviewPrimaryAction(snapshot())).toEqual({ action: "scan", label: "Run observer scan" });
    expect(overviewPrimaryAction(snapshot({
      observer: { state: "paused", enabled: false, circuit_open: false, degraded_reason: null },
    }))).toEqual({ action: "resume", label: "Resume observer" });
    expect(overviewPrimaryAction(snapshot({ state: "corrupt", diagnostics: ["digest mismatch"] }))).toBeNull();
  });
});

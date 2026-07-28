import { describe, expect, it } from "vitest";
import {
  availableActions,
  initialView,
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
    },
    telos: { state: "ready", active_digest: "c".repeat(64), revision_count: 1 },
    observer: {
      state: "ready",
      enabled: true,
      last_scan_at: null,
      observation_count: 0,
    },
    generations: { state: "ready", active_generation_id: null, generation_count: 0 },
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
  it("opens on the organism view", () => {
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
        },
        telos: { state: "partial", active_digest: null, revision_count: 0 },
      }),
    );

    expect(blockers.map(blocker => blocker.source)).toEqual([
      "snapshot",
      "telos",
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
});

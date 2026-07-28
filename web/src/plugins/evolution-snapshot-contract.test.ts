import { describe, expect, it } from "vitest";

import type { EvolutionSnapshot } from "../../../plugins/evolution/dashboard/src/types";

const dashboardSnapshot: EvolutionSnapshot = {
  schema_version: 1,
  state: "ready",
  observed_at: "2026-07-28T10:00:00Z",
  snapshot_digest: "a".repeat(64),
  organism: { id_prefix: "organism", lineage_prefix: "lineage" },
  gnothi: {
    state: "partial",
    revision_id: "revision-1",
    revision_digest: "b".repeat(64),
    node_count: 12,
    edge_count: 18,
    coverage: {
      current_domains: 3,
      total_domains: 4,
      unknown_domains: ["runtime"],
      truncated: false,
      drifted_domains: [],
      drift_truncated: false,
      collector_status: [{ name: "runtime", status: "partial" }],
      collector_status_truncated: false,
    },
  },
  telos: {
    state: "ready",
    active_digest_prefix: "c".repeat(12),
    revision_summary: {
      parent_digest_prefix: null,
      purpose: "Operate safely.",
      desired_trait_count: 1,
      capability_direction_count: 1,
      priority_count: 1,
      prohibition_count: 1,
      success_indicator_count: 1,
    },
  },
  observer: {
    state: "degraded",
    enabled: true,
    circuit_open: true,
    degraded_reason: "circuit_open",
  },
  generations: {
    state: "stale",
    active_generation_prefix: "d".repeat(12),
    last_known_good_generation_prefix: "e".repeat(12),
    overlay_enabled: true,
  },
  pipeline: {
    state: "not_ready",
    suggestions: { total: 0, by_state: {}, truncated: false },
    blueprints: { total: 0, by_state: {}, truncated: false },
    lifecycle: { pending_approval_count: 0, decision_count: 0 },
  },
  diagnostics: [],
};

describe("Evolution snapshot API contract", () => {
  it("models the nested service response including paused/degraded observer states and prefix summaries", () => {
    expect(dashboardSnapshot.observer).toMatchObject({ state: "degraded", circuit_open: true });
    expect(dashboardSnapshot.telos.active_digest_prefix).toHaveLength(12);
    expect(dashboardSnapshot.generations.overlay_enabled).toBe(true);
  });
});

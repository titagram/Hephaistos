import { describe, expect, it } from "vitest";
import type { RevisionDiffResponse } from "../../../plugins/evolution/dashboard/src/types";

// This fixture mirrors the public Python `revision_diff` response. Keeping it
// typed at the consumer boundary prevents structured rows regressing to text.
const revisionDiffApiFixture: RevisionDiffResponse = {
  schema_version: 1,
  left_revision_id: "rev-before",
  right_revision_id: "rev-after",
  added_capabilities: [
    {
      id: "capability:alpha",
      kind: "capability",
      label: "Alpha",
      owner_class: "core",
      generation_scope: "stable",
      state: { available: true },
      evidence_refs: ["evidence:alpha"],
    },
  ],
  removed_capabilities: [],
  changed_state: [
    {
      id: "capability:beta",
      before: { available: false },
      after: { available: true },
    },
  ],
  dependency_changes: [
    { kind: "depends_on", from: "capability:alpha", to: "capability:beta" },
  ],
  invariant_impact: [],
  runtime_changes: [],
  quality_changes: [{ before: "partial", after: "ready" }],
  coverage_changes: [
    { domain: "capabilities", before: "partial", after: "current" },
  ],
  truncated: false,
};

describe("RevisionDiffResponse", () => {
  it("accepts the structured rows returned by the revision diff API", () => {
    expect(revisionDiffApiFixture.changed_state[0]?.after.available).toBe(true);
  });
});

import { describe, expect, it } from "vitest";
import {
  blueprintAction,
  fixedPipelineStages,
  publicResearchBrief,
  sortedAuditEvents,
} from "../../../plugins/evolution/dashboard/src/pipeline-model";
import type { AuditEvent, PipelineBlueprint, PipelineResponse, PipelineSuggestion } from "../../../plugins/evolution/dashboard/src/types";

const suggestion = (state = "eligible"): PipelineSuggestion => ({
  suggestion_id: "suggestion-123",
  suggestion_digest: "a".repeat(64),
  state,
  score: 0.91,
  telos_alignment: 0.86,
  observation_count: 4,
  distinct_session_count: 3,
  public_research_topic: "Local capability improvement",
  summary: "Users repeatedly request a practical capability summary.",
  created_at: "2026-07-28T10:00:00Z",
  updated_at: "2026-07-28T10:01:00Z",
});

const blueprint = (): PipelineBlueprint => ({
  blueprint_id: "blueprint-123",
  attempt_id: "attempt-123",
  canonical_digest: "b".repeat(64),
  state: "draft",
  created_at: "2026-07-28T10:02:00Z",
  suggestion_id: "suggestion-123",
  active_telos_digest: "c".repeat(64),
  summary: "Users repeatedly request a practical capability summary.",
  capability_hypothesis: "Address observer opportunity: Users repeatedly request a practical capability summary.",
  proposed_component_classes: ["skill"],
});

const pipeline = (overrides: Partial<PipelineResponse> = {}): PipelineResponse => ({
  schema_version: 1,
  state: "ready",
  attempt_id: "attempt-123",
  attempts: [{ attempt_id: "attempt-123", source_kind: "observer", state: "complete", created_at: "2026-07-28T10:00:00Z" }],
  total_attempts: 1,
  attempts_truncated: false,
  suggestions: [suggestion()],
  suggestion_counts: { eligible: 1 },
  total_suggestions: 1,
  suggestions_truncated: false,
  blueprints: [],
  total_blueprints: 0,
  blueprints_truncated: false,
  stages: [
    { id: "suggestion", available: true }, { id: "research", available: true }, { id: "blueprint", available: true },
    { id: "build", available: false }, { id: "canary", available: false }, { id: "promotion", available: false }, { id: "stable", available: false },
  ],
  mutable_actions: [],
  ...overrides,
});

const event = (sequence: number): AuditEvent => ({
  sequence,
  event_id: `event-${sequence}`,
  attempt_id: "attempt-123",
  generation_id: null,
  event_type: "blueprint_created",
  prior_state: "eligible",
  next_state: "draft",
  actor: "local",
  reason_code: "observer",
  summary: `Event ${sequence}`,
  created_at: "2026-07-28T10:00:00Z",
  event_digest: "d".repeat(64),
});

describe("Evolution pipeline model", () => {
  it("keeps the fixed stage order and makes unsupported stages descriptive rather than actionable", () => {
    const stages = fixedPipelineStages(pipeline());

    expect(stages.map(stage => stage.id)).toEqual([
      "suggestion", "research", "blueprint", "build", "canary", "promotion", "stable",
    ]);
    expect(stages.slice(3)).toEqual(expect.arrayContaining([
      expect.objectContaining({ available: false, explanation: expect.stringMatching(/not available/i) }),
    ]));
  });

  it("offers immutable blueprint creation only for an eligible suggestion", () => {
    expect(blueprintAction(suggestion(), [])).toEqual({ kind: "create", message: "Create immutable blueprint" });
    expect(blueprintAction(suggestion("observing"), [])).toEqual(expect.objectContaining({ kind: "blocked", message: expect.stringMatching(/eligible/i) }));
  });

  it("displays the existing immutable blueprint instead of proposing a duplicate", () => {
    expect(blueprintAction(suggestion(), [blueprint()])).toEqual({
      kind: "existing",
      blueprint: blueprint(),
      message: "Immutable blueprint already exists",
    });
  });

  it("builds a clipboard-only public research brief with its safe server topic but without evidence, paths, logs, memory, prompts, or authorization", () => {
    const brief = publicResearchBrief({
      ...suggestion(),
      summary: "private /Users/example/log.txt memory prompt artifact",
    });

    expect(brief).toMatchObject({ destination: "/chat", toast: "Research brief copied — paste it in Chat.", authorizationEndpoint: null });
    expect(brief.text).toContain("Topic: Local capability improvement");
    expect(brief.text).toContain("Score: 0.91");
    expect(brief.text).toContain("Telos alignment: 0.86");
    expect(brief.text).not.toMatch(/Users|log|memory|prompt|artifact|evidence|private/i);
  });

  it("orders audit events by their append-only sequence", () => {
    expect(sortedAuditEvents([event(3), event(1), event(2)]).map(item => item.sequence)).toEqual([1, 2, 3]);
  });
});

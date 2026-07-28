import type { AuditEvent, PipelineBlueprint, PipelineResponse, PipelineStageId, PipelineSuggestion } from "./types";

export interface PipelineStagePresentation {
  id: PipelineStageId;
  label: string;
  available: boolean;
  explanation: string;
}

export type BlueprintAction =
  | { kind: "create"; message: "Create immutable blueprint" }
  | { kind: "blocked"; message: string }
  | { kind: "existing"; message: "Immutable blueprint already exists"; blueprint: PipelineBlueprint };

export interface PublicResearchBrief {
  text: string;
  destination: "/chat";
  toast: "Research brief copied — paste it in Chat.";
  /** Research is a client handoff, not an Evolution authorization request. */
  authorizationEndpoint: null;
}

const STAGES: ReadonlyArray<Pick<PipelineStagePresentation, "id" | "label">> = [
  { id: "suggestion", label: "Suggestion" },
  { id: "research", label: "Research" },
  { id: "blueprint", label: "Blueprint" },
  { id: "build", label: "Build" },
  { id: "canary", label: "Canary" },
  { id: "promotion", label: "Promotion" },
  { id: "stable", label: "Stable" },
];

export function fixedPipelineStages(pipeline: Pick<PipelineResponse, "stages">): PipelineStagePresentation[] {
  const availability = new Map(pipeline.stages.map(stage => [stage.id, stage.available]));
  return STAGES.map(stage => {
    const available = availability.get(stage.id) === true;
    return {
      ...stage,
      available,
      explanation: available
        ? `${stage.label} is available in the local evolution pipeline.`
        : `${stage.label} is not available until a local runtime owns this stage.`,
    };
  });
}

export function blueprintAction(
  suggestion: PipelineSuggestion,
  blueprints: readonly PipelineBlueprint[],
): BlueprintAction {
  const existing = blueprints.find(blueprint => blueprint.suggestion_id === suggestion.suggestion_id);
  if (existing !== undefined) {
    return { kind: "existing", message: "Immutable blueprint already exists", blueprint: existing };
  }
  if (suggestion.state !== "eligible") {
    return { kind: "blocked", message: "Blueprint creation requires an eligible suggestion." };
  }
  return { kind: "create", message: "Create immutable blueprint" };
}

function decimal(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : "0.00";
}

export function publicResearchBrief(suggestion: PipelineSuggestion): PublicResearchBrief {
  // This deliberately leaves out the summary and every evidence-bearing field.
  // The server's public projection is still rendered locally, but the research
  // handoff contains only stable numeric classification facts.
  return {
    text: [
      "Research public documentation for a local evolution opportunity.",
      `Score: ${decimal(suggestion.score)}`,
      `Telos alignment: ${decimal(suggestion.telos_alignment)}`,
      `Observed sessions: ${Math.max(0, suggestion.distinct_session_count)}`,
      `Observation count: ${Math.max(0, suggestion.observation_count)}`,
      "Use public documentation only.",
    ].join("\n"),
    destination: "/chat",
    toast: "Research brief copied — paste it in Chat.",
    authorizationEndpoint: null,
  };
}

export function sortedAuditEvents(events: readonly AuditEvent[]): AuditEvent[] {
  return [...events].sort((left, right) => left.sequence - right.sequence);
}

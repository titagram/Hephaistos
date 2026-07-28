export type HealthState =
  | "missing"
  | "ready"
  | "partial"
  | "stale"
  | "blocked"
  | "corrupt";

export type EvolutionView = "overview" | "organism" | "telos" | "pipeline";
export type PipelineStageId =
  | "suggestion"
  | "research"
  | "blueprint"
  | "build"
  | "canary"
  | "promotion"
  | "stable";
export type JobKind = "organism_rebuild" | "observer_scan" | "revision_diff";
export type JobState =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "unknown";

export interface StateCount {
  total: number | null;
  by_state: Record<string, number>;
  truncated: boolean;
}

export interface GnothiSummary {
  state: HealthState | "not_ready";
  revision_id: string | null;
  revision_digest: string | null;
  node_count: number | null;
  edge_count: number | null;
}

export interface TelosSummary {
  state: HealthState | "not_ready";
  active_digest: string | null;
  revision_count: number | null;
}

export interface ObserverSummary {
  state: HealthState | "not_ready";
  enabled: boolean;
  last_scan_at: string | null;
  observation_count: number | null;
}

export interface GenerationSummary {
  state: HealthState | "not_ready";
  active_generation_id: string | null;
  generation_count: number | null;
}

export interface PipelineSummary {
  state: HealthState | "not_ready";
  suggestions: StateCount;
  blueprints: StateCount;
  lifecycle: {
    pending_approval_count: number | null;
    decision_count: number | null;
  };
}

export interface EvolutionSnapshot {
  schema_version: 1;
  state: HealthState;
  observed_at: string;
  snapshot_digest: string;
  organism: { id_prefix: string; lineage_prefix: string } | null;
  gnothi: GnothiSummary;
  telos: TelosSummary;
  observer: ObserverSummary;
  generations: GenerationSummary;
  pipeline: PipelineSummary;
  diagnostics: string[];
}

export interface MutationContext {
  organism_id: string;
  expected_snapshot_digest: string;
}

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  owner_class: string;
  generation_scope: string;
  state: Record<string, boolean>;
  evidence_refs: string[];
}

export interface GraphEdge {
  id: string;
  kind: string;
  from: string;
  to: string;
  evidence_refs: string[];
}

export interface GraphResponse {
  schema_version: 1;
  revision_id: string | null;
  revision_digest: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  blockers: GraphNode[];
  total_nodes: number;
  total_edges: number;
  truncated: boolean;
}

export interface RevisionSummary {
  revision_id: string;
  revision_digest: string;
  collected_at: string;
  status: string;
  node_count: number;
  edge_count: number;
}

export interface RevisionListResponse {
  schema_version: 1;
  items: RevisionSummary[];
  total_revisions: number;
  truncated: boolean;
}

export interface RevisionDiffResponse {
  schema_version: 1;
  left_revision_id: string;
  right_revision_id: string;
  added_capabilities: string[];
  removed_capabilities: string[];
  changed_state: string[];
  dependency_changes: string[];
  invariant_impact: string[];
  runtime_changes: string[];
  quality_changes: string[];
  coverage_changes: string[];
  truncated: boolean;
}

export interface TelosItem {
  id: string;
  statement: string;
  tags: string[];
  priority: number;
}

export interface TelosRevision {
  digest: string;
  parent_digest: string | null;
  purpose: string;
  desired_traits: TelosItem[];
  capability_directions: TelosItem[];
  priorities: TelosItem[];
  tradeoffs: TelosItem[];
  prohibitions: TelosItem[];
  proactivity_policy: TelosItem;
  success_indicators: TelosItem[];
}

export interface TelosResponse {
  schema_version: 1;
  state: HealthState;
  active_digest: string | null;
  active_revision: TelosRevision | null;
  history: TelosRevision[];
  total_revisions: number | null;
  truncated: boolean;
}

export interface PipelineAttempt {
  attempt_id: string;
  source_kind: string;
  state: string;
  created_at: string;
}

export interface PipelineSuggestion {
  suggestion_id: string;
  suggestion_digest: string;
  state: string;
  score: number;
  telos_alignment: number;
  observation_count: number;
  distinct_session_count: number;
  summary: string;
  created_at: string;
  updated_at: string;
}

export interface PipelineBlueprint {
  blueprint_id: string;
  attempt_id: string;
  canonical_digest: string;
  state: string;
  created_at: string;
  suggestion_id: string;
  active_telos_digest: string;
  summary: string;
  capability_hypothesis: string;
  proposed_component_classes: string[];
}

export interface PipelineStage {
  id: PipelineStageId;
  available: boolean;
}

export interface PipelineResponse {
  schema_version: 1;
  state: HealthState | "not_ready";
  attempt_id: string | null;
  attempts: PipelineAttempt[];
  total_attempts: number | null;
  attempts_truncated: boolean;
  suggestions: PipelineSuggestion[];
  suggestion_counts: Record<string, number>;
  total_suggestions: number | null;
  suggestions_truncated: boolean;
  blueprints: PipelineBlueprint[];
  total_blueprints: number | null;
  blueprints_truncated: boolean;
  stages: PipelineStage[];
  mutable_actions: [];
}

export interface AuditEvent {
  sequence: number;
  event_id: string;
  attempt_id: string | null;
  generation_id: string | null;
  event_type: string;
  prior_state: string | null;
  next_state: string | null;
  actor: string;
  reason_code: string;
  summary: string;
  created_at: string;
  event_digest: string;
}

export interface AuditResponse {
  schema_version: 1;
  state: HealthState | "not_ready";
  events: AuditEvent[];
  total_events: number | null;
  truncated: boolean;
  next_after: number;
  mutable_actions: [];
}

export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface EvolutionJob {
  job_id: string;
  kind: JobKind;
  state: JobState;
  progress: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  process_nonce: string;
  result: Record<string, JsonValue> | null;
  error_code: string | null;
}

export interface InitializeResponse {
  organism_id: string;
  snapshot: EvolutionSnapshot;
}

export interface ObserverResponse {
  enabled: boolean;
  snapshot: EvolutionSnapshot;
}

export interface TelosDraftResponse {
  digest: string;
  state: "saved";
}

export interface TelosTransitionPreparation {
  confirmation_id: string;
  display_nonce: string;
  organism_id: string;
  current_digest: string;
  target_digest: string;
  action: "activate" | "rollback";
  expires_at: string;
  required_phrase: string;
}

export interface TelosTransitionConfirmation {
  status: "approved";
}

export interface BlueprintResponse {
  status: string;
  blueprint_id: string;
  attempt_id: string;
  canonical_digest: string;
}

export interface EvolutionApiError {
  code: string;
}

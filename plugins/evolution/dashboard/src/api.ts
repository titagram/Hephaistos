import { SDK } from "./sdk";
import type {
  AuditResponse,
  BlueprintResponse,
  EvolutionJob,
  EvolutionSnapshot,
  GraphResponse,
  InitializeResponse,
  MutationContext,
  ObserverResponse,
  PipelineResponse,
  RevisionDiffResponse,
  RevisionListResponse,
  TelosDocument,
  TelosDraftResponse,
  TelosResponse,
  TelosTransitionConfirmation,
  TelosTransitionPreparation,
} from "./types";

const BASE = "/api/plugins/evolution";

export type EvolutionCollector =
  | "source"
  | "capabilities"
  | "runtime"
  | "contracts"
  | "dependencies"
  | "experience";

export interface GraphQuery {
  rootId?: string;
  depth?: number;
  limit?: number;
  kinds?: readonly string[];
  search?: string;
  expectedRevision?: string;
}

export interface RebuildRequest extends MutationContext {
  force: boolean;
  collectors: EvolutionCollector[];
}

export interface ObserverToggleRequest extends MutationContext {
  enabled: boolean;
}

export interface TelosDraftRequest extends MutationContext {
  document: TelosDocument;
}

export interface TelosTransitionRequest extends MutationContext {
  current_digest: string;
  target_digest: string;
  action: "activate" | "rollback";
}

export interface TelosTransitionConfirmationRequest extends TelosTransitionRequest {
  confirmation_id: string;
  phrase: string;
}

export interface BlueprintRequest extends MutationContext {
  expected_suggestion_digest: string;
}

function getQuery(values: Record<string, string | number | undefined>): string {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) parameters.set(key, String(value));
  }
  const query = parameters.toString();
  return query === "" ? "" : `?${query}`;
}

function graphQuery(query: GraphQuery): string {
  const parameters = new URLSearchParams();
  if (query.rootId !== undefined) parameters.set("root_id", query.rootId);
  if (query.depth !== undefined) parameters.set("depth", String(query.depth));
  if (query.limit !== undefined) parameters.set("limit", String(query.limit));
  for (const kind of query.kinds ?? []) parameters.append("kind", kind);
  if (query.search !== undefined) parameters.set("search", query.search);
  if (query.expectedRevision !== undefined) parameters.set("expected_revision", query.expectedRevision);
  const encoded = parameters.toString();
  return encoded === "" ? "" : `?${encoded}`;
}

function mutate<T>(path: string, body: unknown): Promise<T> {
  return SDK.fetchJSON<T>(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const evolutionApi = {
  snapshot: (): Promise<EvolutionSnapshot> => SDK.fetchJSON(`${BASE}/snapshot`),
  mutationContext: (): Promise<MutationContext> => SDK.fetchJSON(`${BASE}/mutation-context`),
  graph: (query: GraphQuery = {}): Promise<GraphResponse> =>
    SDK.fetchJSON(`${BASE}/graph${graphQuery(query)}`),
  revisions: (limit?: number): Promise<RevisionListResponse> =>
    SDK.fetchJSON(`${BASE}/revisions${getQuery({ limit })}`),
  diff: (left: string, right: string): Promise<RevisionDiffResponse> =>
    SDK.fetchJSON(`${BASE}/diff${getQuery({ left, right })}`),
  telos: (historyLimit?: number): Promise<TelosResponse> =>
    SDK.fetchJSON(`${BASE}/telos${getQuery({ history_limit: historyLimit })}`),
  pipeline: (attemptId?: string, limit?: number): Promise<PipelineResponse> =>
    SDK.fetchJSON(`${BASE}/pipeline${getQuery({ attempt_id: attemptId, limit })}`),
  audit: (after?: number, limit?: number): Promise<AuditResponse> =>
    SDK.fetchJSON(`${BASE}/audit${getQuery({ after, limit })}`),
  job: (jobId: string): Promise<EvolutionJob> => SDK.fetchJSON(`${BASE}/jobs/${jobId}`),
  initialize: (): Promise<InitializeResponse> => mutate("/initialize", {}),
  rebuild: (request: RebuildRequest): Promise<EvolutionJob> => mutate("/jobs/organism-rebuild", request),
  observerScan: (context: MutationContext): Promise<EvolutionJob> => mutate("/jobs/observer-scan", context),
  setObserver: (request: ObserverToggleRequest): Promise<ObserverResponse> => mutate("/observer", request),
  saveTelosDraft: (request: TelosDraftRequest): Promise<TelosDraftResponse> => mutate("/telos/drafts", request),
  prepareTelosTransition: (request: TelosTransitionRequest): Promise<TelosTransitionPreparation> =>
    mutate("/telos/transitions/prepare", request),
  confirmTelosTransition: (request: TelosTransitionConfirmationRequest): Promise<TelosTransitionConfirmation> =>
    mutate("/telos/transitions/confirm", request),
  createBlueprint: (suggestionId: string, request: BlueprintRequest): Promise<BlueprintResponse> =>
    mutate(`/suggestions/${suggestionId}/blueprint`, request),
};

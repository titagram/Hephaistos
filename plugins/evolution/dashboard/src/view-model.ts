import type {
  EvolutionSnapshot,
  EvolutionJob,
  GnothiSummary,
  HealthState,
  JobState,
  PipelineSummary,
} from "./types";

export interface ReadinessBlocker {
  source: "snapshot" | "gnothi" | "telos" | "observer" | "generations" | "pipeline";
  state: HealthState | "not_ready";
  label: string;
}

export interface RefreshWarning {
  code: "refresh_required" | "refresh_failed";
  message: string;
  retryable: boolean;
}

export interface OrganismFacet {
  label: "Local organism · all profiles";
  organism: EvolutionSnapshot["organism"];
}

export type SnapshotAction = "initialize";
export type OverviewMutationAction = "scan" | "pause" | "resume";

export interface OverviewAction {
  action: SnapshotAction | OverviewMutationAction;
  label: string;
}

export interface CoveragePresentation {
  icon: "✓" | "!" | "×";
  text: string;
}

export interface PriorityBlockerLink extends ReadinessBlocker {
  view: EvolutionView;
}

const BLOCKER_PRIORITY: Record<HealthState | "not_ready", number> = {
  corrupt: 6,
  blocked: 5,
  partial: 4,
  stale: 3,
  missing: 2,
  not_ready: 1,
  ready: 0,
};

const BLOCKER_LABELS: Record<ReadinessBlocker["source"], string> = {
  snapshot: "Overall organism state",
  gnothi: "Organism graph",
  telos: "Telos",
  observer: "Observer",
  generations: "Generations",
  pipeline: "Pipeline",
};

const BLOCKER_ORDER: ReadinessBlocker["source"][] = [
  "snapshot",
  "gnothi",
  "telos",
  "observer",
  "generations",
  "pipeline",
];

function isNonReady(state: HealthState | "not_ready"): boolean {
  return state !== "ready";
}

function stateOfPipeline(pipeline: PipelineSummary): HealthState | "not_ready" {
  return pipeline.state;
}

export function initialView(): EvolutionView {
  return "overview";
}

export function readinessBlockers(snapshot: EvolutionSnapshot): ReadinessBlocker[] {
  const sources: Array<[ReadinessBlocker["source"], HealthState | "not_ready"]> = [
    ["snapshot", snapshot.state],
    ["gnothi", snapshot.gnothi.state],
    ["telos", snapshot.telos.state],
    ["observer", snapshot.observer.state],
    ["generations", snapshot.generations.state],
    ["pipeline", stateOfPipeline(snapshot.pipeline)],
  ];

  return sources
    .filter(([, state]) => isNonReady(state))
    .map(([source, state]) => ({ source, state, label: BLOCKER_LABELS[source] }))
    .sort((left, right) => {
      const priority = BLOCKER_PRIORITY[right.state] - BLOCKER_PRIORITY[left.state];
      return priority !== 0
        ? priority
        : BLOCKER_ORDER.indexOf(left.source) - BLOCKER_ORDER.indexOf(right.source);
    });
}

export function snapshotAfterRefreshFailure(
  lastValid: EvolutionSnapshot | null,
): EvolutionSnapshot | null {
  return lastValid;
}

function statusFromError(error: unknown): number | null {
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = Reflect.get(error, "status");
    return typeof status === "number" ? status : null;
  }
  if (error instanceof Error) {
    const match = /^(\d{3})(?::|\s|$)/.exec(error.message);
    return match === null ? null : Number(match[1]);
  }
  return null;
}

export function warningForRefreshFailure(error: unknown): RefreshWarning {
  if (statusFromError(error) === 409) {
    return {
      code: "refresh_required",
      message: "The organism changed elsewhere. Refresh manually before continuing.",
      retryable: false,
    };
  }
  return {
    code: "refresh_failed",
    message: "The latest snapshot could not be loaded. The last valid snapshot remains visible.",
    retryable: true,
  };
}

export function organismFacet(
  snapshot: EvolutionSnapshot,
  _profile: string | null | undefined,
): OrganismFacet {
  return {
    label: "Local organism · all profiles",
    organism: snapshot.organism,
  };
}

export function availableActions(snapshot: EvolutionSnapshot): SnapshotAction[] {
  return snapshot.state === "missing" ? ["initialize"] : [];
}

type EvolutionView = "overview" | "organism" | "telos" | "pipeline";

const BLOCKER_VIEWS: Record<ReadinessBlocker["source"], EvolutionView> = {
  snapshot: "overview",
  gnothi: "organism",
  telos: "telos",
  observer: "overview",
  generations: "pipeline",
  pipeline: "pipeline",
};

function hasCorruption(snapshot: EvolutionSnapshot): boolean {
  return snapshot.state === "corrupt"
    || snapshot.gnothi.state === "corrupt"
    || snapshot.telos.state === "corrupt"
    || snapshot.observer.state === "corrupt"
    || snapshot.generations.state === "corrupt"
    || snapshot.pipeline.state === "corrupt";
}

export function priorityBlockerLinks(snapshot: EvolutionSnapshot): PriorityBlockerLink[] {
  return readinessBlockers(snapshot).map(blocker => ({
    ...blocker,
    view: BLOCKER_VIEWS[blocker.source],
  }));
}

export function coveragePresentation(summary: GnothiSummary): CoveragePresentation {
  if (summary.state === "ready") return { icon: "✓", text: "Graph coverage ready" };
  if (summary.state === "corrupt" || summary.state === "blocked" || summary.state === "missing") {
    return { icon: "×", text: `Graph coverage ${summary.state}` };
  }
  return { icon: "!", text: `Graph coverage ${summary.state}` };
}

export function observerControl(snapshot: EvolutionSnapshot): OverviewAction | null {
  if (hasCorruption(snapshot) || snapshot.state === "blocked") return null;
  return snapshot.observer.enabled
    ? { action: "pause", label: "Pause observer" }
    : { action: "resume", label: "Resume observer" };
}

export function overviewPrimaryAction(snapshot: EvolutionSnapshot): OverviewAction | null {
  if (hasCorruption(snapshot) || snapshot.state === "blocked") return null;
  if (snapshot.state === "missing") return { action: "initialize", label: "Initialize local organism" };
  if (!snapshot.observer.enabled) return { action: "resume", label: "Resume observer" };
  return { action: "scan", label: "Run observer scan" };
}

export function scanJobProgress(job: EvolutionJob): string {
  return `Observer scan ${job.state} (${job.progress}%)`;
}

export function isActiveJobState(state: JobState): boolean {
  return state === "queued" || state === "running";
}

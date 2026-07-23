interface SkippedFile {
  path: string;
  bytes: number | null;
  reason: string;
}

interface PlanFile {
  path: string;
  kind: "source" | "test" | "generated" | "docs";
  hunks: Array<{ newStart: number; newEnd: number }>;
  addedRanges?: Array<{ start: number; end: number }>;
  diffRange?: { startLine: number; endLine: number };
  addedLines: number;
  removedLines: number;
  changedLines: number;
  preLines: number;
  fileLines: number;
  rewriteRatio: number;
  heavy: boolean;
  binary: boolean;
}

interface PlanChunk {
  id: number;
  startLine: number;
  endLine: number;
  lines: number;
  chars: number;
  maxLineChars: number;
  oversized: boolean;
  files: Array<{ path: string; newStart: number; newEnd: number }>;
}

interface DiffPlan {
  diffLines: number;
  diffChars: number;
  srcDiffLines: number;
  testDiffLines: number;
  docsDiffLines: number;
  generatedDiffLines: number;
  files: unknown[];
  chunks: PlanChunk[];
}

interface PlanReport {
  diffLines: number;
  diffChars: number;
  srcDiffLines: number;
  testDiffLines: number;
  docsDiffLines: number;
  generatedDiffLines: number;
  files: PlanFile[];
  chunks: PlanChunk[];
}

export const LITERAL_PATHSPECS: string;
export const PINNED_DIFF_CONFIG: readonly string[];
export const PINNED_DIFF_FLAGS: readonly string[];

export function captureLocalDiff(options: {
  file?: string;
  includeUntracked?: boolean;
}): {
  diff: Buffer;
  untracked: string[];
  skipped: SkippedFile[];
  unbornHead: boolean;
};

export function buildDiffPlan(diff: string, maxChunkLines?: number): DiffPlan;
export function buildPlanReport(
  plan: DiffPlan,
  postImageLines: ((path: string) => number) | null,
): PlanReport;
export function stringifyPlanReport(report: unknown): string;
export function resolveMergeBase(
  remote: string,
  baseRefName: string,
  headRef: string,
  git: {
    fetch(remote: string, ref: string): boolean;
    refExists(ref: string): boolean;
    mergeBase(left: string, right: string): string | null;
  },
): { sha: string | null; baseFetchFailed: boolean };

export interface EfficacyFileEntry {
  path: string;
  kind: string;
}

export interface EfficacyPlan {
  unreachable: string[];
  probes: string[];
  revert: string[];
}

export type ProbeVerdict = "gated" | "inert" | "inconclusive";

export function planTestEfficacy(
  files: EfficacyFileEntry[],
  workspaceGlobs: string[],
): EfficacyPlan;
export function classifyProbeRun(
  exitCode: number,
  stdout: string,
  probes: string[],
  stderr?: string,
): Array<{ file: string; verdict: ProbeVerdict; detail: string }>;
export function safeRmWithin(worktree: string, relPath: string): void;
export function readWorkspaceGlobs(root: string): string[];

export interface WorkspacePackage {
  dir: string;
  name: string;
  scripts: string[];
  deps: string[];
}

export function affectedWorkspaces(
  changedFiles: string[],
  workspaceGlobs: string[],
): string[];
export function buildSetFor(
  affected: string[],
  packages: WorkspacePackage[],
  alsoBuild?: string[],
): string[];
export function hasUnmodeledWorkspaceGlob(globs: string[]): boolean;
export function readRootPackage(root: string): WorkspacePackage | null;
export function readWorkspacePackages(root: string): WorkspacePackage[];

export type RoleId =
  | "0"
  | "1a"
  | "1b"
  | "1c"
  | "2"
  | "3"
  | "4"
  | "5"
  | "6a"
  | "6b"
  | "6c"
  | "7"
  | "test-matrix"
  | "invariant-a"
  | "invariant-b"
  | "invariant-c"
  | "verify"
  | "reverse-audit";

export interface BriefDefinition {
  label: string;
  readsDiff: boolean;
  brief: string;
}

export const BRIEFS: Record<RoleId, BriefDefinition>;

export interface RosterPlan {
  ownerRepo?: unknown;
  chunks?: Array<{ id?: unknown }>;
  files?: Array<{
    path?: unknown;
    kind?: unknown;
    heavy?: unknown;
    removedLines?: unknown;
  }>;
  srcDiffLines?: unknown;
  diffLines?: unknown;
  worktreePath?: unknown;
  prNumber?: unknown;
  untrackedFiles?: unknown;
}

export interface RequiredAgent {
  key: string;
  role: RoleId | "chunk";
  chunk?: number;
  file?: string;
}

export function isTerritoryFanOut(plan: RosterPlan): boolean;
export function requiredAgents(plan: RosterPlan): RequiredAgent[];
export function shellQuotePath(path: string): string;

export function buildChunkAgentPrompt(
  report: object,
  id: number,
  rules?: string,
): string;
export function buildChunkLaunchPrompt(
  report: object,
  id: number,
  briefFile: string,
): string;
export function buildRoleBrief(
  report: object,
  role: RoleId,
  options?: {
    rules?: string;
    file?: string;
    planPath?: string;
    chunk?: number;
  },
): string;
export function buildRoleLaunchPrompt(
  report: object,
  role: RoleId,
  briefFile: string,
  options?: { file?: string; chunk?: number; round?: number },
): string;
export function promptRecordDir(planPath: string): string;
export function briefPath(planPath: string, key: string): string;
export function readRecordedPrompts(planPath: string): Map<string, string>;
export function writeBrief(
  planPath: string,
  key: string,
  brief: string,
): string;
export function recordPrompt(
  planPath: string,
  key: string,
  prompt: string,
): void;

export interface CoverageFromTranscripts {
  ok: boolean;
  agents: number;
  blindAgents: string[];
  idleAgents: string[];
  unopenedAgents: string[];
  rewrittenPrompts: string[];
  missingRoles: string[];
  missingRoleSelectors: string[];
  unreadBriefs: string[];
  missingChunks: number[];
  uncoverableChunks: number[];
  coveredChunks: number[];
  disclosures: Array<{ subject: string; reason: string }>;
}

export class TranscriptsUnavailableError extends Error {}
export function coverageFromTranscripts(
  planPath: string,
  env?: NodeJS.ProcessEnv,
): CoverageFromTranscripts;

export interface AgentRecord {
  agentId: string;
  agentName: string;
  launchPrompt: string;
  successfulToolCalls: number;
  diffToolCalls: number;
  diffReads: Array<[number, number]>;
  successfulCallArgs: string[];
  finalText: string;
  mtimeMs: number;
}

export function readTranscripts(
  since?: number,
  env?: NodeJS.ProcessEnv,
  diffPath?: string,
): AgentRecord[];

export interface AnchorResult {
  id: string;
  path: string;
  anchor: string;
  status: "resolved" | "unmatched";
  line?: number;
  startLine?: number;
  matchCount?: number;
  tier?: string;
  ambiguous?: boolean;
  reason?: string;
}

export function resolveAnchors(
  diffText: string,
  requests: Array<{ id: string; path: string; anchor: string }>,
): AnchorResult[];

export type ReviewEvent = "APPROVE" | "REQUEST_CHANGES" | "COMMENT";
export function composeReview(input: {
  criticalsInline?: number;
  suggestionsInline?: number;
  modelId: string;
}): { event: ReviewEvent; baseEvent: ReviewEvent; body: string };

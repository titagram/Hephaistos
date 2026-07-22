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

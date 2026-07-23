import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  lstatSync,
  readFileSync,
  realpathSync,
  rmSync,
} from "node:fs";
import { basename, isAbsolute, join, relative, resolve, sep } from "node:path";

import type { EngineRequest } from "../protocol.js";
import {
  classifyProbeRun,
  planTestEfficacy,
  readWorkspaceGlobs,
  safeRmWithin,
  type ProbeVerdict,
} from "../shims/qwenReviewRuntime.js";
import type {
  DiffPlan,
  RunnerId,
  TestRunner,
  TestRun,
} from "../runners/types.js";
import { PytestRunner } from "../runners/pytest.js";
import { VitestRunner } from "../runners/vitest.js";
import { deniedExecutionResult, parseExecutionPolicy } from "./execution.js";

export type TestVerdict = ProbeVerdict | "unreachable";

export interface TestEfficacyTest {
  path: string;
  verdict: TestVerdict;
  detail: string;
}

export interface TestEfficacyOutput {
  runner: RunnerId | null;
  tests: TestEfficacyTest[];
  unreachable: string[];
  gated: string[];
  inert: string[];
  inconclusive: string[];
  availableRunners: RunnerId[];
  probeWorktreePath: string | null;
  cleanupFailure: string | null;
}

export interface TestEfficacyResult {
  status: "passed" | "failed" | "inconclusive";
  output: TestEfficacyOutput;
  diagnostics: Array<{ code: string; message: string }>;
}

interface TestEfficacyInput {
  planPath: string;
  baseRef: string;
  runner: "auto" | RunnerId;
  timeoutMs: number;
  execution: ReturnType<typeof parseExecutionPolicy>;
}

const asRecord = (value: unknown): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("input must be an object");
  }
  return value as Record<string, unknown>;
};

const within = (root: string, candidate: string): boolean => {
  const rel = relative(root, candidate);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
};

const validatedPlanPath = (request: EngineRequest, raw: unknown): string => {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new TypeError("planPath must be a non-empty string");
  }
  const artifactRoot = realpathSync(request.artifactRoot);
  const path = realpathSync(resolve(raw));
  if (!within(artifactRoot, path)) {
    throw new TypeError("planPath must be inside artifactRoot");
  }
  if (!lstatSync(path).isFile()) throw new TypeError("planPath must be a file");
  return path;
};

const parseInput = (request: EngineRequest): TestEfficacyInput => {
  const input = asRecord(request.input);
  const unknown = Object.keys(input).find(
    (key) =>
      !["planPath", "baseRef", "runner", "timeoutMs", "execution"].includes(
        key,
      ),
  );
  if (unknown !== undefined)
    throw new TypeError(`unknown test-efficacy input field: ${unknown}`);
  const planPath = validatedPlanPath(request, input.planPath);
  if (
    typeof input.baseRef !== "string" ||
    !/^[0-9a-fA-F]{40,64}$/.test(input.baseRef)
  ) {
    throw new TypeError("baseRef must be a full Git object ID");
  }
  if (!(["auto", "vitest", "pytest"] as unknown[]).includes(input.runner)) {
    throw new TypeError("runner must be auto, vitest, or pytest");
  }
  const timeoutMs = input.timeoutMs ?? 300_000;
  if (
    !Number.isSafeInteger(timeoutMs) ||
    (timeoutMs as number) < 1 ||
    (timeoutMs as number) > 600_000
  ) {
    throw new TypeError("timeoutMs must be an integer between 1 and 600000");
  }
  return {
    planPath,
    baseRef: input.baseRef,
    runner: input.runner as TestEfficacyInput["runner"],
    timeoutMs: timeoutMs as number,
    execution: parseExecutionPolicy(input.execution),
  };
};

const parsePlan = (path: string): DiffPlan => {
  const parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
  const record = asRecord(parsed);
  if (!Array.isArray(record.files))
    throw new TypeError("plan.files must be an array");
  const files = record.files.map((entry) => {
    const file = asRecord(entry);
    if (typeof file.path !== "string" || typeof file.kind !== "string") {
      throw new TypeError(
        "every plan file requires string path and kind fields",
      );
    }
    return { path: file.path, kind: file.kind };
  });
  return { ...record, files };
};

const emptyOutput = (availableRunners: RunnerId[]): TestEfficacyOutput => ({
  runner: null,
  tests: [],
  unreachable: [],
  gated: [],
  inert: [],
  inconclusive: [],
  availableRunners,
  probeWorktreePath: null,
  cleanupFailure: null,
});

export async function selectRunner(
  selection: "auto" | RunnerId,
  workspace: string,
  plan: DiffPlan,
  runners: readonly TestRunner[],
): Promise<
  | { runner: TestRunner; available: RunnerId[] }
  | { code: "no_runner" | "ambiguous_runner"; available: RunnerId[] }
> {
  if (selection !== "auto") {
    const runner = runners.find((candidate) => candidate.id === selection);
    if (runner === undefined) return { code: "no_runner", available: [] };
    const detected = await runner.detect(workspace, plan);
    return detected === "no"
      ? { code: "no_runner", available: [] }
      : { runner, available: [runner.id] };
  }
  const detections = await Promise.all(
    runners.map(async (runner) => ({
      runner,
      detection: await runner.detect(workspace, plan),
    })),
  );
  const yes = detections
    .filter(({ detection }) => detection === "yes")
    .map(({ runner }) => runner);
  const ambiguous = detections
    .filter(({ detection }) => detection === "ambiguous")
    .map(({ runner }) => runner);
  const available = [
    ...new Set([...yes, ...ambiguous].map((runner) => runner.id)),
  ];
  if (yes.length === 1 && ambiguous.length === 0)
    return { runner: yes[0]!, available };
  if (yes.length === 0 && ambiguous.length === 0)
    return { code: "no_runner", available };
  return { code: "ambiguous_runner", available };
}

const git = (cwd: string, args: readonly string[]): string =>
  execFileSync("git", [...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
    timeout: 120_000,
    maxBuffer: 64 * 1024 * 1024,
  }).trim();

const existsAtBase = (cwd: string, baseRef: string, path: string): boolean => {
  const result = spawnSync("git", ["cat-file", "-e", `${baseRef}:${path}`], {
    cwd,
    env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
    stdio: "ignore",
    timeout: 120_000,
  });
  if (result.error !== undefined) throw result.error;
  return result.status === 0;
};

const safeRelativePath = (workspace: string, path: string): string => {
  if (path.length === 0 || path.includes("\0") || isAbsolute(path)) {
    throw new Error(`unsafe plan path: ${JSON.stringify(path)}`);
  }
  const normalized = path.split("\\").join("/");
  if (
    normalized.split("/").some((segment) => segment === ".." || segment === ".")
  ) {
    throw new Error(`unsafe plan path segment: ${JSON.stringify(path)}`);
  }
  const target = resolve(workspace, normalized);
  if (!within(resolve(workspace), target)) {
    throw new Error(
      `plan path escapes the probe worktree: ${JSON.stringify(path)}`,
    );
  }
  return normalized;
};

export const probeTreePath = (workspace: string, runId: string): string => {
  const suffix = createHash("sha256")
    .update(`${resolve(workspace)}\0${runId}`)
    .digest("hex")
    .slice(0, 16);
  return join(workspace, `.hermes-efficacy-${suffix}`);
};

const createProbeTree = (workspace: string, probeTree: string): void => {
  const head = git(workspace, ["rev-parse", "HEAD"]);
  git(workspace, ["worktree", "add", "--detach", probeTree, head]);
};

const revertProduction = (
  probeTree: string,
  baseRef: string,
  revert: readonly string[],
): void => {
  const modified: string[] = [];
  const added: string[] = [];
  for (const rawPath of revert) {
    const path = safeRelativePath(probeTree, rawPath);
    if (existsAtBase(probeTree, baseRef, path)) {
      modified.push(path);
    } else {
      added.push(path);
    }
  }
  if (modified.length > 0)
    git(probeTree, ["checkout", baseRef, "--", ...modified]);
  for (const path of added) safeRmWithin(probeTree, path);
};

export const cleanupProbeTree = (
  workspace: string,
  probeTree: string,
): string | null => {
  if (
    !within(resolve(workspace), resolve(probeTree)) ||
    !probeTree.includes(".hermes-efficacy-")
  ) {
    return "refusing to clean an unrecognized probe worktree";
  }
  let failure: string | null = null;
  try {
    git(workspace, ["worktree", "remove", "--force", probeTree]);
  } catch (cause) {
    failure = cause instanceof Error ? cause.message : String(cause);
  }
  try {
    if (existsSync(probeTree))
      rmSync(probeTree, { recursive: true, force: true });
    git(workspace, ["worktree", "prune"]);
  } catch (cause) {
    failure ??= cause instanceof Error ? cause.message : String(cause);
  }
  return existsSync(probeTree)
    ? (failure ?? `could not remove ${probeTree}`)
    : null;
};

const inconclusiveForRun = (
  file: string,
  run: TestRun,
  phase: string,
): TestEfficacyTest => ({
  path: file,
  verdict: "inconclusive",
  detail: run.timedOut
    ? `${phase} timed out after ${run.durationMs}ms`
    : `${phase} could not run${run.error ? `: ${run.error}` : ""}`,
});

const group = (tests: TestEfficacyTest[], verdict: TestVerdict): string[] =>
  tests.filter((test) => test.verdict === verdict).map((test) => test.path);

export async function runTestEfficacy(
  request: EngineRequest,
  runners?: readonly TestRunner[],
): Promise<TestEfficacyResult> {
  const input = parseInput(request);
  if (!input.execution.allowed) {
    return {
      ...deniedExecutionResult(input.execution),
      output: emptyOutput([]),
    };
  }
  if (input.execution.mode === "sandbox") {
    return {
      status: "inconclusive",
      output: emptyOutput([]),
      diagnostics: [
        {
          code: "sandbox_execution_requires_terminal_environment",
          message:
            "sandbox execution must be routed through the configured Hermes terminal environment",
        },
      ],
    };
  }
  const availableRunners = runners ?? [
    new VitestRunner(undefined, input.execution.sanitizedEnv),
    new PytestRunner(undefined, undefined, input.execution.sanitizedEnv),
  ];
  const workspace = realpathSync(request.workspace);
  const plan = parsePlan(input.planPath);
  const choice = await selectRunner(
    input.runner,
    workspace,
    plan,
    availableRunners,
  );
  if ("code" in choice) {
    const output = emptyOutput(choice.available);
    const choices =
      choice.available.length > 0
        ? choice.available.map((id) => `runner=${id}`).join(", ")
        : "vitest or pytest";
    return {
      status: "inconclusive",
      output,
      diagnostics: [
        {
          code: choice.code,
          message:
            choice.code === "ambiguous_runner"
              ? `multiple test runners apply; retry explicitly with ${choices}`
              : `no applicable test runner was found; retry explicitly with ${choices}`,
        },
      ],
    };
  }

  const runner = choice.runner;
  const workspaceGlobs = readWorkspaceGlobs(workspace);
  const upstreamPlan = planTestEfficacy(plan.files, workspaceGlobs);
  const collectionIsAuthoritative =
    runner.id === "pytest" || workspaceGlobs.length === 0;
  const rootTests = plan.files
    .filter((file) => file.kind === "test")
    .map((file) => file.path);
  const planned = collectionIsAuthoritative
    ? {
        unreachable: [],
        probes: upstreamPlan.revert.length > 0 ? rootTests : [],
        revert: upstreamPlan.revert,
      }
    : upstreamPlan;
  const tests: TestEfficacyTest[] = planned.unreachable.map((path) => ({
    path,
    verdict: "unreachable",
    detail: "the changed test is outside every npm workspace",
  }));
  const scheduled = new Set([...planned.unreachable, ...planned.probes]);
  for (const file of plan.files) {
    if (file.kind === "test" && !scheduled.has(file.path)) {
      tests.push({
        path: file.path,
        verdict: "inconclusive",
        detail:
          "the diff has no production source change to revert, so test efficacy cannot be probed",
      });
    }
  }
  let actualProbeTree: string | null = null;
  let cleanupFailure: string | null = null;
  let probes: string[] = [];
  const runId = basename(request.artifactRoot);
  if (!runId) throw new TypeError("artifactRoot has no run identity");

  try {
    if (planned.probes.length > 0) {
      actualProbeTree = probeTreePath(workspace, runId);
      if (existsSync(actualProbeTree)) {
        const retainedCleanup = cleanupProbeTree(workspace, actualProbeTree);
        if (retainedCleanup !== null) {
          throw new Error(`retained probe cleanup failed: ${retainedCleanup}`);
        }
      }
      createProbeTree(workspace, actualProbeTree);
    }
    if (planned.probes.length > 0) {
      let collected: Set<string>;
      try {
        collected = await runner.collectedFiles(actualProbeTree ?? workspace);
      } catch (cause) {
        const detail = `test collection failed: ${cause instanceof Error ? cause.message : String(cause)}`;
        tests.push(
          ...planned.probes.map((path) => ({
            path,
            verdict: "inconclusive" as const,
            detail,
          })),
        );
        probes = [];
        collected = new Set();
      }
      if (tests.length === planned.unreachable.length) {
        probes = planned.probes.filter((path) => {
          if (collected.has(path)) return true;
          tests.push({
            path,
            verdict: "unreachable",
            detail: `${runner.id} did not collect the changed test file`,
          });
          return false;
        });
      }
    }

    const baselinePassed: string[] = [];
    for (const file of probes) {
      const run = await runner.runFile(
        actualProbeTree ?? workspace,
        file,
        input.timeoutMs,
      );
      if (run.timedOut || run.error || run.exitCode === null) {
        tests.push(inconclusiveForRun(file, run, "baseline test run"));
      } else if (run.exitCode !== 0) {
        tests.push({
          path: file,
          verdict: "inconclusive",
          detail: "the changed test does not pass before the production revert",
        });
      } else {
        baselinePassed.push(file);
      }
    }

    if (baselinePassed.length > 0 && planned.revert.length > 0) {
      if (actualProbeTree === null)
        throw new Error("probe worktree was not created");
      revertProduction(actualProbeTree, input.baseRef, planned.revert);
      for (const file of baselinePassed) {
        const run = await runner.runFile(
          actualProbeTree,
          file,
          input.timeoutMs,
        );
        if (run.timedOut || run.error || run.exitCode === null) {
          tests.push(inconclusiveForRun(file, run, "revert probe"));
          continue;
        }
        const classifierStdout = run.structured
          ? JSON.stringify({
              testResults:
                run.structured.outcome === "passed" ||
                run.structured.outcome === "assertion_failed"
                  ? [
                      {
                        name: file,
                        assertionResults: [
                          ...Array.from(
                            { length: run.structured.failedAssertions },
                            () => ({ status: "failed" }),
                          ),
                          ...Array.from(
                            { length: run.structured.passed },
                            () => ({ status: "passed" }),
                          ),
                        ],
                      },
                    ]
                  : [],
            })
          : run.stdout;
        const classified = classifyProbeRun(
          run.exitCode,
          classifierStdout,
          [file],
          run.stderr,
        )[0];
        tests.push({
          path: file,
          verdict: classified?.verdict ?? "inconclusive",
          detail:
            classified?.detail ??
            "the revert probe returned no file classification",
        });
      }
    }
  } catch (cause) {
    const classified = new Set(tests.map((test) => test.path));
    for (const file of planned.probes) {
      if (!classified.has(file)) {
        tests.push({
          path: file,
          verdict: "inconclusive",
          detail: `probe could not run: ${cause instanceof Error ? cause.message : String(cause)}`,
        });
      }
    }
  } finally {
    if (actualProbeTree !== null)
      cleanupFailure = cleanupProbeTree(workspace, actualProbeTree);
  }

  if (cleanupFailure !== null) {
    for (const test of tests) {
      if (test.verdict !== "unreachable") {
        test.verdict = "inconclusive";
        test.detail = `probe cleanup failed: ${cleanupFailure}`;
      }
    }
  }
  const output: TestEfficacyOutput = {
    runner: runner.id,
    tests,
    unreachable: group(tests, "unreachable"),
    gated: group(tests, "gated"),
    inert: group(tests, "inert"),
    inconclusive: group(tests, "inconclusive"),
    availableRunners: choice.available,
    probeWorktreePath: actualProbeTree,
    cleanupFailure,
  };
  const status =
    output.inconclusive.length > 0
      ? "inconclusive"
      : output.unreachable.length > 0 || output.inert.length > 0
        ? "failed"
        : "passed";
  return {
    status,
    output,
    diagnostics:
      cleanupFailure === null
        ? []
        : [{ code: "cleanup_failed", message: cleanupFailure }],
  };
}

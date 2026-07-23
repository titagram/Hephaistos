import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { execFileSync } from "node:child_process";

import type { EngineRequest } from "../protocol.js";
import { removeWorktree } from "./capture-target.js";
import { cleanupProbeTree, probeTreePath } from "./test-efficacy.js";

export interface CleanupOutput {
  runId: string;
  removedWorktrees: string[];
  recoveryCommand: string | null;
}

const RUN_ID_RE = /^[A-Za-z0-9_-]{16,128}$/;
const gitEnvironment = {
  PATH: process.env.PATH,
  GIT_TERMINAL_PROMPT: "0",
};

const record = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

const repositoryRoot = (workspace: string): string =>
  realpathSync(
    execFileSync("git", ["rev-parse", "--show-toplevel"], {
      cwd: workspace,
      encoding: "utf8",
      env: gitEnvironment,
      shell: false,
    }).trim(),
  );

const expectedCaptureWorktree = (repoRoot: string, runId: string): string => {
  const suffix = createHash("sha256")
    .update(`${repoRoot}\0${runId}`)
    .digest("hex")
    .slice(0, 16);
  return join(dirname(repoRoot), `.hermes-review-${suffix}`);
};

export async function cleanupRun(
  request: EngineRequest,
): Promise<CleanupOutput> {
  const input = record(request.input, "cleanup input");
  if (Object.keys(input).some((key) => key !== "runId")) {
    throw new TypeError("cleanup accepts only runId");
  }
  if (typeof input.runId !== "string" || !RUN_ID_RE.test(input.runId)) {
    throw new TypeError("cleanup runId is invalid");
  }
  const runId = input.runId;
  const artifactRoot = realpathSync(request.artifactRoot);
  if (basename(artifactRoot) !== runId) {
    throw new TypeError("cleanup runId does not match artifactRoot");
  }
  const planPath = join(artifactRoot, "plan.json");
  const planStat = lstatSync(planPath);
  if (!planStat.isFile() || planStat.isSymbolicLink()) {
    throw new TypeError("registered plan is not a regular file");
  }
  const plan = record(
    JSON.parse(readFileSync(planPath, "utf8")) as unknown,
    "plan",
  );
  const hermes = record(plan.hermes, "plan.hermes");
  if (hermes.runId !== runId) {
    throw new TypeError("registered plan run identity does not match");
  }

  const workspace = realpathSync(request.workspace);
  const removedWorktrees: string[] = [];
  const failures: string[] = [];
  const targetKind = hermes.targetKind;
  const registered = hermes.worktreePath;
  if (targetKind === "range" || targetKind === "pr") {
    const repoRoot = repositoryRoot(workspace);
    const expected = expectedCaptureWorktree(repoRoot, runId);
    if (registered !== expected) {
      throw new TypeError(
        "registered capture worktree identity does not match",
      );
    }
    try {
      const existed = existsSync(expected);
      removeWorktree(expected);
      execFileSync("git", ["worktree", "prune"], {
        cwd: repoRoot,
        env: gitEnvironment,
        shell: false,
      });
      const registered = execFileSync(
        "git",
        ["worktree", "list", "--porcelain"],
        {
          cwd: repoRoot,
          encoding: "utf8",
          env: gitEnvironment,
          shell: false,
        },
      );
      if (registered.includes(`worktree ${expected}\n`)) {
        throw new Error("capture worktree lease remains registered");
      }
      if (existed && !existsSync(expected)) removedWorktrees.push(expected);
    } catch (cause) {
      failures.push(cause instanceof Error ? cause.message : String(cause));
    }
  } else if (registered !== null) {
    throw new TypeError(
      "non-isolated target registered an unexpected worktree",
    );
  }

  const probe = probeTreePath(workspace, runId);
  if (existsSync(probe)) {
    const failure = cleanupProbeTree(workspace, probe);
    if (failure === null) removedWorktrees.push(probe);
    else failures.push(failure);
  }

  if (failures.length > 0) {
    throw new Error(failures.join("; "));
  }
  return { runId, removedWorktrees, recoveryCommand: null };
}

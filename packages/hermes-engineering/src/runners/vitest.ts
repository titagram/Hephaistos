import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";

import type { DiffPlan, ProcessRunner, TestRun, TestRunner } from "./types.js";
import { NodeProcessRunner } from "./types.js";

const CONFIG_NAMES = [
  "vitest.config.ts",
  "vitest.config.js",
  "vitest.config.mts",
  "vitest.config.mjs",
];

const within = (root: string, candidate: string): boolean => {
  const rel = relative(root, candidate);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
};

const manifestSignalsVitest = (workspace: string): boolean => {
  try {
    const parsed = JSON.parse(
      readFileSync(resolve(workspace, "package.json"), "utf8"),
    ) as {
      scripts?: Record<string, unknown>;
      dependencies?: Record<string, unknown>;
      devDependencies?: Record<string, unknown>;
    };
    if ("vitest" in (parsed.dependencies ?? {})) return true;
    if ("vitest" in (parsed.devDependencies ?? {})) return true;
    return Object.values(parsed.scripts ?? {}).some(
      (script) =>
        typeof script === "string" && /(^|\s)vitest(?:\s|$)/.test(script),
    );
  } catch {
    return false;
  }
};

const planSignalsVitest = (workspace: string, plan: DiffPlan): boolean => {
  const root = resolve(workspace);
  for (const file of plan.files) {
    if (file.kind !== "test" || isAbsolute(file.path)) continue;
    const absolute = resolve(root, file.path);
    if (!within(root, absolute)) continue;
    let cursor = dirname(absolute);
    for (;;) {
      if (
        CONFIG_NAMES.some((name) => existsSync(resolve(cursor, name))) ||
        manifestSignalsVitest(cursor)
      ) {
        return true;
      }
      if (cursor === root) break;
      const parent = dirname(cursor);
      if (parent === cursor || !within(root, parent)) break;
      cursor = parent;
    }
  }
  return false;
};

const resolveVitestModule = (workspace: string): string | null => {
  let cursor = resolve(workspace);
  for (;;) {
    const candidate = resolve(cursor, "node_modules/vitest/vitest.mjs");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(cursor);
    if (parent === cursor) return null;
    cursor = parent;
  }
};

const parseCollectedFiles = (
  workspace: string,
  stdout: string,
): Set<string> => {
  const parsed = JSON.parse(stdout) as unknown;
  if (!Array.isArray(parsed))
    throw new Error("Vitest collection output is not an array");
  const files = new Set<string>();
  for (const entry of parsed) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry))
      continue;
    const file = (entry as { file?: unknown }).file;
    if (typeof file !== "string") continue;
    const absolute = resolve(workspace, file);
    if (!within(resolve(workspace), absolute)) continue;
    files.add(relative(resolve(workspace), absolute).split(sep).join("/"));
  }
  return files;
};

export class VitestRunner implements TestRunner {
  readonly id = "vitest" as const;

  constructor(
    private readonly processes: ProcessRunner = new NodeProcessRunner(),
    private readonly environment: NodeJS.ProcessEnv = process.env,
  ) {}

  async detect(workspace: string, plan: DiffPlan): Promise<"yes" | "no"> {
    const configured = CONFIG_NAMES.some((name) =>
      existsSync(resolve(workspace, name)),
    );
    return configured ||
      manifestSignalsVitest(workspace) ||
      planSignalsVitest(workspace, plan)
      ? "yes"
      : "no";
  }

  async collectedFiles(workspace: string): Promise<Set<string>> {
    const modulePath = resolveVitestModule(workspace);
    if (modulePath === null)
      throw new Error("a local Vitest installation was not found");
    const run = await this.processes.run(
      {
        executable: process.execPath,
        args: [
          modulePath,
          "list",
          "--filesOnly",
          "--configLoader=runner",
          "--no-cache",
          "--json",
        ],
        cwd: workspace,
        env: { ...this.environment, CI: "1", NO_COLOR: "1" },
      },
      60_000,
    );
    if (run.timedOut) throw new Error("Vitest collection timed out");
    if (run.error)
      throw new Error(`Vitest collection could not start: ${run.error}`);
    if (run.exitCode !== 0) {
      throw new Error(`Vitest collection failed: ${run.stderr.trim()}`);
    }
    return parseCollectedFiles(workspace, run.stdout);
  }

  async runFile(
    workspace: string,
    relativePath: string,
    timeoutMs: number,
  ): Promise<TestRun> {
    if (isAbsolute(relativePath))
      throw new Error("test path must be repository-relative");
    const target = resolve(workspace, relativePath);
    if (!within(resolve(workspace), target)) {
      throw new Error("test path escapes the workspace");
    }
    if (
      existsSync(target) &&
      !within(resolve(workspace), realpathSync(target))
    ) {
      throw new Error("test path resolves outside the workspace");
    }
    const modulePath = resolveVitestModule(workspace);
    if (modulePath === null) {
      return {
        exitCode: null,
        stdout: "",
        stderr: "",
        timedOut: false,
        durationMs: 0,
        error: "a local Vitest installation was not found",
      };
    }
    return await this.processes.run(
      {
        executable: process.execPath,
        args: [
          modulePath,
          "run",
          "--reporter=json",
          "--configLoader=runner",
          "--no-cache",
          relativePath,
        ],
        cwd: workspace,
        env: { ...this.environment, CI: "1", NO_COLOR: "1" },
      },
      timeoutMs,
    );
  }
}

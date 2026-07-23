import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";

import type { EngineRequest } from "../protocol.js";
import {
  affectedWorkspaces,
  buildSetFor,
  classifyProbeRun,
  hasUnmodeledWorkspaceGlob,
  readRootPackage,
  readWorkspaceGlobs,
  readWorkspacePackages,
} from "../shims/qwenReviewRuntime.js";
import {
  NodeProcessRunner,
  type ProcessRunner,
  type TestRun,
} from "../runners/types.js";
import { deniedExecutionResult, parseExecutionPolicy } from "./execution.js";

type BuildTestStatus = "passed" | "failed" | "inconclusive";
type BuildTestPhase = "build" | "test";
type PackageManager = "npm" | "pnpm" | "yarn" | "bun";

interface RecordedCommand {
  phase: BuildTestPhase;
  executable: PackageManager;
  args: string[];
  cwd: string;
  testFiles: string[];
}

export interface RecordedBuildTestPlan {
  packageManager: PackageManager;
  commands: Array<{
    phase: BuildTestPhase;
    executable: PackageManager;
    args: string[];
    cwd: string;
    testFiles?: string[];
  }>;
}

export interface BuildTestCommandOutput {
  phase: BuildTestPhase;
  executable: PackageManager;
  args: string[];
  cwd: string;
  exitCode: number | null;
  timedOut: boolean;
  durationMs: number;
  outcome: BuildTestStatus;
  detail: string;
  output: string;
}

export interface BuildTestOutput {
  packageManager: PackageManager | null;
  commands: BuildTestCommandOutput[];
}

export interface BuildTestResult {
  status: BuildTestStatus;
  output: BuildTestOutput;
  diagnostics: Array<{ code: string; message: string }>;
}

interface BuildTestInput {
  planPath: string;
  timeoutMs: number;
  execution: ReturnType<typeof parseExecutionPolicy>;
}

const PACKAGE_MANAGERS = new Set<PackageManager>([
  "npm",
  "pnpm",
  "yarn",
  "bun",
]);
const COMPILE_OR_IMPORT_RE =
  /(?:Cannot find module|Could not resolve|failed to (?:load|resolve)|SyntaxError|TypeError:.*(?:import|export)|TS\d{4}:|Transform failed|RollupError|ERR_MODULE_NOT_FOUND|No test suite found|test suite failed to run)/i;
const OUTPUT_LIMIT = 8_000;

export function discoverBuildTestPlan(
  workspace: string,
  files: readonly { path: string; kind: string }[],
): RecordedBuildTestPlan | null {
  const root = realpathSync(workspace);
  let declaredManager: string | undefined;
  try {
    const manifest = JSON.parse(
      readFileSync(resolve(root, "package.json"), "utf8"),
    ) as {
      packageManager?: unknown;
    };
    if (typeof manifest.packageManager === "string") {
      declaredManager = manifest.packageManager.split("@")[0];
    }
  } catch {
    return null;
  }
  const alternateLock = [
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
  ].some((name) => existsSync(resolve(root, name)));
  if (
    (declaredManager !== undefined && declaredManager !== "npm") ||
    (declaredManager === undefined &&
      alternateLock &&
      !existsSync(resolve(root, "package-lock.json")))
  ) {
    return null;
  }
  const globs = readWorkspaceGlobs(root);
  if (globs.length > 0 && hasUnmodeledWorkspaceGlob(globs)) return null;
  let packages = readWorkspacePackages(root);
  let affected: string[];
  if (globs.length === 0) {
    const rootPackage = readRootPackage(root);
    if (rootPackage === null) return null;
    packages = [rootPackage];
    affected = files.length > 0 ? ["."] : [];
  } else {
    if (packages.length === 0) return null;
    affected = affectedWorkspaces(
      files.map((file) => file.path),
      globs,
    );
  }
  const byDir = new Map(packages.map((entry) => [entry.dir, entry]));
  if (affected.some((dir) => !byDir.has(dir))) return null;
  const commands: RecordedBuildTestPlan["commands"] = [];
  for (const dir of buildSetFor(affected, packages)) {
    if (!byDir.get(dir)?.scripts.includes("build")) continue;
    commands.push({
      phase: "build",
      executable: "npm",
      args:
        dir === "." ? ["run", "build"] : ["run", "build", "--workspace", dir],
      cwd: ".",
    });
  }
  for (const dir of affected) {
    if (!byDir.get(dir)?.scripts.includes("test")) continue;
    const testFiles = files
      .filter((file) => file.kind === "test")
      .map((file) => file.path)
      .filter(
        (path) => dir === "." || path === dir || path.startsWith(`${dir}/`),
      );
    commands.push({
      phase: "test",
      executable: "npm",
      args: dir === "." ? ["run", "test"] : ["run", "test", "--workspace", dir],
      cwd: ".",
      ...(testFiles.length > 0 ? { testFiles } : {}),
    });
  }
  return { packageManager: "npm", commands };
}

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
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

const validatePlanPath = (request: EngineRequest, value: unknown): string => {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError("planPath must be a non-empty string");
  }
  const artifactRoot = realpathSync(request.artifactRoot);
  const planPath = realpathSync(resolve(value));
  if (!within(artifactRoot, planPath))
    throw new TypeError("planPath must be inside artifactRoot");
  if (!lstatSync(planPath).isFile())
    throw new TypeError("planPath must be a file");
  return planPath;
};

const parseInput = (request: EngineRequest): BuildTestInput => {
  const input = asRecord(request.input, "input");
  const unknown = Object.keys(input).find(
    (key) => !["planPath", "timeoutMs", "execution"].includes(key),
  );
  if (unknown !== undefined)
    throw new TypeError(`unknown build-test input field: ${unknown}`);
  const timeoutMs = input.timeoutMs ?? 300_000;
  if (
    !Number.isSafeInteger(timeoutMs) ||
    (timeoutMs as number) < 1 ||
    (timeoutMs as number) > 600_000
  ) {
    throw new TypeError("timeoutMs must be an integer between 1 and 600000");
  }
  return {
    planPath: validatePlanPath(request, input.planPath),
    timeoutMs: timeoutMs as number,
    execution: parseExecutionPolicy(input.execution),
  };
};

const strings = (value: unknown, label: string): string[] => {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new TypeError(`${label} must be an array of strings`);
  }
  if (
    value.length > 64 ||
    value.some((item) => item.length > 4_096 || item.includes("\0"))
  ) {
    throw new TypeError(
      `${label} is too large or contains an invalid argument`,
    );
  }
  return [...value] as string[];
};

const parseRecordedPlan = (
  planPath: string,
  workspace: string,
): { packageManager: PackageManager; commands: RecordedCommand[] } => {
  const plan = asRecord(
    JSON.parse(readFileSync(planPath, "utf8")) as unknown,
    "plan",
  );
  const hermes = asRecord(plan.hermes, "plan.hermes");
  const buildTest = asRecord(hermes.buildTest, "plan.hermes.buildTest");
  if (
    typeof buildTest.packageManager !== "string" ||
    !PACKAGE_MANAGERS.has(buildTest.packageManager as PackageManager)
  ) {
    throw new TypeError(
      "plan.hermes.buildTest.packageManager is not supported",
    );
  }
  if (!Array.isArray(buildTest.commands) || buildTest.commands.length > 64) {
    throw new TypeError(
      "plan.hermes.buildTest.commands must contain at most 64 recorded commands",
    );
  }
  const packageManager = buildTest.packageManager as PackageManager;
  const commands = buildTest.commands.map((raw, index): RecordedCommand => {
    const command = asRecord(raw, `command ${index}`);
    if (command.phase !== "build" && command.phase !== "test") {
      throw new TypeError(`command ${index} phase must be build or test`);
    }
    if (command.executable !== packageManager) {
      throw new TypeError(
        `command ${index} must use the recorded package-manager executable`,
      );
    }
    const args = strings(command.args, `command ${index} args`);
    if (args.length === 0)
      throw new TypeError(`command ${index} has no arguments`);
    if (
      args[0] !== "run" &&
      !(packageManager === "npm" && args[0] === "test")
    ) {
      throw new TypeError(
        `command ${index} must invoke a recorded package script, not an arbitrary executable`,
      );
    }
    if (
      typeof command.cwd !== "string" ||
      command.cwd.length === 0 ||
      isAbsolute(command.cwd)
    ) {
      throw new TypeError(`command ${index} cwd must be repository-relative`);
    }
    const cwd = resolve(workspace, command.cwd);
    if (!within(workspace, cwd))
      throw new TypeError(`command ${index} cwd escapes the workspace`);
    const canonicalCwd = realpathSync(cwd);
    if (
      !within(workspace, canonicalCwd) ||
      !lstatSync(canonicalCwd).isDirectory()
    ) {
      throw new TypeError(`command ${index} cwd must be a workspace directory`);
    }
    const testFiles =
      command.testFiles === undefined
        ? []
        : strings(command.testFiles, `command ${index} testFiles`);
    for (const file of testFiles) {
      if (isAbsolute(file) || !within(workspace, resolve(workspace, file))) {
        throw new TypeError(`command ${index} test file escapes the workspace`);
      }
    }
    return {
      phase: command.phase,
      executable: packageManager,
      args,
      cwd: relative(workspace, canonicalCwd) || ".",
      testFiles,
    };
  });
  return { packageManager, commands };
};

const boundedOutput = (run: TestRun): string => {
  const value = `${run.stdout}${run.stderr}`.trim();
  if (value.length <= OUTPUT_LIMIT) return value;
  return `${value.slice(0, 2_000)}\n... [output truncated] ...\n${value.slice(-6_000)}`;
};

const structuredVitestFiles = (stdout: string): string[] => {
  const start = stdout.indexOf("{");
  if (start < 0) return [];
  try {
    const parsed = JSON.parse(stdout.slice(start)) as {
      testResults?: Array<{ name?: unknown }>;
    };
    if (!Array.isArray(parsed.testResults)) return [];
    return [
      ...new Set(
        parsed.testResults
          .map((result) => result.name)
          .filter(
            (name): name is string =>
              typeof name === "string" && name.length > 0,
          ),
      ),
    ];
  } catch {
    return [];
  }
};

const classifyCommand = (
  command: RecordedCommand,
  run: TestRun,
): { outcome: BuildTestStatus; detail: string } => {
  if (run.timedOut) {
    return {
      outcome: "inconclusive",
      detail: `command timed out after ${run.durationMs}ms`,
    };
  }
  if (run.error || run.exitCode === null) {
    return {
      outcome: "inconclusive",
      detail: `command could not run${run.error ? `: ${run.error}` : ""}`,
    };
  }
  if (run.exitCode === 0)
    return { outcome: "passed", detail: "command exited 0" };
  const combined = `${run.stdout}\n${run.stderr}`;
  if (command.phase === "build") {
    return {
      outcome: "inconclusive",
      detail:
        "the build failed; compilation and infrastructure failures are not a verified test finding",
    };
  }
  const structuredFiles = structuredVitestFiles(run.stdout);
  const evidenceFiles =
    structuredFiles.length > 0 ? structuredFiles : command.testFiles;
  if (evidenceFiles.length > 0) {
    const classified = classifyProbeRun(
      run.exitCode,
      run.stdout,
      evidenceFiles,
      run.stderr,
    );
    if (classified.some((test) => test.verdict === "gated")) {
      return {
        outcome: "failed",
        detail: "one or more test assertions failed",
      };
    }
    return {
      outcome: "inconclusive",
      detail: classified.map((test) => test.detail).join("; "),
    };
  }
  if (COMPILE_OR_IMPORT_RE.test(combined)) {
    return {
      outcome: "inconclusive",
      detail: "test collection, compilation, or import failed",
    };
  }
  return {
    outcome: "inconclusive",
    detail:
      "the test command failed without structured per-file assertion evidence",
  };
};

export async function runBuildTest(
  request: EngineRequest,
  processes: ProcessRunner = new NodeProcessRunner(),
): Promise<BuildTestResult> {
  const input = parseInput(request);
  if (!input.execution.allowed) {
    return {
      ...deniedExecutionResult(input.execution),
      output: { packageManager: null, commands: [] },
    };
  }
  if (input.execution.mode === "sandbox") {
    return {
      status: "inconclusive",
      output: { packageManager: null, commands: [] },
      diagnostics: [
        {
          code: "sandbox_execution_requires_terminal_environment",
          message:
            "sandbox execution must be routed through the configured Hermes terminal environment",
        },
      ],
    };
  }
  const workspace = realpathSync(request.workspace);
  let recorded: ReturnType<typeof parseRecordedPlan>;
  try {
    recorded = parseRecordedPlan(input.planPath, workspace);
  } catch (cause) {
    return {
      status: "inconclusive",
      output: { packageManager: null, commands: [] },
      diagnostics: [
        {
          code: "invalid_build_plan",
          message: cause instanceof Error ? cause.message : String(cause),
        },
      ],
    };
  }

  const commands: BuildTestCommandOutput[] = [];
  for (const command of recorded.commands) {
    const run = await processes.run(
      {
        executable: command.executable,
        args: command.args,
        cwd: resolve(workspace, command.cwd),
        env: {
          ...input.execution.sanitizedEnv,
          CI: "1",
          NO_COLOR: "1",
          npm_config_yes: "true",
          QWEN_SKIP_PREPARE: "1",
        },
      },
      input.timeoutMs,
    );
    const classification = classifyCommand(command, run);
    commands.push({
      phase: command.phase,
      executable: command.executable,
      args: [...command.args],
      cwd: command.cwd,
      exitCode: run.exitCode,
      timedOut: run.timedOut,
      durationMs: run.durationMs,
      outcome: classification.outcome,
      detail: classification.detail,
      output: boundedOutput(run),
    });
  }

  const status: BuildTestStatus = commands.some(
    (command) => command.outcome === "failed",
  )
    ? "failed"
    : commands.some((command) => command.outcome === "inconclusive")
      ? "inconclusive"
      : "passed";
  return {
    status,
    output: { packageManager: recorded.packageManager, commands },
    diagnostics: [],
  };
}

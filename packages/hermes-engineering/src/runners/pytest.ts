import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";

import type {
  DiffPlan,
  ProcessRunner,
  StructuredTestRun,
  TestRun,
  TestRunner,
} from "./types.js";
import { NodeProcessRunner } from "./types.js";

const CONFIG_NAMES = ["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"];
const ENV_NAMES = new Set([
  "PATH",
  "HOME",
  "USERPROFILE",
  "HOMEDRIVE",
  "HOMEPATH",
  "APPDATA",
  "LOCALAPPDATA",
  "PROGRAMDATA",
  "SYSTEMROOT",
  "WINDIR",
  "COMSPEC",
  "PATHEXT",
  "TMP",
  "TEMP",
  "TMPDIR",
  "LANG",
  "LANGUAGE",
]);

interface PytestProbeResult {
  command: "collect" | "run";
  outcome: StructuredTestRun["outcome"] | "collected";
  pytestExitCode: number;
  files: string[];
  collectionErrors: string[];
  passed?: number;
  failedAssertions?: number;
  skipped?: number;
  error?: string;
}

const within = (root: string, candidate: string): boolean => {
  const rel = relative(root, candidate);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
};

const findModuleRoot = (): string => {
  let cursor = resolve(import.meta.dirname);
  for (;;) {
    if (
      existsSync(
        resolve(cursor, "hermes_cli/engineering_review/pytest_probe.py"),
      )
    ) {
      return cursor;
    }
    const parent = dirname(cursor);
    if (parent === cursor) return resolve(import.meta.dirname);
    cursor = parent;
  }
};

const defaultPython = (): string => {
  const root = findModuleRoot();
  const names =
    process.platform === "win32"
      ? [".venv/Scripts/python.exe", "venv/Scripts/python.exe"]
      : [".venv/bin/python", "venv/bin/python"];
  for (const name of names) {
    const candidate = resolve(root, name);
    if (existsSync(candidate)) return candidate;
  }
  let cursor = root;
  for (;;) {
    const candidate = resolve(
      cursor,
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    if (existsSync(candidate)) return candidate;
    const parent = dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  return process.platform === "win32" ? "python" : "python3";
};

const workspacePython = (workspace: string): string | null => {
  const names =
    process.platform === "win32"
      ? [".venv/Scripts/python.exe", "venv/Scripts/python.exe"]
      : [".venv/bin/python", "venv/bin/python"];
  for (const name of names) {
    const candidate = resolve(workspace, name);
    if (existsSync(candidate)) return candidate;
  }
  return null;
};

const probeEnvironment = (): NodeJS.ProcessEnv => {
  const env: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (
      value !== undefined &&
      (ENV_NAMES.has(name) || name.startsWith("LC_"))
    ) {
      env[name] = value;
    }
  }
  env.CI = "1";
  env.NO_COLOR = "1";
  env.PYTHONDONTWRITEBYTECODE = "1";
  env.PYTHONSAFEPATH = "1";
  env.PYTHONPATH = findModuleRoot();
  return env;
};

const manifestSignalsPytest = (workspace: string): boolean => {
  for (const name of CONFIG_NAMES) {
    try {
      const contents = readFileSync(resolve(workspace, name), "utf8");
      if (
        name === "pytest.ini" ||
        /\[tool\.pytest\.ini_options\]/.test(contents) ||
        /(?:^|\n)\[pytest\](?:\n|$)/.test(contents)
      ) {
        return true;
      }
    } catch {
      // Missing and unreadable configuration files are not detection signals.
    }
  }
  for (const name of ["requirements.txt", "requirements-dev.txt"] as const) {
    try {
      if (
        /^\s*pytest(?:\b|[<=>~!])/m.test(
          readFileSync(resolve(workspace, name), "utf8"),
        )
      ) {
        return true;
      }
    } catch {
      // Optional dependency manifests need not exist.
    }
  }
  return false;
};

const safeRelativeFile = (workspace: string, raw: string): string => {
  if (raw.length === 0 || raw.includes("\0") || isAbsolute(raw)) {
    throw new Error("test path must be repository-relative");
  }
  const normalized = raw.split("\\").join("/");
  if (normalized.split("/").some((part) => part === "." || part === "..")) {
    throw new Error("test path contains an unsafe segment");
  }
  const root = resolve(workspace);
  const target = resolve(root, normalized);
  if (!within(root, target)) throw new Error("test path escapes the workspace");
  if (existsSync(target) && !within(root, realpathSync(target))) {
    throw new Error("test path resolves outside the workspace");
  }
  return normalized;
};

const asStringArray = (value: unknown, field: string): string[] => {
  if (
    !Array.isArray(value) ||
    value.some((entry) => typeof entry !== "string")
  ) {
    throw new Error(`pytest probe ${field} is not a string array`);
  }
  return value as string[];
};

const parseProbeResult = (stdout: string): PytestProbeResult => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw new Error("pytest probe produced no parseable JSON");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("pytest probe result is not an object");
  }
  const record = parsed as Record<string, unknown>;
  if (record.command !== "collect" && record.command !== "run") {
    throw new Error("pytest probe returned an invalid command");
  }
  if (typeof record.outcome !== "string") {
    throw new Error("pytest probe returned no outcome");
  }
  const outcomes = new Set([
    "collected",
    "passed",
    "assertion_failed",
    "collection_or_import_error",
    "setup_or_teardown_error",
    "internal_error",
    "interrupted",
    "no_tests_executed",
    "probe_error",
  ]);
  if (!outcomes.has(record.outcome)) {
    throw new Error("pytest probe returned an invalid outcome");
  }
  if (!Number.isSafeInteger(record.pytestExitCode)) {
    throw new Error("pytest probe returned no valid exit code");
  }
  return {
    command: record.command,
    outcome: record.outcome as PytestProbeResult["outcome"],
    pytestExitCode: record.pytestExitCode as number,
    files: asStringArray(record.files, "files"),
    collectionErrors: asStringArray(
      record.collectionErrors ?? [],
      "collectionErrors",
    ),
    ...(typeof record.passed === "number" ? { passed: record.passed } : {}),
    ...(typeof record.failedAssertions === "number"
      ? { failedAssertions: record.failedAssertions }
      : {}),
    ...(typeof record.skipped === "number" ? { skipped: record.skipped } : {}),
    ...(typeof record.error === "string" ? { error: record.error } : {}),
  };
};

const failedRun = (run: TestRun, message: string): TestRun => ({
  ...run,
  exitCode: null,
  error: message,
});

export class PytestRunner implements TestRunner {
  readonly id = "pytest" as const;

  constructor(
    private readonly processes: ProcessRunner = new NodeProcessRunner(),
    private readonly python?: string,
  ) {}

  private pythonFor(workspace: string): string {
    return this.python ?? workspacePython(workspace) ?? defaultPython();
  }

  async detect(
    workspace: string,
    plan: DiffPlan,
  ): Promise<"yes" | "no" | "ambiguous"> {
    if (manifestSignalsPytest(workspace)) return "yes";
    return plan.files.some(
      (file) =>
        file.kind === "test" &&
        /(?:^|\/)(?:test_[^/]+|[^/]+_test)\.py$/.test(file.path),
    )
      ? "ambiguous"
      : "no";
  }

  async collectedFiles(workspace: string): Promise<Set<string>> {
    const root = resolve(workspace);
    const run = await this.processes.run(
      {
        executable: this.pythonFor(root),
        args: [
          "-m",
          "hermes_cli.engineering_review.pytest_probe",
          "collect",
          "--root",
          root,
        ],
        cwd: root,
        env: probeEnvironment(),
      },
      60_000,
    );
    if (run.timedOut) throw new Error("pytest collection timed out");
    if (run.error)
      throw new Error(`pytest collection could not start: ${run.error}`);
    if (run.exitCode !== 0) {
      throw new Error(`pytest collection probe exited ${run.exitCode}`);
    }
    const result = parseProbeResult(run.stdout);
    if (result.command !== "collect") {
      throw new Error("pytest collection probe returned a run result");
    }
    if (
      result.outcome === "internal_error" ||
      result.outcome === "interrupted" ||
      result.outcome === "probe_error"
    ) {
      throw new Error(`pytest collection was inconclusive: ${result.outcome}`);
    }
    if (result.outcome === "collection_or_import_error") {
      const specificErrors = result.collectionErrors.filter((file) =>
        /(?:^|\/)(?:test_[^/]+|[^/]+_test)\.py$/.test(file),
      );
      if (
        specificErrors.length === 0 ||
        specificErrors.length !== result.collectionErrors.length
      ) {
        throw new Error("pytest collection failed outside a test module");
      }
    }
    return new Set(
      [...result.files, ...result.collectionErrors].map((file) =>
        safeRelativeFile(root, file),
      ),
    );
  }

  async runFile(
    workspace: string,
    relativePath: string,
    timeoutMs: number,
  ): Promise<TestRun> {
    const root = resolve(workspace);
    const file = safeRelativeFile(root, relativePath);
    const run = await this.processes.run(
      {
        executable: this.pythonFor(root),
        args: [
          "-m",
          "hermes_cli.engineering_review.pytest_probe",
          "run",
          "--root",
          root,
          "--file",
          file,
        ],
        cwd: root,
        env: probeEnvironment(),
      },
      timeoutMs,
    );
    if (run.timedOut || run.error || run.exitCode !== 0) return run;
    let result: PytestProbeResult;
    try {
      result = parseProbeResult(run.stdout);
    } catch (cause) {
      return failedRun(
        run,
        cause instanceof Error ? cause.message : String(cause),
      );
    }
    if (result.command !== "run" || result.outcome === "collected") {
      return failedRun(run, "pytest run probe returned a collection result");
    }
    const structured: StructuredTestRun = {
      framework: "pytest",
      outcome: result.outcome,
      passed: result.passed ?? 0,
      failedAssertions: result.failedAssertions ?? 0,
      skipped: result.skipped ?? 0,
    };
    const error = result.outcome === "probe_error" ? result.error : undefined;
    return {
      ...run,
      exitCode:
        result.outcome === "passed" ? 0 : Math.max(result.pytestExitCode, 1),
      structured,
      ...(error === undefined ? {} : { error }),
    };
  }
}

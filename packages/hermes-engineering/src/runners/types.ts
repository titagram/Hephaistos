import { spawn, spawnSync } from "node:child_process";

export type RunnerId = "vitest" | "pytest";
export type RunnerDetection = "yes" | "no" | "ambiguous";

export interface DiffPlanFile {
  path: string;
  kind: string;
}

export interface DiffPlan {
  files: DiffPlanFile[];
  [key: string]: unknown;
}

export interface ProcessInvocation {
  executable: string;
  args: readonly string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
}

export interface TestRun {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  durationMs: number;
  error?: string;
  structured?: StructuredTestRun;
}

export interface StructuredTestRun {
  framework: "pytest";
  outcome:
    | "passed"
    | "assertion_failed"
    | "collection_or_import_error"
    | "setup_or_teardown_error"
    | "internal_error"
    | "interrupted"
    | "no_tests_executed"
    | "probe_error";
  passed: number;
  failedAssertions: number;
  skipped: number;
}

export interface ProcessRunner {
  run(invocation: ProcessInvocation, timeoutMs: number): Promise<TestRun>;
}

export interface TestRunner {
  readonly id: RunnerId;
  detect(workspace: string, plan: DiffPlan): Promise<RunnerDetection>;
  collectedFiles(workspace: string): Promise<Set<string>>;
  runFile(
    workspace: string,
    relativePath: string,
    timeoutMs: number,
  ): Promise<TestRun>;
}

const MAX_CAPTURE_BYTES = 64 * 1024 * 1024;

const killProcessTree = (pid: number): void => {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // The child may have exited between the deadline and the kill.
    }
  }
};

const appendBounded = (
  chunks: Buffer[],
  chunk: Buffer,
  captured: { bytes: number },
): void => {
  if (captured.bytes >= MAX_CAPTURE_BYTES) return;
  const remaining = MAX_CAPTURE_BYTES - captured.bytes;
  const kept = chunk.length <= remaining ? chunk : chunk.subarray(0, remaining);
  chunks.push(kept);
  captured.bytes += kept.length;
};

export class NodeProcessRunner implements ProcessRunner {
  async run(
    invocation: ProcessInvocation,
    timeoutMs: number,
  ): Promise<TestRun> {
    const started = Date.now();
    return await new Promise((resolve) => {
      const stdout: Buffer[] = [];
      const stderr: Buffer[] = [];
      const stdoutSize = { bytes: 0 };
      const stderrSize = { bytes: 0 };
      let timedOut = false;
      let spawnError: Error | undefined;
      let settled = false;
      const child = spawn(invocation.executable, [...invocation.args], {
        cwd: invocation.cwd,
        detached: process.platform !== "win32",
        env: invocation.env ?? process.env,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
      child.stdout.on("data", (chunk: Buffer) =>
        appendBounded(stdout, chunk, stdoutSize),
      );
      child.stderr.on("data", (chunk: Buffer) =>
        appendBounded(stderr, chunk, stderrSize),
      );
      child.on("error", (error) => {
        spawnError = error;
      });
      const timer = setTimeout(() => {
        timedOut = true;
        if (child.pid !== undefined) killProcessTree(child.pid);
      }, timeoutMs);
      timer.unref();

      const finish = (exitCode: number | null): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const result: TestRun = {
          exitCode,
          stdout: Buffer.concat(stdout).toString("utf8"),
          stderr: Buffer.concat(stderr).toString("utf8"),
          timedOut,
          durationMs: Date.now() - started,
        };
        if (spawnError !== undefined) result.error = spawnError.message;
        resolve(result);
      };
      child.on("close", finish);
    });
  }
}

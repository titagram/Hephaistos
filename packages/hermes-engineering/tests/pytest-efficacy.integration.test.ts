import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { runTestEfficacy } from "../src/handlers/test-efficacy.js";
import type { EngineRequest } from "../src/protocol.js";
import { PytestRunner } from "../src/runners/pytest.js";
import type {
  ProcessInvocation,
  ProcessRunner,
  TestRun,
} from "../src/runners/types.js";

const temporaryRoots: string[] = [];
const LOCAL_EXECUTION = {
  mode: "local",
  allowed: true,
  sanitizedEnv: { PATH: process.env.PATH ?? "" },
  network: true,
  reason: "test fixture",
  backend: null,
};

const git = (cwd: string, ...args: string[]): string =>
  execFileSync("git", args, { cwd, encoding: "utf8" }).trim();

const write = (root: string, path: string, contents: string): void => {
  const target = join(root, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, contents);
};

const writePlan = (
  root: string,
  files: Array<{ path: string; kind: "source" | "test" }>,
): string => {
  const path = join(root, "artifacts/plan.json");
  write(root, "artifacts/plan.json", JSON.stringify({ files }));
  return path;
};

const fixtureRepo = (): {
  root: string;
  artifactRoot: string;
  planPath: string;
  baseRef: string;
} => {
  const root = mkdtempSync(join(import.meta.dirname, ".tmp-pytest-efficacy-"));
  temporaryRoots.push(root);
  write(root, "pytest.ini", "[pytest]\ntestpaths = tests\n");
  write(root, "feature.py", "def answer():\n    return 1\n");
  write(
    root,
    "tests/test_effective.py",
    "from feature import answer\n\ndef test_effective():\n    assert answer() == 2\n",
  );
  write(
    root,
    "tests/test_inert.py",
    "from feature import answer\n\ndef test_inert():\n    assert answer() > 0\n",
  );
  write(
    root,
    "tests/test_import_error.py",
    "from head_only import FLAG\n\ndef test_import():\n    assert FLAG\n",
  );
  write(
    root,
    "tests/test_setup_error.py",
    "import pytest\nfrom feature import answer\n\n@pytest.fixture\ndef changed_only():\n    if answer() != 2:\n        raise RuntimeError('setup failed')\n    return answer()\n\ndef test_setup(changed_only):\n    assert changed_only == 2\n",
  );
  write(
    root,
    "tests/test_timeout.py",
    "import time\nfrom feature import answer\n\ndef test_timeout():\n    while answer() != 2:\n        time.sleep(0.05)\n    assert answer() == 2\n",
  );
  write(
    root,
    "checks/test_not_collected.py",
    "def test_outside_discovery():\n    assert True\n",
  );
  git(root, "init", "-q");
  git(root, "config", "user.name", "Hermes Test");
  git(root, "config", "user.email", "hermes@example.invalid");
  git(root, "add", ".");
  git(root, "commit", "-qm", "base");
  const baseRef = git(root, "rev-parse", "HEAD");
  write(root, "feature.py", "def answer():\n    return 2\n");
  write(root, "head_only.py", "FLAG = True\n");
  git(root, "add", ".");
  git(root, "commit", "-qm", "head");
  const planPath = writePlan(root, [
    { path: "feature.py", kind: "source" },
    { path: "head_only.py", kind: "source" },
    { path: "tests/test_effective.py", kind: "test" },
    { path: "tests/test_inert.py", kind: "test" },
    { path: "tests/test_import_error.py", kind: "test" },
    { path: "tests/test_setup_error.py", kind: "test" },
    { path: "tests/test_timeout.py", kind: "test" },
    { path: "checks/test_not_collected.py", kind: "test" },
  ]);
  return { root, artifactRoot: dirname(planPath), planPath, baseRef };
};

const requestFor = (
  fixture: ReturnType<typeof fixtureRepo>,
  only: string,
  timeoutMs = 10_000,
): EngineRequest => {
  const plan = JSON.parse(readFileSync(fixture.planPath, "utf8")) as {
    files: Array<{ path: string; kind: "source" | "test" }>;
  };
  const selected = plan.files.filter(
    (file) => file.kind === "source" || file.path.endsWith(only),
  );
  const planPath = writePlan(fixture.root, selected);
  return {
    protocolVersion: 1,
    requestId: `pytest:${only}`,
    command: "test-efficacy",
    workspace: fixture.root,
    artifactRoot: fixture.artifactRoot,
    input: {
      planPath,
      baseRef: fixture.baseRef,
      runner: "pytest",
      timeoutMs,
      execution: LOCAL_EXECUTION,
    },
  };
};

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("Pytest efficacy", () => {
  it("uses pytest collection in a mixed repository with npm workspaces", async () => {
    const fixture = fixtureRepo();
    write(
      fixture.root,
      "package.json",
      JSON.stringify({ private: true, workspaces: ["js-packages/*"] }),
    );
    git(fixture.root, "add", "package.json");
    git(fixture.root, "commit", "-qm", "add unrelated npm workspace");

    const result = await runTestEfficacy(
      requestFor(fixture, "test_effective.py"),
    );

    expect(result.output.gated).toEqual(["tests/test_effective.py"]);
    expect(result.output.unreachable).toEqual([]);
  });

  it.each([
    ["test_effective.py", "gated", 10_000],
    ["test_inert.py", "inert", 10_000],
    ["test_not_collected.py", "unreachable", 10_000],
    ["test_import_error.py", "inconclusive", 10_000],
    ["test_setup_error.py", "inconclusive", 10_000],
    ["test_timeout.py", "inconclusive", 100],
  ] as const)("classifies %s as %s", async (file, verdict, timeoutMs) => {
    const fixture = fixtureRepo();
    const before = git(fixture.root, "status", "--porcelain=v1", "-z");

    const result = await runTestEfficacy(requestFor(fixture, file, timeoutMs));

    expect(result.output.runner).toBe("pytest");
    expect(
      result.output.tests.find((test) => test.path.endsWith(file)),
    ).toMatchObject({ verdict });
    expect(git(fixture.root, "status", "--porcelain=v1", "-z")).toBe(before);
    expect(existsSync(result.output.probeWorktreePath!)).toBe(false);
  });

  it("spawns the probe with an argument array, explicit cwd, and sanitized environment", async () => {
    let invocation: ProcessInvocation | undefined;
    let timeout: number | undefined;
    const processes: ProcessRunner = {
      async run(received, timeoutMs): Promise<TestRun> {
        invocation = received;
        timeout = timeoutMs;
        return {
          exitCode: 0,
          stdout: JSON.stringify({
            command: "run",
            outcome: "passed",
            pytestExitCode: 0,
            files: ["tests/test_ok.py"],
            passed: 1,
            failedAssertions: 0,
            skipped: 0,
          }),
          stderr: "",
          timedOut: false,
          durationMs: 1,
        };
      },
    };
    const runner = new PytestRunner(processes, "/trusted/python");

    await runner.runFile("/workspace", "tests/test_ok.py", 321);

    expect(invocation).toMatchObject({
      executable: "/trusted/python",
      args: [
        "-m",
        "hermes_cli.engineering_review.pytest_probe",
        "run",
        "--root",
        "/workspace",
        "--file",
        "tests/test_ok.py",
      ],
      cwd: "/workspace",
    });
    expect(invocation?.env).toMatchObject({
      CI: "1",
      NO_COLOR: "1",
      PYTHONSAFEPATH: "1",
    });
    expect(invocation?.env).not.toHaveProperty("OPENAI_API_KEY");
    expect(timeout).toBe(321);
  });
});

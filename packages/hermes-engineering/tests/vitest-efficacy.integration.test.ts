import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  runTestEfficacy,
  selectRunner,
} from "../src/handlers/test-efficacy.js";
import type { EngineRequest } from "../src/protocol.js";
import type {
  DiffPlan,
  RunnerDetection,
  RunnerId,
  TestRunner,
} from "../src/runners/types.js";

const FIXTURE = resolve(import.meta.dirname, "fixtures/vitest-efficacy");
const ROOT_FIXTURE = resolve(
  import.meta.dirname,
  "fixtures/vitest-root-efficacy",
);
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

const writePlan = (
  root: string,
  files: Array<{ path: string; kind: "source" | "test" }>,
): string => {
  const path = join(root, "artifacts", "plan.json");
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  writeFileSync(path, JSON.stringify({ files }), { mode: 0o600 });
  return path;
};

const fixtureRepo = (): {
  root: string;
  artifactRoot: string;
  planPath: string;
  baseRef: string;
} => {
  const root = mkdtempSync(join(import.meta.dirname, ".tmp-efficacy-"));
  temporaryRoots.push(root);
  cpSync(FIXTURE, root, { recursive: true });
  git(root, "init", "-q");
  git(root, "config", "user.name", "Hermes Test");
  git(root, "config", "user.email", "hermes@example.invalid");
  const source = join(root, "packages/app/src/feature.ts");
  writeFileSync(
    source,
    readFileSync(join(root, "packages/app/src/feature.base.ts")),
  );
  rmSync(join(root, "packages/app/src/feature.head.ts"));
  rmSync(join(root, "packages/app/src/head-only.ts"));
  git(root, "add", ".");
  git(root, "commit", "-qm", "base");
  const baseRef = git(root, "rev-parse", "HEAD");
  writeFileSync(
    source,
    readFileSync(join(FIXTURE, "packages/app/src/feature.head.ts")),
  );
  writeFileSync(
    join(root, "packages/app/src/head-only.ts"),
    readFileSync(join(FIXTURE, "packages/app/src/head-only.ts")),
  );
  writeFileSync(
    join(root, "packages/app/__fixtures__/answer.ts"),
    readFileSync(join(FIXTURE, "packages/app/__fixtures__/answer.ts")),
  );
  git(root, "add", ".");
  git(root, "commit", "-qm", "head");
  const files = [
    { path: "packages/app/src/feature.ts", kind: "source" as const },
    { path: "packages/app/src/head-only.ts", kind: "source" as const },
    {
      path: "packages/app/__fixtures__/answer.ts",
      kind: "source" as const,
    },
    ...[
      "effective.test.ts",
      "inert.test.ts",
      "compile-error.test.ts",
      "timeout.test.ts",
    ].map((name) => ({
      path: `packages/app/${name}`,
      kind: "test" as const,
    })),
    { path: "outside-workspace.test.ts", kind: "test" as const },
  ];
  const planPath = writePlan(root, files);
  return { root, artifactRoot: dirname(planPath), planPath, baseRef };
};

const rootFixtureRepo = (): ReturnType<typeof fixtureRepo> => {
  const root = mkdtempSync(join(import.meta.dirname, ".tmp-root-efficacy-"));
  temporaryRoots.push(root);
  cpSync(ROOT_FIXTURE, root, { recursive: true });
  git(root, "init", "-q");
  git(root, "config", "user.name", "Hermes Test");
  git(root, "config", "user.email", "hermes@example.invalid");
  writeFileSync(root + "/feature.ts", readFileSync(root + "/feature.base.ts"));
  rmSync(root + "/feature.head.ts");
  git(root, "add", ".");
  git(root, "commit", "-qm", "base");
  const baseRef = git(root, "rev-parse", "HEAD");
  writeFileSync(
    root + "/feature.ts",
    readFileSync(join(ROOT_FIXTURE, "feature.head.ts")),
  );
  git(root, "add", ".");
  git(root, "commit", "-qm", "head");
  const planPath = writePlan(root, [
    { path: "feature.ts", kind: "source" },
    { path: "effective.test.ts", kind: "test" },
  ]);
  return { root, artifactRoot: dirname(planPath), planPath, baseRef };
};

const requestFor = (
  fixture: ReturnType<typeof fixtureRepo>,
  only: string,
  timeoutMs = 20_000,
): EngineRequest => {
  const parsed = JSON.parse(readFileSync(fixture.planPath, "utf8")) as {
    files: Array<{ path: string; kind: "source" | "test" }>;
  };
  const selected = parsed.files.filter(
    (file) =>
      file.kind === "source" ||
      file.path.endsWith(`/${only}`) ||
      file.path === only,
  );
  const planPath = writePlan(fixture.root, selected);
  return {
    protocolVersion: 1,
    requestId: `efficacy:${only}`,
    command: "test-efficacy",
    workspace: fixture.root,
    artifactRoot: fixture.artifactRoot,
    input: {
      planPath,
      baseRef: fixture.baseRef,
      runner: "vitest",
      timeoutMs,
      execution: LOCAL_EXECUTION,
    },
  };
};

const detectedRunner = (
  id: RunnerId,
  detection: RunnerDetection,
): TestRunner => ({
  id,
  async detect() {
    return detection;
  },
  async collectedFiles() {
    throw new Error("not used by runner-selection tests");
  },
  async runFile() {
    throw new Error("not used by runner-selection tests");
  },
});

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("Vitest efficacy", () => {
  it("requires an explicit retry when auto detects multiple runners", async () => {
    const plan: DiffPlan = { files: [] };
    const selection = await selectRunner("auto", ".", plan, [
      detectedRunner("vitest", "yes"),
      detectedRunner("pytest", "yes"),
    ]);
    expect(selection).toEqual({
      code: "ambiguous_runner",
      available: ["vitest", "pytest"],
    });
  });

  it("returns no_runner instead of guessing when auto detects none", async () => {
    const plan: DiffPlan = { files: [] };
    const selection = await selectRunner("auto", ".", plan, [
      detectedRunner("vitest", "no"),
    ]);
    expect(selection).toEqual({ code: "no_runner", available: [] });
  });

  it("collects and classifies a changed test in a root package without npm workspaces", async () => {
    const fixture = rootFixtureRepo();
    const before = git(fixture.root, "status", "--porcelain=v1", "-z");

    const result = await runTestEfficacy({
      protocolVersion: 1,
      requestId: "efficacy:root-package",
      command: "test-efficacy",
      workspace: fixture.root,
      artifactRoot: fixture.artifactRoot,
      input: {
        planPath: fixture.planPath,
        baseRef: fixture.baseRef,
        runner: "vitest",
        timeoutMs: 20_000,
        execution: LOCAL_EXECUTION,
      },
    });

    expect(result.output.tests).toEqual([
      expect.objectContaining({ path: "effective.test.ts", verdict: "gated" }),
    ]);
    expect(result.output.unreachable).toEqual([]);
    expect(git(fixture.root, "status", "--porcelain=v1", "-z")).toBe(before);
    expect(existsSync(result.output.probeWorktreePath!)).toBe(false);
  });

  it.each([
    ["effective.test.ts", "gated"],
    ["inert.test.ts", "inert"],
    ["outside-workspace.test.ts", "unreachable"],
    ["compile-error.test.ts", "inconclusive"],
  ] as const)("classifies %s as %s", async (file, verdict) => {
    const fixture = fixtureRepo();
    const before = git(fixture.root, "status", "--porcelain=v1", "-z");
    const result = await runTestEfficacy(requestFor(fixture, file));
    expect(
      result.output.tests.find((test) => test.path.endsWith(file))?.verdict,
    ).toBe(verdict);
    expect(git(fixture.root, "status", "--porcelain=v1", "-z")).toBe(before);
    if (result.output.probeWorktreePath !== null) {
      expect(existsSync(result.output.probeWorktreePath)).toBe(false);
    }
  });

  it("classifies effective and inert siblings independently in one probe", async () => {
    const fixture = fixtureRepo();
    const request = requestFor(fixture, "effective.test.ts");
    const planPath = request.input.planPath as string;
    const plan = JSON.parse(readFileSync(planPath, "utf8")) as {
      files: Array<{ path: string; kind: "source" | "test" }>;
    };
    plan.files.push({ path: "packages/app/inert.test.ts", kind: "test" });
    writeFileSync(planPath, JSON.stringify(plan));

    const result = await runTestEfficacy(request);

    expect(result.output.gated).toEqual(["packages/app/effective.test.ts"]);
    expect(result.output.inert).toEqual(["packages/app/inert.test.ts"]);
    expect(result.output.inconclusive).toEqual([]);
  });

  it("keeps every reachable test in the output when a test-only diff cannot be probed", async () => {
    const fixture = fixtureRepo();
    const request = requestFor(fixture, "effective.test.ts");
    const planPath = request.input.planPath as string;
    const plan = JSON.parse(readFileSync(planPath, "utf8")) as {
      files: Array<{ path: string; kind: "source" | "test" }>;
    };
    plan.files = plan.files.filter((file) => file.kind === "test");
    writeFileSync(planPath, JSON.stringify(plan));

    const result = await runTestEfficacy(request);

    expect(result.status).toBe("inconclusive");
    expect(result.output.tests).toEqual([
      expect.objectContaining({
        path: "packages/app/effective.test.ts",
        verdict: "inconclusive",
      }),
    ]);
  });

  it("removes the isolated probe worktree after a runner timeout", async () => {
    const fixture = fixtureRepo();
    const result = await runTestEfficacy(
      requestFor(fixture, "timeout.test.ts", 100),
    );
    expect(result.status).toBe("inconclusive");
    expect(result.output.inconclusive).toEqual([
      "packages/app/timeout.test.ts",
    ]);
    expect(existsSync(result.output.probeWorktreePath!)).toBe(false);
    expect(git(fixture.root, "worktree", "list", "--porcelain")).not.toContain(
      relative(fixture.root, result.output.probeWorktreePath!),
    );
  });

  it("rejects backslash traversal before safe removal can reach the source checkout", async () => {
    const fixture = fixtureRepo();
    const sentinel = join(fixture.root, "victim");
    writeFileSync(sentinel, "keep");
    const request = requestFor(fixture, "effective.test.ts");
    const planPath = request.input.planPath as string;
    const plan = JSON.parse(readFileSync(planPath, "utf8")) as {
      files: Array<{ path: string; kind: "source" | "test" }>;
    };
    plan.files.push({
      path: "packages/app\\..\\..\\..\\victim",
      kind: "source",
    });
    writeFileSync(planPath, JSON.stringify(plan));

    const result = await runTestEfficacy(request);

    expect(result.status).toBe("inconclusive");
    expect(readFileSync(sentinel, "utf8")).toBe("keep");
    expect(existsSync(result.output.probeWorktreePath!)).toBe(false);
  });
});

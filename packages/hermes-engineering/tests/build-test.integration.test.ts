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
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  discoverBuildTestPlan,
  runBuildTest,
} from "../src/handlers/build-test.js";
import type { EngineRequest } from "../src/protocol.js";

const FIXTURE = resolve(import.meta.dirname, "fixtures/build-test");
const BUNDLE = resolve(
  import.meta.dirname,
  "../../../hermes_cli/engineering_dist/hermes-engineering.mjs",
);
const temporaryRoots: string[] = [];

type Phase = "build" | "test";
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

const runBundle = (
  request: EngineRequest,
): {
  status: string;
  output: Record<string, unknown>;
  diagnostics: Array<{ code: string; message: string }>;
} =>
  JSON.parse(
    execFileSync(process.execPath, [BUNDLE], {
      input: JSON.stringify(request),
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    }),
  ) as {
    status: string;
    output: Record<string, unknown>;
    diagnostics: Array<{ code: string; message: string }>;
  };

const requestFor = (
  script: string,
  phase: Phase,
  timeoutMs = 10_000,
): EngineRequest => {
  const root = mkdtempSync(join(import.meta.dirname, ".tmp-build-test-"));
  temporaryRoots.push(root);
  cpSync(FIXTURE, root, { recursive: true });
  const artifactRoot = join(root, "artifacts");
  mkdirSync(artifactRoot, { mode: 0o700 });
  const planPath = join(artifactRoot, "plan.json");
  writeFileSync(
    planPath,
    JSON.stringify({
      files: [{ path: "src/change.ts", kind: "source" }],
      hermes: {
        buildTest: {
          packageManager: "npm",
          commands: [
            {
              phase,
              executable: "npm",
              args: ["run", script],
              cwd: ".",
              ...(script === "test:fail"
                ? { testFiles: ["failing.test.ts"] }
                : script === "test:compile"
                  ? { testFiles: ["compile-error.test.ts"] }
                  : {}),
            },
          ],
        },
      },
    }),
  );
  return {
    protocolVersion: 1,
    requestId: `build-test:${script}`,
    command: "build-test",
    workspace: root,
    artifactRoot,
    input: { planPath, timeoutMs, execution: LOCAL_EXECUTION },
  };
};

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("build-test", () => {
  it("classifies an existing test assertion failure through capture and the built engine", () => {
    const outer = mkdtempSync(join(import.meta.dirname, ".tmp-capture-build-"));
    temporaryRoots.push(outer);
    const root = join(outer, "repo");
    const artifactRoot = join(outer, "artifacts");
    mkdirSync(root);
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(artifactRoot, { mode: 0o700 });
    writeFileSync(
      join(root, "package.json"),
      JSON.stringify({
        name: "capture-build-fixture",
        private: true,
        scripts: {
          test: "vitest run --reporter=json --configLoader=runner --no-cache",
        },
        devDependencies: { vitest: "^4.1.5" },
      }),
    );
    writeFileSync(
      join(root, "vitest.config.ts"),
      'import { defineConfig } from "vitest/config";\n' +
        'export default defineConfig({ test: { include: ["*.test.ts"] } });\n',
    );
    writeFileSync(join(root, "src/value.ts"), "export const value = 2;\n");
    writeFileSync(
      join(root, "value.test.ts"),
      'import { expect, it } from "vitest";\n' +
        'import { value } from "./src/value.js";\n' +
        'it("keeps the behavior", () => expect(value).toBe(2));\n',
    );
    git(root, "init", "-q");
    git(root, "config", "user.name", "Hermes Test");
    git(root, "config", "user.email", "hermes@example.invalid");
    git(root, "add", ".");
    git(root, "commit", "-qm", "base");
    writeFileSync(join(root, "src/value.ts"), "export const value = 1;\n");
    const before = git(root, "status", "--porcelain=v1", "-z");

    const capture = runBundle({
      protocolVersion: 1,
      requestId: "capture-build:capture",
      command: "capture-target",
      workspace: root,
      artifactRoot,
      input: { kind: "local" },
    });
    expect(capture.status).toBe("passed");
    const planPath = capture.output.planPath as string;
    const plan = JSON.parse(readFileSync(planPath, "utf8")) as {
      hermes: {
        buildTest: {
          commands: Array<{ phase: string; testFiles?: string[] }>;
        };
      };
    };
    expect(
      plan.hermes.buildTest.commands.find((command) => command.phase === "test")
        ?.testFiles,
    ).toBeUndefined();

    const built = runBundle({
      protocolVersion: 1,
      requestId: "capture-build:build-test",
      command: "build-test",
      workspace: root,
      artifactRoot,
      input: {
        planPath,
        timeoutMs: 20_000,
        execution: LOCAL_EXECUTION,
      },
    });

    expect(built.status).toBe("failed");
    expect(
      (built.output.commands as Array<{ outcome: string }>)[0]?.outcome,
    ).toBe("failed");
    expect(git(root, "status", "--porcelain=v1", "-z")).toBe(before);
  });

  it("executes the Qwen-scoped commands recorded during planning", async () => {
    const request = requestFor("build:pass", "build");
    const discovered = discoverBuildTestPlan(request.workspace, [
      { path: "src/change.ts", kind: "source" },
    ]);
    expect(discovered).toEqual({
      packageManager: "npm",
      commands: [
        {
          phase: "build",
          executable: "npm",
          args: ["run", "build"],
          cwd: ".",
        },
      ],
    });
    const planPath = request.input.planPath as string;
    writeFileSync(
      planPath,
      JSON.stringify({
        files: [{ path: "src/change.ts", kind: "source" }],
        hermes: { buildTest: discovered },
      }),
    );

    const result = await runBuildTest(request);

    expect(result.status).toBe("passed");
    expect(result.output.commands[0]?.args).toEqual(["run", "build"]);
  });

  it("passes a recorded build command", async () => {
    const result = await runBuildTest(requestFor("build:pass", "build"));
    expect(result.status).toBe("passed");
    expect(result.output.commands).toHaveLength(1);
    expect(result.output.commands[0]?.outcome).toBe("passed");
  });

  it("reports a genuine test assertion failure", async () => {
    const result = await runBuildTest(requestFor("test:fail", "test"));
    expect(result.status).toBe("failed");
    expect(result.output.commands[0]?.outcome).toBe("failed");
  });

  it("keeps a compile or import failure inconclusive", async () => {
    const result = await runBuildTest(requestFor("test:compile", "test"));
    expect(result.status).toBe("inconclusive");
    expect(result.output.commands[0]?.outcome).toBe("inconclusive");
  });

  it("applies the execution timeout as an inconclusive result", async () => {
    const request = requestFor("test:timeout", "test", 100);
    const result = await runBuildTest(request);
    expect(result.status).toBe("inconclusive");
    expect(result.output.commands[0]?.timedOut).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(existsSync(join(request.workspace, "timeout-survived"))).toBe(false);
  });

  it("never treats plan arguments as a shell command or package-manager exec", async () => {
    const request = requestFor("build:pass", "build");
    const planPath = request.input.planPath as string;
    const plan = JSON.parse(readFileSync(planPath, "utf8")) as {
      hermes: { buildTest: { commands: Array<{ args: string[] }> } };
    };
    plan.hermes.buildTest.commands[0]!.args = [
      "exec",
      "--",
      "node",
      "-e",
      "require('fs').writeFileSync('escaped', 'bad')",
    ];
    writeFileSync(planPath, JSON.stringify(plan));

    const result = await runBuildTest(request);

    expect(result.status).toBe("inconclusive");
    expect(result.diagnostics[0]?.code).toBe("invalid_build_plan");
    expect(existsSync(join(request.workspace, "escaped"))).toBe(false);
  });

  it("does not spawn remote code when authority denies execution", async () => {
    const request = requestFor("build:pass", "build");
    request.input.execution = {
      mode: "denied",
      allowed: false,
      sanitizedEnv: {},
      network: false,
      reason: "untrusted_remote_code_requires_sandbox_or_consent",
      backend: null,
    };

    const result = await runBuildTest(request);

    expect(result.status).toBe("inconclusive");
    expect(result.diagnostics[0]?.code).toBe(
      "untrusted_execution_not_authorized",
    );
    expect(result.output.commands).toEqual([]);
    expect(existsSync(join(request.workspace, "dist/value.js"))).toBe(false);
  });

  it("never falls back to host execution for a configured sandbox", async () => {
    const request = requestFor("build:pass", "build");
    request.input.execution = {
      mode: "sandbox",
      allowed: true,
      sanitizedEnv: { PATH: process.env.PATH ?? "" },
      network: false,
      reason: "untrusted_remote_code_sandboxed",
      backend: "docker",
    };

    const result = await runBuildTest(request);

    expect(result.status).toBe("inconclusive");
    expect(result.diagnostics[0]?.code).toBe(
      "sandbox_execution_requires_terminal_environment",
    );
    expect(existsSync(join(request.workspace, "dist/value.js"))).toBe(false);
  });
});

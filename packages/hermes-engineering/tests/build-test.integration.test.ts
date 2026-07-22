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
const temporaryRoots: string[] = [];

type Phase = "build" | "test";

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
    input: { planPath, timeoutMs },
  };
};

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("build-test", () => {
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
});

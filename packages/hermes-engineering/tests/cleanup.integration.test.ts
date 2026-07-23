import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import type { EngineRequest } from "../src/protocol.js";

const BUNDLE = resolve(
  import.meta.dirname,
  "../../../hermes_cli/engineering_dist/hermes-engineering.mjs",
);
const roots: string[] = [];

const git = (cwd: string, ...args: string[]): string =>
  execFileSync("git", args, { cwd, encoding: "utf8" }).trim();

const fixture = () => {
  const outer = mkdtempSync(join(import.meta.dirname, ".tmp-cleanup-"));
  roots.push(outer);
  const repo = join(outer, "repo");
  mkdirSync(repo);
  git(repo, "init", "-q");
  git(repo, "config", "user.name", "Hermes Test");
  git(repo, "config", "user.email", "hermes@example.invalid");
  writeFileSync(join(repo, "value.ts"), "export const value = 1;\n");
  git(repo, "add", ".");
  git(repo, "commit", "-qm", "base");
  writeFileSync(join(repo, "value.ts"), "export const value = 2;\n");
  git(repo, "add", ".");
  git(repo, "commit", "-qm", "head");
  const runId = "cleanup-test-run-123456";
  const artifactRoot = join(outer, "reviews", "session", runId);
  mkdirSync(artifactRoot, { recursive: true, mode: 0o700 });
  return { outer, repo, runId, artifactRoot };
};

const runBundle = (request: EngineRequest) =>
  JSON.parse(
    execFileSync(process.execPath, [BUNDLE], {
      input: JSON.stringify(request),
      encoding: "utf8",
    }),
  ) as {
    status: string;
    output: Record<string, unknown>;
    diagnostics: Array<{ code: string; message: string }>;
  };

const capture = (value: ReturnType<typeof fixture>) => {
  const result = runBundle({
    protocolVersion: 1,
    requestId: "cleanup:capture",
    command: "capture-target",
    workspace: value.repo,
    artifactRoot: value.artifactRoot,
    input: { kind: "range", range: "HEAD~1..HEAD" },
  });
  expect(result.status).toBe("passed");
  return result.output;
};

const cleanupRequest = (
  value: ReturnType<typeof fixture>,
  input: Record<string, unknown> = { runId: value.runId },
): EngineRequest => ({
  protocolVersion: 1,
  requestId: "cleanup:run",
  command: "cleanup",
  workspace: value.repo,
  artifactRoot: value.artifactRoot,
  input,
});

afterEach(() => {
  for (const root of roots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("cleanup recovery", () => {
  it("resolves the registered run and removes only its captured worktree", () => {
    const value = fixture();
    const output = capture(value);
    const worktree = output.worktreePath as string;
    expect(existsSync(worktree)).toBe(true);

    const result = runBundle(cleanupRequest(value));

    expect(result.status).toBe("passed");
    expect(existsSync(worktree)).toBe(false);
    expect(existsSync(value.repo)).toBe(true);
    expect(result.output.recoveryCommand).toBeNull();
  });

  it("never accepts an arbitrary deletion path", () => {
    const value = fixture();
    capture(value);
    const protectedPath = join(value.outer, "protected");
    mkdirSync(protectedPath);
    writeFileSync(join(protectedPath, "keep"), "safe");

    const result = runBundle(
      cleanupRequest(value, {
        runId: value.runId,
        path: protectedPath,
      }),
    );

    expect(result.status).toBe("inconclusive");
    expect(readFileSync(join(protectedPath, "keep"), "utf8")).toBe("safe");
    expect(result.output.recoveryCommand).toBe(
      `hermes review cleanup --run ${value.runId}`,
    );
  });

  it("refuses a symlink worktree without touching its external target", () => {
    const value = fixture();
    const output = capture(value);
    const worktree = output.worktreePath as string;
    git(value.repo, "worktree", "remove", "--force", worktree);
    const external = join(value.outer, "external");
    mkdirSync(external);
    writeFileSync(join(external, "keep"), "safe");
    symlinkSync(external, worktree);

    const result = runBundle(cleanupRequest(value));

    expect(result.status).toBe("inconclusive");
    expect(readFileSync(join(external, "keep"), "utf8")).toBe("safe");
    expect(existsSync(value.repo)).toBe(true);
    expect(result.diagnostics[0]?.code).toBe("cleanup_failed");
  });
});

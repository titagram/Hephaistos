import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  CaptureTargetError,
  captureTarget,
  cleanupCapture,
} from "../src/handlers/capture-target.js";
import { dispatch } from "../src/handlers/index.js";
import type {
  CaptureInput,
  CaptureTargetOutput,
  EngineRequest,
} from "../src/protocol.js";

const roots: string[] = [];

const gitEnvironment = {
  ...process.env,
  GIT_AUTHOR_NAME: "Hermes Test",
  GIT_AUTHOR_EMAIL: "hermes@example.test",
  GIT_COMMITTER_NAME: "Hermes Test",
  GIT_COMMITTER_EMAIL: "hermes@example.test",
  GIT_CONFIG_GLOBAL: process.platform === "win32" ? "NUL" : "/dev/null",
  GIT_CONFIG_NOSYSTEM: "1",
};

class FixtureRepo {
  readonly path: string;
  readonly artifactRoot: string;

  constructor(root: string) {
    this.path = join(root, "repo");
    this.artifactRoot = join(root, "review-run");
    mkdirSync(this.path);
    mkdirSync(this.artifactRoot, { mode: 0o700 });
    chmodSync(this.artifactRoot, 0o700);
    this.git("init", "-q", "-b", "main");
    this.write("tracked.ts", "initial\n");
    this.git("add", "tracked.ts");
    this.git("commit", "-q", "-m", "initial");
  }

  git(...args: string[]): string {
    return execFileSync("git", args, {
      cwd: this.path,
      env: gitEnvironment,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  }

  write(path: string, contents: string | Buffer): void {
    const destination = join(this.path, path);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, contents);
  }

  commit(path: string, contents: string, message = path): string {
    this.write(path, contents);
    this.git("add", "--", path);
    this.git("commit", "-q", "-m", message);
    return this.git("rev-parse", "HEAD");
  }

  statusPorcelain(): Buffer {
    return execFileSync("git", ["status", "--porcelain=v1", "-z"], {
      cwd: this.path,
      env: gitEnvironment,
    });
  }
}

const fixtureRepo = (): FixtureRepo => {
  const root = mkdtempSync(join(tmpdir(), "hermes-capture-target-"));
  roots.push(root);
  return new FixtureRepo(root);
};

const request = (repo: FixtureRepo, input: CaptureInput): EngineRequest => ({
  protocolVersion: 1,
  requestId: `run-${roots.length}`,
  command: "capture-target",
  workspace: repo.path,
  artifactRoot: repo.artifactRoot,
  input,
});

const captureWithoutMutation = async (
  repo: FixtureRepo,
  input: CaptureInput,
): Promise<CaptureTargetOutput> => {
  const before = repo.statusPorcelain();
  const output = await captureTarget(request(repo, input));
  expect(repo.statusPorcelain().equals(before)).toBe(true);
  return output;
};

const planFor = (output: CaptureTargetOutput): Record<string, unknown> =>
  JSON.parse(readFileSync(output.planPath, "utf8")) as Record<string, unknown>;

afterEach(() => {
  for (const root of roots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("captureTarget with real Git repositories", () => {
  it("captures staged, unstaged, and untracked files without changing git state", async () => {
    const repo = fixtureRepo();
    repo.write("staged.ts", "staged\n");
    repo.git("add", "staged.ts");
    repo.write("tracked.ts", "unstaged\n");
    repo.write("new.ts", "untracked\n");

    const output = await captureWithoutMutation(repo, { kind: "local" });
    const diff = readFileSync(output.diffPath, "utf8");

    expect(diff).toContain("staged.ts");
    expect(diff).toContain("tracked.ts");
    expect(diff).toContain("new.ts");
    expect(output.worktreePath).toBeNull();
    expect(output.files.map((file) => file.path)).toEqual(
      expect.arrayContaining(["staged.ts", "tracked.ts", "new.ts"]),
    );
  });

  it("captures a single commit in an isolated worktree", async () => {
    const repo = fixtureRepo();
    const parent = repo.git("rev-parse", "HEAD");
    const commit = repo.commit("commit.ts", "commit\n");

    const output = await captureWithoutMutation(repo, {
      kind: "range",
      range: commit,
    });

    expect(output.baseRef).toBe(parent);
    expect(output.headRef).toBe(commit);
    expect(output.worktreePath).not.toBe(repo.path);
    expect(readFileSync(output.diffPath, "utf8")).toContain("commit.ts");
    await cleanupCapture(output);
    expect(existsSync(output.worktreePath!)).toBe(false);
  });

  it("captures A..B with A as the exact base", async () => {
    const repo = fixtureRepo();
    const base = repo.git("rev-parse", "HEAD");
    const head = repo.commit("range.ts", "range\n");

    const output = await captureWithoutMutation(repo, {
      kind: "range",
      range: `${base}..${head}`,
    });

    expect(output.baseRef).toBe(base);
    expect(output.headRef).toBe(head);
    expect(readFileSync(output.diffPath, "utf8")).toContain("range.ts");
    await cleanupCapture(output);
  });

  it("captures A...B from the actual merge base", async () => {
    const repo = fixtureRepo();
    const fork = repo.git("rev-parse", "HEAD");
    repo.commit("main.ts", "main\n");
    repo.git("branch", "feature", fork);
    repo.git("switch", "-q", "feature");
    const feature = repo.commit("feature.ts", "feature\n");
    repo.git("switch", "-q", "main");

    const output = await captureWithoutMutation(repo, {
      kind: "range",
      range: `main...feature`,
    });

    expect(output.baseRef).toBe(fork);
    expect(output.headRef).toBe(feature);
    const diff = readFileSync(output.diffPath, "utf8");
    expect(diff).toContain("feature.ts");
    expect(diff).not.toContain("main.ts");
    await cleanupCapture(output);
  });

  it("captures only the requested repository-relative file", async () => {
    const repo = fixtureRepo();
    repo.write("tracked.ts", "requested\n");
    repo.write("other.ts", "other\n");

    const output = await captureWithoutMutation(repo, {
      kind: "file",
      path: "tracked.ts",
    });

    const diff = readFileSync(output.diffPath, "utf8");
    expect(diff).toContain("tracked.ts");
    expect(diff).not.toContain("other.ts");
  });

  it("captures one file from an explicit base while preserving later local edits", async () => {
    const repo = fixtureRepo();
    const base = repo.git("rev-parse", "HEAD");
    repo.commit("tracked.ts", "committed\n", "committed edit");
    repo.write("tracked.ts", "local edit\n");
    repo.write("other.ts", "other\n");

    const output = await captureWithoutMutation(repo, {
      kind: "file",
      path: "tracked.ts",
      base,
    });

    expect(output.baseRef).toBe(base);
    const diff = readFileSync(output.diffPath, "utf8");
    expect(diff).toContain("local edit");
    expect(diff).not.toContain("other.ts");
  });

  it("resolves a file path relative to a workspace subdirectory", async () => {
    const repo = fixtureRepo();
    repo.write("src/nested.ts", "initial\n");
    repo.git("add", "src/nested.ts");
    repo.git("commit", "-q", "-m", "nested");
    repo.write("src/nested.ts", "changed\n");
    const nestedRequest = request(repo, { kind: "file", path: "nested.ts" });
    nestedRequest.workspace = join(repo.path, "src");
    const before = repo.statusPorcelain();

    const output = await captureTarget(nestedRequest);

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(readFileSync(output.diffPath, "utf8")).toContain("src/nested.ts");
  });

  it("captures renames and deletions without changing porcelain state", async () => {
    const repo = fixtureRepo();
    repo.write("delete.ts", "delete me\n");
    repo.git("add", "delete.ts");
    repo.git("commit", "-q", "-m", "add files");
    const base = repo.git("rev-parse", "HEAD");
    repo.git("mv", "tracked.ts", "renamed.ts");
    repo.git("rm", "-q", "delete.ts");
    repo.git("commit", "-q", "-m", "rename and delete");
    const head = repo.git("rev-parse", "HEAD");

    const output = await captureWithoutMutation(repo, {
      kind: "range",
      range: `${base}..${head}`,
    });

    const diff = readFileSync(output.diffPath, "utf8");
    expect(diff).toContain("rename from tracked.ts");
    expect(diff).toContain("rename to renamed.ts");
    expect(diff).toContain("deleted file mode");
    await cleanupCapture(output);
  });

  it("reports binary changes explicitly and caps the response", async () => {
    const repo = fixtureRepo();
    const base = repo.git("rev-parse", "HEAD");
    repo.write("image.bin", Buffer.from([0, 1, 2, 3]));
    repo.git("add", "image.bin");
    repo.git("commit", "-q", "-m", "binary");
    const head = repo.git("rev-parse", "HEAD");
    const engineRequest = request(repo, {
      kind: "range",
      range: `${base}..${head}`,
    });
    const before = repo.statusPorcelain();

    const response = await dispatch(engineRequest);

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(response.status).toBe("inconclusive");
    expect(response.diagnostics).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: "capture_incomplete" }),
      ]),
    );
    const output = response.output as unknown as CaptureTargetOutput;
    expect(output.skippedFiles).toEqual(
      expect.arrayContaining([expect.objectContaining({ path: "image.bin" })]),
    );
    expect(output.files).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "image.bin", binary: true }),
      ]),
    );
    await cleanupCapture(output);
  });

  it("rejects a symlinked file path that escapes the repository", async () => {
    const repo = fixtureRepo();
    const outside = join(dirname(repo.path), "outside.ts");
    writeFileSync(outside, "outside\n");
    symlinkSync(outside, join(repo.path, "escape.ts"));
    const before = repo.statusPorcelain();

    await expect(
      captureTarget(request(repo, { kind: "file", path: "escape.ts" })),
    ).rejects.toMatchObject({ code: "invalid_target" });
    expect(repo.statusPorcelain().equals(before)).toBe(true);
  });

  it("reports a deliberately oversized untracked file instead of hiding it", async () => {
    const repo = fixtureRepo();
    repo.write("oversized.ts", "x".repeat(1_000_001));
    const before = repo.statusPorcelain();

    const response = await dispatch(request(repo, { kind: "local" }));

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(response.status).toBe("inconclusive");
    const output = response.output as unknown as CaptureTargetOutput;
    expect(output.skippedFiles).toEqual([
      expect.objectContaining({ path: "oversized.ts", bytes: 1_000_001 }),
    ]);
    expect(readFileSync(output.diffPath, "utf8")).toBe("");
  });

  it("returns a typed failed/no_changes response for a clean target", async () => {
    const repo = fixtureRepo();
    const before = repo.statusPorcelain();

    const response = await dispatch(request(repo, { kind: "local" }));

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(response).toMatchObject({
      status: "failed",
      diagnostics: [{ code: "no_changes" }],
    });
  });

  it("rejects a symlinked artifact root before writing files", async () => {
    const repo = fixtureRepo();
    repo.write("tracked.ts", "changed\n");
    const actual = join(dirname(repo.artifactRoot), "actual-run");
    mkdirSync(actual, { mode: 0o700 });
    rmSync(repo.artifactRoot, { recursive: true });
    symlinkSync(actual, repo.artifactRoot);
    const before = repo.statusPorcelain();

    await expect(
      captureTarget(request(repo, { kind: "local" })),
    ).rejects.toBeInstanceOf(CaptureTargetError);
    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(readFileSync(join(repo.path, "tracked.ts"), "utf8")).toBe(
      "changed\n",
    );
  });

  it("fetches a PR head into a disposable worktree and never removes its source checkout", async () => {
    const repo = fixtureRepo();
    repo.git("switch", "-q", "-c", "pull-7");
    const head = repo.commit("pull.ts", "pull request\n");
    repo.git("switch", "-q", "main");
    repo.git("update-ref", "refs/pull/7/head", head);
    repo.git("remote", "add", "origin", repo.path);
    const before = repo.statusPorcelain();

    const output = await captureTarget(
      request(repo, { kind: "pr", number: 7, ownerRepo: "owner/repo" }),
    );

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(output.headRef).toBe(head);
    expect(output.baseRef).toBe(repo.git("rev-parse", "main"));
    expect(output.worktreePath).not.toBe(repo.path);
    expect(
      resolve(output.diffPath).startsWith(realpathSync(repo.artifactRoot)),
    ).toBe(true);
    expect(
      resolve(output.planPath).startsWith(realpathSync(repo.artifactRoot)),
    ).toBe(true);
    const diff = readFileSync(output.diffPath);
    expect(planFor(output)).toMatchObject({
      hermes: {
        schemaVersion: 1,
        runId: "review-run",
        targetKind: "pr",
        requestedTarget: { kind: "pr", number: 7, ownerRepo: "owner/repo" },
        baseRef: output.baseRef,
        headRef: head,
        diffSha256: createHash("sha256").update(diff).digest("hex"),
      },
      diffPathAbsolute: output.diffPath,
    });

    await cleanupCapture(output);
    expect(existsSync(repo.path)).toBe(true);
    expect(existsSync(output.worktreePath!)).toBe(false);
  });
});

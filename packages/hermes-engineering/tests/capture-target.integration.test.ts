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
import { delimiter, dirname, join, resolve } from "node:path";

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
let fixtureSequence = 0;

const gitEnvironment = {
  ...process.env,
  GIT_AUTHOR_NAME: "Hermes Test",
  GIT_AUTHOR_EMAIL: "hermes@example.test",
  GIT_COMMITTER_NAME: "Hermes Test",
  GIT_COMMITTER_EMAIL: "hermes@example.test",
  GIT_AUTHOR_DATE: "2000-01-01T00:00:00Z",
  GIT_COMMITTER_DATE: "2000-01-01T00:00:00Z",
  GIT_CONFIG_GLOBAL: process.platform === "win32" ? "NUL" : "/dev/null",
  GIT_CONFIG_NOSYSTEM: "1",
};

class FixtureRepo {
  readonly path: string;
  readonly artifactRoot: string;

  constructor(root: string, identity: number) {
    this.path = join(root, "repo");
    this.artifactRoot = join(root, "review-run");
    mkdirSync(this.path);
    mkdirSync(this.artifactRoot, { mode: 0o700 });
    chmodSync(this.artifactRoot, 0o700);
    this.git("init", "-q", "-b", "main");
    this.write("tracked.ts", `initial fixture ${identity}\n`);
    this.git("add", "tracked.ts");
    this.git("commit", "-q", "-m", `initial fixture ${identity}`);
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
  fixtureSequence += 1;
  return new FixtureRepo(root, fixtureSequence);
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

interface FakePrContext {
  url: string;
  headRefName: string;
  headRefOid: string;
  baseRefName: string;
  baseRefOid: string;
}

const withFakeGh = async <T>(
  repo: FixtureRepo,
  context: FakePrContext,
  action: () => Promise<T>,
): Promise<T> => {
  const bin = join(dirname(repo.path), `fake-gh-${Date.now()}`);
  const executable = join(bin, "gh");
  mkdirSync(bin);
  const expectedArgs = [
    "pr",
    "view",
    "7",
    "--repo",
    "owner/repo",
    "--json",
    "url,headRefName,headRefOid,baseRefName,baseRefOid",
  ];
  writeFileSync(
    executable,
    `#!${process.execPath}\n` +
      `const actual = process.argv.slice(2);\n` +
      `const expected = ${JSON.stringify(expectedArgs)};\n` +
      `if (JSON.stringify(actual) !== JSON.stringify(expected)) {\n` +
      `  process.stderr.write(JSON.stringify(actual));\n` +
      `  process.exit(2);\n` +
      `}\n` +
      `process.stdout.write(${JSON.stringify(JSON.stringify(context))});\n`,
  );
  chmodSync(executable, 0o755);
  const previousPath = process.env.PATH;
  process.env.PATH = `${bin}${delimiter}${previousPath ?? ""}`;
  try {
    return await action();
  } finally {
    if (previousPath === undefined) delete process.env.PATH;
    else process.env.PATH = previousPath;
  }
};

const configureAuthoritativePr = (
  source: FixtureRepo,
): {
  context: FakePrContext;
  baseSha: string;
  headSha: string;
  wrongSha: string;
  sourceRootSha: string;
  authoritativeRootSha: string;
} => {
  const sourceRootSha = source.git("rev-list", "--max-parents=0", "HEAD");
  source.git("switch", "-q", "-c", "wrong-pull-7");
  const wrongSha = source.commit("wrong.ts", "wrong repository\n");
  source.git("switch", "-q", "main");
  source.git("update-ref", "refs/pull/7/head", wrongSha);
  source.git("remote", "add", "origin", source.path);

  const authoritative = fixtureRepo();
  const authoritativeRootSha = authoritative.git(
    "rev-list",
    "--max-parents=0",
    "HEAD",
  );
  authoritative.git("switch", "-q", "-c", "maintenance/1.x");
  const baseSha = authoritative.commit("maintenance.ts", "maintenance base\n");
  authoritative.git("switch", "-q", "-c", "pull-7");
  const headSha = authoritative.commit(
    "pull.ts",
    "authoritative pull request\n",
  );
  authoritative.git("switch", "-q", "main");
  authoritative.git("update-ref", "refs/pull/7/head", headSha);

  const repositoryUrl = "https://github.com/owner/repo.git";
  source.git("config", `url.${authoritative.path}.insteadOf`, repositoryUrl);
  return {
    baseSha,
    headSha,
    wrongSha,
    sourceRootSha,
    authoritativeRootSha,
    context: {
      url: "https://github.com/owner/repo/pull/7",
      headRefName: "pull-7",
      headRefOid: headSha,
      baseRefName: "maintenance/1.x",
      baseRefOid: baseSha,
    },
  };
};

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

  it("uses authoritative PR repository and non-default base context", async () => {
    const repo = fixtureRepo();
    const {
      context,
      baseSha,
      headSha,
      wrongSha,
      sourceRootSha,
      authoritativeRootSha,
    } = configureAuthoritativePr(repo);
    const before = repo.statusPorcelain();

    const output = await withFakeGh(repo, context, () =>
      captureTarget(
        request(repo, { kind: "pr", number: 7, ownerRepo: "owner/repo" }),
      ),
    );

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(authoritativeRootSha).not.toBe(sourceRootSha);
    expect(output.headRef).toBe(headSha);
    expect(output.headRef).not.toBe(wrongSha);
    expect(output.baseRef).toBe(baseSha);
    expect(output.worktreePath).not.toBe(repo.path);
    expect(
      resolve(output.diffPath).startsWith(realpathSync(repo.artifactRoot)),
    ).toBe(true);
    expect(
      resolve(output.planPath).startsWith(realpathSync(repo.artifactRoot)),
    ).toBe(true);
    const diff = readFileSync(output.diffPath);
    expect(diff.toString("utf8")).toContain("pull.ts");
    expect(diff.toString("utf8")).not.toContain("maintenance.ts");
    expect(diff.toString("utf8")).not.toContain("wrong.ts");
    expect(planFor(output)).toMatchObject({
      hermes: {
        schemaVersion: 1,
        runId: "review-run",
        targetKind: "pr",
        requestedTarget: { kind: "pr", number: 7, ownerRepo: "owner/repo" },
        baseRef: output.baseRef,
        headRef: headSha,
        prContext: {
          ownerRepo: "owner/repo",
          number: 7,
          url: context.url,
          headRefName: context.headRefName,
          headSha,
          baseRefName: context.baseRefName,
          baseSha,
        },
        resolvedIdentity: { baseRef: baseSha, headRef: headSha },
        diffSha256: createHash("sha256").update(diff).digest("hex"),
      },
      diffPathAbsolute: output.diffPath,
    });

    await cleanupCapture(output);
    expect(existsSync(repo.path)).toBe(true);
    expect(existsSync(output.worktreePath!)).toBe(false);
  });

  it("fails before fetch when gh context does not prove ownerRepo identity", async () => {
    const repo = fixtureRepo();
    const { context, wrongSha } = configureAuthoritativePr(repo);
    const before = repo.statusPorcelain();

    const response = await withFakeGh(
      repo,
      { ...context, url: "https://github.com/other/repo/pull/7" },
      () =>
        dispatch(
          request(repo, { kind: "pr", number: 7, ownerRepo: "owner/repo" }),
        ),
    );

    expect(repo.statusPorcelain().equals(before)).toBe(true);
    expect(response).toMatchObject({
      status: "failed",
      diagnostics: [{ code: "pr_repository_mismatch" }],
    });
    expect(repo.git("rev-parse", "refs/pull/7/head")).toBe(wrongSha);
    expect(repo.git("worktree", "list", "--porcelain")).not.toContain(
      ".hermes-review-",
    );
  });
});

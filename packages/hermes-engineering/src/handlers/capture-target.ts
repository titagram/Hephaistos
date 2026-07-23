import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "node:path";

// The JavaScript facade keeps Hermes' stricter TypeScript settings from being
// imposed on untouched vendored sources; its declaration is the typed boundary.
import {
  buildDiffPlan,
  buildPlanReport,
  captureLocalDiff,
  LITERAL_PATHSPECS,
  PINNED_DIFF_CONFIG,
  PINNED_DIFF_FLAGS,
  resolveMergeBase,
  stringifyPlanReport,
} from "../shims/qwenReviewRuntime.js";
import {
  parseCaptureInput,
  type CaptureInput,
  type CaptureSkippedFile,
  type CaptureTargetOutput,
  type EngineRequest,
} from "../protocol.js";
import { discoverBuildTestPlan } from "./build-test.js";

const GIT_TIMEOUT_MS = 120_000;
const DIFF_NAME = "target.diff";
const PLAN_NAME = "plan.json";
const WORKTREE_PREFIX = ".hermes-review-";

interface CapturedDiff {
  targetKind: CaptureInput["kind"];
  baseRef: string | null;
  headRef: string;
  diff: Buffer;
  worktreePath: string | null;
  postImageRoot: string;
  skipped: CaptureSkippedFile[];
  prContext?: CapturedPrContext;
}

interface GitProbe {
  fetch(remote: string, ref: string): boolean;
  refExists(ref: string): boolean;
  mergeBase(left: string, right: string): string | null;
}

interface PullRequestContext {
  url: string;
  headRefName: string;
  headRefOid: string;
  baseRefName: string;
  baseRefOid: string;
}

interface CapturedPrContext {
  ownerRepo: string;
  number: number;
  url: string;
  headRefName: string;
  headSha: string;
  baseRefName: string;
  baseSha: string;
}

export class CaptureTargetError extends Error {
  readonly code: string;
  readonly output?: CaptureTargetOutput;

  constructor(code: string, message: string, output?: CaptureTargetOutput) {
    super(message);
    this.name = "CaptureTargetError";
    this.code = code;
    if (output !== undefined) this.output = output;
  }
}

const gitOptions = (cwd: string) => ({
  cwd,
  env: { ...process.env, GIT_TERMINAL_PROMPT: "0" },
  timeout: GIT_TIMEOUT_MS,
  maxBuffer: 512 * 1024 * 1024,
});

const gitText = (cwd: string, ...args: string[]): string =>
  execFileSync("git", args, { ...gitOptions(cwd), encoding: "utf8" })
    .replace(/\r\n/g, "\n")
    .trim();

const gitTextOptional = (cwd: string, ...args: string[]): string | null => {
  try {
    return gitText(cwd, ...args);
  } catch {
    return null;
  }
};

const gitSucceeds = (cwd: string, ...args: string[]): boolean => {
  try {
    gitText(cwd, ...args);
    return true;
  } catch {
    return false;
  }
};

const gitRaw = (cwd: string, ...args: string[]): Buffer =>
  execFileSync("git", args, gitOptions(cwd));

const escaped = (root: string, candidate: string): boolean => {
  const rel = relative(root, candidate);
  return rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel);
};

const validatedDirectory = (path: string, label: string): string => {
  let stat;
  try {
    stat = lstatSync(path);
  } catch (cause) {
    throw new CaptureTargetError(
      "invalid_path",
      `${label} could not be inspected: ${(cause as Error).message}`,
    );
  }
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new CaptureTargetError(
      "invalid_path",
      `${label} must be a real directory, not a symlink`,
    );
  }
  return realpathSync(path);
};

const validatedArtifactRoot = (path: string): string => {
  const root = validatedDirectory(path, "artifactRoot");
  if (process.platform !== "win32" && (lstatSync(root).mode & 0o077) !== 0) {
    throw new CaptureTargetError(
      "invalid_artifact_root",
      "artifactRoot must be private to the current user",
    );
  }
  return root;
};

const repositoryFor = (
  workspace: string,
): { repoRoot: string; workspace: string } => {
  const canonicalWorkspace = validatedDirectory(workspace, "workspace");
  const rawRoot = gitTextOptional(
    canonicalWorkspace,
    "rev-parse",
    "--show-toplevel",
  );
  if (!rawRoot) {
    throw new CaptureTargetError(
      "invalid_repository",
      "workspace is not a Git repository",
    );
  }
  const repoRoot = realpathSync(rawRoot);
  if (escaped(repoRoot, canonicalWorkspace)) {
    throw new CaptureTargetError(
      "invalid_repository",
      "workspace is outside the resolved Git repository",
    );
  }
  return { repoRoot, workspace: canonicalWorkspace };
};

const validateRelativeFile = (
  workspace: string,
  repoRoot: string,
  path: string,
): string => {
  if (path.length === 0 || isAbsolute(path) || path.includes("\0")) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path must be a non-empty repository-relative path",
    );
  }
  const absolute = resolve(workspace, path);
  if (escaped(repoRoot, absolute) || absolute === repoRoot) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path escapes the repository",
    );
  }

  let existing = absolute;
  while (!existsSync(existing) && existing !== repoRoot)
    existing = dirname(existing);
  let canonicalExisting: string;
  try {
    canonicalExisting = realpathSync(existing);
  } catch (cause) {
    throw new CaptureTargetError(
      "invalid_target",
      `file path could not be resolved: ${(cause as Error).message}`,
    );
  }
  if (escaped(repoRoot, canonicalExisting)) {
    throw new CaptureTargetError(
      "invalid_target",
      "file path resolves outside the repository",
    );
  }
  return relative(repoRoot, absolute).split(sep).join("/");
};

const resolveCommit = (repoRoot: string, ref: string): string => {
  if (ref.length === 0 || ref.startsWith("-") || /[\0\r\n]/.test(ref)) {
    throw new CaptureTargetError("invalid_target", `invalid Git ref: ${ref}`);
  }
  const sha = gitTextOptional(
    repoRoot,
    "rev-parse",
    "--verify",
    `${ref}^{commit}`,
  );
  if (!sha) {
    throw new CaptureTargetError(
      "invalid_target",
      `Git ref does not resolve: ${ref}`,
    );
  }
  return sha;
};

const emptyTree = (repoRoot: string): string =>
  execFileSync("git", ["hash-object", "-t", "tree", "--stdin"], {
    ...gitOptions(repoRoot),
    input: Buffer.alloc(0),
    encoding: "utf8",
  }).trim();

const withWorkingDirectory = <T>(cwd: string, action: () => T): T => {
  const previous = process.cwd();
  try {
    process.chdir(cwd);
    return action();
  } finally {
    process.chdir(previous);
  }
};

const captureLocal = (
  input: Extract<CaptureInput, { kind: "local" | "file" }>,
  repoRoot: string,
  workspace: string,
): CapturedDiff => {
  const requestedPath = input.kind === "file" ? input.path : undefined;
  const path =
    requestedPath === undefined
      ? undefined
      : validateRelativeFile(workspace, repoRoot, requestedPath);
  const local = withWorkingDirectory(workspace, () =>
    captureLocalDiff(
      requestedPath === undefined ? {} : { file: requestedPath },
    ),
  );
  const currentHead = gitTextOptional(
    repoRoot,
    "rev-parse",
    "--verify",
    "HEAD",
  );
  let baseRef = currentHead ?? emptyTree(repoRoot);
  let diff = local.diff;

  if (input.kind === "file" && input.base !== undefined) {
    baseRef = resolveCommit(repoRoot, input.base);
    if (!local.untracked.includes(path!)) {
      diff = gitRaw(
        repoRoot,
        LITERAL_PATHSPECS,
        ...PINNED_DIFF_CONFIG,
        "diff",
        ...PINNED_DIFF_FLAGS,
        baseRef,
        "--",
        path!,
      );
    }
  }

  return {
    targetKind: input.kind,
    baseRef,
    headRef: "WORKTREE",
    diff,
    worktreePath: null,
    postImageRoot: repoRoot,
    skipped: local.skipped,
  };
};

const rangeIdentity = (
  repoRoot: string,
  range: string,
): { baseRef: string; headRef: string } => {
  const triple = range.indexOf("...");
  if (triple >= 0) {
    if (range.indexOf("...", triple + 3) >= 0) {
      throw new CaptureTargetError(
        "invalid_target",
        `invalid Git range: ${range}`,
      );
    }
    const left = range.slice(0, triple);
    const right = range.slice(triple + 3);
    const leftSha = resolveCommit(repoRoot, left);
    const headRef = resolveCommit(repoRoot, right);
    const baseRef = gitTextOptional(repoRoot, "merge-base", leftSha, headRef);
    if (!baseRef) {
      throw new CaptureTargetError("invalid_target", "range has no merge base");
    }
    return { baseRef, headRef };
  }

  const double = range.indexOf("..");
  if (double >= 0) {
    if (range.indexOf("..", double + 2) >= 0) {
      throw new CaptureTargetError(
        "invalid_target",
        `invalid Git range: ${range}`,
      );
    }
    return {
      baseRef: resolveCommit(repoRoot, range.slice(0, double)),
      headRef: resolveCommit(repoRoot, range.slice(double + 2)),
    };
  }

  const headRef = resolveCommit(repoRoot, range);
  return {
    baseRef:
      gitTextOptional(repoRoot, "rev-parse", "--verify", `${headRef}^`) ??
      emptyTree(repoRoot),
    headRef,
  };
};

const worktreePathFor = (repoRoot: string, runId: string): string => {
  const suffix = createHash("sha256")
    .update(`${repoRoot}\0${runId}`)
    .digest("hex")
    .slice(0, 16);
  return join(dirname(repoRoot), `${WORKTREE_PREFIX}${suffix}`);
};

const addWorktree = (
  repoRoot: string,
  worktreePath: string,
  headRef: string,
): void => {
  if (existsSync(worktreePath)) {
    throw new CaptureTargetError(
      "worktree_exists",
      `disposable worktree path already exists: ${worktreePath}`,
    );
  }
  gitText(
    repoRoot,
    "worktree",
    "add",
    "--quiet",
    "--detach",
    worktreePath,
    headRef,
  );
};

const captureRange = (
  input: Extract<CaptureInput, { kind: "range" }>,
  repoRoot: string,
  runId: string,
): CapturedDiff => {
  const { baseRef, headRef } = rangeIdentity(repoRoot, input.range);
  const worktreePath = worktreePathFor(repoRoot, runId);
  addWorktree(repoRoot, worktreePath, headRef);
  try {
    return {
      targetKind: "range",
      baseRef,
      headRef,
      diff: gitRaw(
        repoRoot,
        ...PINNED_DIFF_CONFIG,
        "diff",
        ...PINNED_DIFF_FLAGS,
        `${baseRef}..${headRef}`,
      ),
      worktreePath,
      postImageRoot: worktreePath,
      skipped: [],
    };
  } catch (cause) {
    removeWorktree(worktreePath);
    throw cause;
  }
};

const pullRequestContext = (
  repoRoot: string,
  input: Extract<CaptureInput, { kind: "pr" }>,
): PullRequestContext => {
  let parsed: unknown;
  try {
    const raw = execFileSync(
      "gh",
      [
        "pr",
        "view",
        String(input.number),
        "--repo",
        input.ownerRepo,
        "--json",
        "url,headRefName,headRefOid,baseRefName,baseRefOid",
      ],
      {
        cwd: repoRoot,
        env: process.env,
        encoding: "utf8",
        timeout: GIT_TIMEOUT_MS,
        maxBuffer: 16 * 1024 * 1024,
      },
    );
    parsed = JSON.parse(raw) as unknown;
  } catch (cause) {
    throw new CaptureTargetError(
      "pr_context_unresolved",
      `could not resolve ${input.ownerRepo}#${input.number} with gh: ${(cause as Error).message}`,
    );
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new CaptureTargetError(
      "pr_context_unresolved",
      "gh PR context must be an object",
    );
  }
  const value = parsed as Record<string, unknown>;
  const field = (name: keyof PullRequestContext): string => {
    const candidate = value[name];
    if (typeof candidate !== "string" || candidate.length === 0) {
      throw new CaptureTargetError(
        "pr_context_unresolved",
        `gh PR context field ${name} must be a non-empty string`,
      );
    }
    return candidate;
  };
  const context: PullRequestContext = {
    url: field("url"),
    headRefName: field("headRefName"),
    headRefOid: field("headRefOid").toLowerCase(),
    baseRefName: field("baseRefName"),
    baseRefOid: field("baseRefOid").toLowerCase(),
  };
  if (
    !/^[0-9a-f]{40,64}$/.test(context.headRefOid) ||
    !/^[0-9a-f]{40,64}$/.test(context.baseRefOid) ||
    !gitSucceeds(
      repoRoot,
      "check-ref-format",
      `refs/heads/${context.headRefName}`,
    ) ||
    !gitSucceeds(
      repoRoot,
      "check-ref-format",
      `refs/heads/${context.baseRefName}`,
    )
  ) {
    throw new CaptureTargetError(
      "pr_context_unresolved",
      "gh PR context contains an invalid ref name or object ID",
    );
  }

  let actualUrl: URL;
  try {
    actualUrl = new URL(context.url);
  } catch {
    throw new CaptureTargetError(
      "pr_repository_mismatch",
      "gh PR context returned an invalid repository URL",
    );
  }
  const expectedPath = `/${input.ownerRepo}/pull/${input.number}`.toLowerCase();
  if (
    actualUrl.protocol !== "https:" ||
    actualUrl.hostname.toLowerCase() !== "github.com" ||
    actualUrl.pathname.replace(/\/$/u, "").toLowerCase() !== expectedPath
  ) {
    throw new CaptureTargetError(
      "pr_repository_mismatch",
      `gh resolved ${context.url}, not ${input.ownerRepo}#${input.number}`,
    );
  }
  return context;
};

const fetchVerifiedRef = (
  repoRoot: string,
  repositoryUrl: string,
  ref: string,
  expectedSha: string,
  mismatchCode: "pr_head_changed" | "pr_base_changed",
): string => {
  try {
    gitText(repoRoot, "fetch", "--quiet", "--no-tags", repositoryUrl, ref);
  } catch (cause) {
    throw new CaptureTargetError(
      "pr_fetch_failed",
      `could not fetch ${ref} from the validated PR repository: ${(cause as Error).message}`,
    );
  }
  const fetchedSha = resolveCommit(repoRoot, "FETCH_HEAD");
  if (fetchedSha !== expectedSha) {
    throw new CaptureTargetError(
      mismatchCode,
      `fetched ${ref} at ${fetchedSha}, but gh resolved ${expectedSha}`,
    );
  }
  return fetchedSha;
};

const capturePullRequest = (
  input: Extract<CaptureInput, { kind: "pr" }>,
  repoRoot: string,
  runId: string,
): CapturedDiff => {
  const context = pullRequestContext(repoRoot, input);
  const repositoryUrl = `https://github.com/${input.ownerRepo}.git`;
  const headRef = fetchVerifiedRef(
    repoRoot,
    repositoryUrl,
    `refs/pull/${input.number}/head`,
    context.headRefOid,
    "pr_head_changed",
  );
  const baseSha = fetchVerifiedRef(
    repoRoot,
    repositoryUrl,
    `refs/heads/${context.baseRefName}`,
    context.baseRefOid,
    "pr_base_changed",
  );
  const remoteIdentity = input.ownerRepo;
  const probe: GitProbe = {
    fetch: (remoteName, ref) =>
      remoteName === remoteIdentity && ref === context.baseRefName,
    refExists: (ref) => ref === `${remoteIdentity}/${context.baseRefName}`,
    mergeBase: (_left, right) =>
      right === headRef
        ? gitTextOptional(repoRoot, "merge-base", baseSha, headRef)
        : null,
  };
  const mergeBase = resolveMergeBase(
    remoteIdentity,
    context.baseRefName,
    headRef,
    probe,
  );
  if (!mergeBase.sha) {
    throw new CaptureTargetError(
      "pr_base_unresolved",
      `could not resolve the merge base of ${context.baseRefName} and ${headRef}`,
    );
  }

  const worktreePath = worktreePathFor(repoRoot, runId);
  addWorktree(repoRoot, worktreePath, headRef);
  try {
    return {
      targetKind: "pr",
      baseRef: mergeBase.sha,
      headRef,
      diff: gitRaw(
        repoRoot,
        ...PINNED_DIFF_CONFIG,
        "diff",
        ...PINNED_DIFF_FLAGS,
        `${mergeBase.sha}..${headRef}`,
      ),
      worktreePath,
      postImageRoot: worktreePath,
      skipped: [],
      prContext: {
        ownerRepo: input.ownerRepo,
        number: input.number,
        url: context.url,
        headRefName: context.headRefName,
        headSha: context.headRefOid,
        baseRefName: context.baseRefName,
        baseSha: context.baseRefOid,
      },
    };
  } catch (cause) {
    removeWorktree(worktreePath);
    throw cause;
  }
};

const lineCount = (root: string, path: string): number => {
  const absolute = resolve(root, path);
  if (escaped(root, absolute)) return 0;
  try {
    const stat = lstatSync(absolute);
    if (!stat.isFile()) return 0;
    const contents = readFileSync(absolute);
    if (contents.length === 0) return 0;
    let lines = 0;
    for (const byte of contents) if (byte === 0x0a) lines++;
    return contents[contents.length - 1] === 0x0a ? lines : lines + 1;
  } catch {
    return 0;
  }
};

const atomicWrite = (
  root: string,
  name: string,
  contents: string | Buffer,
): string => {
  const destination = join(root, name);
  if (dirname(destination) !== root) {
    throw new CaptureTargetError(
      "invalid_artifact",
      "artifact path escapes run root",
    );
  }
  const temporary = join(
    root,
    `.${name}.${randomBytes(12).toString("hex")}.tmp`,
  );
  let descriptor: number | undefined;
  try {
    descriptor = openSync(temporary, "wx", 0o600);
    writeFileSync(descriptor, contents);
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporary, destination);
    chmodSync(destination, 0o600);
    return destination;
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    try {
      unlinkSync(temporary);
    } catch {
      // The successful rename removes the temporary name; failed writes clean up best-effort.
    }
  }
};

const binarySkips = (
  files: ReturnType<typeof buildPlanReport>["files"],
): CaptureSkippedFile[] =>
  files
    .filter((file) => file.binary)
    .map((file) => ({
      path: file.path,
      bytes: null,
      reason: "binary content is not represented in the unified diff",
    }));

const mergeSkips = (
  left: CaptureSkippedFile[],
  right: CaptureSkippedFile[],
): CaptureSkippedFile[] => {
  const result = [...left];
  for (const skipped of right) {
    if (!result.some((existing) => existing.path === skipped.path))
      result.push(skipped);
  }
  return result;
};

export async function captureTarget(
  request: EngineRequest,
): Promise<CaptureTargetOutput> {
  const input = parseCaptureInput(request.input);
  const artifactRoot = validatedArtifactRoot(request.artifactRoot);
  const runId = basename(artifactRoot);
  const { repoRoot, workspace } = repositoryFor(request.workspace);
  let captured: CapturedDiff | undefined;

  try {
    if (input.kind === "local" || input.kind === "file") {
      captured = captureLocal(input, repoRoot, workspace);
    } else if (input.kind === "range") {
      captured = captureRange(input, repoRoot, runId);
    } else {
      captured = capturePullRequest(input, repoRoot, runId);
    }

    const diffPath = atomicWrite(artifactRoot, DIFF_NAME, captured.diff);
    const plan = buildDiffPlan(captured.diff.toString("utf8"));
    const reportBase = buildPlanReport(plan, (path) =>
      lineCount(captured!.postImageRoot, path),
    );
    const skippedFiles = mergeSkips(
      captured.skipped,
      binarySkips(reportBase.files),
    );
    const report = {
      ...reportBase,
      hermes: {
        schemaVersion: 1,
        runId,
        targetKind: captured.targetKind,
        requestedTarget: input,
        baseRef: captured.baseRef,
        headRef: captured.headRef,
        ...(captured.prContext
          ? {
              prContext: captured.prContext,
              resolvedIdentity: {
                baseRef: captured.baseRef,
                headRef: captured.headRef,
              },
            }
          : {}),
        diffSha256: createHash("sha256").update(captured.diff).digest("hex"),
        skippedFiles,
        buildTest: discoverBuildTestPlan(
          captured.postImageRoot,
          reportBase.files,
        ),
      },
      diffPathAbsolute: diffPath,
    };
    const planPath = atomicWrite(
      artifactRoot,
      PLAN_NAME,
      stringifyPlanReport(report),
    );
    const output: CaptureTargetOutput = {
      targetKind: captured.targetKind,
      baseRef: captured.baseRef,
      headRef: captured.headRef,
      diffPath,
      planPath,
      worktreePath: captured.worktreePath,
      skippedFiles,
      files: reportBase.files,
      chunks: reportBase.chunks,
    };
    if (captured.diff.length === 0 && skippedFiles.length === 0) {
      throw new CaptureTargetError(
        "no_changes",
        "the selected target has no changes to review",
        output,
      );
    }
    return output;
  } catch (cause) {
    if (captured?.worktreePath) {
      try {
        removeWorktree(captured.worktreePath);
      } catch {
        // Preserve the capture error. Cleanup can be retried using the recorded path.
      }
    }
    throw cause;
  }
}

const removeWorktree = (worktreePath: string): void => {
  if (!basename(worktreePath).startsWith(WORKTREE_PREFIX)) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "refusing to remove an unknown worktree",
    );
  }
  if (!existsSync(worktreePath)) return;
  const stat = lstatSync(worktreePath);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "worktree path is not a real directory",
    );
  }
  const canonical = realpathSync(worktreePath);
  if (canonical !== resolve(worktreePath)) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "worktree path is not canonical",
    );
  }
  const gitEntry = lstatSync(join(canonical, ".git"));
  if (!gitEntry.isFile() || gitEntry.isSymbolicLink()) {
    throw new CaptureTargetError(
      "unsafe_cleanup",
      "refusing to remove a source checkout as a disposable worktree",
    );
  }
  const common = gitText(canonical, "rev-parse", "--git-common-dir");
  const commonDirectory = realpathSync(resolve(canonical, common));
  execFileSync(
    "git",
    [
      `--git-dir=${commonDirectory}`,
      "worktree",
      "remove",
      "--force",
      canonical,
    ],
    gitOptions(dirname(canonical)),
  );
  gitTextOptional(
    dirname(canonical),
    `--git-dir=${commonDirectory}`,
    "worktree",
    "prune",
  );
};

export async function cleanupCapture(
  output: CaptureTargetOutput,
): Promise<void> {
  if (output.worktreePath) removeWorktree(output.worktreePath);
}

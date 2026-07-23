import { createHash, randomBytes } from "node:crypto";
import {
  closeSync,
  constants,
  fchmodSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { isAbsolute, join, posix, resolve, win32 } from "node:path";

import type {
  AuthenticatedReviewerRecord,
  EngineRequest,
} from "../protocol.js";
import {
  readTranscripts,
  resolveAnchors as resolveQwenAnchors,
  TranscriptsUnavailableError,
  type AgentRecord,
} from "../shims/qwenReviewRuntime.js";

export type FindingSeverity = "blocker" | "high" | "medium" | "low";
export type FindingVerification = "confirmed" | "rejected" | "uncertain";

export interface VerifiedFinding {
  id: string;
  severity: FindingSeverity;
  title: string;
  body: string;
  path: string;
  quotedCode: string;
  sourceReviewerIds: string[];
  verification: FindingVerification;
}

export interface ResolvedFinding extends VerifiedFinding {
  startLine: number;
  line: number;
  quotedCodeSha256: string;
  matchTier: string;
  ambiguous: boolean;
}

export interface UnresolvedFinding extends VerifiedFinding {
  reason: string;
}

export interface ResolveFindingAnchorsOutput {
  schemaVersion: 1;
  findingsPath: string;
  diffSha256: string;
  findings: ResolvedFinding[];
  unresolvedFindings: UnresolvedFinding[];
  stats: {
    total: number;
    resolved: number;
    unresolved: number;
    deduplicated: number;
  };
}

export interface ReviewArtifacts {
  artifactRoot: string;
  planPath: string;
  diffPath: string;
  plan: Record<string, unknown>;
  diff: string;
  diffSha256: string;
}

export class ReviewerEvidenceUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReviewerEvidenceUnavailableError";
  }
}

const FINDINGS_NAME = "findings.json";
const FINDING_KEYS = [
  "id",
  "severity",
  "title",
  "body",
  "path",
  "quotedCode",
  "sourceReviewerIds",
  "verification",
] as const;
const FINDING_KEY_SET = new Set<string>(FINDING_KEYS);
const REVIEWER_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u;
const MAX_FINDINGS = 256;
const MAX_TITLE_BYTES = 4_096;
const MAX_BODY_BYTES = 65_536;
const MAX_QUOTE_BYTES = 262_144;
const MAX_PATH_BYTES = 4_096;
const MAX_REVIEWER_RECORDS = 1_024;
export const VERIFIED_FINDINGS_EVIDENCE_MARKER =
  "Hermes-Verified-Findings-v1\n";

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

const boundedString = (
  value: unknown,
  label: string,
  maxBytes: number,
  options: { singleLine?: boolean } = {},
): string => {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
  if (
    value.includes("\0") ||
    (options.singleLine === true && /[\r\n]/u.test(value))
  ) {
    throw new TypeError(`${label} contains forbidden control characters`);
  }
  if (Buffer.byteLength(value, "utf8") > maxBytes) {
    throw new TypeError(`${label} exceeds ${maxBytes} bytes`);
  }
  return value;
};

const canonicalPath = (value: unknown, label: string): string => {
  const raw = boundedString(value, label, MAX_PATH_BYTES, { singleLine: true });
  if (isAbsolute(raw) || win32.isAbsolute(raw) || raw.includes("\\")) {
    throw new TypeError(`${label} must be a repository-relative POSIX path`);
  }
  const segments = raw.split("/");
  if (segments.includes("..")) {
    throw new TypeError(`${label} must not contain traversal segments`);
  }
  const normalized = posix.normalize(raw);
  if (
    normalized === "." ||
    normalized.startsWith("../") ||
    normalized === ".."
  ) {
    throw new TypeError(`${label} must name a repository file`);
  }
  return normalized.replace(/^\.\//u, "");
};

const realFile = (path: string, label: string): string => {
  const canonical = realpathSync(path);
  const stat = lstatSync(canonical);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new TypeError(`${label} must be a real file`);
  }
  if (process.platform !== "win32" && (stat.mode & 0o077) !== 0) {
    throw new TypeError(`${label} must be private to the current user`);
  }
  return canonical;
};

export const validatedReviewArtifacts = (
  request: EngineRequest,
): ReviewArtifacts => {
  const suppliedRoot = resolve(request.artifactRoot);
  const suppliedStat = lstatSync(suppliedRoot);
  if (!suppliedStat.isDirectory() || suppliedStat.isSymbolicLink()) {
    throw new TypeError("artifactRoot must be a real directory");
  }
  const artifactRoot = realpathSync(suppliedRoot);
  const rootStat = lstatSync(artifactRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    throw new TypeError("artifactRoot must be a real directory");
  }
  if (process.platform !== "win32" && (rootStat.mode & 0o077) !== 0) {
    throw new TypeError("artifactRoot must be private to the current user");
  }
  const planPath = realFile(join(artifactRoot, "plan.json"), "plan.json");
  if (planPath !== join(artifactRoot, "plan.json")) {
    throw new TypeError("plan.json must be the canonical run plan");
  }
  const plan = asRecord(
    JSON.parse(readFileSync(planPath, "utf8")) as unknown,
    "plan.json",
  );
  if (typeof plan.diffPathAbsolute !== "string") {
    throw new TypeError("plan.diffPathAbsolute must be a string");
  }
  const diffPath = realFile(resolve(plan.diffPathAbsolute), "target.diff");
  if (diffPath !== join(artifactRoot, "target.diff")) {
    throw new TypeError(
      "plan.diffPathAbsolute must name the run's canonical target.diff",
    );
  }
  const diff = readFileSync(diffPath, "utf8");
  const diffSha256 = createHash("sha256").update(diff).digest("hex");
  const hermes = asRecord(plan.hermes, "plan.hermes");
  if (
    typeof hermes.diffSha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(hermes.diffSha256)
  ) {
    throw new TypeError("plan.hermes.diffSha256 must be a SHA-256 digest");
  }
  if (hermes.diffSha256 !== diffSha256) {
    throw new TypeError("target.diff does not match plan.hermes.diffSha256");
  }
  return { artifactRoot, planPath, diffPath, plan, diff, diffSha256 };
};

const atomicWrite = (
  artifactRoot: string,
  name: string,
  content: string,
): string => {
  const destination = join(artifactRoot, name);
  validatePrivateDestination(artifactRoot, name);
  const temporary = join(
    artifactRoot,
    `.${name}.${randomBytes(12).toString("hex")}.tmp`,
  );
  const descriptor = openSync(temporary, "wx", 0o600);
  try {
    writeFileSync(descriptor, content, "utf8");
    fchmodSync(descriptor, 0o600);
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
  try {
    renameSync(temporary, destination);
    if (process.platform !== "win32") {
      const directory = openSync(artifactRoot, "r");
      try {
        fsyncSync(directory);
      } finally {
        closeSync(directory);
      }
    }
  } finally {
    try {
      unlinkSync(temporary);
    } catch {
      // The successful rename removes the temporary path.
    }
  }
  return destination;
};

export const readPrivateFileNoFollow = (
  path: string,
  label: string,
): string => {
  const before = lstatSync(path);
  if (!before.isFile() || before.isSymbolicLink()) {
    throw new TypeError(`${label} must be a regular non-symlink file`);
  }
  const flags =
    constants.O_RDONLY |
    (process.platform === "win32" ? 0 : constants.O_NOFOLLOW);
  let descriptor: number;
  try {
    descriptor = openSync(path, flags);
  } catch (cause) {
    throw new TypeError(
      `${label} could not be opened safely: ${(cause as Error).message}`,
    );
  }
  try {
    const stat = fstatSync(descriptor);
    if (!stat.isFile()) throw new TypeError(`${label} must be a regular file`);
    // Windows exposes no O_NOFOLLOW through node:fs. Binding the lstat identity
    // to the opened descriptor rejects a symlink/reparse-point substitution
    // between inspection and open; after open all reads stay on this descriptor.
    if (stat.dev !== before.dev || stat.ino !== before.ino) {
      throw new TypeError(`${label} changed while it was being opened`);
    }
    if (process.platform !== "win32" && (stat.mode & 0o777) !== 0o600) {
      throw new TypeError(`${label} must be private to the current user`);
    }
    if (
      process.platform !== "win32" &&
      typeof process.getuid === "function" &&
      stat.uid !== process.getuid()
    ) {
      throw new TypeError(`${label} must be owned by the current user`);
    }
    return readFileSync(descriptor, "utf8");
  } finally {
    closeSync(descriptor);
  }
};

const atomicJson = (
  artifactRoot: string,
  name: string,
  value: unknown,
): string =>
  atomicWrite(artifactRoot, name, `${JSON.stringify(value, null, 2)}\n`);

const lstatExists = (path: string): boolean => {
  try {
    lstatSync(path);
    return true;
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw cause;
  }
};

export const validatePrivateDestination = (
  artifactRoot: string,
  name: string,
): string => {
  const destination = join(artifactRoot, name);
  if (lstatExists(destination)) {
    const stat = lstatSync(destination);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new TypeError(
        `${name} must be a regular file when it already exists`,
      );
    }
  }
  return destination;
};

const reviewerRecords = (
  artifacts: ReviewArtifacts,
  authenticated?: readonly AuthenticatedReviewerRecord[],
): AgentRecord[] => {
  if (authenticated !== undefined) return [...authenticated];
  // Direct handler calls remain useful for package-level tests and diagnostics,
  // but Hermes never treats their output as authoritative. The Python bridge
  // requires and injects authenticated records for every evidence command.
  const since = lstatSync(artifacts.planPath).mtimeMs;
  try {
    const records = readTranscripts(
      since,
      {
        ...process.env,
        QWEN_CODE_PROJECT_DIR: artifacts.artifactRoot,
        QWEN_CODE_SESSION_ID: "reviewers",
      },
      artifacts.diffPath,
    );
    if (records.length > MAX_REVIEWER_RECORDS) {
      throw new ReviewerEvidenceUnavailableError(
        `reviewer evidence exceeds ${MAX_REVIEWER_RECORDS} current-run records`,
      );
    }
    return records;
  } catch (cause) {
    if (cause instanceof TranscriptsUnavailableError) {
      throw new ReviewerEvidenceUnavailableError(cause.message);
    }
    throw cause;
  }
};

const parseVerifiedFindings = (
  value: unknown,
  knownReviewers: ReadonlySet<string>,
): VerifiedFinding[] => {
  if (!Array.isArray(value) || value.length > MAX_FINDINGS) {
    throw new TypeError(
      `findings must be an array of at most ${MAX_FINDINGS} entries`,
    );
  }
  const seenIds = new Set<string>();
  return value.map((entry, index) => {
    const finding = asRecord(entry, `findings[${index}]`);
    const unknown = Object.keys(finding).find(
      (key) => !FINDING_KEY_SET.has(key),
    );
    if (unknown !== undefined) {
      throw new TypeError(`unknown findings[${index}] field: ${unknown}`);
    }
    const id = boundedString(finding.id, `findings[${index}].id`, 128, {
      singleLine: true,
    });
    if (!REVIEWER_ID.test(id))
      throw new TypeError(`findings[${index}].id is invalid`);
    if (seenIds.has(id)) throw new TypeError(`duplicate finding id: ${id}`);
    seenIds.add(id);
    if (
      !(
        finding.severity === "blocker" ||
        finding.severity === "high" ||
        finding.severity === "medium" ||
        finding.severity === "low"
      )
    ) {
      throw new TypeError(`findings[${index}].severity is invalid`);
    }
    if (
      !(
        finding.verification === "confirmed" ||
        finding.verification === "rejected" ||
        finding.verification === "uncertain"
      )
    ) {
      throw new TypeError(`findings[${index}].verification is invalid`);
    }
    if (
      !Array.isArray(finding.sourceReviewerIds) ||
      finding.sourceReviewerIds.length === 0 ||
      finding.sourceReviewerIds.length > knownReviewers.size
    ) {
      throw new TypeError(
        `findings[${index}].sourceReviewerIds must contain 1-${knownReviewers.size} current-run ids`,
      );
    }
    const sourceReviewerIds = finding.sourceReviewerIds.map(
      (source, sourceIndex) => {
        const reviewerId = boundedString(
          source,
          `findings[${index}].sourceReviewerIds[${sourceIndex}]`,
          128,
          { singleLine: true },
        );
        if (!REVIEWER_ID.test(reviewerId) || !knownReviewers.has(reviewerId)) {
          throw new TypeError(`unknown reviewer id: ${reviewerId}`);
        }
        return reviewerId;
      },
    );
    if (new Set(sourceReviewerIds).size !== sourceReviewerIds.length) {
      throw new TypeError(
        `findings[${index}].sourceReviewerIds contains duplicates`,
      );
    }
    return {
      id,
      severity: finding.severity,
      title: boundedString(
        finding.title,
        `findings[${index}].title`,
        MAX_TITLE_BYTES,
        { singleLine: true },
      )
        .trim()
        .replace(/\s+/gu, " "),
      body: boundedString(
        finding.body,
        `findings[${index}].body`,
        MAX_BODY_BYTES,
      ).trim(),
      path: canonicalPath(finding.path, `findings[${index}].path`),
      quotedCode: boundedString(
        finding.quotedCode,
        `findings[${index}].quotedCode`,
        MAX_QUOTE_BYTES,
      ),
      sourceReviewerIds: sourceReviewerIds.sort(),
      verification: finding.verification,
    };
  });
};

export const validateVerifiedFindings = (
  value: unknown,
  artifacts: ReviewArtifacts,
  authenticated?: readonly AuthenticatedReviewerRecord[],
): VerifiedFinding[] => {
  const knownReviewers = new Set(
    reviewerRecords(artifacts, authenticated).map((record) => record.agentId),
  );
  return parseVerifiedFindings(value, knownReviewers);
};

export const verifiedFindingsFromEvidence = (
  artifacts: ReviewArtifacts,
  authenticated?: readonly AuthenticatedReviewerRecord[],
): VerifiedFinding[] => {
  const records = reviewerRecords(artifacts, authenticated);
  const evidence = records.filter((record) =>
    record.finalText.startsWith(VERIFIED_FINDINGS_EVIDENCE_MARKER),
  );
  if (evidence.length !== 1) {
    throw new ReviewerEvidenceUnavailableError(
      `expected exactly one current-run verifier transcript evidence record, found ${evidence.length}`,
    );
  }
  const record = evidence[0]!;
  if (record.successfulToolCalls === 0) {
    throw new ReviewerEvidenceUnavailableError(
      "verifier transcript evidence has no successful tool calls",
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(
      record.finalText.slice(VERIFIED_FINDINGS_EVIDENCE_MARKER.length),
    ) as unknown;
  } catch (cause) {
    throw new TypeError(
      `verifier transcript evidence is not valid JSON: ${(cause as Error).message}`,
    );
  }
  const knownReviewers = new Set(records.map((entry) => entry.agentId));
  return parseVerifiedFindings(parsed, knownReviewers);
};

const severityRank: Record<FindingSeverity, number> = {
  blocker: 4,
  high: 3,
  medium: 2,
  low: 1,
};
const verificationRank: Record<FindingVerification, number> = {
  confirmed: 3,
  uncertain: 2,
  rejected: 1,
};
const normalizedTitle = (title: string): string =>
  title.normalize("NFKC").toLowerCase().trim().replace(/\s+/gu, " ");
const quoteHash = (quotedCode: string): string =>
  createHash("sha256").update(quotedCode).digest("hex");

const deduplicate = (findings: ResolvedFinding[]): ResolvedFinding[] => {
  const groups = new Map<string, ResolvedFinding[]>();
  for (const finding of findings) {
    const key = [
      finding.path,
      finding.startLine,
      finding.line,
      normalizedTitle(finding.title),
      finding.quotedCodeSha256,
    ].join("\0");
    const group = groups.get(key) ?? [];
    group.push(finding);
    groups.set(key, group);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, group]) => {
      const ranked = [...group].sort(
        (left, right) =>
          verificationRank[right.verification] -
            verificationRank[left.verification] ||
          severityRank[right.severity] - severityRank[left.severity] ||
          left.id.localeCompare(right.id) ||
          left.body.localeCompare(right.body),
      );
      const selected = ranked[0]!;
      return {
        ...selected,
        sourceReviewerIds: [
          ...new Set(group.flatMap((entry) => entry.sourceReviewerIds)),
        ].sort(),
      };
    });
};

export const deriveResolvedFindings = (
  artifacts: ReviewArtifacts,
  findings: VerifiedFinding[],
): ResolveFindingAnchorsOutput => {
  const resolutions = resolveQwenAnchors(
    artifacts.diff,
    findings.map((entry) => ({
      id: entry.id,
      path: entry.path,
      anchor: entry.quotedCode,
    })),
  );
  const byId = new Map(findings.map((entry) => [entry.id, entry]));
  const resolved: ResolvedFinding[] = [];
  const unresolved: UnresolvedFinding[] = [];
  for (const resolution of resolutions) {
    const source = byId.get(resolution.id)!;
    if (
      resolution.status === "resolved" &&
      resolution.startLine !== undefined &&
      resolution.line !== undefined
    ) {
      resolved.push({
        ...source,
        startLine: resolution.startLine,
        line: resolution.line,
        quotedCodeSha256: quoteHash(source.quotedCode),
        matchTier: resolution.tier ?? "unknown",
        ambiguous: resolution.ambiguous ?? false,
      });
    } else {
      unresolved.push({
        ...source,
        reason: resolution.reason ?? "anchor could not be resolved",
      });
    }
  }
  const deduplicated = deduplicate(resolved);
  const findingsPath = join(artifacts.artifactRoot, FINDINGS_NAME);
  return {
    schemaVersion: 1 as const,
    findingsPath,
    diffSha256: artifacts.diffSha256,
    findings: deduplicated,
    unresolvedFindings: unresolved.sort((left, right) =>
      left.id.localeCompare(right.id),
    ),
    stats: {
      total: findings.length,
      resolved: deduplicated.length,
      unresolved: unresolved.length,
      deduplicated: resolved.length - deduplicated.length,
    },
  };
};

export async function resolveFindingAnchors(
  request: EngineRequest,
): Promise<ResolveFindingAnchorsOutput> {
  const input = asRecord(request.input, "input");
  const unknown = Object.keys(input).find((key) => key !== "findings");
  if (unknown !== undefined)
    throw new TypeError(`unknown resolve-anchors input field: ${unknown}`);
  const artifacts = validatedReviewArtifacts(request);
  const findings = validateVerifiedFindings(
    input.findings,
    artifacts,
    request.authenticatedReviewerRecords,
  );
  const evidenced = verifiedFindingsFromEvidence(
    artifacts,
    request.authenticatedReviewerRecords,
  );
  if (JSON.stringify(findings) !== JSON.stringify(evidenced)) {
    throw new TypeError(
      "findings do not match the current-run verifier transcript evidence",
    );
  }
  const output = deriveResolvedFindings(artifacts, evidenced);
  atomicJson(artifacts.artifactRoot, FINDINGS_NAME, output);
  return output;
}

export const writePrivateJson = atomicJson;
export const writePrivateText = atomicWrite;

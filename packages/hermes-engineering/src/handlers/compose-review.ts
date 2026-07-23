import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import { join } from "node:path";

import type { CheckStatus, EngineRequest } from "../protocol.js";
import {
  composeReview as composeQwenReview,
  resolveAnchors as resolveQwenAnchors,
  type ReviewEvent,
} from "../shims/qwenReviewRuntime.js";
import {
  validateReverseAuditState,
  type ReverseAuditState,
} from "../reverse-audit.js";
import {
  validatedReviewArtifacts,
  validatePrivateDestination,
  writePrivateJson,
  writePrivateText,
  type FindingSeverity,
  type FindingVerification,
  type ResolveFindingAnchorsOutput,
  type ResolvedFinding,
  type UnresolvedFinding,
} from "./resolve-anchors.js";
import { checkCoverage } from "./check-coverage.js";

type ReviewEffort = "low" | "medium" | "high";
type CiStatus = CheckStatus | "not_available";

interface ComposeReviewFacts {
  effort: ReviewEffort;
  buildTestStatus: CheckStatus;
  testEfficacyStatus: CheckStatus;
  ciStatus: CiStatus;
  reverseAudit?: ReverseAuditState;
}

export interface ReviewVerdict {
  schemaVersion: 1;
  event: ReviewEvent;
  baseEvent: ReviewEvent;
  counts: {
    confirmedBlocking: number;
    confirmedAdvisory: number;
    uncertain: number;
    rejected: number;
    unresolved: number;
  };
  checks: {
    coverage: CheckStatus;
    buildTest: CheckStatus;
    testEfficacy: CheckStatus;
    ci: CiStatus;
  };
  reverseAudit: ReverseAuditState | null;
  disclosures: string[];
}

export interface ComposeReviewOutput {
  event: ReviewEvent;
  findingsPath: string;
  verdictPath: string;
  reportPath: string;
  verdict: ReviewVerdict;
  report: string;
}

const VERDICT_NAME = "verdict.json";
const REPORT_NAME = "review.md";
const INPUT_KEYS = new Set([
  "effort",
  "buildTestStatus",
  "testEfficacyStatus",
  "ciStatus",
  "reverseAudit",
]);
const CHECK_STATUSES = new Set<CheckStatus>([
  "passed",
  "failed",
  "inconclusive",
]);
const SEVERITIES = new Set<FindingSeverity>([
  "blocker",
  "high",
  "medium",
  "low",
]);
const VERIFICATIONS = new Set<FindingVerification>([
  "confirmed",
  "rejected",
  "uncertain",
]);

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

const checkStatus = (value: unknown, label: string): CheckStatus => {
  if (!CHECK_STATUSES.has(value as CheckStatus)) {
    throw new TypeError(`${label} must be passed, failed, or inconclusive`);
  }
  return value as CheckStatus;
};

const parseInput = (request: EngineRequest): ComposeReviewFacts => {
  const input = asRecord(request.input, "input");
  const unknown = Object.keys(input).find((key) => !INPUT_KEYS.has(key));
  if (unknown !== undefined) {
    throw new TypeError(`unknown compose-review input field: ${unknown}`);
  }
  if (
    !(
      input.effort === "low" ||
      input.effort === "medium" ||
      input.effort === "high"
    )
  ) {
    throw new TypeError("effort must be low, medium, or high");
  }
  const ciStatus = input.ciStatus;
  if (
    !(
      ciStatus === "not_available" ||
      CHECK_STATUSES.has(ciStatus as CheckStatus)
    )
  ) {
    throw new TypeError(
      "ciStatus must be passed, failed, inconclusive, or not_available",
    );
  }
  if (input.effort === "high" && input.reverseAudit === undefined) {
    throw new TypeError("reverseAudit is required for high effort");
  }
  if (input.effort !== "high" && input.reverseAudit !== undefined) {
    throw new TypeError("reverseAudit is only valid for high effort");
  }
  const reverseAudit =
    input.reverseAudit === undefined
      ? undefined
      : validateReverseAuditState(input.reverseAudit);
  return {
    effort: input.effort,
    buildTestStatus: checkStatus(input.buildTestStatus, "buildTestStatus"),
    testEfficacyStatus: checkStatus(
      input.testEfficacyStatus,
      "testEfficacyStatus",
    ),
    ciStatus: ciStatus as CiStatus,
    ...(reverseAudit === undefined ? {} : { reverseAudit }),
  };
};

const parseFinding = <T extends ResolvedFinding | UnresolvedFinding>(
  value: unknown,
  resolved: boolean,
): T => {
  const finding = asRecord(value, "stored finding");
  if (
    typeof finding.id !== "string" ||
    typeof finding.title !== "string" ||
    typeof finding.body !== "string" ||
    typeof finding.path !== "string" ||
    typeof finding.quotedCode !== "string" ||
    !Array.isArray(finding.sourceReviewerIds) ||
    finding.sourceReviewerIds.some((id) => typeof id !== "string") ||
    !SEVERITIES.has(finding.severity as FindingSeverity) ||
    !VERIFICATIONS.has(finding.verification as FindingVerification)
  ) {
    throw new TypeError("findings.json contains an invalid finding");
  }
  if (
    resolved &&
    (!Number.isSafeInteger(finding.startLine) ||
      (finding.startLine as number) < 1 ||
      !Number.isSafeInteger(finding.line) ||
      (finding.line as number) < (finding.startLine as number) ||
      typeof finding.quotedCodeSha256 !== "string")
  ) {
    throw new TypeError("findings.json contains an invalid resolved range");
  }
  if (!resolved && typeof finding.reason !== "string") {
    throw new TypeError("findings.json contains an invalid unresolved finding");
  }
  return finding as unknown as T;
};

const readFindings = (
  artifactRoot: string,
  expectedDiffSha256: string,
  diff: string,
): ResolveFindingAnchorsOutput => {
  const findingsPath = join(artifactRoot, "findings.json");
  const stat = lstatSync(findingsPath);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new TypeError("findings.json must be a real file");
  }
  const value = asRecord(
    JSON.parse(readFileSync(findingsPath, "utf8")) as unknown,
    "findings.json",
  );
  if (
    value.schemaVersion !== 1 ||
    value.findingsPath !== findingsPath ||
    value.diffSha256 !== expectedDiffSha256 ||
    !Array.isArray(value.findings) ||
    !Array.isArray(value.unresolvedFindings)
  ) {
    throw new TypeError("findings.json does not belong to the captured diff");
  }
  const output = {
    ...(value as unknown as ResolveFindingAnchorsOutput),
    findings: value.findings.map((entry) =>
      parseFinding<ResolvedFinding>(entry, true),
    ),
    unresolvedFindings: value.unresolvedFindings.map((entry) =>
      parseFinding<UnresolvedFinding>(entry, false),
    ),
  };
  const all = [...output.findings, ...output.unresolvedFindings];
  const resolvedCount = output.findings.length;
  const anchors = resolveQwenAnchors(
    diff,
    all.map((entry) => ({
      id: entry.id,
      path: entry.path,
      anchor: entry.quotedCode,
    })),
  );
  for (const [index, anchor] of anchors.entries()) {
    const stored = all[index]!;
    if (index < resolvedCount) {
      const resolved = stored as ResolvedFinding;
      const quoteSha256 = createHash("sha256")
        .update(resolved.quotedCode)
        .digest("hex");
      if (
        anchor.status !== "resolved" ||
        anchor.startLine !== resolved.startLine ||
        anchor.line !== resolved.line ||
        quoteSha256 !== resolved.quotedCodeSha256
      ) {
        throw new TypeError(
          "findings.json contains an anchor not derived from target.diff",
        );
      }
    } else if (anchor.status !== "unmatched") {
      throw new TypeError(
        "findings.json marks a captured-diff anchor as unresolved",
      );
    }
  }
  return output;
};

const hermesMetadata = (
  plan: Record<string, unknown>,
): Record<string, unknown> => asRecord(plan.hermes, "plan.hermes");

const skippedFileCount = (plan: Record<string, unknown>): number => {
  const skipped = hermesMetadata(plan).skippedFiles;
  if (!Array.isArray(skipped)) {
    throw new TypeError("plan.hermes.skippedFiles must be an array");
  }
  return skipped.length;
};

const findingIsBlocking = (severity: FindingSeverity): boolean =>
  severity === "blocker" || severity === "high";

const renderFinding = (
  finding: ResolvedFinding | UnresolvedFinding,
): string => {
  const location =
    "line" in finding
      ? `${finding.path}:${finding.startLine}${finding.line === finding.startLine ? "" : `-${finding.line}`}`
      : `${finding.path} (unresolved anchor)`;
  const sources = finding.sourceReviewerIds.join(", ");
  const body = finding.body.replace(/\r\n?/gu, "\n").replace(/\n/gu, "\n  ");
  return `- [${finding.severity.toUpperCase()}] ${finding.title} — ${location}\n  ${body}\n  Sources: ${sources}; verification: ${finding.verification}`;
};

const renderReport = (
  verdict: ReviewVerdict,
  findings: ResolveFindingAnchorsOutput,
): string => {
  const sections = [
    "# Hermes Engineering Review",
    "",
    `Verdict: ${verdict.event}`,
    "",
    "## Checks",
    "",
    `- Coverage: ${verdict.checks.coverage}`,
    `- Build/test: ${verdict.checks.buildTest}`,
    `- Test efficacy: ${verdict.checks.testEfficacy}`,
    `- CI: ${verdict.checks.ci}`,
    "",
    "## Findings",
    "",
    ...(findings.findings.length + findings.unresolvedFindings.length === 0
      ? ["No verified findings."]
      : [...findings.findings, ...findings.unresolvedFindings].map(
          renderFinding,
        )),
  ];
  if (verdict.disclosures.length > 0) {
    sections.push(
      "",
      "## Residual uncertainty",
      "",
      ...verdict.disclosures.map((entry) => `- ${entry}`),
    );
  }
  return `${sections.join("\n")}\n`;
};

export async function composeReview(
  request: EngineRequest,
): Promise<ComposeReviewOutput> {
  const facts = parseInput(request);
  const artifacts = validatedReviewArtifacts(request);
  let coverageStatus: CheckStatus;
  let coverageFailure: string | null = null;
  try {
    const promptPlan = asRecord(
      JSON.parse(
        readFileSync(join(artifacts.artifactRoot, "prompts.json"), "utf8"),
      ) as unknown,
      "prompts.json",
    );
    if (promptPlan.effort !== facts.effort) {
      throw new TypeError(
        "prompts.json effort does not match compose-review effort",
      );
    }
    const coverage = await checkCoverage({
      ...request,
      command: "check-coverage",
      input: { planPath: artifacts.planPath },
    });
    coverageStatus = coverage.status;
  } catch (cause) {
    coverageStatus = "inconclusive";
    coverageFailure = cause instanceof Error ? cause.message : String(cause);
  }
  const findings = readFindings(
    artifacts.artifactRoot,
    artifacts.diffSha256,
    artifacts.diff,
  );
  const allFindings = [...findings.findings, ...findings.unresolvedFindings];
  const relevantUnresolved = findings.unresolvedFindings.filter(
    (entry) => entry.verification !== "rejected",
  );
  const confirmed = allFindings.filter(
    (entry) => entry.verification === "confirmed",
  );
  const confirmedBlocking = confirmed.filter((entry) =>
    findingIsBlocking(entry.severity),
  ).length;
  const confirmedAdvisory = confirmed.length - confirmedBlocking;
  const failedChecks = [facts.buildTestStatus, facts.testEfficacyStatus].filter(
    (status) => status === "failed",
  ).length;
  const upstream = composeQwenReview({
    criticalsInline: confirmedBlocking + failedChecks,
    suggestionsInline: confirmedAdvisory,
    modelId: "Hermes Engineering Review",
  });
  const disclosures: string[] = [];
  if (coverageStatus !== "passed")
    disclosures.push(`review coverage is ${coverageStatus}`);
  if (coverageFailure !== null)
    disclosures.push(
      `review coverage could not be recomputed: ${coverageFailure}`,
    );
  if (facts.buildTestStatus === "inconclusive")
    disclosures.push("build/test check is inconclusive");
  if (facts.testEfficacyStatus === "inconclusive")
    disclosures.push("test efficacy is inconclusive");
  if (facts.ciStatus === "inconclusive")
    disclosures.push("CI state is inconclusive");
  if (facts.ciStatus === "failed") disclosures.push("CI is failing");
  const skipped = skippedFileCount(artifacts.plan);
  if (skipped > 0) disclosures.push(`captured diff skipped ${skipped} file(s)`);
  const uncertain = allFindings.filter(
    (entry) => entry.verification === "uncertain",
  ).length;
  if (uncertain > 0)
    disclosures.push(`${uncertain} finding(s) remain uncertain`);
  if (relevantUnresolved.length > 0) {
    disclosures.push(
      `${relevantUnresolved.length} finding anchor(s) could not be resolved`,
    );
  }
  if (facts.reverseAudit !== undefined && !facts.reverseAudit.complete) {
    disclosures.push("high-effort reverse audit is incomplete");
  }
  const exhaustedReverseAudit =
    facts.reverseAudit?.round === 5 &&
    facts.reverseAudit.consecutiveDryRounds < 2;
  if (exhaustedReverseAudit) {
    disclosures.push(
      "reverse audit reached five rounds without two consecutive dry rounds; residual uncertainty remains",
    );
  }
  let event = upstream.baseEvent;
  if (event === "APPROVE" && disclosures.length > 0) event = "COMMENT";
  if (exhaustedReverseAudit) event = "COMMENT";
  const verdict: ReviewVerdict = {
    schemaVersion: 1,
    event,
    baseEvent: upstream.baseEvent,
    counts: {
      confirmedBlocking,
      confirmedAdvisory,
      uncertain,
      rejected: allFindings.filter((entry) => entry.verification === "rejected")
        .length,
      unresolved: relevantUnresolved.length,
    },
    checks: {
      coverage: coverageStatus,
      buildTest: facts.buildTestStatus,
      testEfficacy: facts.testEfficacyStatus,
      ci: facts.ciStatus,
    },
    reverseAudit: facts.reverseAudit ?? null,
    disclosures,
  };
  const report = renderReport(verdict, findings);
  validatePrivateDestination(artifacts.artifactRoot, REPORT_NAME);
  validatePrivateDestination(artifacts.artifactRoot, VERDICT_NAME);
  const reportPath = writePrivateText(
    artifacts.artifactRoot,
    REPORT_NAME,
    report,
  );
  const verdictPath = writePrivateJson(
    artifacts.artifactRoot,
    VERDICT_NAME,
    verdict,
  );
  return {
    event,
    findingsPath: findings.findingsPath,
    verdictPath,
    reportPath,
    verdict,
    report,
  };
}

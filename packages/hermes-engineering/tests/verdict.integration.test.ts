import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { composeReview } from "../src/handlers/compose-review.js";
import {
  buildPrompts,
  type BuildPromptsOutput,
} from "../src/handlers/build-prompts.js";
import { dispatch } from "../src/handlers/index.js";
import { resolveFindingAnchors } from "../src/handlers/resolve-anchors.js";
import {
  initialReverseAuditState,
  nextReverseAudit,
} from "../src/reverse-audit.js";
import type { EngineRequest } from "../src/protocol.js";
import { briefPath } from "../../../third_party/qwen-code/packages/cli/src/commands/review/lib/prompt-record.js";

const roots: string[] = [];

const diff = [
  "diff --git a/src/x.ts b/src/x.ts",
  "index 1111111..2222222 100644",
  "--- a/src/x.ts",
  "+++ b/src/x.ts",
  "@@ -18,2 +18,3 @@",
  " export const before = true;",
  "+export const shifted = dangerous();",
  "+export const after = true;",
  "",
].join("\n");

const finding = (
  overrides: Record<string, unknown> = {},
): Record<string, unknown> => ({
  id: "finding-a",
  severity: "high",
  title: "Unchecked dangerous call",
  body: "The newly added call can throw before cleanup runs.",
  path: "src/x.ts",
  quotedCode: "export const shifted = dangerous();",
  sourceReviewerIds: ["reviewer-a"],
  verification: "confirmed",
  ...overrides,
});

const transcript = (agentId: string): string =>
  `${JSON.stringify({
    agentId,
    agentName: "reviewer",
    type: "user",
    message: { role: "user", parts: [{ text: "review fixture" }] },
  })}\n`;

interface Fixture {
  root: string;
  workspace: string;
  artifactRoot: string;
  planPath: string;
  request(
    command: "resolve-anchors" | "compose-review" | "build-prompts",
    input: Record<string, unknown>,
  ): EngineRequest;
}

const fixture = (options: { skipped?: boolean } = {}): Fixture => {
  const root = mkdtempSync(join(tmpdir(), "hermes-verdict-"));
  roots.push(root);
  const workspace = join(root, "workspace");
  const artifactRoot = join(root, "review-run");
  mkdirSync(workspace);
  mkdirSync(artifactRoot, { mode: 0o700 });
  chmodSync(artifactRoot, 0o700);
  const diffPath = join(artifactRoot, "target.diff");
  const planPath = join(artifactRoot, "plan.json");
  writeFileSync(diffPath, diff, { mode: 0o600 });
  writeFileSync(
    planPath,
    JSON.stringify({
      diffPathAbsolute: diffPath,
      diffLines: diff.split("\n").length,
      srcDiffLines: diff.split("\n").length,
      chunks: [
        {
          id: 1,
          startLine: 1,
          endLine: diff.split("\n").length,
          lines: diff.split("\n").length,
          chars: diff.length,
          maxLineChars: 48,
          oversized: false,
          files: [{ path: "src/x.ts", newStart: 18, newEnd: 20 }],
        },
      ],
      files: [
        {
          path: "src/x.ts",
          kind: "source",
          heavy: false,
          removedLines: 0,
        },
      ],
      hermes: {
        schemaVersion: 1,
        runId: "review-run",
        targetKind: "local",
        skippedFiles: options.skipped
          ? [{ path: "large.ts", bytes: 1_000_001, reason: "too large" }]
          : [],
      },
    }),
    { mode: 0o600 },
  );
  const transcriptRoot = join(artifactRoot, "subagents", "reviewers");
  mkdirSync(transcriptRoot, { recursive: true, mode: 0o700 });
  writeFileSync(
    join(transcriptRoot, "agent-a.jsonl"),
    transcript("reviewer-a"),
  );
  writeFileSync(
    join(transcriptRoot, "agent-b.jsonl"),
    transcript("reviewer-b"),
  );
  return {
    root,
    workspace,
    artifactRoot,
    planPath,
    request: (command, input) => ({
      protocolVersion: 1,
      requestId: `${command}-fixture`,
      command,
      workspace,
      artifactRoot,
      input,
    }),
  };
};

const cleanFacts = {
  effort: "medium",
  buildTestStatus: "passed",
  testEfficacyStatus: "passed",
  ciStatus: "passed",
} as const;

const completeTranscript = (
  agentId: string,
  prompt: BuildPromptsOutput["prompts"][number],
  prompts: BuildPromptsOutput,
): string => {
  const records = [
    {
      agentId,
      agentName: "reviewer",
      type: "user",
      message: { role: "user", parts: [{ text: prompt.text }] },
    },
    {
      agentId,
      agentName: "reviewer",
      type: "assistant",
      message: {
        role: "model",
        parts: [
          {
            functionCall: {
              id: "brief",
              name: "read_file",
              args: { file_path: briefPath(prompts.planPath, prompt.key) },
            },
          },
          {
            functionCall: {
              id: "diff",
              name: "read_file",
              args: {
                file_path: prompts.diffPath,
                offset: 0,
                limit: 10_000,
              },
            },
          },
        ],
      },
    },
    ...["brief", "diff"].map((id) => ({
      agentId,
      agentName: "reviewer",
      type: "tool_result",
      message: {
        role: "user",
        parts: [{ functionResponse: { id, response: { output: "read" } } }],
      },
    })),
    {
      agentId,
      agentName: "reviewer",
      type: "assistant",
      message: { role: "model", parts: [{ text: "Review complete." }] },
    },
  ];
  return `${records.map((record) => JSON.stringify(record)).join("\n")}\n`;
};

const prepareCoverage = async (
  run: Fixture,
  effort: "low" | "medium" | "high" = "medium",
): Promise<void> => {
  const prompts = await buildPrompts(
    run.request("build-prompts", {
      planPath: run.planPath,
      effort,
    }),
  );
  const reviewerIds = prompts.prompts.map((_, index) =>
    index === 0
      ? "reviewer-a"
      : index === 1
        ? "reviewer-b"
        : `reviewer-${index}`,
  );
  const transcriptRoot = join(run.artifactRoot, "subagents", "reviewers");
  prompts.prompts.forEach((prompt, index) => {
    writeFileSync(
      join(transcriptRoot, `agent-${reviewerIds[index]}.jsonl`),
      completeTranscript(reviewerIds[index]!, prompt, prompts),
    );
  });
};

const resolveOne = async (
  run: Fixture,
  findings: Array<Record<string, unknown>> = [finding()],
) => resolveFindingAnchors(run.request("resolve-anchors", { findings }));

afterEach(() => {
  for (const root of roots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("verified findings and anchors", () => {
  it("reanchors by quoted code after line shifts and deduplicates identical findings", async () => {
    const run = fixture();
    const result = await resolveOne(run, [
      finding(),
      finding({
        id: "finding-b",
        title: "  unchecked   DANGEROUS call ",
        path: "src//x.ts",
        sourceReviewerIds: ["reviewer-b"],
      }),
    ]);

    expect(result.findings).toHaveLength(1);
    expect(result.findings[0]).toMatchObject({
      path: "src/x.ts",
      startLine: 19,
      line: 19,
      sourceReviewerIds: ["reviewer-a", "reviewer-b"],
    });
    expect(JSON.parse(readFileSync(result.findingsPath, "utf8"))).toEqual(
      result,
    );
    expect(statSync(result.findingsPath).mode & 0o777).toBe(0o600);
  });

  it.each([
    ["absolute paths", finding({ path: "/tmp/x.ts" })],
    ["Windows absolute paths", finding({ path: "C:\\tmp\\x.ts" })],
    ["traversal paths", finding({ path: "src/../x.ts" })],
    ["missing quoted code", finding({ quotedCode: "  " })],
    ["unknown reviewer ids", finding({ sourceReviewerIds: ["forged"] })],
    ["unbounded titles", finding({ title: "x".repeat(4_097) })],
    ["caller line numbers", finding({ line: 19 })],
  ])("rejects %s", async (_label, candidate) => {
    const run = fixture();
    await expect(resolveOne(run, [candidate])).rejects.toThrow();
  });
});

describe("computed verdict", () => {
  it.each([
    ["missing-coverage", cleanFacts, "COMMENT"],
    [
      "inert-test",
      { ...cleanFacts, testEfficacyStatus: "failed" },
      "REQUEST_CHANGES",
    ],
    ["clean", cleanFacts, "APPROVE"],
    ["failing-ci", { ...cleanFacts, ciStatus: "failed" }, "COMMENT"],
  ] as const)(
    "computes %s without trusting a caller verdict",
    async (_name, facts, event) => {
      const run = fixture();
      await resolveOne(run, [finding({ verification: "rejected" })]);
      if (_name !== "missing-coverage") await prepareCoverage(run);

      const result = await composeReview(run.request("compose-review", facts));

      expect(result.event).toBe(event);
      expect(JSON.parse(readFileSync(result.verdictPath, "utf8"))).toEqual(
        result.verdict,
      );
      expect(readFileSync(result.reportPath, "utf8")).toBe(result.report);
      expect(statSync(result.reportPath).mode & 0o777).toBe(0o600);
      expect(readdirSync(run.artifactRoot)).not.toContain(
        ".review.md.text.json",
      );
    },
  );

  it("derives blockers from confirmed severity and ignores rejected findings", async () => {
    const run = fixture();
    await resolveOne(run, [
      finding({ id: "confirmed", severity: "blocker" }),
      finding({
        id: "rejected",
        title: "Different rejected concern",
        quotedCode: "export const after = true;",
        verification: "rejected",
      }),
    ]);
    await prepareCoverage(run);

    const result = await composeReview(
      run.request("compose-review", cleanFacts),
    );
    expect(result.event).toBe("REQUEST_CHANGES");
  });

  it("rechecks stored line numbers against the captured diff", async () => {
    const run = fixture();
    const resolved = await resolveOne(run);
    await prepareCoverage(run);
    const stored = JSON.parse(readFileSync(resolved.findingsPath, "utf8")) as {
      findings: Array<{ line: number }>;
    };
    stored.findings[0]!.line = 999;
    writeFileSync(resolved.findingsPath, JSON.stringify(stored));

    await expect(
      composeReview(run.request("compose-review", cleanFacts)),
    ).rejects.toThrow(/not derived from target\.diff/);
  });

  it("derives skipped diff uncertainty from the captured plan", async () => {
    const run = fixture({ skipped: true });
    await resolveOne(run, [finding({ verification: "rejected" })]);
    await prepareCoverage(run);

    const result = await composeReview(
      run.request("compose-review", cleanFacts),
    );
    expect(result.event).toBe("COMMENT");
    expect(result.verdict.disclosures).toContain(
      "captured diff skipped 1 file(s)",
    );
  });

  it.each(["event", "approved", "coverageStatus"])(
    "rejects caller-supplied %s",
    async (key) => {
      const run = fixture();
      await resolveOne(run, [finding({ verification: "rejected" })]);
      await expect(
        composeReview(
          run.request("compose-review", {
            ...cleanFacts,
            [key]:
              key === "event"
                ? "APPROVE"
                : key === "coverageStatus"
                  ? "passed"
                  : true,
          }),
        ),
      ).rejects.toThrow(/unknown compose-review input field/);
    },
  );
});

describe("engine dispatch", () => {
  it("returns typed statuses for anchor and verdict operations", async () => {
    const run = fixture();
    const anchors = await dispatch(
      run.request("resolve-anchors", { findings: [finding()] }),
    );
    expect(anchors.status).toBe("passed");
    await prepareCoverage(run);

    const verdict = await dispatch(
      run.request("compose-review", {
        ...cleanFacts,
        testEfficacyStatus: "failed",
      }),
    );
    expect(verdict).toMatchObject({
      status: "failed",
      output: { event: "REQUEST_CHANGES" },
    });
  });

  it("returns a typed failure for an invalid finding schema", async () => {
    const run = fixture();
    const response = await dispatch(
      run.request("resolve-anchors", {
        findings: [finding({ line: 19 })],
      }),
    );
    expect(response).toMatchObject({
      status: "failed",
      output: {},
      diagnostics: [{ code: "invalid_findings" }],
    });
  });

  it("fails closed when the reviewer-id evidence is unavailable", async () => {
    const run = fixture();
    rmSync(join(run.artifactRoot, "subagents"), {
      recursive: true,
      force: true,
    });
    const response = await dispatch(
      run.request("resolve-anchors", { findings: [finding()] }),
    );
    expect(response).toMatchObject({
      status: "inconclusive",
      output: {},
      diagnostics: [{ code: "reviewer_evidence_unavailable" }],
    });
  });
});

describe("bounded high-effort reverse audit", () => {
  it("stops after two consecutive dry rounds", () => {
    const first = nextReverseAudit(initialReverseAuditState(), 0);
    const second = nextReverseAudit(first, 0);
    expect(second).toEqual({
      round: 2,
      consecutiveDryRounds: 2,
      complete: true,
    });
    expect(() => nextReverseAudit(second, 0)).toThrow(/already complete/);
  });

  it("caps the fifth non-dry round at COMMENT and discloses uncertainty", async () => {
    const run = fixture();
    await resolveOne(run);
    await prepareCoverage(run, "high");
    let state = initialReverseAuditState();
    for (let round = 0; round < 5; round += 1)
      state = nextReverseAudit(state, 1);

    const result = await composeReview(
      run.request("compose-review", {
        ...cleanFacts,
        effort: "high",
        reverseAudit: state,
      }),
    );

    expect(state).toEqual({
      round: 5,
      consecutiveDryRounds: 0,
      complete: true,
    });
    expect(result.event).toBe("COMMENT");
    expect(result.verdict.disclosures).toContain(
      "reverse audit reached five rounds without two consecutive dry rounds; residual uncertainty remains",
    );
  });

  it("requires reverse-audit state only for high effort", async () => {
    const medium = fixture();
    await resolveOne(medium, [finding({ verification: "rejected" })]);
    await expect(
      composeReview(
        medium.request("compose-review", {
          ...cleanFacts,
          reverseAudit: initialReverseAuditState(),
        }),
      ),
    ).rejects.toThrow(/only valid for high effort/);

    const high = fixture();
    await resolveOne(high, [finding({ verification: "rejected" })]);
    await expect(
      composeReview(
        high.request("compose-review", { ...cleanFacts, effort: "high" }),
      ),
    ).rejects.toThrow(/required for high effort/);
  });
});

describe("no publication surface", () => {
  it("contains no mutation command in source or release bundle", () => {
    const sourceRoot = join(import.meta.dirname, "..", "src");
    const source = readdirSync(join(sourceRoot, "handlers"))
      .filter((name) => name.endsWith(".ts"))
      .map((name) => readFileSync(join(sourceRoot, "handlers", name), "utf8"))
      .join("\n");
    const bundle = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "..",
        "..",
        "hermes_cli",
        "engineering_dist",
        "hermes-engineering.mjs",
      ),
      "utf8",
    );
    const prohibited = /gh pr review|git push|git merge|submitReview/u;
    expect(source).not.toMatch(prohibited);
    expect(bundle).not.toMatch(prohibited);
  });
});

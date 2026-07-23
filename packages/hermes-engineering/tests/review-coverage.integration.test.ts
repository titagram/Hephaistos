import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  buildPrompts,
  type BuildPromptsOutput,
} from "../src/handlers/build-prompts.js";
import { checkCoverage } from "../src/handlers/check-coverage.js";
import type { EngineRequest } from "../src/protocol.js";
import {
  briefPath,
  readRecordedPrompts,
} from "../../../third_party/qwen-code/packages/cli/src/commands/review/lib/prompt-record.js";

const roots: string[] = [];

const fixture = (chunks = 1, territory = false) => {
  const root = mkdtempSync(join(tmpdir(), "hermes-review-coverage-"));
  roots.push(root);
  const workspace = join(root, "workspace");
  const artifactRoot = join(root, "review-run");
  mkdirSync(workspace);
  mkdirSync(artifactRoot, { mode: 0o700 });
  chmodSync(artifactRoot, 0o700);
  const diffPath = join(artifactRoot, "target.diff");
  const planPath = join(artifactRoot, "plan.json");
  const plannedChunks = Array.from({ length: chunks }, (_, index) => ({
    id: index + 1,
    startLine: index * 3 + 1,
    endLine: index * 3 + 3,
    lines: 3,
    chars: 30,
    maxLineChars: 10,
    oversized: false,
    files: [{ path: `src/file-${index + 1}.ts`, newStart: 1, newEnd: 1 }],
  }));
  writeFileSync(
    diffPath,
    Array.from({ length: chunks * 3 }, () => "line\n").join(""),
  );
  writeFileSync(
    planPath,
    JSON.stringify({
      diffPathAbsolute: diffPath,
      diffLines: territory ? 800 : chunks * 3,
      srcDiffLines: territory ? 800 : chunks * 3,
      chunks: plannedChunks,
      files: plannedChunks.map((chunk) => ({
        path: chunk.files[0]!.path,
        kind: "source",
        heavy: false,
        removedLines: 0,
      })),
      hermes: {
        schemaVersion: 1,
        runId: basename(artifactRoot),
        targetKind: "local",
      },
    }),
  );
  const request = (
    command: "build-prompts" | "check-coverage",
    input: Record<string, unknown>,
  ): EngineRequest => ({
    protocolVersion: 1,
    requestId: `${command}-request`,
    command,
    workspace,
    artifactRoot,
    input: { planPath, ...input },
  });
  return { artifactRoot, diffPath, planPath, request };
};

const record = (
  agentId: string,
  type: string,
  parts: Array<Record<string, unknown>>,
) => ({
  agentId,
  agentName: "reviewer",
  type,
  message: { role: type === "user" ? "user" : "model", parts },
});

const writeTranscript = (
  out: BuildPromptsOutput,
  artifactRoot: string,
  promptIndex: number,
  fixtureKind: "complete" | "idle" | "rewritten" | "unopened" | "unread",
): void => {
  const prompt = out.prompts[promptIndex]!;
  const launch =
    fixtureKind === "rewritten"
      ? `Extra orchestration preamble.\n${prompt.text}`
      : prompt.text;
  const records = [record(`agent-${promptIndex}`, "user", [{ text: launch }])];
  if (fixtureKind !== "idle") {
    const calls: Array<[string, Record<string, unknown>]> = [];
    if (fixtureKind !== "unread") {
      calls.push(["brief", { file_path: briefPath(out.planPath, prompt.key) }]);
    }
    if (fixtureKind !== "unopened") {
      calls.push([
        "diff",
        { file_path: out.diffPath, offset: 0, limit: 100_000 },
      ]);
    }
    for (const [id, args] of calls) {
      records.push(
        record(`agent-${promptIndex}`, "assistant", [
          { functionCall: { id, name: "read_file", args } },
        ]),
      );
      records.push(
        record(`agent-${promptIndex}`, "tool_result", [
          {
            functionResponse: {
              id,
              name: "read_file",
              response: { output: "read" },
            },
          },
        ]),
      );
    }
  }
  records.push(
    record(`agent-${promptIndex}`, "assistant", [
      { text: "No findings after examining the assigned code and cases." },
    ]),
  );
  const directory = join(artifactRoot, "subagents", "reviewers");
  mkdirSync(directory, { recursive: true });
  writeFileSync(
    join(directory, `agent-${promptIndex}.jsonl`),
    `${records.map((value) => JSON.stringify(value)).join("\n")}\n`,
  );
};

afterEach(() => {
  for (const root of roots.splice(0))
    rmSync(root, { recursive: true, force: true });
});

describe("deterministic review prompts", () => {
  it("builds exactly the medium effort roster and records byte-identical prompts", async () => {
    const run = fixture();
    const result = await buildPrompts(
      run.request("build-prompts", { effort: "medium" }),
    );

    expect(result.prompts).toHaveLength(3);
    expect(result.prompts.map((prompt) => prompt.key)).toEqual([
      "1a",
      "2",
      "3",
    ]);
    const recorded = readRecordedPrompts(result.planPath);
    for (const prompt of result.prompts) {
      expect(prompt.text).toContain(`Hermes-Review-Run: ${result.runId}`);
      expect(prompt.text).toContain(`Hermes-Review-Plan: ${result.planPath}`);
      expect(recorded.get(prompt.key)).toBe(prompt.text);
    }
    expect(JSON.parse(readFileSync(result.promptsPath, "utf8"))).toEqual(
      result,
    );
  });

  it("preserves every required chunk in bounded waves before specialists", async () => {
    const run = fixture(5, true);
    const result = await buildPrompts(
      run.request("build-prompts", { effort: "medium" }),
    );

    expect(result.prompts.map((prompt) => prompt.key)).toEqual([
      "chunk-1",
      "chunk-2",
      "chunk-3",
      "chunk-4",
      "chunk-5",
    ]);
    expect(result.waves.map((wave) => wave.promptKeys)).toEqual([
      ["chunk-1", "chunk-2", "chunk-3"],
      ["chunk-4", "chunk-5"],
    ]);
    expect(result.omittedSpecialists.map((entry) => entry.key)).toContain(
      "test-matrix",
    );
  });

  it("applies the declared effort features without displacing source coverage", async () => {
    const lowRun = fixture();
    const low = await buildPrompts(
      lowRun.request("build-prompts", { effort: "low" }),
    );
    expect(low.prompts.map((prompt) => prompt.key)).toEqual(["1a"]);
    expect(low.limits).toEqual({
      maxReviewers: 1,
      verifyFindings: false,
      reverseAudit: false,
    });

    const highRun = fixture();
    const high = await buildPrompts(
      highRun.request("build-prompts", { effort: "high" }),
    );
    expect(high.prompts[0]?.key).toBe("1a");
    expect(high.limits).toEqual({
      maxReviewers: 24,
      verifyFindings: true,
      reverseAudit: true,
    });
  });

  it("refuses to rewrite an already recorded prompt", async () => {
    const run = fixture();
    await buildPrompts(run.request("build-prompts", { effort: "medium" }));
    await expect(
      buildPrompts(
        run.request("build-prompts", {
          effort: "medium",
          rules: "A new rule changes every prompt.",
        }),
      ),
    ).rejects.toThrow(/immutable/i);
  });
});

describe("fail-closed transcript coverage", () => {
  it("passes only complete harness-authored reviewer evidence", async () => {
    const run = fixture();
    const prompts = await buildPrompts(
      run.request("build-prompts", { effort: "medium" }),
    );
    prompts.prompts.forEach((_, index) =>
      writeTranscript(prompts, run.artifactRoot, index, "complete"),
    );

    const result = await checkCoverage(run.request("check-coverage", {}));
    expect(result.status).toBe("passed");
    expect(result.output.coverage.ok).toBe(true);
  });

  it.each(["missing", "idle", "rewritten", "unopened", "unread"] as const)(
    "fails closed for a %s required reviewer",
    async (fixtureKind) => {
      const run = fixture();
      const prompts = await buildPrompts(
        run.request("build-prompts", { effort: "medium" }),
      );
      prompts.prompts.forEach((_, index) => {
        if (fixtureKind === "missing" && index === 0) return;
        writeTranscript(
          prompts,
          run.artifactRoot,
          index,
          index === 0 && fixtureKind !== "missing" ? fixtureKind : "complete",
        );
      });

      const result = await checkCoverage(run.request("check-coverage", {}));
      expect(result.status).toBe("failed");
      expect(result.output.coverage.ok).toBe(false);
      if (fixtureKind === "rewritten") {
        expect(result.output.coverage.exactPromptMismatches).toContain("1a");
      }
    },
  );

  it("reports absent transcript infrastructure as inconclusive", async () => {
    const run = fixture();
    await buildPrompts(run.request("build-prompts", { effort: "medium" }));

    const result = await checkCoverage(run.request("check-coverage", {}));
    expect(result.status).toBe("inconclusive");
    expect(result.diagnostics[0]?.code).toBe("transcripts_unavailable");
  });
});

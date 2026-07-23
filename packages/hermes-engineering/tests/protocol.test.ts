import { isAbsolute } from "node:path";

import { describe, expect, it } from "vitest";

import { dispatch } from "../src/handlers/index.js";
import { main, processRequest } from "../src/main.js";
import {
  MAX_REQUEST_BYTES,
  parseCaptureInput,
  parseRequest,
  type EngineRequest,
  type EngineResponse,
} from "../src/protocol.js";

const validRequest = (
  overrides: Partial<EngineRequest> = {},
): EngineRequest => ({
  protocolVersion: 1,
  requestId: "request-1",
  command: "capture-target",
  workspace: ".",
  artifactRoot: "./artifacts",
  input: {},
  ...overrides,
});

describe("parseRequest", () => {
  it("rejects unknown protocol versions without dispatching", () => {
    expect(() => parseRequest({ protocolVersion: 2 })).toThrow(
      /protocolVersion 1/,
    );
  });

  it("accepts every stable command and resolves both paths", () => {
    const commands: EngineRequest["command"][] = [
      "capture-target",
      "build-prompts",
      "build-test",
      "test-efficacy",
      "check-coverage",
      "resolve-anchors",
      "compose-review",
      "cleanup",
    ];

    for (const command of commands) {
      const request = parseRequest(validRequest({ command }));
      expect(request.command).toBe(command);
      expect(isAbsolute(request.workspace)).toBe(true);
      expect(isAbsolute(request.artifactRoot)).toBe(true);
    }
  });

  it.each([
    ["unknown command", { command: "fetch-pr" }],
    ["empty request ID", { requestId: "" }],
    ["non-string workspace", { workspace: 3 }],
    ["array input", { input: [] }],
    ["unknown key", { extra: true }],
  ])("rejects %s", (_name, replacement) => {
    expect(() => parseRequest({ ...validRequest(), ...replacement })).toThrow();
  });

  it("rejects requests larger than 1 MiB", () => {
    const request = validRequest({
      input: { value: "x".repeat(MAX_REQUEST_BYTES) },
    });
    expect(() => parseRequest(request)).toThrow(/1 MiB/);
  });

  it("accepts exact bridge-authenticated reviewer records", () => {
    const authenticatedReviewerRecords = [
      {
        schemaVersion: 1 as const,
        agentId: "reviewer-a",
        agentName: "reviewer",
        launchPrompt: "Review the captured diff.",
        successfulToolCalls: 1,
        diffToolCalls: 1,
        diffReads: [[1, 5] as [number, number]],
        successfulCallArgs: ['{"file_path":"/tmp/target.diff"}'],
        finalText: "No findings.",
        mtimeMs: 1,
      },
    ];

    expect(
      parseRequest(validRequest({ authenticatedReviewerRecords }))
        .authenticatedReviewerRecords,
    ).toEqual(authenticatedReviewerRecords);
  });

  it.each([
    [[]],
    [[{ schemaVersion: 1, agentId: "../escape" }]],
    [
      [
        {
          schemaVersion: 1,
          agentId: "reviewer-a",
          agentName: "reviewer",
          launchPrompt: "prompt",
          successfulToolCalls: 0,
          diffToolCalls: 1,
          diffReads: [],
          successfulCallArgs: [],
          finalText: "done",
          mtimeMs: 1,
        },
      ],
    ],
  ])("rejects invalid authenticated reviewer records %#", (records) => {
    expect(() =>
      parseRequest({
        ...validRequest(),
        authenticatedReviewerRecords: records,
      }),
    ).toThrow(/authenticatedReviewerRecords|authenticated reviewer/);
  });
});

describe("parseCaptureInput", () => {
  it.each([
    { kind: "local" },
    { kind: "file", path: "src/a.ts" },
    { kind: "file", path: "src/a.ts", base: "main" },
    { kind: "range", range: "main...topic" },
    { kind: "pr", number: 7, ownerRepo: "owner/repo" },
  ])("accepts the exact capture union member %#", (input) => {
    expect(parseCaptureInput(input)).toEqual(input);
  });

  it.each([
    {},
    { kind: "local", extra: true },
    { kind: "file", path: "" },
    { kind: "range", range: "" },
    { kind: "pr", number: 0, ownerRepo: "owner/repo" },
    { kind: "pr", number: 1, ownerRepo: "not-a-repository" },
    { kind: "pr", number: 1, ownerRepo: "owner/repo?redirect=other" },
  ])("rejects an invalid capture input %#", (input) => {
    expect(() => parseCaptureInput(input)).toThrow();
  });
});

describe("dispatch", () => {
  it("returns a typed failure for an invalid capture target", async () => {
    const request = parseRequest(validRequest());
    const response = await dispatch(request);

    expect(response).toMatchObject({
      protocolVersion: 1,
      requestId: request.requestId,
      status: "failed",
      output: {},
      diagnostics: [{ code: "invalid_target" }],
    });
  });
});

describe("processRequest", () => {
  it("turns unhandled errors into a typed inconclusive exit-3 result", async () => {
    const result = await processRequest(
      JSON.stringify(validRequest()),
      async () => {
        throw new Error("handler exploded");
      },
    );

    expect(result.exitCode).toBe(3);
    expect(result.response).toMatchObject({
      protocolVersion: 1,
      requestId: "request-1",
      status: "inconclusive",
      diagnostics: [{ code: "internal_error" }],
    });
    expect(result.error?.stack).toContain("handler exploded");
  });
});

describe("main", () => {
  it("returns exactly one typed exit-3 response when stdin reading fails", async () => {
    const output: string[] = [];
    const previousExitCode = process.exitCode;
    try {
      await main({
        input: (async function* () {
          throw new Error("stdin exploded");
        })(),
        writeOutput: (line) => void output.push(line),
      });

      expect(process.exitCode).toBe(3);
      expect(output).toHaveLength(1);
      expect(JSON.parse(output[0]!)).toMatchObject({
        protocolVersion: 1,
        status: "inconclusive",
        diagnostics: [{ code: "internal_error" }],
      });
    } finally {
      process.exitCode = previousExitCode;
    }
  });

  it("returns exactly one typed exit-3 response for nonserializable output", async () => {
    const output: string[] = [];
    const previousExitCode = process.exitCode;
    try {
      await main({
        input: (async function* () {
          yield JSON.stringify(validRequest());
        })(),
        dispatchRequest: async (request) =>
          ({
            protocolVersion: 1,
            requestId: request.requestId,
            status: "passed",
            output: { unsafe: 1n },
            diagnostics: [],
          }) as unknown as EngineResponse,
        writeOutput: (line) => void output.push(line),
      });

      expect(process.exitCode).toBe(3);
      expect(output).toHaveLength(1);
      expect(JSON.parse(output[0]!)).toMatchObject({
        protocolVersion: 1,
        requestId: "request-1",
        status: "inconclusive",
        diagnostics: [{ code: "internal_error" }],
      });
    } finally {
      process.exitCode = previousExitCode;
    }
  });
});

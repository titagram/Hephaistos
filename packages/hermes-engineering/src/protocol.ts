import { resolve } from "node:path";

export const MAX_REQUEST_BYTES = 1024 * 1024;
export const MAX_TRANSPORT_BYTES = 4 * MAX_REQUEST_BYTES;

export type CheckStatus = "passed" | "failed" | "inconclusive";

export type EngineCommand =
  | "capture-target"
  | "build-prompts"
  | "build-test"
  | "test-efficacy"
  | "check-coverage"
  | "resolve-anchors"
  | "compose-review"
  | "cleanup";

export interface EngineRequest {
  protocolVersion: 1;
  requestId: string;
  command: EngineCommand;
  workspace: string;
  artifactRoot: string;
  input: Record<string, unknown>;
  authenticatedReviewerRecords?: AuthenticatedReviewerRecord[];
}

export interface AuthenticatedReviewerRecord {
  schemaVersion: 1;
  agentId: string;
  agentName: string;
  launchPrompt: string;
  successfulToolCalls: number;
  diffToolCalls: number;
  diffReads: Array<[number, number]>;
  successfulCallArgs: string[];
  finalText: string;
  mtimeMs: number;
}

export interface EngineResponse {
  protocolVersion: 1;
  requestId: string;
  status: CheckStatus;
  output: Record<string, unknown>;
  diagnostics: Array<{ code: string; message: string }>;
}

export type CaptureInput =
  | { kind: "local" }
  | { kind: "file"; path: string; base?: string }
  | { kind: "range"; range: string }
  | { kind: "pr"; number: number; ownerRepo: string };

export interface CaptureSkippedFile {
  path: string;
  bytes: number | null;
  reason: string;
}

export interface CaptureTargetOutput {
  targetKind: CaptureInput["kind"];
  baseRef: string | null;
  headRef: string;
  diffPath: string;
  planPath: string;
  worktreePath: string | null;
  skippedFiles: CaptureSkippedFile[];
  files: Array<{
    path: string;
    kind: "source" | "test" | "generated" | "docs";
    hunks: Array<{ newStart: number; newEnd: number }>;
    addedRanges?: Array<{ start: number; end: number }>;
    diffRange?: { startLine: number; endLine: number };
    addedLines: number;
    removedLines: number;
    changedLines: number;
    preLines: number;
    fileLines: number;
    rewriteRatio: number;
    heavy: boolean;
    binary: boolean;
  }>;
  chunks: Array<{
    id: number;
    startLine: number;
    endLine: number;
    lines: number;
    chars: number;
    maxLineChars: number;
    oversized: boolean;
    files: Array<{ path: string; newStart: number; newEnd: number }>;
  }>;
}

const REQUEST_KEYS = new Set([
  "protocolVersion",
  "requestId",
  "command",
  "workspace",
  "artifactRoot",
  "input",
  "authenticatedReviewerRecords",
]);

const ENGINE_COMMANDS = new Set<EngineCommand>([
  "capture-target",
  "build-prompts",
  "build-test",
  "test-efficacy",
  "check-coverage",
  "resolve-anchors",
  "compose-review",
  "cleanup",
]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const requiredString = (
  value: Record<string, unknown>,
  key: string,
): string => {
  const field = value[key];
  if (typeof field !== "string" || field.length === 0) {
    throw new TypeError(`${key} must be a non-empty string`);
  }
  return field;
};

const rejectUnknownFields = (
  value: Record<string, unknown>,
  allowed: readonly string[],
): void => {
  const allowedFields = new Set(allowed);
  const unknown = Object.keys(value).find((key) => !allowedFields.has(key));
  if (unknown) throw new TypeError(`unknown capture input field: ${unknown}`);
};

export function parseCaptureInput(value: unknown): CaptureInput {
  if (!isRecord(value)) throw new TypeError("capture input must be an object");
  const kind = value.kind;
  if (kind === "local") {
    rejectUnknownFields(value, ["kind"]);
    return { kind };
  }
  if (kind === "file") {
    rejectUnknownFields(value, ["kind", "path", "base"]);
    const path = requiredString(value, "path");
    const base = value.base;
    if (base === undefined) return { kind, path };
    if (typeof base !== "string" || base.length === 0) {
      throw new TypeError("base must be a non-empty string");
    }
    return { kind, path, base };
  }
  if (kind === "range") {
    rejectUnknownFields(value, ["kind", "range"]);
    return { kind, range: requiredString(value, "range") };
  }
  if (kind === "pr") {
    rejectUnknownFields(value, ["kind", "number", "ownerRepo"]);
    if (!Number.isSafeInteger(value.number) || (value.number as number) < 1) {
      throw new TypeError("number must be a positive integer");
    }
    const ownerRepo = requiredString(value, "ownerRepo");
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(ownerRepo)) {
      throw new TypeError('ownerRepo must look like "owner/repo"');
    }
    return { kind, number: value.number as number, ownerRepo };
  }
  throw new TypeError("capture input kind must be local, file, range, or pr");
}

export function parseRequest(value: unknown): EngineRequest {
  let encoded: string;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new TypeError("request must be JSON-serializable");
  }
  if (
    encoded === undefined ||
    Buffer.byteLength(encoded, "utf8") > MAX_TRANSPORT_BYTES
  ) {
    throw new TypeError("request transport must not exceed 4 MiB");
  }
  if (!isRecord(value)) {
    throw new TypeError("request must be an object");
  }
  const callerValue = { ...value };
  delete callerValue.authenticatedReviewerRecords;
  if (
    Buffer.byteLength(JSON.stringify(callerValue), "utf8") > MAX_REQUEST_BYTES
  ) {
    throw new TypeError("caller request must not exceed 1 MiB");
  }
  if (value.protocolVersion !== 1) {
    throw new TypeError("request requires protocolVersion 1");
  }
  const unknownKeys = Object.keys(value).filter(
    (key) => !REQUEST_KEYS.has(key),
  );
  if (unknownKeys.length > 0) {
    throw new TypeError(`unknown request field: ${unknownKeys[0]}`);
  }

  const command = value.command;
  if (
    typeof command !== "string" ||
    !ENGINE_COMMANDS.has(command as EngineCommand)
  ) {
    throw new TypeError("command is not supported by protocolVersion 1");
  }
  if (!isRecord(value.input)) {
    throw new TypeError("input must be an object");
  }

  const authenticatedReviewerRecords =
    value.authenticatedReviewerRecords === undefined
      ? undefined
      : parseAuthenticatedReviewerRecords(value.authenticatedReviewerRecords);

  return {
    protocolVersion: 1,
    requestId: requiredString(value, "requestId"),
    command: command as EngineCommand,
    workspace: resolve(requiredString(value, "workspace")),
    artifactRoot: resolve(requiredString(value, "artifactRoot")),
    input: value.input,
    ...(authenticatedReviewerRecords === undefined
      ? {}
      : { authenticatedReviewerRecords }),
  };
}

const AUTHENTICATED_RECORD_KEYS = new Set([
  "schemaVersion",
  "agentId",
  "agentName",
  "launchPrompt",
  "successfulToolCalls",
  "diffToolCalls",
  "diffReads",
  "successfulCallArgs",
  "finalText",
  "mtimeMs",
]);

const parseAuthenticatedReviewerRecords = (
  value: unknown,
): AuthenticatedReviewerRecord[] => {
  if (!Array.isArray(value) || value.length === 0 || value.length > 1_024) {
    throw new TypeError(
      "authenticatedReviewerRecords must contain 1-1024 records",
    );
  }
  const seen = new Set<string>();
  return value.map((entry, index) => {
    if (!isRecord(entry)) {
      throw new TypeError(`authenticatedReviewerRecords[${index}] is invalid`);
    }
    const unknown = Object.keys(entry).find(
      (key) => !AUTHENTICATED_RECORD_KEYS.has(key),
    );
    if (unknown !== undefined) {
      throw new TypeError(`unknown authenticated reviewer field: ${unknown}`);
    }
    const agentId = entry.agentId;
    if (
      entry.schemaVersion !== 1 ||
      typeof agentId !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(agentId) ||
      seen.has(agentId) ||
      typeof entry.agentName !== "string" ||
      typeof entry.launchPrompt !== "string" ||
      typeof entry.finalText !== "string" ||
      !Number.isSafeInteger(entry.successfulToolCalls) ||
      (entry.successfulToolCalls as number) < 0 ||
      !Number.isSafeInteger(entry.diffToolCalls) ||
      (entry.diffToolCalls as number) < 0 ||
      (entry.diffToolCalls as number) > (entry.successfulToolCalls as number) ||
      !Number.isFinite(entry.mtimeMs)
    ) {
      throw new TypeError(`authenticatedReviewerRecords[${index}] is invalid`);
    }
    if (
      !Array.isArray(entry.successfulCallArgs) ||
      entry.successfulCallArgs.length !== entry.successfulToolCalls ||
      entry.successfulCallArgs.some(
        (argument) => typeof argument !== "string",
      ) ||
      !Array.isArray(entry.diffReads) ||
      entry.diffReads.some(
        (range) =>
          !Array.isArray(range) ||
          range.length !== 2 ||
          !Number.isSafeInteger(range[0]) ||
          !Number.isSafeInteger(range[1]) ||
          (range[0] as number) < 1 ||
          (range[1] as number) < (range[0] as number),
      )
    ) {
      throw new TypeError(
        `authenticatedReviewerRecords[${index}] has invalid call evidence`,
      );
    }
    seen.add(agentId);
    return entry as unknown as AuthenticatedReviewerRecord;
  });
};

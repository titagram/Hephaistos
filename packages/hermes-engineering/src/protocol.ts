import { resolve } from "node:path";

export const MAX_REQUEST_BYTES = 1024 * 1024;

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
}

export interface EngineResponse {
  protocolVersion: 1;
  requestId: string;
  status: CheckStatus;
  output: Record<string, unknown>;
  diagnostics: Array<{ code: string; message: string }>;
}

const REQUEST_KEYS = new Set([
  "protocolVersion",
  "requestId",
  "command",
  "workspace",
  "artifactRoot",
  "input",
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

export function parseRequest(value: unknown): EngineRequest {
  let encoded: string;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new TypeError("request must be JSON-serializable");
  }
  if (
    encoded === undefined ||
    Buffer.byteLength(encoded, "utf8") > MAX_REQUEST_BYTES
  ) {
    throw new TypeError("request must not exceed 1 MiB");
  }
  if (!isRecord(value)) {
    throw new TypeError("request must be an object");
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

  return {
    protocolVersion: 1,
    requestId: requiredString(value, "requestId"),
    command: command as EngineCommand,
    workspace: resolve(requiredString(value, "workspace")),
    artifactRoot: resolve(requiredString(value, "artifactRoot")),
    input: value.input,
  };
}

// packages/hermes-engineering/src/main.ts
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

// packages/hermes-engineering/src/handlers/index.ts
async function dispatch(request) {
  return {
    protocolVersion: 1,
    requestId: request.requestId,
    status: "inconclusive",
    output: {},
    diagnostics: [
      {
        code: "handler_not_implemented",
        message: `No handler is installed for ${request.command}`
      }
    ]
  };
}

// packages/hermes-engineering/src/protocol.ts
import { resolve } from "node:path";
var MAX_REQUEST_BYTES = 1024 * 1024;
var REQUEST_KEYS = /* @__PURE__ */ new Set([
  "protocolVersion",
  "requestId",
  "command",
  "workspace",
  "artifactRoot",
  "input"
]);
var ENGINE_COMMANDS = /* @__PURE__ */ new Set([
  "capture-target",
  "build-prompts",
  "build-test",
  "test-efficacy",
  "check-coverage",
  "resolve-anchors",
  "compose-review",
  "cleanup"
]);
var isRecord = (value) => typeof value === "object" && value !== null && !Array.isArray(value);
var requiredString = (value, key) => {
  const field = value[key];
  if (typeof field !== "string" || field.length === 0) {
    throw new TypeError(`${key} must be a non-empty string`);
  }
  return field;
};
function parseRequest(value) {
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new TypeError("request must be JSON-serializable");
  }
  if (encoded === void 0 || Buffer.byteLength(encoded, "utf8") > MAX_REQUEST_BYTES) {
    throw new TypeError("request must not exceed 1 MiB");
  }
  if (!isRecord(value)) {
    throw new TypeError("request must be an object");
  }
  if (value.protocolVersion !== 1) {
    throw new TypeError("request requires protocolVersion 1");
  }
  const unknownKeys = Object.keys(value).filter(
    (key) => !REQUEST_KEYS.has(key)
  );
  if (unknownKeys.length > 0) {
    throw new TypeError(`unknown request field: ${unknownKeys[0]}`);
  }
  const command = value.command;
  if (typeof command !== "string" || !ENGINE_COMMANDS.has(command)) {
    throw new TypeError("command is not supported by protocolVersion 1");
  }
  if (!isRecord(value.input)) {
    throw new TypeError("input must be an object");
  }
  return {
    protocolVersion: 1,
    requestId: requiredString(value, "requestId"),
    command,
    workspace: resolve(requiredString(value, "workspace")),
    artifactRoot: resolve(requiredString(value, "artifactRoot")),
    input: value.input
  };
}

// packages/hermes-engineering/src/shims/stdioHelpers.ts
var writeStdoutLine = (line) => void process.stdout.write(`${line}
`);
var writeStderrLine = (line) => void process.stderr.write(`${line}
`);

// packages/hermes-engineering/src/main.ts
var InvalidInputError = class extends Error {
};
var requestIdFrom = (value) => {
  if (typeof value === "object" && value !== null && !Array.isArray(value) && typeof value.requestId === "string") {
    const requestId = value.requestId;
    if (requestId.length > 0) return requestId;
  }
  return "invalid";
};
var diagnosticResponse = (requestId, status, code, message) => ({
  protocolVersion: 1,
  requestId,
  status,
  output: {},
  diagnostics: [{ code, message }]
});
var asError = (value) => value instanceof Error ? value : new Error(String(value));
var internalErrorResult = (requestId, cause) => {
  const error = asError(cause);
  return {
    response: diagnosticResponse(
      requestId,
      "inconclusive",
      "internal_error",
      error.message
    ),
    exitCode: 3,
    error
  };
};
var serializeResponse = (response) => {
  const serialized = JSON.stringify(response);
  if (serialized === void 0) {
    throw new TypeError("dispatcher response is not JSON-serializable");
  }
  return serialized;
};
async function processRequest(raw, dispatchRequest = dispatch) {
  let value;
  try {
    if (Buffer.byteLength(raw, "utf8") > MAX_REQUEST_BYTES) {
      throw new TypeError("request must not exceed 1 MiB");
    }
    value = JSON.parse(raw);
    const request = parseRequest(value);
    try {
      const response = await dispatchRequest(request);
      serializeResponse(response);
      return { response, exitCode: 0 };
    } catch (cause) {
      return internalErrorResult(request.requestId, cause);
    }
  } catch (cause) {
    const error = asError(cause);
    return {
      response: diagnosticResponse(
        requestIdFrom(value),
        "failed",
        "invalid_request",
        error.message
      ),
      exitCode: 2
    };
  }
}
var readStdin = async (input) => {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of input) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes <= MAX_REQUEST_BYTES) chunks.push(buffer);
  }
  if (bytes > MAX_REQUEST_BYTES) {
    throw new InvalidInputError("request must not exceed 1 MiB");
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(
      Buffer.concat(chunks)
    );
  } catch (cause) {
    throw new InvalidInputError("stdin must contain valid UTF-8", { cause });
  }
};
async function main(options = {}) {
  let result;
  try {
    const raw = await readStdin(options.input ?? process.stdin);
    result = await processRequest(raw, options.dispatchRequest ?? dispatch);
  } catch (cause) {
    result = cause instanceof InvalidInputError ? {
      response: diagnosticResponse(
        "invalid",
        "failed",
        "invalid_request",
        cause.message
      ),
      exitCode: 2
    } : internalErrorResult("invalid", cause);
  }
  let serialized;
  try {
    serialized = serializeResponse(result.response);
  } catch (cause) {
    result = internalErrorResult(requestIdFrom(result.response), cause);
    serialized = serializeResponse(result.response);
  }
  (options.writeOutput ?? writeStdoutLine)(serialized);
  if (options.includeStackTrace === true && result.error?.stack) {
    (options.writeError ?? writeStderrLine)(result.error.stack);
  }
  process.exitCode = result.exitCode;
}
var entrypoint = process.argv[1];
if (entrypoint && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(entrypoint)) {
  await main();
}
export {
  main,
  processRequest
};
/**
 * @license
 * Copyright 2025 Qwen Team
 * SPDX-License-Identifier: Apache-2.0
 */

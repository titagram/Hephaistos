import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { dispatch } from "./handlers/index.js";
import {
  MAX_REQUEST_BYTES,
  parseRequest,
  type EngineRequest,
  type EngineResponse,
} from "./protocol.js";
import { writeStderrLine, writeStdoutLine } from "./shims/stdioHelpers.js";

type Dispatcher = (request: EngineRequest) => Promise<EngineResponse>;
type LineWriter = (line: string) => void;
type InputStream = AsyncIterable<string | Uint8Array>;

class InvalidInputError extends Error {}

export interface ProcessResult {
  response: EngineResponse;
  exitCode: 0 | 2 | 3;
  error?: Error;
}

const requestIdFrom = (value: unknown): string => {
  if (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    typeof (value as Record<string, unknown>).requestId === "string"
  ) {
    const requestId = (value as Record<string, unknown>).requestId as string;
    if (requestId.length > 0) return requestId;
  }
  return "invalid";
};

const diagnosticResponse = (
  requestId: string,
  status: "failed" | "inconclusive",
  code: string,
  message: string,
): EngineResponse => ({
  protocolVersion: 1,
  requestId,
  status,
  output: {},
  diagnostics: [{ code, message }],
});

const asError = (value: unknown): Error =>
  value instanceof Error ? value : new Error(String(value));

const internalErrorResult = (
  requestId: string,
  cause: unknown,
): ProcessResult => {
  const error = asError(cause);
  return {
    response: diagnosticResponse(
      requestId,
      "inconclusive",
      "internal_error",
      error.message,
    ),
    exitCode: 3,
    error,
  };
};

const serializeResponse = (response: EngineResponse): string => {
  const serialized = JSON.stringify(response);
  if (serialized === undefined) {
    throw new TypeError("dispatcher response is not JSON-serializable");
  }
  return serialized;
};

export async function processRequest(
  raw: string,
  dispatchRequest: Dispatcher = dispatch,
): Promise<ProcessResult> {
  let value: unknown;
  try {
    if (Buffer.byteLength(raw, "utf8") > MAX_REQUEST_BYTES) {
      throw new TypeError("request must not exceed 1 MiB");
    }
    value = JSON.parse(raw) as unknown;
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
        error.message,
      ),
      exitCode: 2,
    };
  }
}

const readStdin = async (input: InputStream): Promise<string> => {
  const chunks: Buffer[] = [];
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
      Buffer.concat(chunks),
    );
  } catch (cause) {
    throw new InvalidInputError("stdin must contain valid UTF-8", { cause });
  }
};

export async function main(
  options: {
    includeStackTrace?: boolean;
    input?: InputStream;
    dispatchRequest?: Dispatcher;
    writeOutput?: LineWriter;
    writeError?: LineWriter;
  } = {},
): Promise<void> {
  let result: ProcessResult;
  try {
    const raw = await readStdin(options.input ?? process.stdin);
    result = await processRequest(raw, options.dispatchRequest ?? dispatch);
  } catch (cause) {
    result =
      cause instanceof InvalidInputError
        ? {
            response: diagnosticResponse(
              "invalid",
              "failed",
              "invalid_request",
              cause.message,
            ),
            exitCode: 2,
          }
        : internalErrorResult("invalid", cause);
  }

  let serialized: string;
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

const entrypoint = process.argv[1];
if (
  entrypoint &&
  realpathSync(fileURLToPath(import.meta.url)) === realpathSync(entrypoint)
) {
  await main();
}

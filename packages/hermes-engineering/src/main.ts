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
      return { response: await dispatchRequest(request), exitCode: 0 };
    } catch (cause) {
      const error = asError(cause);
      return {
        response: diagnosticResponse(
          request.requestId,
          "inconclusive",
          "internal_error",
          error.message,
        ),
        exitCode: 3,
        error,
      };
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

const readStdin = async (): Promise<string> => {
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes <= MAX_REQUEST_BYTES) chunks.push(buffer);
  }
  if (bytes > MAX_REQUEST_BYTES) {
    throw new TypeError("request must not exceed 1 MiB");
  }
  return Buffer.concat(chunks).toString("utf8");
};

export async function main(
  options: {
    includeStackTrace?: boolean;
  } = {},
): Promise<void> {
  let result: ProcessResult;
  try {
    result = await processRequest(await readStdin());
  } catch (cause) {
    const error = asError(cause);
    result = {
      response: diagnosticResponse(
        "invalid",
        "failed",
        "invalid_request",
        error.message,
      ),
      exitCode: 2,
    };
  }

  writeStdoutLine(JSON.stringify(result.response));
  if (options.includeStackTrace === true && result.error?.stack) {
    writeStderrLine(result.error.stack);
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

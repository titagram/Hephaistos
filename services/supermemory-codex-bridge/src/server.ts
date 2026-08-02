import crypto from "node:crypto";
import http, { type IncomingMessage, type ServerResponse } from "node:http";

import {
  CodexUpstreamError,
  type CodexUpstreamErrorKind,
  type CodexRunner,
} from "./codex-app-server.js";
import type { BridgeConfig } from "./config.js";
import {
  ApiError,
  createChatCompletion,
  parseChatCompletionRequest,
} from "./openai.js";
import { buildCodexInvocation } from "./prompt.js";

interface ErrorResponse {
  error: {
    message: string;
    type: "codex_bridge_error";
    code: string;
  };
}

interface MappedError {
  status: number;
  code: string;
  message: string;
}

type RequestValueType = "null" | "array" | "object" | "string" | "number" | "boolean" | "undefined/other";

interface RequestShapeField {
  readonly field: string;
  readonly type: RequestValueType;
}

interface RequestShape {
  readonly knownFields: RequestShapeField[];
  readonly unknownFieldCount: number;
  readonly unknownFieldTypes: Array<{
    readonly type: RequestValueType;
    readonly count: number;
  }>;
}

const requestValueTypes: readonly RequestValueType[] = [
  "null",
  "array",
  "object",
  "string",
  "number",
  "boolean",
  "undefined/other",
];

const knownChatCompletionRequestFields = new Set([
  "audio",
  "frequency_penalty",
  "function_call",
  "functions",
  "logit_bias",
  "logprobs",
  "max_completion_tokens",
  "max_tokens",
  "messages",
  "metadata",
  "modalities",
  "model",
  "n",
  "parallel_tool_calls",
  "prediction",
  "presence_penalty",
  "prompt_cache_key",
  "reasoning_effort",
  "response_format",
  "safety_identifier",
  "seed",
  "service_tier",
  "serviceTier",
  "stop",
  "store",
  "stream",
  "stream_options",
  "temperature",
  "tool_choice",
  "tools",
  "top_logprobs",
  "top_p",
  "user",
  "verbosity",
  "web_search_options",
]);

const startupByServer = new WeakMap<http.Server, Promise<void>>();

interface Waiter {
  readonly resolve: (release: () => void) => void;
  readonly reject: (error: Error) => void;
  readonly signal: AbortSignal;
  readonly onAbort: () => void;
}

class RequestAbortedError extends Error {
  constructor() {
    super("Request aborted");
    this.name = "RequestAbortedError";
  }
}

class FifoSemaphore {
  private permits: number;
  private readonly queue: Waiter[] = [];

  constructor(permits: number) {
    this.permits = permits;
  }

  acquire(signal: AbortSignal): Promise<() => void> {
    if (signal.aborted) return Promise.reject(new RequestAbortedError());
    if (this.permits > 0) {
      this.permits -= 1;
      return Promise.resolve(this.releaseOnce());
    }

    return new Promise((resolve, reject) => {
      const waiter: Waiter = {
        resolve,
        reject,
        signal,
        onAbort: () => {
          const index = this.queue.indexOf(waiter);
          if (index >= 0) this.queue.splice(index, 1);
          reject(new RequestAbortedError());
        },
      };
      signal.addEventListener("abort", waiter.onAbort, { once: true });
      this.queue.push(waiter);
    });
  }

  private releaseOnce(): () => void {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.release();
    };
  }

  private release(): void {
    while (this.queue.length > 0) {
      const waiter = this.queue.shift()!;
      waiter.signal.removeEventListener("abort", waiter.onAbort);
      if (waiter.signal.aborted) {
        waiter.reject(new RequestAbortedError());
        continue;
      }
      waiter.resolve(this.releaseOnce());
      return;
    }
    this.permits += 1;
  }
}

const upstreamErrors: Record<CodexUpstreamErrorKind, MappedError> = {
  authentication: {
    status: 503,
    code: "codex_authentication_required",
    message: "Codex authentication is required.",
  },
  rate_limit: {
    status: 429,
    code: "codex_rate_limited",
    message: "Codex is rate limited.",
  },
  timeout: {
    status: 504,
    code: "codex_timeout",
    message: "Codex did not respond before the deadline.",
  },
  structured_output: {
    status: 502,
    code: "codex_structured_output_error",
    message: "Codex could not produce the requested structured output.",
  },
  forbidden_tool: {
    status: 502,
    code: "codex_policy_violation",
    message: "Codex attempted an action prohibited by bridge policy.",
  },
  unavailable: {
    status: 503,
    code: "codex_unavailable",
    message: "Codex is unavailable.",
  },
  upstream: {
    status: 502,
    code: "codex_upstream_error",
    message: "Codex returned an upstream error.",
  },
};

const genericErrors = {
  unauthorized: { status: 401, code: "unauthorized", message: "Authentication is required." },
  notFound: { status: 404, code: "not_found", message: "The requested route was not found." },
  methodNotAllowed: { status: 405, code: "method_not_allowed", message: "The request method is not allowed." },
  invalidJson: { status: 400, code: "invalid_json", message: "The request body is not valid JSON." },
  bodyTooLarge: { status: 413, code: "body_too_large", message: "The request body is too large." },
  upstream: upstreamErrors.upstream,
} satisfies Record<string, MappedError>;

export function createBridgeServer(config: BridgeConfig, codex: CodexRunner): http.Server {
  const semaphore = new FifoSemaphore(config.maxConcurrency);
  let ready = false;
  const startup = codex.start();
  void startup.then(() => {
    ready = true;
  }, () => {
    ready = false;
  });

  const server = http.createServer((request, response) => {
    void handleRequest(request, response, config, codex, semaphore, () => ready);
  });
  startupByServer.set(server, startup);
  server.requestTimeout = config.timeoutMs + 5_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  return server;
}

export function waitForBridgeStartup(server: http.Server): Promise<void> {
  return startupByServer.get(server)
    ?? Promise.reject(new CodexUpstreamError("unavailable"));
}

async function handleRequest(
  request: IncomingMessage,
  response: ServerResponse,
  config: BridgeConfig,
  codex: CodexRunner,
  semaphore: FifoSemaphore,
  isReady: () => boolean,
): Promise<void> {
  const startedAt = Date.now();
  const requestId = crypto.randomUUID();
  const route = requestPath(request);
  const logRoute = canonicalRoute(route);
  let status = 500;
  let errorCode: string | undefined;
  let requestShape: RequestShape | undefined;
  let disconnected = false;
  const controller = new AbortController();
  const onDisconnect = () => {
    if (response.writableEnded) return;
    disconnected = true;
    controller.abort("client_disconnect");
  };
  request.once("aborted", onDisconnect);
  response.once("close", onDisconnect);

  try {
    if (route === "/healthz") {
      if (request.method !== "GET") {
        response.setHeader("allow", "GET");
        throw mappedApiError(genericErrors.methodNotAllowed);
      }
      status = isReady() ? 200 : 503;
      sendJson(response, status, { status: isReady() ? "ok" : "starting" });
      return;
    }

    if (route.startsWith("/v1/") && !isAuthorized(request.headers.authorization, config.apiKey)) {
      throw mappedApiError(genericErrors.unauthorized);
    }
    if (route !== "/v1/chat/completions") {
      throw mappedApiError(genericErrors.notFound);
    }
    if (request.method !== "POST") {
      response.setHeader("allow", "POST");
      throw mappedApiError(genericErrors.methodNotAllowed);
    }

    const timer = setTimeout(() => controller.abort("deadline"), config.timeoutMs);
    let release: (() => void) | undefined;
    try {
      const rawBody = await readBody(request, config.maxBodyBytes, controller.signal);
      let parsedBody: unknown;
      try {
        parsedBody = JSON.parse(rawBody.toString("utf8"));
      } catch {
        throw mappedApiError(genericErrors.invalidJson);
      }
      requestShape = describeRequestShape(parsedBody);
      const parsed = parseChatCompletionRequest(parsedBody, config.publicModel);
      release = await semaphore.acquire(controller.signal);
      const result = await abortable(codex.run(buildCodexInvocation(parsed), controller.signal), controller.signal);
      if (disconnected || response.destroyed) return;
      status = 200;
      sendJson(response, status, createChatCompletion(parsed, result));
    } finally {
      clearTimeout(timer);
      release?.();
    }
  } catch (error) {
    if (disconnected || response.destroyed) return;
    const mapped = mapError(error, controller.signal);
    status = mapped.status;
    errorCode = mapped.code;
    sendError(response, mapped);
  } finally {
    request.off("aborted", onDisconnect);
    response.off("close", onDisconnect);
    if (disconnected) {
      status = 499;
      errorCode = "client_disconnect";
    }
    console.info(JSON.stringify({
      requestId,
      route: logRoute,
      status,
      durationMs: Date.now() - startedAt,
      ...(errorCode ? { errorCode } : {}),
      ...(requestShape ? { requestShape } : {}),
    }));
  }
}

function describeRequestShape(value: unknown): RequestShape {
  const knownFields: RequestShapeField[] = [];
  const unknownCounts = new Map<RequestValueType, number>();
  let unknownFieldCount = 0;

  if (value !== null && !Array.isArray(value) && typeof value === "object") {
    for (const field of Object.keys(value).sort()) {
      const type = requestValueType((value as Record<string, unknown>)[field]);
      if (knownChatCompletionRequestFields.has(field)) {
        knownFields.push({ field, type });
      } else {
        unknownFieldCount += 1;
        unknownCounts.set(type, (unknownCounts.get(type) ?? 0) + 1);
      }
    }
  }

  return {
    knownFields,
    unknownFieldCount,
    unknownFieldTypes: requestValueTypes.flatMap((type) => {
      const count = unknownCounts.get(type);
      return count === undefined ? [] : [{ type, count }];
    }),
  };
}

function requestValueType(value: unknown): RequestValueType {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  switch (typeof value) {
    case "object": return "object";
    case "string": return "string";
    case "number": return "number";
    case "boolean": return "boolean";
    default: return "undefined/other";
  }
}

function requestPath(request: IncomingMessage): string {
  try {
    return new URL(request.url ?? "/", "http://bridge.invalid").pathname;
  } catch {
    return "/";
  }
}

function canonicalRoute(route: string): string {
  if (route === "/healthz") return "/healthz";
  if (route === "/v1/chat/completions") return "/v1/chat/completions";
  return "unknown";
}

function isAuthorized(header: string | undefined, expectedKey: string): boolean {
  if (!header?.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice("Bearer ".length));
  const expected = Buffer.from(expectedKey);
  return supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected);
}

function readBody(request: IncomingMessage, limit: number, signal: AbortSignal): Promise<Buffer> {
  const contentLength = Number(request.headers["content-length"]);
  if (Number.isFinite(contentLength) && contentLength > limit) {
    request.resume();
    return Promise.reject(mappedApiError(genericErrors.bodyTooLarge));
  }

  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let bytes = 0;
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      action();
    };
    const onAbort = () => finish(() => reject(new RequestAbortedError()));
    signal.addEventListener("abort", onAbort, { once: true });
    request.on("data", (chunk: Buffer | string) => {
      if (settled) return;
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      bytes += buffer.length;
      if (bytes > limit) {
        chunks.length = 0;
        finish(() => reject(mappedApiError(genericErrors.bodyTooLarge)));
        request.resume();
        return;
      }
      chunks.push(buffer);
    });
    request.once("end", () => finish(() => resolve(Buffer.concat(chunks, bytes))));
    request.once("error", () => finish(() => reject(new RequestAbortedError())));
  });
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new RequestAbortedError());
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(new RequestAbortedError());
    signal.addEventListener("abort", onAbort, { once: true });
    void promise.then((value) => {
      signal.removeEventListener("abort", onAbort);
      resolve(value);
    }, (error: unknown) => {
      signal.removeEventListener("abort", onAbort);
      reject(error);
    });
  });
}

function mappedApiError(mapped: MappedError): ApiError {
  return new ApiError(mapped.status, mapped.code, mapped.message);
}

function mapError(error: unknown, signal: AbortSignal): MappedError {
  if (signal.aborted && signal.reason === "deadline") return upstreamErrors.timeout;
  if (error instanceof ApiError) {
    return { status: error.status, code: error.code, message: error.message };
  }
  if (error instanceof CodexUpstreamError) return upstreamErrors[error.kind];
  return genericErrors.upstream;
}

function sendError(response: ServerResponse, mapped: MappedError): void {
  const body: ErrorResponse = {
    error: {
      message: mapped.message,
      type: "codex_bridge_error",
      code: mapped.code,
    },
  };
  sendJson(response, mapped.status, body);
}

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  if (response.destroyed || response.writableEnded) return;
  const encoded = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(encoded),
  });
  response.end(encoded);
}

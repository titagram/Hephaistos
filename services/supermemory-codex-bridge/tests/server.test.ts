import assert from "node:assert/strict";
import crypto from "node:crypto";
import http from "node:http";
import test from "node:test";

import { CodexUpstreamError, type CodexRunner } from "../src/codex-app-server.js";
import type { BridgeConfig } from "../src/config.js";
import type { CodexInvocation, CodexResult } from "../src/openai.js";
import { createBridgeServer } from "../src/server.js";
import { startBridge, stopBridge } from "../src/index.js";

const config: BridgeConfig = {
  host: "127.0.0.1",
  port: 0,
  apiKey: "bridge-test-key",
  publicModel: "supermemory-codex",
  codexModel: "gpt-5.6-sol",
  codexHome: "/tmp/codex-home",
  codexCwd: "/workspace",
  timeoutMs: 100,
  maxBodyBytes: 1_024,
  maxConcurrency: 2,
};

interface RunCall {
  invocation: CodexInvocation;
  signal: AbortSignal;
}

class FakeRunner implements CodexRunner {
  readonly runs: RunCall[] = [];
  startCalls = 0;
  closeCalls = 0;
  ready = false;
  startImplementation: () => Promise<void> = async () => {};
  runImplementation: (invocation: CodexInvocation, signal: AbortSignal) => Promise<CodexResult> =
    async () => ({ text: "fake answer", usage: { inputTokens: 4, outputTokens: 2 } });

  async start(): Promise<void> {
    this.startCalls += 1;
    try {
      await this.startImplementation();
      this.ready = true;
    } catch (error) {
      this.ready = false;
      throw error;
    }
  }

  isReady(): boolean { return this.ready; }

  run(invocation: CodexInvocation, signal: AbortSignal): Promise<CodexResult> {
    this.runs.push({ invocation, signal });
    return this.runImplementation(invocation, signal);
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
    this.ready = false;
  }
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function listen(runner: FakeRunner, overrides: Partial<BridgeConfig> = {}) {
  const server = createBridgeServer({ ...config, ...overrides }, runner);
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object");
  return {
    server,
    origin: `http://127.0.0.1:${address.port}`,
    close: () => new Promise<void>((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    }),
  };
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    authorization: `Bearer ${config.apiKey}`,
    "content-type": "application/json",
    ...extra,
  };
}

function chatBody(content = "Summarize Ada."): string {
  return JSON.stringify({
    model: config.publicModel,
    messages: [{ role: "user", content }],
  });
}

async function waitFor(predicate: () => boolean, timeoutMs = 1_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error("condition was not met");
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
}

test("health is unavailable until runner startup succeeds", async () => {
  const runner = new FakeRunner();
  const startup = deferred<void>();
  runner.startImplementation = () => startup.promise;
  const harness = await listen(runner);
  try {
    const before = await fetch(`${harness.origin}/healthz`);
    assert.equal(before.status, 503);

    startup.resolve();
    await new Promise((resolve) => setImmediate(resolve));
    const after = await fetch(`${harness.origin}/healthz`);
    assert.equal(after.status, 200);
    assert.deepEqual(await after.json(), { status: "ok" });
    assert.equal(runner.startCalls, 1);
  } finally {
    await harness.close();
  }
});

test("health follows runner death and successful lazy recovery", async () => {
  const runner = new FakeRunner();
  const harness = await listen(runner);
  try {
    await waitFor(() => runner.ready);
    assert.equal((await fetch(`${harness.origin}/healthz`)).status, 200);

    runner.ready = false;
    assert.equal((await fetch(`${harness.origin}/healthz`)).status, 503);

    runner.runImplementation = async () => {
      runner.ready = true;
      return { text: "recovered" };
    };
    assert.equal((await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: chatBody("recover"),
    })).status, 200);
    assert.equal((await fetch(`${harness.origin}/healthz`)).status, 200);
  } finally {
    await harness.close();
  }
});

test("structured Draft-07 requests reach Codex without the root dialect marker", async () => {
  const runner = new FakeRunner();
  runner.runImplementation = async () => ({ text: "{}" });
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        model: config.publicModel,
        messages: [],
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "answer",
            schema: { $schema: "http://json-schema.org/draft-07/schema#", type: "object" },
          },
        },
      }),
    });
    assert.equal(response.status, 200);
    assert.deepEqual(runner.runs[0]?.invocation.outputSchema, { type: "object" });
  } finally {
    await harness.close();
  }
});

test("every v1 route authenticates before routing and never calls the runner when unauthorized", async () => {
  const runner = new FakeRunner();
  const harness = await listen(runner);
  try {
    for (const [path, options] of [
      ["/v1/chat/completions", { method: "POST", body: chatBody() }],
      ["/v1/chat/completions", { method: "GET" }],
      ["/v1/unknown", { method: "POST", body: "{}" }],
    ] as const) {
      const response = await fetch(`${harness.origin}${path}`, options);
      assert.equal(response.status, 401);
      assert.equal((await response.json() as { error: { code: string } }).error.code, "unauthorized");
    }
    assert.equal(runner.runs.length, 0);
  } finally {
    await harness.close();
  }
});

test("bearer authentication compares only equal-length buffers with timingSafeEqual", async (t) => {
  const runner = new FakeRunner();
  const lengths: Array<[number, number]> = [];
  const original = crypto.timingSafeEqual;
  t.mock.method(crypto, "timingSafeEqual", (left: NodeJS.ArrayBufferView, right: NodeJS.ArrayBufferView) => {
    lengths.push([left.byteLength, right.byteLength]);
    return original(left, right);
  });
  const harness = await listen(runner);
  try {
    const wrongLength = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders({ authorization: "Bearer x" }),
      body: chatBody(),
    });
    assert.equal(wrongLength.status, 401);

    const equalLengthWrongKey = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders({ authorization: "Bearer bridge-test-kez" }),
      body: chatBody(),
    });
    assert.equal(equalLengthWrongKey.status, 401);

    const valid = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: chatBody(),
    });
    assert.equal(valid.status, 200);
    assert.deepEqual(lengths, [
      [Buffer.byteLength(config.apiKey), Buffer.byteLength(config.apiKey)],
      [Buffer.byteLength(config.apiKey), Buffer.byteLength(config.apiKey)],
    ]);
  } finally {
    await harness.close();
  }
});

test("chat completions parse, invoke Codex, and return the conventional response", async () => {
  const runner = new FakeRunner();
  runner.runImplementation = async () => ({
    text: "Ada used analytical engines.",
    usage: { inputTokens: 9, outputTokens: 5 },
  });
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: chatBody(),
    });
    assert.equal(response.status, 200);
    const body = await response.json() as Record<string, any>;
    assert.equal(body.object, "chat.completion");
    assert.equal(body.model, config.publicModel);
    assert.equal(body.choices[0].message.content, "Ada used analytical engines.");
    assert.deepEqual(body.usage, { prompt_tokens: 9, completion_tokens: 5, total_tokens: 14 });
    assert.equal(runner.runs.length, 1);
    assert.match(runner.runs[0]!.invocation.prompt, /Summarize Ada\./);
  } finally {
    await harness.close();
  }
});

test("tool chat completions stay symbolic end to end", async (t) => {
  const runner = new FakeRunner();
  const logs: string[] = [];
  t.mock.method(console, "info", (...values: unknown[]) => logs.push(values.join(" ")));
  runner.runImplementation = async () => ({
    text: JSON.stringify({ content: "", tool_calls: [{ name: "add_memory", arguments: "{\"memory\":\"Ada\"}" }] }),
  });
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        model: config.publicModel,
        messages: [{ role: "user", content: "Remember Ada." }],
        max_tokens: 200,
        serviceTier: "flex",
        service_tier: "flex",
        tools: [{ type: "function", function: {
          name: "add_memory",
          description: "Store a memory",
          parameters: { type: "object", properties: { memory: { type: "string" } }, required: ["memory"] },
        } }],
        tool_choice: "auto",
      }),
    });
    assert.equal(response.status, 200);
    const body = await response.json() as any;
    assert.equal(body.choices[0].finish_reason, "tool_calls");
    assert.equal(body.choices[0].message.content, null);
    assert.equal(body.choices[0].message.tool_calls[0].function.name, "add_memory");
    assert.match(runner.runs[0]!.invocation.prompt, /symbolic tool calls only/i);
    assert.deepEqual((runner.runs[0]!.invocation.outputSchema as any).properties.tool_calls.maxItems, 8);
    await waitFor(() => logs.length === 1);
    const requestShape = JSON.parse(logs[0]!).requestShape;
    assert.equal(requestShape.unknownFieldCount, 0);
    assert.deepEqual(requestShape.knownFields.map((field: { field: string }) => field.field), [
      "max_tokens",
      "messages",
      "model",
      "serviceTier",
      "service_tier",
      "tool_choice",
      "tools",
    ]);
  } finally {
    await harness.close();
  }
});

test("strict tool schemas are rejected before Codex can run", async () => {
  const runner = new FakeRunner();
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        model: config.publicModel,
        messages: [],
        tools: [{ type: "function", function: {
          name: "add_memory",
          strict: true,
          parameters: { type: "object", required: ["memory"] },
        } }],
      }),
    });
    assert.equal(response.status, 400);
    assert.equal((await response.json() as any).error.code, "invalid_tools");
    assert.equal(runner.runs.length, 0);
  } finally {
    await harness.close();
  }
});

test("invalid symbolic tool output fails closed at the HTTP boundary", async () => {
  const runner = new FakeRunner();
  runner.runImplementation = async () => ({ text: "not-json" });
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        model: config.publicModel,
        messages: [],
        tools: [{ type: "function", function: {
          name: "add_memory",
          parameters: { type: "object" },
        } }],
      }),
    });
    assert.equal(response.status, 502);
    assert.equal((await response.json() as any).error.code, "codex_structured_output_error");
  } finally {
    await harness.close();
  }
});

test("authenticated wrong methods and paths return stable errors", async () => {
  const runner = new FakeRunner();
  const harness = await listen(runner);
  try {
    const method = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "GET",
      headers: authHeaders(),
    });
    assert.equal(method.status, 405);
    assert.equal(method.headers.get("allow"), "POST");
    assert.equal((await method.json() as any).error.code, "method_not_allowed");

    const missing = await fetch(`${harness.origin}/v1/missing`, {
      method: "POST",
      headers: authHeaders(),
      body: "{}",
    });
    assert.equal(missing.status, 404);
    assert.equal((await missing.json() as any).error.code, "not_found");
    assert.equal(runner.runs.length, 0);
  } finally {
    await harness.close();
  }
});

test("invalid JSON and oversized bodies return stable client errors", async () => {
  const runner = new FakeRunner();
  const harness = await listen(runner, { maxBodyBytes: 32 });
  try {
    const invalid = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: "{not-json",
    });
    assert.equal(invalid.status, 400);
    assert.equal((await invalid.json() as any).error.code, "invalid_json");

    const oversized = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ private: "x".repeat(128) }),
    });
    assert.equal(oversized.status, 413);
    assert.equal((await oversized.json() as any).error.code, "body_too_large");
    assert.equal(runner.runs.length, 0);
  } finally {
    await harness.close();
  }
});

test("the concurrency semaphore is FIFO and queued acquisition is abortable", async () => {
  const runner = new FakeRunner();
  const pending: Array<Deferred<CodexResult>> = [];
  runner.runImplementation = async () => {
    const result = deferred<CodexResult>();
    pending.push(result);
    return result.promise;
  };
  const harness = await listen(runner, { timeoutMs: 1_000, maxConcurrency: 2 });
  try {
    const calls = ["first", "second", "third"].map((content) => fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: chatBody(content),
    }));
    await waitFor(() => runner.runs.length === 2);
    assert.match(runner.runs[0]!.invocation.prompt, /first/);
    assert.match(runner.runs[1]!.invocation.prompt, /second/);

    pending[0]!.resolve({ text: "one" });
    await waitFor(() => runner.runs.length === 3);
    assert.match(runner.runs[2]!.invocation.prompt, /third/);
    pending[1]!.resolve({ text: "two" });
    pending[2]!.resolve({ text: "three" });
    assert.deepEqual(await Promise.all(calls).then((responses) => responses.map((response) => response.status)), [200, 200, 200]);
  } finally {
    await harness.close();
  }
});

test("the request deadline aborts Codex and maps to codex_timeout", async () => {
  const runner = new FakeRunner();
  const neverCompletes = deferred<CodexResult>();
  runner.runImplementation = () => neverCompletes.promise;
  const harness = await listen(runner, { timeoutMs: 20 });
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: chatBody(),
    });
    assert.equal(response.status, 504);
    assert.equal((await response.json() as any).error.code, "codex_timeout");
    assert.equal(runner.runs[0]!.signal.aborted, true);
  } finally {
    await harness.close();
  }
});

test("a queued client disconnect is removed before it can call Codex", async () => {
  const runner = new FakeRunner();
  const firstResult = deferred<CodexResult>();
  runner.runImplementation = () => firstResult.promise;
  const harness = await listen(runner, { timeoutMs: 1_000, maxConcurrency: 1 });
  try {
    const first = fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: chatBody("first occupies the permit"),
    });
    await waitFor(() => runner.runs.length === 1);

    const secondBody = chatBody("second disconnects while queued");
    const second = http.request(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: { ...authHeaders(), "content-length": Buffer.byteLength(secondBody) },
    });
    second.on("error", () => {});
    const clientClosed = new Promise<void>((resolve) => second.once("close", resolve));
    second.end(secondBody);
    await new Promise((resolve) => setTimeout(resolve, 20));
    second.destroy();
    await clientClosed;
    await new Promise((resolve) => setTimeout(resolve, 10));

    firstResult.resolve({ text: "first completed" });
    assert.equal((await first).status, 200);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(runner.runs.length, 1);
  } finally {
    await harness.close();
  }
});

test("client disconnect aborts Codex without a response write and logs a stable code", async (t) => {
  const runner = new FakeRunner();
  const aborted = deferred<void>();
  const logs: string[] = [];
  t.mock.method(console, "info", (...values: unknown[]) => logs.push(values.join(" ")));
  runner.runImplementation = (_invocation, signal) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {
      aborted.resolve();
      reject(new CodexUpstreamError("timeout"));
    }, { once: true });
  });
  const harness = await listen(runner, { timeoutMs: 1_000 });
  try {
    const request = http.request(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: { ...authHeaders(), "content-length": Buffer.byteLength(chatBody()) },
    });
    request.on("error", () => {});
    request.end(chatBody());
    await waitFor(() => runner.runs.length === 1);
    request.destroy();
    await aborted.promise;
    assert.equal(runner.runs[0]!.signal.aborted, true);
    await waitFor(() => logs.length === 1);
    assert.match(logs.join("\n"), /"status":499.*"errorCode":"client_disconnect"/);
  } finally {
    await harness.close();
  }
});

const upstreamMappings = [
  ["authentication", 503, "codex_authentication_required"],
  ["rate_limit", 429, "codex_rate_limited"],
  ["timeout", 504, "codex_timeout"],
  ["structured_output", 502, "codex_structured_output_error"],
  ["forbidden_tool", 502, "codex_policy_violation"],
  ["unavailable", 503, "codex_unavailable"],
  ["upstream", 502, "codex_upstream_error"],
] as const;

for (const [kind, status, code] of upstreamMappings) {
  test(`maps ${kind} to ${status} ${code}`, async () => {
    const runner = new FakeRunner();
    runner.runImplementation = async () => { throw new CodexUpstreamError(kind); };
    const harness = await listen(runner);
    try {
      const response = await fetch(`${harness.origin}/v1/chat/completions`, {
        method: "POST",
        headers: authHeaders(),
        body: chatBody(),
      });
      assert.equal(response.status, status);
      const body = await response.json() as { error: { message: unknown; type: string; code: string } };
      assert.equal(typeof body.error.message, "string");
      assert.deepEqual({ type: body.error.type, code: body.error.code }, {
        type: "codex_bridge_error",
        code,
      });
    } finally {
      await harness.close();
    }
  });
}

test("error responses and logs never expose request or upstream secrets", async (t) => {
  const runner = new FakeRunner();
  const secret = "raw-prompt-and-secret-token";
  runner.runImplementation = async () => { throw new Error(`upstream leaked ${secret}`); };
  const logs: string[] = [];
  t.mock.method(console, "info", (...values: unknown[]) => logs.push(values.join(" ")));
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders({ authorization: `Bearer ${config.apiKey}` }),
      body: chatBody(secret),
    });
    assert.equal(response.status, 502);
    const body = await response.text();
    assert.doesNotMatch(body, new RegExp(secret));
    assert.doesNotMatch(logs.join("\n"), new RegExp(secret));
    assert.doesNotMatch(logs.join("\n"), new RegExp(config.apiKey));
    assert.match(logs.join("\n"), /codex_upstream_error/);
  } finally {
    await harness.close();
  }
});

test("chat completion logs expose only sorted allowlisted top-level field types", async (t) => {
  const runner = new FakeRunner();
  const logs: string[] = [];
  t.mock.method(console, "info", (...values: unknown[]) => logs.push(values.join(" ")));
  const harness = await listen(runner);
  const promptSecret = "prompt-secret-never-log";
  const toolName = "private-tool-name-never-log";
  const schemaProperty = "private-schema-property-never-log";
  const callerControlledFieldSecret = "top-level-caller-secret-never-log";
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        [callerControlledFieldSecret]: "private-field-value-never-log",
        tools: [{ type: "function", function: { name: toolName } }],
        temperature: 0,
        stop: null,
        stream: false,
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "private-schema-name-never-log",
            schema: { type: "object", properties: { [schemaProperty]: { type: "string" } } },
          },
        },
        model: config.publicModel,
        messages: [{ role: "user", content: promptSecret }],
      }),
    });
    assert.equal(response.status, 400);
    await waitFor(() => logs.length === 1);

    const event = JSON.parse(logs[0]!) as {
      requestShape: {
        knownFields: Array<{ field: string; type: string }>;
        unknownFieldCount: number;
        unknownFieldTypes: Array<{ type: string; count: number }>;
      };
    };
    assert.deepEqual(event.requestShape, {
      knownFields: [
        { field: "messages", type: "array" },
        { field: "model", type: "string" },
        { field: "response_format", type: "object" },
        { field: "stop", type: "null" },
        { field: "stream", type: "boolean" },
        { field: "temperature", type: "number" },
        { field: "tools", type: "array" },
      ],
      unknownFieldCount: 1,
      unknownFieldTypes: [{ type: "string", count: 1 }],
    });

    const serialized = logs.join("\n");
    for (const secret of [
      promptSecret,
      toolName,
      schemaProperty,
      "private-schema-name-never-log",
      "private-field-value-never-log",
      callerControlledFieldSecret,
      config.apiKey,
    ]) {
      assert.doesNotMatch(serialized, new RegExp(secret));
    }
  } finally {
    await harness.close();
  }
});

test("unknown top-level fields are summarized without logging caller-controlled names", async (t) => {
  const runner = new FakeRunner();
  const logs: string[] = [];
  t.mock.method(console, "info", (...values: unknown[]) => logs.push(values.join(" ")));
  const harness = await listen(runner);
  const secretFieldName = "short-top-level-secret";
  try {
    const response = await fetch(`${harness.origin}/v1/chat/completions`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        model: config.publicModel,
        messages: [],
        [secretFieldName]: true,
        another_unknown_field: 42,
      }),
    });
    assert.equal(response.status, 400);
    await waitFor(() => logs.length === 1);

    const event = JSON.parse(logs[0]!) as {
      requestShape: {
        knownFields: Array<{ field: string; type: string }>;
        unknownFieldCount: number;
        unknownFieldTypes: Array<{ type: string; count: number }>;
      };
    };
    assert.deepEqual(event.requestShape, {
      knownFields: [
        { field: "messages", type: "array" },
        { field: "model", type: "string" },
      ],
      unknownFieldCount: 2,
      unknownFieldTypes: [
        { type: "number", count: 1 },
        { type: "boolean", count: 1 },
      ],
    });
    assert.doesNotMatch(logs.join("\n"), new RegExp(secretFieldName));
    assert.doesNotMatch(logs.join("\n"), /another_unknown_field/);
  } finally {
    await harness.close();
  }
});

test("unknown request paths are logged canonically without path secrets", async (t) => {
  const runner = new FakeRunner();
  const secret = "unknown-path-secret-token";
  const logs: string[] = [];
  t.mock.method(console, "info", (...values: unknown[]) => logs.push(values.join(" ")));
  const harness = await listen(runner);
  try {
    const response = await fetch(`${harness.origin}/v1/${secret}`, {
      method: "POST",
      headers: authHeaders(),
      body: "{}",
    });
    assert.equal(response.status, 404);
    const body = await response.text();
    await waitFor(() => logs.length === 1);
    assert.doesNotMatch(body, new RegExp(secret));
    assert.doesNotMatch(logs.join("\n"), new RegExp(secret));
    assert.match(logs.join("\n"), /"route":"unknown"/);
  } finally {
    await harness.close();
  }
});

test("production lifecycle listens only after startup and closes Codex after HTTP draining", async () => {
  const runner = new FakeRunner();
  const startup = deferred<void>();
  runner.startImplementation = () => startup.promise;
  const starting = startBridge({ ...config, port: 0 }, runner);
  let listening = false;
  void starting.then(() => { listening = true; });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(listening, false);

  startup.resolve();
  const server = await starting;
  try {
    assert.equal(server.listening, true);
    assert.equal(runner.startCalls, 1);
  } finally {
    await stopBridge(server, runner, 50);
  }

  assert.equal(server.listening, false);
  assert.equal(runner.closeCalls, 1);
});

test("production lifecycle surfaces startup failure before listening", async () => {
  const runner = new FakeRunner();
  const failure = new CodexUpstreamError("authentication");
  runner.startImplementation = async () => { throw failure; };

  await assert.rejects(
    startBridge({ ...config, port: 0 }, runner),
    (error: unknown) => error === failure,
  );
  assert.equal(runner.startCalls, 1);
  assert.equal(runner.closeCalls, 0);
});

test("bounded shutdown disconnects in-flight HTTP before closing Codex", async () => {
  const runner = new FakeRunner();
  const aborted = deferred<void>();
  runner.runImplementation = (_invocation, signal) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {
      aborted.resolve();
      reject(new CodexUpstreamError("timeout"));
    }, { once: true });
  });
  const server = await startBridge({ ...config, port: 0, timeoutMs: 1_000 }, runner);
  const address = server.address();
  assert(address && typeof address === "object");
  const body = chatBody("in flight during shutdown");
  const request = http.request(`http://127.0.0.1:${address.port}/v1/chat/completions`, {
    method: "POST",
    headers: { ...authHeaders(), "content-length": Buffer.byteLength(body) },
  });
  request.on("error", () => {});
  request.end(body);
  await waitFor(() => runner.runs.length === 1);

  const startedAt = Date.now();
  await stopBridge(server, runner, 10);
  await aborted.promise;
  assert(Date.now() - startedAt < 500);
  assert.equal(runner.closeCalls, 1);
  assert.equal(runner.runs[0]!.signal.aborted, true);
});

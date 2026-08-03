import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  CodexAppServer,
  CodexUpstreamError,
  type CodexAppServerOptions,
  type CodexProcess,
  type CodexProcessFactory,
} from "../src/codex-app-server.js";
import type { BridgeConfig } from "../src/config.js";
import { JsonRpcClient } from "../src/json-rpc.js";
import type { CodexInvocation, CodexResult } from "../src/openai.js";

interface RpcMessage {
  jsonrpc: "2.0";
  id?: string | number;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code: number; message: string };
}

class FakeCodexProcess extends EventEmitter implements CodexProcess {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly signals: NodeJS.Signals[] = [];
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  exitOnTerminate = true;
  exitOnKill = true;
  errorOnTerminate = false;
  errorOnKill = false;

  kill(signal: NodeJS.Signals = "SIGTERM"): boolean {
    this.signals.push(signal);
    if (signal === "SIGTERM" && this.errorOnTerminate) {
      queueMicrotask(() => this.emit("error", new Error("signal delivery failed")));
    }
    if (signal === "SIGTERM" && this.exitOnTerminate) {
      queueMicrotask(() => this.exit(0, signal));
    }
    if (signal === "SIGKILL" && this.errorOnKill) {
      queueMicrotask(() => this.emit("error", new Error("forced signal delivery failed")));
    }
    if (signal === "SIGKILL" && this.exitOnKill) {
      queueMicrotask(() => this.exit(null, signal));
    }
    return true;
  }

  exit(code: number | null, signal: NodeJS.Signals | null = null): void {
    if (this.exitCode !== null || this.signalCode !== null) return;
    this.exitCode = code;
    this.signalCode = signal;
    this.emit("exit", code, signal);
    this.emit("close", code, signal);
  }
}

class FakeRpcPeer {
  private readonly messages: RpcMessage[] = [];
  private readonly waiters: Array<(message: RpcMessage) => void> = [];
  private buffer = "";

  constructor(readonly process: FakeCodexProcess) {
    process.stdin.on("data", (chunk: Buffer | string) => {
      this.buffer += String(chunk);
      let newline = this.buffer.indexOf("\n");
      while (newline >= 0) {
        const line = this.buffer.slice(0, newline);
        this.buffer = this.buffer.slice(newline + 1);
        if (line) this.push(JSON.parse(line) as RpcMessage);
        newline = this.buffer.indexOf("\n");
      }
    });
  }

  async next(): Promise<RpcMessage> {
    const queued = this.messages.shift();
    if (queued) return queued;
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  queuedMessageCount(): number {
    return this.messages.length;
  }

  respond(id: string | number, result: unknown): void {
    this.write({ jsonrpc: "2.0", id, result });
  }

  respondError(id: string | number, code: number, message: string): void {
    this.write({ jsonrpc: "2.0", id, error: { code, message } });
  }

  notify(method: string, params: unknown): void {
    this.write({ jsonrpc: "2.0", method, params });
  }

  request(id: string | number, method: string, params: unknown): void {
    this.write({ jsonrpc: "2.0", id, method, params });
  }

  private push(message: RpcMessage): void {
    const waiter = this.waiters.shift();
    if (waiter) waiter(message);
    else this.messages.push(message);
  }

  private write(message: RpcMessage): void {
    this.process.stdout.write(`${JSON.stringify(message)}\n`);
  }
}

const config: BridgeConfig = {
  host: "127.0.0.1",
  port: 8646,
  apiKey: "bridge-secret-must-not-reach-codex",
  publicModel: "supermemory-codex",
  codexModel: "gpt-5.6-sol",
  codexHome: "/var/lib/supermemory-codex",
  codexCwd: "/workspace",
  timeoutMs: 120_000,
  maxBodyBytes: 2_097_152,
  maxConcurrency: 2,
  maxQueueDepth: 8,
};

interface HarnessOptions extends CodexAppServerOptions {
  configureProcess?: (process: FakeCodexProcess, index: number) => void;
}

function createHarness(options: HarnessOptions = {}) {
  const processes: FakeCodexProcess[] = [];
  const calls: Array<{ command: string; args: readonly string[]; options: Record<string, unknown> }> = [];
  const { configureProcess, ...serverOptions } = options;
  const factory: CodexProcessFactory = (command, args, spawnOptions) => {
    calls.push({ command, args, options: spawnOptions });
    const process = new FakeCodexProcess();
    processes.push(process);
    configureProcess?.(process, processes.length - 1);
    return process;
  };
  const server = new CodexAppServer(config, { processFactory: factory, ...serverOptions });
  return { server, processes, calls };
}

async function initialize(peer: FakeRpcPeer): Promise<void> {
  const request = await peer.next();
  assert.deepEqual(request, {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      clientInfo: {
        name: "supermemory-codex-bridge",
        title: "Supermemory Codex Bridge",
        version: "0.1.0",
      },
      capabilities: { experimentalApi: true },
    },
  });
  peer.respond(1, { userAgent: "fake-codex" });
  assert.deepEqual(await peer.next(), { jsonrpc: "2.0", method: "initialized" });
}

async function acceptRun(
  peer: FakeRpcPeer,
  threadId: string,
  turnId: string,
  invocation: CodexInvocation,
): Promise<void> {
  const threadStart = await peer.next();
  assert.deepEqual(threadStart, {
    jsonrpc: "2.0",
    id: threadStart.id,
    method: "thread/start",
    params: {
      model: config.codexModel,
      cwd: config.codexCwd,
      approvalPolicy: "never",
      sandbox: "read-only",
      personality: "none",
      ephemeral: true,
      selectedCapabilityRoots: [],
      dynamicTools: [],
    },
  });
  assert.notEqual(threadStart.id, undefined);
  peer.respond(threadStart.id!, { thread: { id: threadId } });

  const turnStart = await peer.next();
  assert.deepEqual(turnStart, {
    jsonrpc: "2.0",
    id: turnStart.id,
    method: "turn/start",
    params: {
      threadId,
      input: [{ type: "text", text: invocation.prompt }],
      ...(invocation.outputSchema === undefined ? {} : { outputSchema: invocation.outputSchema }),
    },
  });
  assert.notEqual(turnStart.id, undefined);
  peer.respond(turnStart.id!, {
    turn: { id: turnId, status: "inProgress", items: [], error: null },
  });
}

function completeRun(
  peer: FakeRpcPeer,
  threadId: string,
  turnId: string,
  text: string,
  usage = { inputTokens: 17, outputTokens: 5 },
): void {
  peer.notify("item/completed", {
    threadId,
    turnId,
    item: { id: `${turnId}-message`, type: "agentMessage", text },
  });
  peer.notify("thread/tokenUsage/updated", {
    threadId,
    turnId,
    tokenUsage: {
      last: {
        inputTokens: usage.inputTokens,
        cachedInputTokens: 3,
        outputTokens: usage.outputTokens,
        reasoningOutputTokens: 2,
        totalTokens: usage.inputTokens + usage.outputTokens,
      },
      total: {
        inputTokens: usage.inputTokens,
        cachedInputTokens: 3,
        outputTokens: usage.outputTokens,
        reasoningOutputTokens: 2,
        totalTokens: usage.inputTokens + usage.outputTokens,
      },
    },
  });
  peer.notify("turn/completed", {
    threadId,
    turn: { id: turnId, status: "completed", items: [], error: null },
  });
}

async function expectKind(promise: Promise<unknown>, kind: CodexUpstreamError["kind"]): Promise<CodexUpstreamError> {
  try {
    await promise;
  } catch (error) {
    assert(error instanceof CodexUpstreamError);
    assert.equal(error.kind, kind);
    return error;
  }
  assert.fail(`expected ${kind} rejection`);
}

async function within<T>(promise: Promise<T>, timeoutMs = 100): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error("timed out waiting for terminal result")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

test("starts one dedicated app-server and uses a fresh ephemeral thread for every successful run", async () => {
  const { server, processes, calls } = createHarness();
  const invocation: CodexInvocation = {
    prompt: "Return a tiny summary.",
    outputSchema: { type: "object", properties: { answer: { type: "string" } } },
  };
  const first = server.run(invocation);
  assert.equal(processes.length, 1);
  const peer = new FakeRpcPeer(processes[0]!);

  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.command, "codex");
  assert.deepEqual(calls[0]!.args, ["app-server", "--listen", "stdio://"]);
  assert.deepEqual((calls[0]!.options.stdio as unknown[]).length, 3);
  const env = calls[0]!.options.env as NodeJS.ProcessEnv;
  assert.equal(env.CODEX_HOME, config.codexHome);
  assert.notEqual(env.BRIDGE_API_KEY, config.apiKey);
  assert.notEqual(env.OPENAI_API_KEY, config.apiKey);

  await initialize(peer);
  await acceptRun(peer, "thread-1", "turn-1", invocation);
  completeRun(peer, "thread-1", "turn-1", "first answer");
  assert.deepEqual(await first, {
    text: "first answer",
    usage: { inputTokens: 17, outputTokens: 5 },
  } satisfies CodexResult);

  const secondInvocation = { prompt: "Second request." };
  const second = server.run(secondInvocation);
  await acceptRun(peer, "thread-2", "turn-2", secondInvocation);
  completeRun(peer, "thread-2", "turn-2", "second answer", { inputTokens: 4, outputTokens: 2 });
  assert.deepEqual(await second, {
    text: "second answer",
    usage: { inputTokens: 4, outputTokens: 2 },
  });
  assert.equal(processes.length, 1);
  await server.close();
});

test("concurrent start and run share one app-server initialization", async () => {
  const { server, processes, calls } = createHarness();
  const controller = new AbortController();
  const starting = server.start();
  const invocation = { prompt: "Run while initialization is pending." };
  const result = server.run(invocation, controller.signal);

  assert.equal(processes.length, 1);
  assert.equal(calls.length, 1);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await starting;
  await acceptRun(peer, "thread-start", "turn-start", invocation);
  completeRun(peer, "thread-start", "turn-start", "shared initialization");

  assert.equal((await result).text, "shared initialization");
  assert.equal(processes.length, 1);
  await server.close();
});

test("start surfaces sanitized initialization failures", async () => {
  const { server, processes } = createHarness();
  const starting = server.start();
  const peer = new FakeRpcPeer(processes[0]!);
  const initializeRequest = await peer.next();
  peer.respondError(initializeRequest.id!, -32000, "initialization secret must stay private");

  const error = await expectKind(starting, "unavailable");
  assert.doesNotMatch(error.message, /secret|initialization/i);
  assert.equal(server.isReady(), false);
  await server.close();
});

for (const itemType of ["commandExecution", "fileChange", "mcpToolCall", "collabToolCall", "webSearch"]) {
  test(`interrupts and rejects a ${itemType} event`, async () => {
    const { server, processes } = createHarness();
    const invocation = { prompt: `Do not run ${itemType}.` };
    const result = server.run(invocation);
    const peer = new FakeRpcPeer(processes[0]!);
    await initialize(peer);
    await acceptRun(peer, "thread-policy", "turn-policy", invocation);

    peer.notify("item/started", {
      threadId: "thread-policy",
      turnId: "turn-policy",
      item: { id: "forbidden-1", type: itemType },
    });
    const interrupt = await peer.next();
    assert.deepEqual(interrupt, {
      jsonrpc: "2.0",
      id: interrupt.id,
      method: "turn/interrupt",
      params: { threadId: "thread-policy", turnId: "turn-policy" },
    });
    await expectKind(result, "forbidden_tool");

    peer.respond(interrupt.id!, {});
    completeRun(peer, "thread-policy", "turn-policy", "late success");
    await server.close();
  });
}

test("denies every server request and interrupts approval requests", async () => {
  const { server, processes } = createHarness();
  const invocation = { prompt: "No interactive actions." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-approval", "turn-approval", invocation);

  peer.request(91, "account/readInteractiveValue", { threadId: "thread-approval" });
  assert.deepEqual(await peer.next(), {
    jsonrpc: "2.0",
    id: 91,
    error: { code: -32601, message: "Interactive requests are disabled" },
  });

  peer.request("approval-1", "item/commandExecution/requestApproval", {
    threadId: "thread-approval",
    turnId: "turn-approval",
  });
  assert.deepEqual(await peer.next(), {
    jsonrpc: "2.0",
    id: "approval-1",
    error: { code: -32601, message: "Interactive requests are disabled" },
  });
  const interrupt = await peer.next();
  assert.equal(interrupt.method, "turn/interrupt");
  assert.deepEqual(interrupt.params, { threadId: "thread-approval", turnId: "turn-approval" });
  await expectKind(result, "forbidden_tool");
  peer.respond(interrupt.id!, {});
  await server.close();
});

test("caller deadline abort interrupts exactly once and maps to timeout", async () => {
  const { server, processes } = createHarness();
  const controller = new AbortController();
  const invocation = { prompt: "Wait for the caller." };
  const result = server.run(invocation, controller.signal);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-timeout", "turn-timeout", invocation);

  controller.abort(new DOMException("request deadline", "TimeoutError"));
  controller.abort(new DOMException("request deadline", "TimeoutError"));
  const interrupt = await peer.next();
  assert.equal(interrupt.method, "turn/interrupt");
  assert.deepEqual(interrupt.params, { threadId: "thread-timeout", turnId: "turn-timeout" });
  await expectKind(result, "timeout");
  peer.respond(interrupt.id!, {});

  completeRun(peer, "thread-timeout", "turn-timeout", "late answer");
  await new Promise((resolve) => setImmediate(resolve));
  await server.close();
});

test("abort before turn binding reaps Codex and the next run lazily recovers", async () => {
  const { server, processes } = createHarness();
  const controller = new AbortController();
  const first = server.run({ prompt: "Abort before binding." }, controller.signal);
  const firstPeer = new FakeRpcPeer(processes[0]!);
  await initialize(firstPeer);

  const threadStart = await firstPeer.next();
  assert.equal(threadStart.method, "thread/start");
  firstPeer.respond(threadStart.id!, { thread: { id: "thread-unbound" } });
  const turnStart = await firstPeer.next();
  assert.equal(turnStart.method, "turn/start");

  controller.abort(new DOMException("request deadline", "TimeoutError"));
  await expectKind(first, "timeout");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(processes[0]!.signals, ["SIGTERM"]);
  assert.equal(server.isReady(), false);

  const secondInvocation = { prompt: "Recover after abort." };
  const second = server.run(secondInvocation);
  assert.equal(processes.length, 2);
  const secondPeer = new FakeRpcPeer(processes[1]!);
  await initialize(secondPeer);
  await acceptRun(secondPeer, "thread-recovered", "turn-recovered", secondInvocation);
  completeRun(secondPeer, "thread-recovered", "turn-recovered", "recovered");
  assert.equal((await second).text, "recovered");
  await server.close();
});

test("self-closed JSON-RPC transport invalidates readiness, reaps Codex, and recovers", async () => {
  const { server, processes } = createHarness();
  const firstInvocation = { prompt: "Lose transport." };
  const first = server.run(firstInvocation);
  const firstPeer = new FakeRpcPeer(processes[0]!);
  await initialize(firstPeer);
  await acceptRun(firstPeer, "thread-transport", "turn-transport", firstInvocation);
  assert.equal(server.isReady(), true);

  processes[0]!.stdout.end();
  await expectKind(first, "unavailable");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(server.isReady(), false);
  assert.deepEqual(processes[0]!.signals, ["SIGTERM"]);

  const secondInvocation = { prompt: "Recover transport." };
  const second = server.run(secondInvocation);
  assert.equal(processes.length, 2);
  const secondPeer = new FakeRpcPeer(processes[1]!);
  await initialize(secondPeer);
  await acceptRun(secondPeer, "thread-transport-2", "turn-transport-2", secondInvocation);
  completeRun(secondPeer, "thread-transport-2", "turn-transport-2", "recovered transport");
  assert.equal((await second).text, "recovered transport");
  await server.close();
});

test("process death fails active work and one later run lazily starts one replacement", async () => {
  const { server, processes } = createHarness();
  assert.equal(server.isReady(), false);
  const firstInvocation = { prompt: "First process." };
  const first = server.run(firstInvocation);
  const firstPeer = new FakeRpcPeer(processes[0]!);
  await initialize(firstPeer);
  assert.equal(server.isReady(), true);
  await acceptRun(firstPeer, "thread-dead", "turn-dead", firstInvocation);

  processes[0]!.exit(70);
  await expectKind(first, "unavailable");
  assert.equal(server.isReady(), false);

  const secondInvocation = { prompt: "Replacement process." };
  const second = server.run(secondInvocation);
  assert.equal(processes.length, 2);
  assert.equal(server.isReady(), false);
  const secondPeer = new FakeRpcPeer(processes[1]!);
  await initialize(secondPeer);
  assert.equal(server.isReady(), true);
  await acceptRun(secondPeer, "thread-replacement", "turn-replacement", secondInvocation);
  completeRun(secondPeer, "thread-replacement", "turn-replacement", "recovered");
  assert.equal((await second).text, "recovered");
  assert.equal(processes.length, 2);
  await server.close();
  assert.equal(server.isReady(), false);
});

for (const failure of [
  { message: "Credentials missing; token sk-super-secret was rejected", kind: "authentication" },
  { message: "HTTP 429 rate limit exceeded for tenant secret-tenant", kind: "rate_limit" },
  { message: "output schema validation failed near secret-payload", kind: "structured_output" },
  { message: "provider disconnected with secret-upstream-detail", kind: "upstream" },
] as const) {
  test(`maps a failed turn to ${failure.kind} without exposing upstream details`, async () => {
    const { server, processes } = createHarness();
    const invocation = { prompt: "Map failure." };
    const result = server.run(invocation);
    const peer = new FakeRpcPeer(processes[0]!);
    await initialize(peer);
    await acceptRun(peer, "thread-error", "turn-error", invocation);

    peer.notify("turn/completed", {
      threadId: "thread-error",
      turn: {
        id: "turn-error",
        status: "failed",
        items: [],
        error: { message: failure.message },
      },
    });

    const error = await expectKind(result, failure.kind);
    assert.doesNotMatch(error.message, /secret|credential token|tenant/i);
    await server.close();
  });
}

test("routes interleaved concurrent notifications by thread and turn", async () => {
  const { server, processes } = createHarness();
  const firstInvocation = { prompt: "Concurrent A" };
  const secondInvocation = { prompt: "Concurrent B" };
  const first = server.run(firstInvocation);
  const second = server.run(secondInvocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);

  const threadRequests = [await peer.next(), await peer.next()];
  assert.deepEqual(threadRequests.map((request) => request.method), ["thread/start", "thread/start"]);
  peer.respond(threadRequests[0]!.id!, { thread: { id: "thread-a" } });
  peer.respond(threadRequests[1]!.id!, { thread: { id: "thread-b" } });

  const turnRequests = [await peer.next(), await peer.next()];
  const turnByThread = new Map(turnRequests.map((request) => [
    (request.params as { threadId: string }).threadId,
    request,
  ]));
  peer.respond(turnByThread.get("thread-a")!.id!, {
    turn: { id: "turn-a", status: "inProgress", items: [], error: null },
  });
  peer.respond(turnByThread.get("thread-b")!.id!, {
    turn: { id: "turn-b", status: "inProgress", items: [], error: null },
  });

  peer.notify("item/completed", {
    threadId: "thread-b",
    turnId: "turn-b",
    item: { id: "message-b", type: "agentMessage", text: "answer B" },
  });
  peer.notify("thread/tokenUsage/updated", {
    threadId: "thread-b",
    turnId: "turn-b",
    tokenUsage: {
      last: { inputTokens: 22, cachedInputTokens: 0, outputTokens: 2, reasoningOutputTokens: 0, totalTokens: 24 },
      total: { inputTokens: 22, cachedInputTokens: 0, outputTokens: 2, reasoningOutputTokens: 0, totalTokens: 24 },
    },
  });
  peer.notify("turn/completed", {
    threadId: "thread-b",
    turn: { id: "turn-b", status: "completed", items: [], error: null },
  });
  peer.notify("item/completed", {
    threadId: "thread-a",
    turnId: "turn-a",
    item: { id: "message-a", type: "agentMessage", text: "answer A" },
  });
  peer.notify("thread/tokenUsage/updated", {
    threadId: "thread-a",
    turnId: "turn-a",
    tokenUsage: {
      last: { inputTokens: 11, cachedInputTokens: 0, outputTokens: 1, reasoningOutputTokens: 0, totalTokens: 12 },
      total: { inputTokens: 11, cachedInputTokens: 0, outputTokens: 1, reasoningOutputTokens: 0, totalTokens: 12 },
    },
  });
  peer.notify("turn/completed", {
    threadId: "thread-a",
    turn: { id: "turn-a", status: "completed", items: [], error: null },
  });

  assert.deepEqual(await Promise.all([first, second]), [
    { text: "answer A", usage: { inputTokens: 11, outputTokens: 1 } },
    { text: "answer B", usage: { inputTokens: 22, outputTokens: 2 } },
  ]);
  assert.equal(processes.length, 1);
  await server.close();
});

test("fails and interrupts both turns when a notification crosses known thread and turn ids", async () => {
  const { server, processes } = createHarness();
  const first = server.run({ prompt: "Conflicting notification A." });
  const second = server.run({ prompt: "Conflicting notification B." });
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);

  const threadStarts = [await peer.next(), await peer.next()];
  peer.respond(threadStarts[0]!.id!, { thread: { id: "thread-conflict-a" } });
  peer.respond(threadStarts[1]!.id!, { thread: { id: "thread-conflict-b" } });
  const turnStarts = [await peer.next(), await peer.next()];
  const byThread = new Map(turnStarts.map((request) => [(request.params as { threadId: string }).threadId, request]));
  peer.respond(byThread.get("thread-conflict-a")!.id!, {
    turn: { id: "turn-conflict-a", status: "inProgress", items: [], error: null },
  });
  peer.respond(byThread.get("thread-conflict-b")!.id!, {
    turn: { id: "turn-conflict-b", status: "inProgress", items: [], error: null },
  });
  await Promise.resolve();

  peer.notify("turn/completed", {
    threadId: "thread-conflict-a",
    turn: { id: "turn-conflict-b", status: "completed", items: [], error: null },
  });

  await Promise.all([
    within(expectKind(first, "upstream")),
    within(expectKind(second, "upstream")),
  ]);
  const interrupts = [await within(peer.next()), await within(peer.next())];
  assert.deepEqual(
    interrupts.map((message) => message.params).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    [
      { threadId: "thread-conflict-a", turnId: "turn-conflict-a" },
      { threadId: "thread-conflict-b", turnId: "turn-conflict-b" },
    ],
  );
  for (const interrupt of interrupts) peer.respond(interrupt.id!, {});
  await server.close();
});

test("close interrupts active turns, terminates gracefully, and prevents later runs", async () => {
  const { server, processes } = createHarness();
  const invocation = { prompt: "Close while active." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-close", "turn-close", invocation);

  const closing = server.close();
  const interrupt = await peer.next();
  assert.equal(interrupt.method, "turn/interrupt");
  assert.deepEqual(interrupt.params, { threadId: "thread-close", turnId: "turn-close" });
  peer.respond(interrupt.id!, {});
  await closing;

  assert.deepEqual(processes[0]!.signals, ["SIGTERM"]);
  await expectKind(result, "unavailable");
  await expectKind(server.run({ prompt: "after close" }), "unavailable");
});

test("close escalates to SIGKILL only when the process ignores SIGTERM", async () => {
  const { server, processes } = createHarness({ shutdownTimeoutMs: 5 });
  const invocation = { prompt: "Start then close." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-kill", "turn-kill", invocation);
  processes[0]!.exitOnTerminate = false;
  const rejected = expectKind(result, "unavailable");

  const closing = server.close();
  const interrupt = await peer.next();
  peer.respond(interrupt.id!, {});
  await closing;

  assert.deepEqual(processes[0]!.signals, ["SIGTERM", "SIGKILL"]);
  await rejected;
});

test("maps a synchronous process factory failure to unavailable", async () => {
  const server = new CodexAppServer(config, {
    processFactory: () => {
      throw new Error("spawn failed with secret-path");
    },
  });

  const error = await expectKind(server.run({ prompt: "Cannot start." }), "unavailable");
  assert.doesNotMatch(error.message, /secret|spawn failed/i);
  await server.close();
});

test("close during initialization sends SIGTERM only once", async () => {
  const { server, processes } = createHarness();
  const result = server.run({ prompt: "Close before initialized." });
  const peer = new FakeRpcPeer(processes[0]!);
  const initializeRequest = await peer.next();
  assert.equal(initializeRequest.method, "initialize");
  const rejected = expectKind(result, "unavailable");

  await server.close();

  assert.deepEqual(processes[0]!.signals, ["SIGTERM"]);
  await rejected;
});

test("reaps a child after initialize failure before allowing one replacement", async () => {
  const { server, processes } = createHarness({
    shutdownTimeoutMs: 5,
    configureProcess: (process, index) => {
      if (index === 0) process.exitOnTerminate = false;
    },
  });
  const first = server.run({ prompt: "Initialization will fail." });
  const firstPeer = new FakeRpcPeer(processes[0]!);
  const initializeRequest = await firstPeer.next();
  firstPeer.respondError(initializeRequest.id!, -32000, "initialization failed");

  await within(expectKind(first, "unavailable"));
  assert.deepEqual(processes[0]!.signals, ["SIGTERM", "SIGKILL"]);
  assert.notEqual(processes[0]!.signalCode, null);

  const secondInvocation = { prompt: "Replacement after failed initialization." };
  const second = server.run(secondInvocation);
  assert.equal(processes.length, 2);
  const secondPeer = new FakeRpcPeer(processes[1]!);
  await initialize(secondPeer);
  await acceptRun(secondPeer, "thread-init-replacement", "turn-init-replacement", secondInvocation);
  completeRun(secondPeer, "thread-init-replacement", "turn-init-replacement", "recovered");
  assert.equal((await second).text, "recovered");
  await server.close();
});

test("does not release a child when an error fires during reap without an exit", async () => {
  const { server, processes } = createHarness({
    shutdownTimeoutMs: 5,
    configureProcess: (process, index) => {
      if (index === 0) {
        process.exitOnTerminate = false;
        process.exitOnKill = false;
        process.errorOnTerminate = true;
        process.errorOnKill = true;
      }
    },
  });
  const first = server.run({ prompt: "Unreaped initialization failure." });
  const firstPeer = new FakeRpcPeer(processes[0]!);
  const initializeRequest = await firstPeer.next();
  firstPeer.respondError(initializeRequest.id!, -32000, "initialization failed");

  await within(expectKind(first, "unavailable"));
  assert.deepEqual(processes[0]!.signals, ["SIGTERM", "SIGKILL"]);
  await within(expectKind(server.run({ prompt: "Must not replace yet." }), "unavailable"));
  assert.equal(processes.length, 1);

  processes[0]!.exit(null, "SIGKILL");
  const replacementInvocation = { prompt: "Replace only after exit." };
  const replacement = server.run(replacementInvocation);
  assert.equal(processes.length, 2);
  const replacementPeer = new FakeRpcPeer(processes[1]!);
  await initialize(replacementPeer);
  await acceptRun(replacementPeer, "thread-after-reap", "turn-after-reap", replacementInvocation);
  completeRun(replacementPeer, "thread-after-reap", "turn-after-reap", "replaced safely");
  assert.equal((await replacement).text, "replaced safely");
  await server.close();
});

test("reaps a child when rpc construction throws before allowing one replacement", async () => {
  let rpcFactoryCalls = 0;
  const { server, processes } = createHarness({
    shutdownTimeoutMs: 5,
    configureProcess: (process, index) => {
      if (index === 0) process.exitOnTerminate = false;
    },
    rpcFactory: (stdout, stdin) => {
      rpcFactoryCalls += 1;
      if (rpcFactoryCalls === 1) throw new Error("rpc construction failed");
      return new JsonRpcClient(stdout, stdin);
    },
  });

  await within(expectKind(server.run({ prompt: "RPC construction fails." }), "unavailable"));
  assert.deepEqual(processes[0]!.signals, ["SIGTERM", "SIGKILL"]);
  assert.notEqual(processes[0]!.signalCode, null);

  const replacementInvocation = { prompt: "RPC replacement." };
  const replacement = server.run(replacementInvocation);
  assert.equal(processes.length, 2);
  const replacementPeer = new FakeRpcPeer(processes[1]!);
  await initialize(replacementPeer);
  await acceptRun(replacementPeer, "thread-rpc-replacement", "turn-rpc-replacement", replacementInvocation);
  completeRun(replacementPeer, "thread-rpc-replacement", "turn-rpc-replacement", "rpc recovered");
  assert.equal((await replacement).text, "rpc recovered");
  await server.close();
});

test("fails closed when two active runs receive the same thread id", async () => {
  const { server, processes } = createHarness();
  const first = server.run({ prompt: "First duplicate thread." });
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  const firstThreadStart = await peer.next();
  peer.respond(firstThreadStart.id!, { thread: { id: "thread-duplicate" } });
  const firstTurnStart = await peer.next();
  peer.respond(firstTurnStart.id!, {
    turn: { id: "turn-first", status: "inProgress", items: [], error: null },
  });

  const second = server.run({ prompt: "Second duplicate thread." });
  const firstRejected = within(expectKind(first, "upstream"));
  const secondRejected = within(expectKind(second, "upstream"));
  const secondThreadStart = await peer.next();
  peer.respond(secondThreadStart.id!, { thread: { id: "thread-duplicate" } });

  const interrupt = await peer.next();
  assert.equal(interrupt.method, "turn/interrupt");
  assert.deepEqual(interrupt.params, { threadId: "thread-duplicate", turnId: "turn-first" });
  await Promise.all([firstRejected, secondRejected]);
  peer.respond(interrupt.id!, {});
  await server.close();
});

test("fails and interrupts both runs when turn ids collide", async () => {
  const { server, processes } = createHarness();
  const first = server.run({ prompt: "First duplicate turn." });
  const second = server.run({ prompt: "Second duplicate turn." });
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  const threadStarts = [await peer.next(), await peer.next()];
  peer.respond(threadStarts[0]!.id!, { thread: { id: "thread-turn-a" } });
  peer.respond(threadStarts[1]!.id!, { thread: { id: "thread-turn-b" } });
  const turnStarts = [await peer.next(), await peer.next()];
  const byThread = new Map(turnStarts.map((request) => [(request.params as { threadId: string }).threadId, request]));
  peer.respond(byThread.get("thread-turn-a")!.id!, {
    turn: { id: "turn-duplicate", status: "inProgress", items: [], error: null },
  });
  peer.respond(byThread.get("thread-turn-b")!.id!, {
    turn: { id: "turn-duplicate", status: "inProgress", items: [], error: null },
  });

  await Promise.all([
    within(expectKind(first, "upstream")),
    within(expectKind(second, "upstream")),
  ]);
  const interrupts = [await peer.next(), await peer.next()];
  assert.deepEqual(
    interrupts.map((message) => message.params).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    [
      { threadId: "thread-turn-a", turnId: "turn-duplicate" },
      { threadId: "thread-turn-b", turnId: "turn-duplicate" },
    ],
  );
  for (const interrupt of interrupts) peer.respond(interrupt.id!, {});
  await server.close();
});

test("fails closed and interrupts both candidate ids when early and returned turn ids conflict", async () => {
  const { server, processes } = createHarness();
  const result = server.run({ prompt: "Conflicting early turn id." });
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  const threadStart = await peer.next();
  peer.respond(threadStart.id!, { thread: { id: "thread-early-conflict" } });
  const turnStart = await peer.next();
  peer.notify("turn/started", {
    threadId: "thread-early-conflict",
    turn: { id: "turn-early", status: "inProgress", items: [], error: null },
  });
  peer.respond(turnStart.id!, {
    turn: { id: "turn-returned", status: "inProgress", items: [], error: null },
  });

  await within(expectKind(result, "upstream"));
  const interrupts = [await peer.next(), await peer.next()];
  assert.deepEqual(
    interrupts.map((message) => (message.params as { turnId: string }).turnId).sort(),
    ["turn-early", "turn-returned"],
  );
  for (const interrupt of interrupts) peer.respond(interrupt.id!, {});
  await server.close();
});

for (const approval of [
  { name: "unroutable", params: {} },
  { name: "conflicting", params: { threadId: "thread-approval-a", turnId: "turn-approval-b" } },
] as const) {
  test(`fails and interrupts every active run for an ${approval.name} approval request`, async () => {
    const { server, processes } = createHarness();
    const firstInvocation = { prompt: "Approval A." };
    const secondInvocation = { prompt: "Approval B." };
    const first = server.run(firstInvocation);
    const second = server.run(secondInvocation);
    const peer = new FakeRpcPeer(processes[0]!);
    await initialize(peer);
    const threadStarts = [await peer.next(), await peer.next()];
    peer.respond(threadStarts[0]!.id!, { thread: { id: "thread-approval-a" } });
    peer.respond(threadStarts[1]!.id!, { thread: { id: "thread-approval-b" } });
    const turnStarts = [await peer.next(), await peer.next()];
    const byThread = new Map(turnStarts.map((request) => [(request.params as { threadId: string }).threadId, request]));
    peer.respond(byThread.get("thread-approval-a")!.id!, {
      turn: { id: "turn-approval-a", status: "inProgress", items: [], error: null },
    });
    peer.respond(byThread.get("thread-approval-b")!.id!, {
      turn: { id: "turn-approval-b", status: "inProgress", items: [], error: null },
    });
    if (approval.name === "conflicting") await Promise.resolve();

    peer.request("approval-unroutable", "item/commandExecution/requestApproval", approval.params);
    assert.deepEqual(await peer.next(), {
      jsonrpc: "2.0",
      id: "approval-unroutable",
      error: { code: -32601, message: "Interactive requests are disabled" },
    });
    await Promise.all([
      within(expectKind(first, "forbidden_tool")),
      within(expectKind(second, "forbidden_tool")),
    ]);
    const expectedInterrupts = [
      { threadId: "thread-approval-a", turnId: "turn-approval-a" },
      { threadId: "thread-approval-b", turnId: "turn-approval-b" },
    ];
    const interrupts: RpcMessage[] = [];
    for (let index = 0; index < expectedInterrupts.length; index += 1) {
      interrupts.push(await within(peer.next()));
    }
    assert.equal(interrupts.every((message) => message.method === "turn/interrupt"), true);
    assert.deepEqual(
      interrupts.map((message) => message.params).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
      expectedInterrupts,
    );
    assert.equal(peer.queuedMessageCount(), 0);
    for (const interrupt of interrupts) peer.respond(interrupt.id!, {});
    await server.close();
  });
}

test("does not send a crossed candidate interrupt when multiple unbound turns make approval routing ambiguous", async () => {
  const { server, processes } = createHarness();
  const first = server.run({ prompt: "Ambiguous candidate A." });
  const second = server.run({ prompt: "Ambiguous candidate B." });
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  const threadStarts = [await peer.next(), await peer.next()];
  peer.respond(threadStarts[0]!.id!, { thread: { id: "thread-ambiguous-a" } });
  peer.respond(threadStarts[1]!.id!, { thread: { id: "thread-ambiguous-b" } });
  const turnStarts = [await peer.next(), await peer.next()];
  const byThread = new Map(turnStarts.map((request) => [(request.params as { threadId: string }).threadId, request]));

  peer.request("approval-ambiguous", "item/commandExecution/requestApproval", {
    threadId: "thread-ambiguous-a",
    turnId: "turn-ambiguous-b",
  });
  assert.deepEqual(await peer.next(), {
    jsonrpc: "2.0",
    id: "approval-ambiguous",
    error: { code: -32601, message: "Interactive requests are disabled" },
  });
  await Promise.all([
    within(expectKind(first, "forbidden_tool")),
    within(expectKind(second, "forbidden_tool")),
  ]);
  assert.equal(peer.queuedMessageCount(), 0);

  peer.respond(byThread.get("thread-ambiguous-a")!.id!, {
    turn: { id: "turn-ambiguous-a", status: "inProgress", items: [], error: null },
  });
  peer.respond(byThread.get("thread-ambiguous-b")!.id!, {
    turn: { id: "turn-ambiguous-b", status: "inProgress", items: [], error: null },
  });
  const interrupts = [await within(peer.next()), await within(peer.next())];
  assert.equal(interrupts.every((message) => message.method === "turn/interrupt"), true);
  assert.deepEqual(
    interrupts.map((message) => message.params).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
    [
      { threadId: "thread-ambiguous-a", turnId: "turn-ambiguous-a" },
      { threadId: "thread-ambiguous-b", turnId: "turn-ambiguous-b" },
    ],
  );
  assert.equal(peer.queuedMessageCount(), 0);
  for (const interrupt of interrupts) peer.respond(interrupt.id!, {});
  await server.close();
});

test("interrupts the candidate turn id from an approval received before turn binding", async () => {
  const { server, processes } = createHarness();
  const result = server.run({ prompt: "Approval before turn binding." });
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  const threadStart = await peer.next();
  peer.respond(threadStart.id!, { thread: { id: "thread-approval-candidate" } });
  await peer.next();

  peer.request("approval-candidate", "item/commandExecution/requestApproval", {
    threadId: "thread-approval-candidate",
    turnId: "turn-approval-candidate",
  });
  assert.deepEqual(await peer.next(), {
    jsonrpc: "2.0",
    id: "approval-candidate",
    error: { code: -32601, message: "Interactive requests are disabled" },
  });
  await within(expectKind(result, "forbidden_tool"));
  const interrupt = await within(peer.next());
  assert.equal(interrupt.method, "turn/interrupt");
  assert.deepEqual(interrupt.params, {
    threadId: "thread-approval-candidate",
    turnId: "turn-approval-candidate",
  });
  peer.respond(interrupt.id!, {});
  await server.close();
});

test("rejects a successful completion snapshot containing a forbidden item", async () => {
  const { server, processes } = createHarness();
  const invocation = { prompt: "Snapshot policy." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-snapshot", "turn-snapshot", invocation);

  peer.notify("turn/completed", {
    threadId: "thread-snapshot",
    turn: {
      id: "turn-snapshot",
      status: "completed",
      error: null,
      items: [
        { id: "forbidden-snapshot", type: "commandExecution", command: "whoami" },
        { id: "message-snapshot", type: "agentMessage", text: "must not resolve" },
      ],
    },
  });

  await within(expectKind(result, "forbidden_tool"));
  const interrupt = await peer.next();
  assert.equal(interrupt.method, "turn/interrupt");
  assert.deepEqual(interrupt.params, { threadId: "thread-snapshot", turnId: "turn-snapshot" });
  peer.respond(interrupt.id!, {});
  await server.close();
});

test("rejects a completed turn without an agent message", async () => {
  const { server, processes } = createHarness();
  const invocation = { prompt: "Require an answer." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-no-answer", "turn-no-answer", invocation);

  peer.notify("turn/completed", {
    threadId: "thread-no-answer",
    turn: { id: "turn-no-answer", status: "completed", items: [], error: null },
  });

  await expectKind(result, "upstream");
  await server.close();
});

test("accepts an explicit empty agent message", async () => {
  const { server, processes } = createHarness();
  const invocation = { prompt: "An empty answer is valid." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-empty-answer", "turn-empty-answer", invocation);

  peer.notify("turn/completed", {
    threadId: "thread-empty-answer",
    turn: {
      id: "turn-empty-answer",
      status: "completed",
      items: [{ id: "empty-message", type: "agentMessage", text: "" }],
      error: null,
    },
  });

  assert.deepEqual(await result, { text: "" });
  await server.close();
});

test("passes only allowlisted runtime environment variables to Codex", async () => {
  const ambient: NodeJS.ProcessEnv = {
    PATH: "/safe/bin",
    HOME: "/safe/home",
    TMPDIR: "/safe/tmp",
    LANG: "C.UTF-8",
    SSL_CERT_FILE: "/safe/certs.pem",
    AWS_ACCESS_KEY_ID: "aws-key",
    AWS_SECRET_ACCESS_KEY: "aws-secret",
    ANTHROPIC_API_KEY: "anthropic-secret",
    AZURE_OPENAI_API_KEY: "azure-secret",
    GOOGLE_API_KEY: "google-secret",
    OPENAI_API_KEY: "openai-secret",
    GITHUB_TOKEN: "github-secret",
    DATABASE_URL: "postgres://secret",
    NODE_OPTIONS: "--require=/secret/inject.js",
    SOME_PROVIDER_CREDENTIAL: "provider-secret",
  };
  const { server, processes, calls } = createHarness({ environment: ambient });
  const result = server.run({ prompt: "Inspect environment." });
  const peer = new FakeRpcPeer(processes[0]!);
  const env = calls[0]!.options.env as NodeJS.ProcessEnv;

  assert.deepEqual(env, {
    PATH: "/safe/bin",
    HOME: "/safe/home",
    TMPDIR: "/safe/tmp",
    LANG: "C.UTF-8",
    SSL_CERT_FILE: "/safe/certs.pem",
    CODEX_HOME: config.codexHome,
  });

  const rejected = expectKind(result, "unavailable");
  await server.close();
  await rejected;
  assert.equal(peer.process.signals.includes("SIGTERM"), true);
});

test("stderr logging records categories and exit status without raw sensitive text", async () => {
  const events: Array<Record<string, unknown>> = [];
  const { server, processes } = createHarness({ log: (event) => events.push(event) });
  const invocation = { prompt: "Observe sanitized logs." };
  const result = server.run(invocation);
  const peer = new FakeRpcPeer(processes[0]!);
  await initialize(peer);
  await acceptRun(peer, "thread-log", "turn-log", invocation);

  processes[0]!.stderr.write("ERROR auth failed for sk-sensitive-value\n");
  processes[0]!.exit(78);
  await expectKind(result, "unavailable");
  assert(events.length >= 2);
  assert.equal(events.some((event) => event.category === "stderr"), true);
  assert.equal(events.some((event) => event.category === "exit" && event.exitStatus === 78), true);
  assert.doesNotMatch(JSON.stringify(events), /sensitive|auth failed/i);
  await server.close();
});

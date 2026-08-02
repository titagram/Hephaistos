import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  CodexAppServer,
  CodexUpstreamError,
  type CodexProcess,
  type CodexProcessFactory,
} from "../src/codex-app-server.js";
import type { BridgeConfig } from "../src/config.js";
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

  kill(signal: NodeJS.Signals = "SIGTERM"): boolean {
    this.signals.push(signal);
    if (signal === "SIGTERM" && this.exitOnTerminate) {
      queueMicrotask(() => this.exit(0, signal));
    }
    if (signal === "SIGKILL") {
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
  codexModel: "gpt-5.3-codex",
  codexHome: "/var/lib/supermemory-codex",
  codexCwd: "/workspace",
  timeoutMs: 120_000,
  maxBodyBytes: 2_097_152,
  maxConcurrency: 2,
};

function createHarness(options: { shutdownTimeoutMs?: number; log?: (event: Record<string, unknown>) => void } = {}) {
  const processes: FakeCodexProcess[] = [];
  const calls: Array<{ command: string; args: readonly string[]; options: Record<string, unknown> }> = [];
  const factory: CodexProcessFactory = (command, args, spawnOptions) => {
    calls.push({ command, args, options: spawnOptions });
    const process = new FakeCodexProcess();
    processes.push(process);
    return process;
  };
  const server = new CodexAppServer(config, { processFactory: factory, ...options });
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
      capabilities: { experimentalApi: false },
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
      sandbox: "readOnly",
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

test("process death fails active work and one later run lazily starts one replacement", async () => {
  const { server, processes } = createHarness();
  const firstInvocation = { prompt: "First process." };
  const first = server.run(firstInvocation);
  const firstPeer = new FakeRpcPeer(processes[0]!);
  await initialize(firstPeer);
  await acceptRun(firstPeer, "thread-dead", "turn-dead", firstInvocation);

  processes[0]!.exit(70);
  await expectKind(first, "unavailable");

  const secondInvocation = { prompt: "Replacement process." };
  const second = server.run(secondInvocation);
  assert.equal(processes.length, 2);
  const secondPeer = new FakeRpcPeer(processes[1]!);
  await initialize(secondPeer);
  await acceptRun(secondPeer, "thread-replacement", "turn-replacement", secondInvocation);
  completeRun(secondPeer, "thread-replacement", "turn-replacement", "recovered");
  assert.equal((await second).text, "recovered");
  assert.equal(processes.length, 2);
  await server.close();
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
  peer.notify("item/completed", {
    threadId: "thread-b",
    turnId: "turn-a",
    item: { id: "crossed-message", type: "agentMessage", text: "must be ignored" },
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

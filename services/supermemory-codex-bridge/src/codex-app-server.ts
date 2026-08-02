import { spawn } from "node:child_process";
import type { Readable, Writable } from "node:stream";

import type { BridgeConfig } from "./config.js";
import { JsonRpcClient } from "./json-rpc.js";
import type { CodexInvocation, CodexResult } from "./openai.js";

export type CodexUpstreamErrorKind =
  | "authentication"
  | "rate_limit"
  | "timeout"
  | "structured_output"
  | "forbidden_tool"
  | "unavailable"
  | "upstream";

export class CodexUpstreamError extends Error {
  constructor(public readonly kind: CodexUpstreamErrorKind) {
    super(errorMessage(kind));
    this.name = "CodexUpstreamError";
  }
}

export interface CodexRunner {
  run(invocation: CodexInvocation, signal?: AbortSignal): Promise<CodexResult>;
}

export interface CodexProcess {
  readonly stdin: Writable;
  readonly stdout: Readable;
  readonly stderr: Readable;
  readonly exitCode: number | null;
  readonly signalCode: NodeJS.Signals | null;
  kill(signal?: NodeJS.Signals): boolean;
  once(event: "exit", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this;
  once(event: "error", listener: (error: Error) => void): this;
  off(event: "exit", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this;
}

export interface CodexProcessSpawnOptions {
  stdio: readonly ["pipe", "pipe", "pipe"];
  env: NodeJS.ProcessEnv;
}

export type CodexProcessFactory = (
  command: string,
  args: readonly string[],
  options: CodexProcessSpawnOptions,
) => CodexProcess;

export interface CodexDiagnosticEvent {
  category: "stderr" | "exit";
  exitStatus?: number | string;
}

export interface CodexAppServerOptions {
  processFactory?: CodexProcessFactory;
  rpcFactory?: (stdout: Readable, stdin: Writable) => JsonRpcClient;
  log?: (event: CodexDiagnosticEvent) => void;
  shutdownTimeoutMs?: number;
}

interface ThreadStartResponse {
  thread?: { id?: unknown };
}

interface TurnStartResponse {
  turn?: { id?: unknown };
}

interface TurnState {
  readonly threadId: string;
  turnId?: string;
  finalText?: string;
  usage?: CodexResult["usage"];
  finished: boolean;
  interruptSent: boolean;
  resolve: (result: CodexResult) => void;
  reject: (error: CodexUpstreamError) => void;
  readonly result: Promise<CodexResult>;
  signal?: AbortSignal;
  abortListener?: () => void;
}

const CLIENT_INFO = {
  name: "supermemory-codex-bridge",
  title: "Supermemory Codex Bridge",
  version: "0.1.0",
};
const FORBIDDEN_ITEM_TYPES = new Set([
  "commandExecution",
  "fileChange",
  "mcpToolCall",
  "collabToolCall",
  "webSearch",
]);
const DEFAULT_SHUTDOWN_TIMEOUT_MS = 5_000;

const defaultProcessFactory: CodexProcessFactory = (command, args, options) =>
  spawn(command, [...args], {
    stdio: ["pipe", "pipe", "pipe"],
    env: options.env,
  });

export class CodexAppServer implements CodexRunner {
  private readonly processFactory: CodexProcessFactory;
  private readonly rpcFactory: (stdout: Readable, stdin: Writable) => JsonRpcClient;
  private readonly log?: (event: CodexDiagnosticEvent) => void;
  private readonly shutdownTimeoutMs: number;
  private readonly turnsByThread = new Map<string, TurnState>();
  private readonly turnsById = new Map<string, TurnState>();
  private readonly deadProcesses = new WeakSet<object>();
  private process: CodexProcess | undefined;
  private rpc: JsonRpcClient | undefined;
  private startPromise: Promise<JsonRpcClient> | undefined;
  private closePromise: Promise<void> | undefined;
  private closed = false;

  constructor(private readonly config: BridgeConfig, options: CodexAppServerOptions = {}) {
    this.processFactory = options.processFactory ?? defaultProcessFactory;
    this.rpcFactory = options.rpcFactory ?? ((stdout, stdin) => new JsonRpcClient(stdout, stdin));
    this.log = options.log;
    this.shutdownTimeoutMs = options.shutdownTimeoutMs ?? DEFAULT_SHUTDOWN_TIMEOUT_MS;
  }

  async run(invocation: CodexInvocation, signal?: AbortSignal): Promise<CodexResult> {
    if (this.closed) {
      throw new CodexUpstreamError("unavailable");
    }
    if (signal?.aborted) {
      throw new CodexUpstreamError("timeout");
    }

    let rpc: JsonRpcClient;
    try {
      rpc = await this.startedClient();
    } catch (error) {
      throw mapUpstreamError(error, "unavailable");
    }
    let threadResponse: ThreadStartResponse;
    try {
      threadResponse = await rpc.request<ThreadStartResponse>("thread/start", {
        model: this.config.codexModel,
        cwd: this.config.codexCwd,
        approvalPolicy: "never",
        sandbox: "readOnly",
        personality: "none",
        ephemeral: true,
        selectedCapabilityRoots: [],
        dynamicTools: [],
      }, signal);
    } catch (error) {
      throw signal?.aborted ? new CodexUpstreamError("timeout") : mapUpstreamError(error);
    }

    const threadId = readId(threadResponse.thread?.id);
    if (!threadId) {
      throw new CodexUpstreamError("upstream");
    }

    const state = createTurnState(threadId);
    this.turnsByThread.set(threadId, state);
    this.watchAbort(state, signal);

    void rpc.request<TurnStartResponse>("turn/start", {
      threadId,
      input: [{ type: "text", text: invocation.prompt }],
      outputSchema: invocation.outputSchema,
    }).then((response) => {
      const turnId = readId(response.turn?.id);
      if (!turnId) {
        this.failTurn(state, new CodexUpstreamError("upstream"));
        return;
      }
      this.bindTurnId(state, turnId);
      if (state.finished) {
        this.interruptTurn(state, rpc);
      }
    }, (error: unknown) => {
      this.failTurn(state, mapUpstreamError(error));
    });

    return state.result;
  }

  close(): Promise<void> {
    if (!this.closePromise) {
      this.closed = true;
      this.closePromise = this.closeOnce();
    }
    return this.closePromise;
  }

  private async startedClient(): Promise<JsonRpcClient> {
    if (this.closed) {
      throw new CodexUpstreamError("unavailable");
    }
    if (this.startPromise) {
      return this.startPromise;
    }
    if (this.rpc && this.process) {
      return this.rpc;
    }
    const startup = this.startProcess();
    this.startPromise = startup;
    void startup.catch(() => {
      if (this.startPromise === startup) this.startPromise = undefined;
    });
    return this.startPromise;
  }

  private async startProcess(): Promise<JsonRpcClient> {
    const process = this.processFactory("codex", ["app-server", "--listen", "stdio://"], {
      stdio: ["pipe", "pipe", "pipe"],
      env: codexEnvironment(this.config.codexHome),
    });
    const rpc = this.rpcFactory(process.stdout, process.stdin);
    this.process = process;
    this.rpc = rpc;

    process.once("exit", (code, signal) => this.handleProcessDeath(process, code, signal));
    process.once("error", () => this.handleProcessDeath(process, null, null));
    this.drainSanitizedStderr(process.stderr);
    rpc.onNotification((method, params) => this.handleNotification(rpc, method, params));
    rpc.onRequest((id, method, params) => this.handleServerRequest(rpc, id, method, params));

    try {
      await rpc.request("initialize", {
        clientInfo: CLIENT_INFO,
        capabilities: { experimentalApi: false },
      });
      if (this.process !== process || this.rpc !== rpc || this.closed) {
        throw new CodexUpstreamError("unavailable");
      }
      rpc.notify("initialized");
      return rpc;
    } catch (error) {
      if (this.process === process) {
        this.process = undefined;
        this.rpc = undefined;
      }
      rpc.close();
      if (!this.closed && process.exitCode === null && process.signalCode === null) {
        process.kill("SIGTERM");
      }
      throw mapUpstreamError(error, "unavailable");
    }
  }

  private handleNotification(rpc: JsonRpcClient, method: string, params: unknown): void {
    const record = asRecord(params);
    if (!record) return;
    const state = this.findTurn(record);
    if (!state) return;

    const turn = asRecord(record.turn);
    const notificationTurnId = readId(record.turnId) ?? readId(turn?.id);
    if (notificationTurnId) this.bindTurnId(state, notificationTurnId);

    if (method === "item/started" || method === "item/completed") {
      const item = asRecord(record.item);
      const itemType = typeof item?.type === "string" ? item.type : undefined;
      if (itemType && FORBIDDEN_ITEM_TYPES.has(itemType)) {
        this.failTurn(state, new CodexUpstreamError("forbidden_tool"), rpc, true);
        return;
      }
      if (method === "item/completed" && itemType === "agentMessage" && typeof item?.text === "string") {
        state.finalText = item.text;
      }
      return;
    }

    if (method === "thread/tokenUsage/updated") {
      state.usage = readUsage(record.tokenUsage) ?? state.usage;
      return;
    }

    if (method === "error") {
      this.failTurn(state, mapUpstreamError(record.error));
      return;
    }

    if (method !== "turn/completed" || !turn) return;
    state.usage = readUsage(record.tokenUsage) ?? readUsage(record.usage) ?? readUsage(turn.usage) ?? state.usage;
    if (state.finalText === undefined) {
      state.finalText = latestAgentText(turn.items);
    }

    if (turn.status === "completed") {
      this.resolveTurn(state, { text: state.finalText ?? "", ...(state.usage ? { usage: state.usage } : {}) });
      return;
    }
    if (turn.status === "failed") {
      this.failTurn(state, mapUpstreamError(turn.error));
      return;
    }
    if (turn.status === "interrupted") {
      this.failTurn(state, new CodexUpstreamError("upstream"));
    }
  }

  private handleServerRequest(
    rpc: JsonRpcClient,
    id: string | number,
    method: string,
    params: unknown,
  ): void {
    rpc.respondError(id, -32601, "Interactive requests are disabled");
    if (!method.includes("requestApproval")) return;
    const record = asRecord(params);
    if (!record) return;
    const state = this.findTurn(record);
    if (!state) return;
    const turnId = readId(record.turnId);
    if (turnId) this.bindTurnId(state, turnId);
    this.failTurn(state, new CodexUpstreamError("forbidden_tool"), rpc, true);
  }

  private findTurn(params: Record<string, unknown>): TurnState | undefined {
    const turn = asRecord(params.turn);
    const turnId = readId(params.turnId) ?? readId(turn?.id);
    const threadId = readId(params.threadId);
    const byTurn = turnId ? this.turnsById.get(turnId) : undefined;
    if (byTurn) return !threadId || byTurn.threadId === threadId ? byTurn : undefined;
    const byThread = threadId ? this.turnsByThread.get(threadId) : undefined;
    if (turnId && byThread?.turnId && byThread.turnId !== turnId) return undefined;
    return byThread;
  }

  private bindTurnId(state: TurnState, turnId: string): void {
    if (state.turnId && state.turnId !== turnId) return;
    state.turnId = turnId;
    if (!state.finished) this.turnsById.set(turnId, state);
  }

  private watchAbort(state: TurnState, signal: AbortSignal | undefined): void {
    if (!signal) return;
    state.signal = signal;
    state.abortListener = () => {
      const rpc = this.rpc;
      this.failTurn(state, new CodexUpstreamError("timeout"), rpc, true);
    };
    if (signal.aborted) {
      state.abortListener();
    } else {
      signal.addEventListener("abort", state.abortListener, { once: true });
    }
  }

  private interruptTurn(state: TurnState, rpc: JsonRpcClient): void {
    if (state.interruptSent || !state.turnId) return;
    state.interruptSent = true;
    void rpc.request("turn/interrupt", {
      threadId: state.threadId,
      turnId: state.turnId,
    }).catch(() => {
      // Interruption is best effort after the caller-visible result is terminal.
    });
  }

  private resolveTurn(state: TurnState, result: CodexResult): void {
    if (state.finished) return;
    state.finished = true;
    this.removeTurn(state);
    state.resolve(result);
  }

  private failTurn(
    state: TurnState,
    error: CodexUpstreamError,
    rpc?: JsonRpcClient,
    interrupt = false,
  ): void {
    if (state.finished) return;
    state.finished = true;
    this.removeTurn(state);
    state.reject(error);
    if (interrupt && rpc) this.interruptTurn(state, rpc);
  }

  private removeTurn(state: TurnState): void {
    if (this.turnsByThread.get(state.threadId) === state) this.turnsByThread.delete(state.threadId);
    if (state.turnId && this.turnsById.get(state.turnId) === state) this.turnsById.delete(state.turnId);
    if (state.signal && state.abortListener) {
      state.signal.removeEventListener("abort", state.abortListener);
    }
  }

  private handleProcessDeath(
    process: CodexProcess,
    code: number | null,
    signal: NodeJS.Signals | null,
  ): void {
    if (this.deadProcesses.has(process as object)) return;
    this.deadProcesses.add(process as object);
    this.safeLog({ category: "exit", exitStatus: code ?? signal ?? "unknown" });
    if (this.process !== process) return;

    const rpc = this.rpc;
    this.process = undefined;
    this.rpc = undefined;
    this.startPromise = undefined;
    rpc?.close();
    for (const state of [...this.turnsByThread.values()]) {
      this.failTurn(state, new CodexUpstreamError("unavailable"));
    }
  }

  private drainSanitizedStderr(stderr: Readable): void {
    let hasPartialLine = false;
    stderr.on("data", (chunk: Buffer | string) => {
      for (const character of String(chunk)) {
        if (character === "\n") {
          this.safeLog({ category: "stderr" });
          hasPartialLine = false;
        } else {
          hasPartialLine = true;
        }
      }
    });
    stderr.once("end", () => {
      if (hasPartialLine) this.safeLog({ category: "stderr" });
      hasPartialLine = false;
    });
  }

  private safeLog(event: CodexDiagnosticEvent): void {
    try {
      this.log?.(event);
    } catch {
      // Diagnostic sinks cannot affect inference lifecycle.
    }
  }

  private async closeOnce(): Promise<void> {
    const process = this.process;
    const rpc = this.rpc;
    const active = [...this.turnsByThread.values()];
    if (rpc) {
      for (const state of active) this.interruptTurn(state, rpc);
    }
    for (const state of active) {
      this.failTurn(state, new CodexUpstreamError("unavailable"));
    }

    rpc?.close();
    this.rpc = undefined;
    this.process = undefined;
    this.startPromise = undefined;
    if (!process || process.exitCode !== null || process.signalCode !== null) return;

    const exited = waitForExit(process, this.shutdownTimeoutMs);
    process.kill("SIGTERM");
    if (await exited) return;
    if (process.exitCode === null && process.signalCode === null) {
      process.kill("SIGKILL");
    }
  }
}

function createTurnState(threadId: string): TurnState {
  let resolve!: (result: CodexResult) => void;
  let reject!: (error: CodexUpstreamError) => void;
  const result = new Promise<CodexResult>((resolveResult, rejectResult) => {
    resolve = resolveResult;
    reject = rejectResult;
  });
  return {
    threadId,
    finished: false,
    interruptSent: false,
    resolve,
    reject,
    result,
  };
}

function codexEnvironment(codexHome: string): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, CODEX_HOME: codexHome };
  delete env.BRIDGE_API_KEY;
  delete env.CODEX_API_KEY;
  delete env.OPENAI_API_KEY;
  return env;
}

function readId(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function latestAgentText(items: unknown): string | undefined {
  if (!Array.isArray(items)) return undefined;
  let text: string | undefined;
  for (const value of items) {
    const item = asRecord(value);
    if (item?.type === "agentMessage" && typeof item.text === "string") text = item.text;
  }
  return text;
}

function readUsage(value: unknown): CodexResult["usage"] | undefined {
  const usage = asRecord(value);
  if (!usage) return undefined;
  const breakdown = asRecord(usage.last) ?? usage;
  const inputTokens = breakdown.inputTokens;
  const outputTokens = breakdown.outputTokens;
  if (
    typeof inputTokens !== "number"
    || !Number.isFinite(inputTokens)
    || typeof outputTokens !== "number"
    || !Number.isFinite(outputTokens)
  ) {
    return undefined;
  }
  return { inputTokens, outputTokens };
}

function mapUpstreamError(error: unknown, fallback: CodexUpstreamErrorKind = "upstream"): CodexUpstreamError {
  if (error instanceof CodexUpstreamError) return error;
  const classification = classifyError(error);
  return new CodexUpstreamError(classification ?? fallback);
}

function classifyError(error: unknown): CodexUpstreamErrorKind | undefined {
  let text = "";
  if (error instanceof Error) text = error.message;
  else if (typeof error === "string") text = error;
  else {
    try {
      text = JSON.stringify(error);
    } catch {
      text = "";
    }
  }
  const normalized = text.toLowerCase();
  if (/\b(401|unauthori[sz]ed|authentication|credentials?|log[ -]?in|login)\b/.test(normalized)) {
    return "authentication";
  }
  if (/\b(429|rate[ -]?limit|quota|usage[ -]?limit)\b/.test(normalized)) {
    return "rate_limit";
  }
  if (/(output|response)[ -]?schema|schema validation|structured[ -]?output/.test(normalized)) {
    return "structured_output";
  }
  if (/\b(timeout|timed out|deadline|cancelled|canceled)\b/.test(normalized)) {
    return "timeout";
  }
  if (/transport closed|process closed|not running|enoent|econnrefused/.test(normalized)) {
    return "unavailable";
  }
  return undefined;
}

function errorMessage(kind: CodexUpstreamErrorKind): string {
  switch (kind) {
    case "authentication": return "Codex authentication failed.";
    case "rate_limit": return "Codex rate limit exceeded.";
    case "timeout": return "Codex request timed out.";
    case "structured_output": return "Codex could not produce the required structured output.";
    case "forbidden_tool": return "Codex attempted a forbidden tool or interactive action.";
    case "unavailable": return "Codex app-server is unavailable.";
    case "upstream": return "Codex upstream request failed.";
  }
}

function waitForExit(process: CodexProcess, timeoutMs: number): Promise<boolean> {
  if (process.exitCode !== null || process.signalCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const onExit = (): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      process.off("exit", onExit);
      resolve(false);
    }, timeoutMs);
    process.once("exit", onExit);
  });
}

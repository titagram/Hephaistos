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
  start(): Promise<void>;
  isReady(): boolean;
  run(invocation: CodexInvocation, signal: AbortSignal): Promise<CodexResult>;
  close(): Promise<void>;
}

export interface CodexProcess {
  readonly stdin: Writable;
  readonly stdout: Readable;
  readonly stderr: Readable;
  readonly exitCode: number | null;
  readonly signalCode: NodeJS.Signals | null;
  kill(signal?: NodeJS.Signals): boolean;
  once(event: "exit" | "close", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this;
  once(event: "error", listener: (error: Error) => void): this;
  on(event: "error", listener: (error: Error) => void): this;
  off(event: "exit" | "close", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this;
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
  environment?: NodeJS.ProcessEnv;
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
  readonly interruptedTurnIds: Set<string>;
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
const SAFE_ENVIRONMENT_KEYS = [
  "PATH",
  "Path",
  "PATHEXT",
  "HOME",
  "USER",
  "LOGNAME",
  "SHELL",
  "TMPDIR",
  "TMP",
  "TEMP",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "TZ",
  "SYSTEMROOT",
  "SystemRoot",
  "WINDIR",
  "COMSPEC",
  "ComSpec",
  "SSL_CERT_FILE",
  "SSL_CERT_DIR",
  "NODE_EXTRA_CA_CERTS",
] as const;

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
  private readonly environment: NodeJS.ProcessEnv;
  private readonly turnsByThread = new Map<string, TurnState>();
  private readonly turnsById = new Map<string, TurnState>();
  private readonly deadProcesses = new WeakSet<object>();
  private readonly terminationPromises = new WeakMap<object, Promise<boolean>>();
  private process: CodexProcess | undefined;
  private rpc: JsonRpcClient | undefined;
  private startPromise: Promise<JsonRpcClient> | undefined;
  private readyProcess: CodexProcess | undefined;
  private closePromise: Promise<void> | undefined;
  private closed = false;

  constructor(private readonly config: BridgeConfig, options: CodexAppServerOptions = {}) {
    this.processFactory = options.processFactory ?? defaultProcessFactory;
    this.rpcFactory = options.rpcFactory ?? ((stdout, stdin) => new JsonRpcClient(stdout, stdin));
    this.log = options.log;
    this.shutdownTimeoutMs = options.shutdownTimeoutMs ?? DEFAULT_SHUTDOWN_TIMEOUT_MS;
    this.environment = options.environment ?? process.env;
  }

  async start(): Promise<void> {
    await this.startedClient();
  }

  isReady(): boolean {
    const process = this.process;
    return !this.closed
      && process !== undefined
      && this.readyProcess === process
      && this.rpc !== undefined
      && process.exitCode === null
      && process.signalCode === null;
  }

  async run(invocation: CodexInvocation, signal: AbortSignal): Promise<CodexResult> {
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
        sandbox: "read-only",
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

    const existing = this.turnsByThread.get(threadId);
    if (existing && !existing.finished) {
      const error = new CodexUpstreamError("upstream");
      this.failTurn(existing, error, rpc, true);
      throw error;
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
      this.bindTurnId(state, turnId, rpc);
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
    if (this.process && !this.rpc) {
      throw new CodexUpstreamError("unavailable");
    }
    if (this.startPromise) {
      return this.startPromise;
    }
    if (this.rpc && this.process) {
      return this.rpc;
    }
    if (this.process) {
      throw new CodexUpstreamError("unavailable");
    }
    const startup = this.startProcess();
    this.startPromise = startup;
    void startup.catch(() => {
      if (this.startPromise === startup && !this.process) this.startPromise = undefined;
    });
    return this.startPromise;
  }

  private async startProcess(): Promise<JsonRpcClient> {
    this.readyProcess = undefined;
    const process = this.processFactory("codex", ["app-server", "--listen", "stdio://"], {
      stdio: ["pipe", "pipe", "pipe"],
      env: codexEnvironment(this.config.codexHome, this.environment),
    });
    this.process = process;

    process.once("exit", (code, signal) => this.handleProcessDeath(process, code, signal));
    process.once("close", (code, signal) => this.handleProcessDeath(process, code, signal));
    process.on("error", () => this.handleProcessError(process));
    this.drainSanitizedStderr(process.stderr);

    let rpc: JsonRpcClient | undefined;
    try {
      rpc = this.rpcFactory(process.stdout, process.stdin);
      this.rpc = rpc;
      rpc.onClose(() => this.handleRpcClose(process, rpc!));
      rpc.onNotification((method, params) => this.handleNotification(rpc!, method, params));
      rpc.onRequest((id, method, params) => this.handleServerRequest(rpc!, id, method, params));
      await rpc.request("initialize", {
        clientInfo: CLIENT_INFO,
        capabilities: { experimentalApi: true },
      });
      if (this.process !== process || this.rpc !== rpc || this.closed) {
        throw new CodexUpstreamError("unavailable");
      }
      rpc.notify("initialized");
      this.readyProcess = process;
      return rpc;
    } catch (error) {
      if (this.readyProcess === process) this.readyProcess = undefined;
      rpc?.close();
      const reaped = await this.terminateAndReap(process);
      if (this.rpc === rpc) this.rpc = undefined;
      if (reaped && this.process === process) {
        this.process = undefined;
      }
      throw mapUpstreamError(error, "unavailable");
    }
  }

  private handleNotification(rpc: JsonRpcClient, method: string, params: unknown): void {
    const record = asRecord(params);
    if (!record) return;
    const state = this.findTurn(record);
    if (!state) {
      const conflicts = this.findConflictingTurns(record);
      if (conflicts.length > 0) {
        const error = new CodexUpstreamError("upstream");
        for (const conflict of conflicts) this.failTurn(conflict, error, rpc, true);
      }
      return;
    }

    const turn = asRecord(record.turn);
    const notificationTurnId = readId(record.turnId) ?? readId(turn?.id);
    if (notificationTurnId && !this.bindTurnId(state, notificationTurnId, rpc)) return;

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
    if (containsForbiddenItem(turn.items)) {
      this.failTurn(state, new CodexUpstreamError("forbidden_tool"), rpc, true);
      return;
    }
    state.usage = readUsage(record.tokenUsage) ?? readUsage(record.usage) ?? readUsage(turn.usage) ?? state.usage;
    if (state.finalText === undefined) {
      state.finalText = latestAgentText(turn.items);
    }

    if (turn.status === "completed") {
      if (state.finalText === undefined) {
        this.failTurn(state, new CodexUpstreamError("upstream"));
        return;
      }
      this.resolveTurn(state, { text: state.finalText, ...(state.usage ? { usage: state.usage } : {}) });
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
    this.failApproval(rpc, params);
  }

  private failApproval(rpc: JsonRpcClient, params: unknown): void {
    const record = asRecord(params);
    const active = [...this.turnsByThread.values()];
    if (!record) {
      this.failTurns(active, new CodexUpstreamError("forbidden_tool"), rpc);
      return;
    }
    const state = this.findTurn(record);
    if (!state) {
      this.failTurns(active, new CodexUpstreamError("forbidden_tool"), rpc);
      return;
    }
    const turn = asRecord(record.turn);
    const turnId = readId(record.turnId) ?? readId(turn?.id);
    if (turnId && !state.turnId) {
      const error = new CodexUpstreamError("forbidden_tool");
      if (active.length > 1) {
        this.failTurns(active, error, rpc);
        return;
      }
      this.failTurn(state, error);
      this.interruptTurn(state, rpc, turnId);
      return;
    }
    if (turnId && !this.bindTurnId(state, turnId, rpc)) {
      this.failTurns(active, new CodexUpstreamError("forbidden_tool"), rpc);
      return;
    }
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

  private findConflictingTurns(params: Record<string, unknown>): TurnState[] {
    const turn = asRecord(params.turn);
    const turnId = readId(params.turnId) ?? readId(turn?.id);
    const threadId = readId(params.threadId);
    if (!turnId || !threadId) return [];
    const byTurn = this.turnsById.get(turnId);
    const byThread = this.turnsByThread.get(threadId);
    if (byTurn && byThread && byTurn !== byThread) return [byThread, byTurn];
    if (byTurn && byTurn.threadId !== threadId) return [byTurn];
    if (byThread?.turnId && byThread.turnId !== turnId) return [byThread];
    return [];
  }

  private bindTurnId(state: TurnState, turnId: string, rpc: JsonRpcClient): boolean {
    if (state.finished) {
      if (state.turnId && state.turnId !== turnId) {
        this.interruptTurn(state, rpc, state.turnId);
        if (!this.turnsById.has(turnId)) this.interruptTurn(state, rpc, turnId);
      } else if (!state.turnId && !this.turnsById.has(turnId)) {
        state.turnId = turnId;
        this.interruptTurn(state, rpc, turnId);
      }
      return false;
    }
    if (state.turnId && state.turnId !== turnId) {
      const existingTurnId = state.turnId;
      this.failTurn(state, new CodexUpstreamError("upstream"));
      this.interruptTurn(state, rpc, existingTurnId);
      this.interruptTurn(state, rpc, turnId);
      return false;
    }
    const owner = this.turnsById.get(turnId);
    if (owner && owner !== state) {
      const error = new CodexUpstreamError("upstream");
      this.failTurn(owner, error);
      this.failTurn(state, error);
      this.interruptTurn(owner, rpc, turnId);
      this.interruptTurn(state, rpc, turnId);
      return false;
    }
    state.turnId = turnId;
    this.turnsById.set(turnId, state);
    return true;
  }

  private watchAbort(state: TurnState, signal: AbortSignal | undefined): void {
    if (!signal) return;
    state.signal = signal;
    state.abortListener = () => {
      const rpc = this.rpc;
      const process = this.process;
      if (!state.turnId && rpc && process) {
        this.failTurn(state, new CodexUpstreamError("timeout"));
        this.retireRpcProcess(process, rpc);
        return;
      }
      this.failTurn(state, new CodexUpstreamError("timeout"), rpc, true);
    };
    if (signal.aborted) {
      state.abortListener();
    } else {
      signal.addEventListener("abort", state.abortListener, { once: true });
    }
  }

  private interruptTurn(state: TurnState, rpc: JsonRpcClient, turnId = state.turnId): void {
    if (!turnId || state.interruptedTurnIds.has(turnId)) return;
    state.interruptedTurnIds.add(turnId);
    void rpc.request("turn/interrupt", {
      threadId: state.threadId,
      turnId,
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

  private failTurns(states: readonly TurnState[], error: CodexUpstreamError, rpc: JsonRpcClient): void {
    for (const state of states) this.failTurn(state, error, rpc, true);
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
    this.readyProcess = undefined;
    this.startPromise = undefined;
    rpc?.close();
    for (const state of [...this.turnsByThread.values()]) {
      this.failTurn(state, new CodexUpstreamError("unavailable"));
    }
  }

  private handleProcessError(process: CodexProcess): void {
    if (this.process !== process) return;
    const rpc = this.rpc;
    if (this.rpc === rpc) this.rpc = undefined;
    this.readyProcess = undefined;
    this.startPromise = undefined;
    rpc?.close();
    for (const state of [...this.turnsByThread.values()]) {
      this.failTurn(state, new CodexUpstreamError("unavailable"));
    }
    void this.terminateAndReap(process).then((reaped) => {
      if (reaped && this.process === process) this.process = undefined;
    });
  }

  private handleRpcClose(process: CodexProcess, rpc: JsonRpcClient): void {
    this.releaseRpcProcess(process, rpc, false);
  }

  private retireRpcProcess(process: CodexProcess, rpc: JsonRpcClient): void {
    this.releaseRpcProcess(process, rpc, true);
  }

  private releaseRpcProcess(process: CodexProcess, rpc: JsonRpcClient, closeRpc: boolean): void {
    if (this.process !== process || this.rpc !== rpc) return;
    this.rpc = undefined;
    this.readyProcess = undefined;
    this.startPromise = undefined;
    if (closeRpc) rpc.close();
    for (const state of [...this.turnsByThread.values()]) {
      this.failTurn(state, new CodexUpstreamError("unavailable"));
    }
    void this.terminateAndReap(process).then((reaped) => {
      if (reaped && this.process === process) this.process = undefined;
    });
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
    this.readyProcess = undefined;
    this.startPromise = undefined;
    if (!process) return;
    const reaped = await this.terminateAndReap(process);
    if (reaped && this.process === process) {
      this.process = undefined;
    }
  }

  private terminateAndReap(process: CodexProcess): Promise<boolean> {
    const existing = this.terminationPromises.get(process as object);
    if (existing) return existing;
    const termination = Promise.resolve().then(() => this.terminateAndReapOnce(process));
    this.terminationPromises.set(process as object, termination);
    return termination;
  }

  private async terminateAndReapOnce(process: CodexProcess): Promise<boolean> {
    if (process.exitCode !== null || process.signalCode !== null) return true;
    const terminated = waitForExit(process, this.shutdownTimeoutMs);
    process.kill("SIGTERM");
    if (await terminated) return true;
    if (process.exitCode !== null || process.signalCode !== null) return true;
    const killed = waitForExit(process, this.shutdownTimeoutMs);
    process.kill("SIGKILL");
    return killed;
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
    interruptedTurnIds: new Set(),
    resolve,
    reject,
    result,
  };
}

function codexEnvironment(codexHome: string, source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of SAFE_ENVIRONMENT_KEYS) {
    if (source[key] !== undefined) env[key] = source[key];
  }
  env.CODEX_HOME = codexHome;
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

function containsForbiddenItem(items: unknown): boolean {
  if (!Array.isArray(items)) return false;
  return items.some((value) => {
    const item = asRecord(value);
    return typeof item?.type === "string" && FORBIDDEN_ITEM_TYPES.has(item.type);
  });
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
      process.off("exit", onExit);
      process.off("close", onExit);
      resolve(true);
    };
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      process.off("exit", onExit);
      process.off("close", onExit);
      resolve(false);
    }, timeoutMs);
    process.once("exit", onExit);
    process.once("close", onExit);
  });
}

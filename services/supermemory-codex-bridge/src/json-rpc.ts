import type { Readable, Writable } from "node:stream";

const MAX_PROTOCOL_LINE_LENGTH = 4 * 1024 * 1024;
const CLOSED_MESSAGE = "JSON-RPC transport closed.";
const PROTOCOL_MESSAGE = "JSON-RPC protocol error.";

type JsonRpcId = string | number;

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  signal?: AbortSignal;
  abortHandler?: () => void;
}

type NotificationListener = (method: string, params: unknown) => void;
type RequestListener = (id: JsonRpcId, method: string, params: unknown) => void;

export class JsonRpcError extends Error {
  constructor(public readonly code: number, message: string, public readonly data?: unknown) {
    super(message);
    this.name = "JsonRpcError";
  }
}

export class JsonRpcClient {
  private readonly pending = new Map<JsonRpcId, PendingRequest>();
  private readonly notificationListeners = new Set<NotificationListener>();
  private readonly requestListeners = new Set<RequestListener>();
  private nextId = 1;
  private buffer = "";
  private closed = false;

  private readonly onData = (chunk: unknown): void => {
    if (this.closed) {
      return;
    }

    this.buffer += String(chunk);

    let newlineIndex = this.buffer.indexOf("\n");
    while (newlineIndex !== -1) {
      let line = this.buffer.slice(0, newlineIndex);
      this.buffer = this.buffer.slice(newlineIndex + 1);
      if (line.endsWith("\r")) {
        line = line.slice(0, -1);
      }
      if (line.length > MAX_PROTOCOL_LINE_LENGTH || !this.handleLine(line)) {
        this.closeProtocolError();
        return;
      }
      newlineIndex = this.buffer.indexOf("\n");
    }
    if (this.buffer.length > MAX_PROTOCOL_LINE_LENGTH) {
      this.closeProtocolError();
    }
  };

  private readonly onEnd = (): void => {
    this.close();
  };

  private readonly onStreamError = (): void => {
    this.close();
  };

  constructor(private readonly stdout: Readable, private readonly stdin: Writable) {
    stdout.on("data", this.onData);
    stdout.once("end", this.onEnd);
    stdout.once("close", this.onEnd);
    stdout.once("error", this.onStreamError);
  }

  request<T>(method: string, params?: unknown, signal?: AbortSignal): Promise<T> {
    if (this.closed) {
      return Promise.reject(new Error(CLOSED_MESSAGE));
    }
    if (signal?.aborted) {
      return Promise.reject(new Error("JSON-RPC request cancelled."));
    }

    const id = this.nextId++;
    return new Promise<unknown>((resolve, reject) => {
      const pending: PendingRequest = { resolve, reject, signal };
      if (signal) {
        pending.abortHandler = () => {
          if (this.pending.get(id) !== pending) {
            return;
          }
          this.removePending(id, pending);
          reject(new Error("JSON-RPC request cancelled."));
        };
        signal.addEventListener("abort", pending.abortHandler, { once: true });
      }
      this.pending.set(id, pending);

      try {
        this.write({ jsonrpc: "2.0", id, method, ...(params === undefined ? {} : { params }) });
      } catch {
        this.removePending(id, pending);
        reject(new Error(CLOSED_MESSAGE));
      }
    }) as Promise<T>;
  }

  notify(method: string, params?: unknown): void {
    if (this.closed) {
      return;
    }
    this.write({ jsonrpc: "2.0", method, ...(params === undefined ? {} : { params }) });
  }

  onNotification(listener: NotificationListener): () => void {
    this.notificationListeners.add(listener);
    return () => this.notificationListeners.delete(listener);
  }

  onRequest(listener: RequestListener): () => void {
    this.requestListeners.add(listener);
    return () => this.requestListeners.delete(listener);
  }

  respondResult(id: JsonRpcId, result: unknown): void {
    if (!this.closed) {
      this.write({ jsonrpc: "2.0", id, result });
    }
  }

  respondError(id: JsonRpcId, code: number, message: string): void {
    if (!this.closed) {
      this.write({ jsonrpc: "2.0", id, error: { code, message } });
    }
  }

  close(error?: Error): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.stdout.off("data", this.onData);
    this.stdout.off("end", this.onEnd);
    this.stdout.off("close", this.onEnd);
    this.stdout.off("error", this.onStreamError);
    this.notificationListeners.clear();
    this.requestListeners.clear();

    const closeError = error?.message === PROTOCOL_MESSAGE
      ? new Error(PROTOCOL_MESSAGE)
      : new Error(CLOSED_MESSAGE);
    for (const [id, pending] of this.pending) {
      this.removePending(id, pending);
      pending.reject(closeError);
    }
  }

  private write(message: Record<string, unknown>): void {
    this.stdin.write(`${JSON.stringify(message)}\n`);
  }

  private removePending(id: JsonRpcId, pending: PendingRequest): void {
    this.pending.delete(id);
    if (pending.signal && pending.abortHandler) {
      pending.signal.removeEventListener("abort", pending.abortHandler);
    }
  }

  private closeProtocolError(): void {
    this.close(new Error(PROTOCOL_MESSAGE));
  }

  private handleLine(line: string): boolean {
    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      return false;
    }
    if (!isJsonRpcMessage(message)) {
      return false;
    }

    const hasId = Object.hasOwn(message, "id") && isJsonRpcId(message.id);
    const hasMethod = typeof message.method === "string";
    const hasResult = Object.hasOwn(message, "result");
    const hasError = Object.hasOwn(message, "error");

    if (hasId && (hasResult || hasError)) {
      if (hasResult === hasError) {
        return false;
      }
      this.handleResponse(message.id as JsonRpcId, message);
      return true;
    }

    if (hasMethod && hasId) {
      this.dispatchRequest(message.id as JsonRpcId, message.method as string, message.params);
      return true;
    }

    if (hasMethod && !Object.hasOwn(message, "id")) {
      this.dispatchNotification(message.method as string, message.params);
      return true;
    }

    return false;
  }

  private handleResponse(id: JsonRpcId, message: Record<string, unknown>): void {
    const pending = this.pending.get(id);
    if (!pending) {
      return;
    }
    if (Object.hasOwn(message, "result")) {
      this.removePending(id, pending);
      pending.resolve(message.result);
      return;
    }

    const error = message.error;
    if (!isJsonRpcError(error)) {
      this.closeProtocolError();
      return;
    }
    this.removePending(id, pending);
    pending.reject(new JsonRpcError(error.code, error.message, error.data));
  }

  private dispatchNotification(method: string, params: unknown): void {
    for (const listener of this.notificationListeners) {
      try {
        listener(method, params);
      } catch {
        // A consumer callback cannot be allowed to break the protocol reader.
      }
    }
  }

  private dispatchRequest(id: JsonRpcId, method: string, params: unknown): void {
    for (const listener of this.requestListeners) {
      try {
        listener(id, method, params);
      } catch {
        // A consumer callback cannot be allowed to break the protocol reader.
      }
    }
  }
}

function isJsonRpcMessage(value: unknown): value is Record<string, unknown> {
  return typeof value === "object"
    && value !== null
    && !Array.isArray(value)
    && (value as Record<string, unknown>).jsonrpc === "2.0";
}

function isJsonRpcId(value: unknown): value is JsonRpcId {
  return typeof value === "string" || (typeof value === "number" && Number.isFinite(value));
}

function isJsonRpcError(value: unknown): value is { code: number; message: string; data?: unknown } {
  return typeof value === "object"
    && value !== null
    && !Array.isArray(value)
    && typeof (value as Record<string, unknown>).code === "number"
    && typeof (value as Record<string, unknown>).message === "string";
}

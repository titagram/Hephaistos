import assert from "node:assert/strict";
import test from "node:test";
import { PassThrough } from "node:stream";

import { JsonRpcClient, JsonRpcError } from "../src/json-rpc.js";

function createTransport(): { client: JsonRpcClient; stdin: PassThrough; stdout: PassThrough } {
  const stdin = new PassThrough();
  const stdout = new PassThrough();
  return { client: new JsonRpcClient(stdout, stdin), stdin, stdout };
}

async function nextLine(stream: NodeJS.ReadableStream): Promise<string> {
  return new Promise((resolve, reject) => {
    const cleanup = (): void => {
      stream.off("data", onData);
      stream.off("end", onEnd);
      stream.off("error", onEnd);
    };
    const onData = (chunk: unknown): void => {
      cleanup();
      const line = String(chunk).split("\n")[0];
      if (line) resolve(line);
      else reject(new Error("stream ended before a JSON-RPC line arrived"));
    };
    const onEnd = (): void => {
      cleanup();
      reject(new Error("stream ended before a JSON-RPC line arrived"));
    };
    stream.once("data", onData);
    stream.once("end", onEnd);
    stream.once("error", onEnd);
  });
}

async function expectRejection(promise: Promise<unknown>, message: string): Promise<Error> {
  try {
    await promise;
  } catch (error) {
    assert(error instanceof Error);
    assert.match(error.message, new RegExp(message));
    return error;
  }
  assert.fail("expected promise to reject");
}

test("serializes requests and resolves matching results", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request<{ thread: { id: string } }>("thread/start", { ephemeral: true });

  assert.deepEqual(JSON.parse(await nextLine(stdin)), {
    jsonrpc: "2.0",
    id: 1,
    method: "thread/start",
    params: { ephemeral: true },
  });

  stdout.write('{"jsonrpc":"2.0","id":1,"result":{"thread":{"id":"t1"}}}\n');
  assert.equal((await request).thread.id, "t1");
  client.close();
});

test("accepts Codex responses without a jsonrpc member", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request<{ userAgent: string }>("initialize");
  await nextLine(stdin);

  stdout.write('{"id":1,"result":{"userAgent":"codex_cli_rs/0.146.0"}}\n');

  assert.deepEqual(await request, { userAgent: "codex_cli_rs/0.146.0" });
  client.close();
});

test("dispatches Codex notifications without a jsonrpc member", () => {
  const { client, stdout } = createTransport();
  const notifications: Array<{ method: string; params: unknown }> = [];
  client.onNotification((method, params) => notifications.push({ method, params }));

  stdout.write('{"method":"configWarning","params":{"message":"configuration warning"}}\n');

  assert.deepEqual(notifications, [{
    method: "configWarning",
    params: { message: "configuration warning" },
  }]);
  client.close();
});

test("buffers a JSON-RPC line split across stdout chunks", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request<string>("thread/read");
  await nextLine(stdin);

  stdout.write('{"jsonrpc":"2.0","id":1,"res');
  stdout.write('ult":"complete"}\n');

  assert.equal(await request, "complete");
  client.close();
});

test("preserves a multi-byte result split between stdout chunks", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request<string>("thread/read");
  await nextLine(stdin);
  const response = Buffer.from("{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":\"café\"}\n");
  const splitAt = response.indexOf(Buffer.from("é")) + 1;

  stdout.write(response.subarray(0, splitAt));
  stdout.write(response.subarray(splitAt));

  assert.equal(await request, "café");
  client.close();
});

test("dispatches every message delivered in one stdout chunk", async () => {
  const { client, stdin, stdout } = createTransport();
  const notifications: Array<{ method: string; params: unknown }> = [];
  client.onNotification((method, params) => notifications.push({ method, params }));
  const request = client.request<string>("turn/start");
  await nextLine(stdin);

  stdout.write('{"jsonrpc":"2.0","method":"turn/started","params":{"id":"turn-1"}}\n{"jsonrpc":"2.0","id":1,"result":"ok"}\n');

  assert.deepEqual(notifications, [{ method: "turn/started", params: { id: "turn-1" } }]);
  assert.equal(await request, "ok");
  client.close();
});

test("serializes notifications without an id", async () => {
  const { client, stdin } = createTransport();

  client.notify("initialized", { client: "bridge" });

  assert.deepEqual(JSON.parse(await nextLine(stdin)), {
    jsonrpc: "2.0",
    method: "initialized",
    params: { client: "bridge" },
  });
  client.close();
});

test("dispatches server requests and serializes result and error helpers", async () => {
  const { client, stdin, stdout } = createTransport();
  const received: Array<{ id: string | number; method: string; params: unknown }> = [];
  client.onRequest((id, method, params) => received.push({ id, method, params }));

  stdout.write('{"jsonrpc":"2.0","id":"req-7","method":"item/read","params":{"item":"x"}}\n');
  assert.deepEqual(received, [{ id: "req-7", method: "item/read", params: { item: "x" } }]);

  client.respondResult("req-7", { item: "x" });
  assert.deepEqual(JSON.parse(await nextLine(stdin)), {
    jsonrpc: "2.0",
    id: "req-7",
    result: { item: "x" },
  });

  client.respondError(8, -32601, "Method not found");
  assert.deepEqual(JSON.parse(await nextLine(stdin)), {
    jsonrpc: "2.0",
    id: 8,
    error: { code: -32601, message: "Method not found" },
  });
  client.close();
});

test("rejects requests with JSON-RPC error details", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request("turn/start");
  await nextLine(stdin);

  stdout.write('{"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":"Not ready"}}\n');

  await assert.rejects(request, (error: unknown) =>
    error instanceof JsonRpcError && error.code === -32001 && error.message === "Not ready",
  );
  client.close();
});

test("cancels a pending request and ignores its late response", async () => {
  const { client, stdin, stdout } = createTransport();
  const controller = new AbortController();
  const cancelled = client.request("turn/start", undefined, controller.signal);
  await nextLine(stdin);

  controller.abort();
  await expectRejection(cancelled, "cancelled");

  stdout.write('{"jsonrpc":"2.0","id":1,"result":"late"}\n');
  const next = client.request<string>("turn/next");
  assert.deepEqual(JSON.parse(await nextLine(stdin)), { jsonrpc: "2.0", id: 2, method: "turn/next" });
  stdout.write('{"jsonrpc":"2.0","id":2,"result":"current"}\n');

  assert.equal(await next, "current");
  client.close();
});

test("rejects every pending request when stdout contains malformed JSON", async () => {
  const { client, stdin, stdout } = createTransport();
  const first = client.request("turn/one");
  const second = client.request("turn/two");

  stdout.write("{not json}\n");

  const firstError = await expectRejection(first, "protocol");
  const secondError = await expectRejection(second, "protocol");
  assert.equal(firstError, secondError);
});

test("rejects every pending request when stdout ends", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request("turn/start");
  await nextLine(stdin);

  stdout.end();

  await expectRejection(request, "closed");
});

test("rejects malformed JSON-RPC error responses as protocol failures", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request("turn/start");
  await nextLine(stdin);

  stdout.write('{"jsonrpc":"2.0","id":1,"error":{"code":"bad","message":"Not ready"}}\n');

  await expectRejection(request, "protocol");
});

for (const jsonrpc of ["1.0", null, 2] as const) {
  test(`rejects an explicit invalid jsonrpc version (${String(jsonrpc)})`, async () => {
    const { client, stdin, stdout } = createTransport();
    const request = client.request("initialize");
    await nextLine(stdin);

    stdout.write(`${JSON.stringify({ jsonrpc, id: 1, result: { userAgent: "codex_cli_rs/0.146.0" } })}\n`);

    await expectRejection(request, "protocol");
  });
}

test("rejects malformed errors for unknown response ids", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request("turn/start");
  await nextLine(stdin);

  stdout.write('{"jsonrpc":"2.0","id":999,"error":{"code":"bad","message":"Not ready"}}\n');

  let outcome = "still pending";
  void request.then(() => { outcome = "resolved"; }, () => { outcome = "rejected"; });
  await Promise.resolve();
  assert.equal(outcome, "rejected");
});

test("accepts a four MiB protocol line containing multi-byte text", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request<string>("turn/start");
  await nextLine(stdin);
  const prefix = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":\"";
  const suffix = "\"}";
  const resultBytes = 4 * 1024 * 1024 - Buffer.byteLength(prefix) - Buffer.byteLength(suffix);
  const result = "é".repeat(Math.floor(resultBytes / 2)) + (resultBytes % 2 === 0 ? "" : "x");
  stdout.write(`${prefix}${result}${suffix}\n`);

  assert.equal(Buffer.byteLength(await request), resultBytes);
  client.close();
});

test("closes when multi-byte text exceeds the four MiB protocol limit", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request("turn/start");
  await nextLine(stdin);
  const prefix = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":\"";
  const suffix = "\"}";
  const resultBytes = 4 * 1024 * 1024 - Buffer.byteLength(prefix) - Buffer.byteLength(suffix);
  const result = "é".repeat(Math.floor(resultBytes / 2) + 1) + (resultBytes % 2 === 0 ? "" : "x");
  stdout.write(`${prefix}${result}${suffix}\n`);

  await expectRejection(request, "protocol");
});

test("closes when a protocol line exceeds four MiB", async () => {
  const { client, stdin, stdout } = createTransport();
  const request = client.request("turn/start");
  await nextLine(stdin);

  stdout.write("x".repeat(4 * 1024 * 1024 + 1));

  await expectRejection(request, "protocol");
});

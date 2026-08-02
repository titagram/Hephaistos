# Supermemory Codex Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the official self-hosted Supermemory server at `https://persephone.cc`, backed only by a private dedicated Codex bridge for LLM inference, while keeping Hermes/Hades unchanged.

**Architecture:** A pinned native `supermemory-server` container serves its built-in UI and API through the VPS's existing Traefik instance. A standalone Node/TypeScript service exposes only the OpenAI-compatible surface Supermemory needs and translates each request into a fresh ephemeral thread on one long-lived `codex app-server` child process. The bridge has a private Docker network only; Traefik protects UI/reference routes with BasicAuth and leaves native Supermemory Bearer authentication intact on API routes.

**Tech Stack:** Docker Compose v5, Traefik Docker labels, Supermemory `server-v0.0.6` Linux x64, Node.js 22, TypeScript 6.0.3, `@openai/codex` 0.146.0, Node `http` and `node:test` APIs.

## Global Constraints

- Deploy on Linux x86_64 and use Supermemory release `server-v0.0.6` from `https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.6/supermemory-server-linux-x64`.
- Verify the Supermemory binary against SHA-256 `bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8` during the image build.
- Pin the bridge base image to `node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732`.
- Pin `@openai/codex` to `0.146.0`; do not use an OpenAI Platform API key.
- Keep the bridge independent from Hermes, Hades, `AIAgent`, Hermes memory, and Hermes provider selection.
- Use a fresh ephemeral Codex thread per HTTP request and one persistent `codex app-server` process per bridge container.
- Permit no shell, file-change, web-search, MCP, collaboration, or dynamic tools in bridge requests.
- Keep local embeddings in Supermemory; only Supermemory's LLM calls go through Codex.
- Publish no bridge host port and create no Traefik router for it.
- Use the existing external Docker network `traefik_default` and certificate resolver `le`.
- Route `persephone.cc` to VPS IPv4 `162.19.229.31`; DNS propagation is an external prerequisite for final TLS validation.
- Protect UI and reference routes with Traefik BasicAuth user `titagram`; never place its password or generated htpasswd hash in Git.
- Keep native Supermemory Bearer authentication on `/v3`, `/v4`, and `/files` except for the dedicated browser reference subroutes.
- Never commit the bridge key, generated Supermemory API key, Codex credentials, authorization headers, document contents, or model responses.
- Limit request bodies to 2 MiB, concurrent Codex requests to 2, and individual inference to 120 seconds.
- Milestone 1 does not modify `plugins/memory/supermemory`; Hermes/Hades integration remains a separate milestone.

---

## File map

Create these focused units; do not add a replacement web dashboard:

```text
services/supermemory-codex-bridge/
├── .dockerignore                  # excludes local state and development output
├── Dockerfile                     # pinned production bridge image
├── package.json                   # exact scripts and dependency versions
├── package-lock.json              # reproducible npm dependency graph
├── tsconfig.json                  # strict NodeNext build
├── src/
│   ├── codex-app-server.ts        # persistent process, fresh threads, event policy
│   ├── config.ts                  # validated runtime configuration
│   ├── index.ts                   # process lifecycle and signal handling
│   ├── json-rpc.ts                # line-delimited JSON-RPC transport
│   ├── openai.ts                  # Chat Completions parsing and response/error shapes
│   ├── prompt.ts                  # deterministic message-to-Codex prompt mapping
│   └── server.ts                  # private HTTP server, auth, limits, semaphore
└── tests/
    ├── codex-app-server.test.ts
    ├── config.test.ts
    ├── json-rpc.test.ts
    ├── openai.test.ts
    ├── prompt.test.ts
    └── server.test.ts

deploy/supermemory/
├── .env.example                   # names and safe defaults only
├── README.md                      # bootstrap, operation, rollback, milestone handoff
├── codex-config.toml              # tool-free dedicated Codex policy
├── compose.yaml                   # services, networks, volumes, Traefik labels
├── server.Dockerfile              # pinned native Supermemory image
├── scripts/
│   ├── bootstrap.sh               # secret generation, Codex login, first startup
│   └── smoke.sh                   # local and public acceptance checks
└── tests/
    ├── test-compose-config.sh      # rendered topology and router assertions
    └── test-server-image.sh        # checksum-built binary and built-in UI probe
```

The public TypeScript interfaces shared between tasks are:

```ts
export interface BridgeConfig {
  host: string;
  port: number;
  apiKey: string;
  publicModel: string;
  codexModel: string;
  codexHome: string;
  codexCwd: string;
  timeoutMs: number;
  maxBodyBytes: number;
  maxConcurrency: number;
}

export interface CodexInvocation {
  prompt: string;
  outputSchema?: Record<string, unknown>;
}

export interface CodexResult {
  text: string;
  usage?: { inputTokens: number; outputTokens: number };
}

export interface ChatCompletionResponse {
  id: string;
  object: "chat.completion";
  created: number;
  model: string;
  choices: Array<{
    index: 0;
    message: { role: "assistant"; content: string };
    finish_reason: "stop";
  }>;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

export interface CodexRunner {
  start(): Promise<void>;
  run(invocation: CodexInvocation, signal: AbortSignal): Promise<CodexResult>;
  close(): Promise<void>;
}
```

### Task 1: Build and probe the pinned native Supermemory image

**Files:**
- Create: `deploy/supermemory/server.Dockerfile`
- Create: `deploy/supermemory/tests/test-server-image.sh`

**Interfaces:**
- Consumes: the pinned release URL and checksum from Global Constraints.
- Produces: local image `hephaistos-supermemory-server:test`, listening on port `6767`, persisting under `/var/lib/supermemory`.

- [ ] **Step 1: Write the failing container probe**

Create `deploy/supermemory/tests/test-server-image.sh` with a trap that always removes its named test container, builds `server.Dockerfile`, starts it with a temporary Docker volume, waits up to 60 seconds for `/`, and asserts all of the following:

```bash
#!/usr/bin/env bash
set -euo pipefail

image="hephaistos-supermemory-server:test"
container="hephaistos-supermemory-server-test"
volume="hephaistos_supermemory_server_test"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; docker volume rm "$volume" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker build -f deploy/supermemory/server.Dockerfile -t "$image" .
docker volume create "$volume" >/dev/null
docker run -d --name "$container" -p 127.0.0.1:16767:6767 \
  -e SUPERMEMORY_DATA_DIR=/var/lib/supermemory \
  -e SUPERMEMORY_SKIP_EMBEDDING_PREWARM=true \
  -e OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  -e OPENAI_API_KEY=test-only \
  -e OPENAI_MODEL=supermemory-codex \
  -v "$volume:/var/lib/supermemory" "$image"

for _ in $(seq 1 60); do
  html="$(curl -fsS http://127.0.0.1:16767/ 2>/dev/null || true)"
  [[ "$html" == *"supermemory · local"* ]] && break
  sleep 1
done
[[ "$html" == *"supermemory · local"* ]]
[[ "$html" == *"/v4/reference"* ]]
[[ "$html" == *"/v4/openapi"* ]]
test "$(docker inspect -f '{{.Config.User}}' "$container")" = "node"
```

- [ ] **Step 2: Run the probe to verify it fails**

Run: `bash deploy/supermemory/tests/test-server-image.sh`

Expected: FAIL because `deploy/supermemory/server.Dockerfile` does not exist.

- [ ] **Step 3: Implement the pinned image**

Create a multi-stage `deploy/supermemory/server.Dockerfile` using the pinned Node image for both stages. Install only `ca-certificates` and `curl` in the download stage, fetch the exact Linux x64 asset, run `echo '<checksum>  /tmp/supermemory-server' | sha256sum -c -`, then copy it to `/usr/local/bin/supermemory-server` with mode `0755`. In the runtime stage:

```dockerfile
ENV PORT=6767 \
    SUPERMEMORY_DATA_DIR=/var/lib/supermemory
RUN mkdir -p /var/lib/supermemory && chown node:node /var/lib/supermemory
USER node
VOLUME ["/var/lib/supermemory"]
EXPOSE 6767
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD node -e "require('http').get('http://127.0.0.1:6767/',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
ENTRYPOINT ["/usr/local/bin/supermemory-server"]
```

- [ ] **Step 4: Re-run the image probe**

Run: `bash deploy/supermemory/tests/test-server-image.sh`

Expected: PASS; `/` contains the built-in `supermemory · local` UI and reference links.

- [ ] **Step 5: Commit the independently runnable server image**

```bash
git add deploy/supermemory/server.Dockerfile deploy/supermemory/tests/test-server-image.sh
git commit -m "build: add pinned supermemory server image"
```

### Task 2: Scaffold the strict bridge package and configuration

**Files:**
- Create: `services/supermemory-codex-bridge/package.json`
- Create: `services/supermemory-codex-bridge/package-lock.json`
- Create: `services/supermemory-codex-bridge/tsconfig.json`
- Create: `services/supermemory-codex-bridge/src/config.ts`
- Create: `services/supermemory-codex-bridge/tests/config.test.ts`

**Interfaces:**
- Consumes: environment variables listed below.
- Produces: `loadConfig(env: NodeJS.ProcessEnv): BridgeConfig` and `ConfigurationError`.

- [ ] **Step 1: Create package metadata and install exact dependencies**

Use this `package.json` shape:

```json
{
  "name": "@hephaistos/supermemory-codex-bridge",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "test": "tsx --test tests/*.test.ts",
    "start": "node dist/index.js"
  },
  "dependencies": { "@openai/codex": "0.146.0" },
  "devDependencies": {
    "@types/node": "22.18.0",
    "tsx": "4.23.2",
    "typescript": "6.0.3"
  }
}
```

Use a strict NodeNext `tsconfig.json` with `rootDir: "."`, `outDir: "dist"`, `target: "ES2022"`, `module` and `moduleResolution` set to `NodeNext`, `strict: true`, `noUncheckedIndexedAccess: true`, and include `src/**/*.ts` only. Run `npm install --package-lock-only` in the bridge directory to produce the lock file without widening versions.

- [ ] **Step 2: Write failing configuration tests**

Test safe defaults and exact validation:

```ts
const validEnv: NodeJS.ProcessEnv = {
  BRIDGE_API_KEY: "bridge-secret",
  CODEX_MODEL: "gpt-5.3-codex",
  CODEX_HOME: "/var/lib/codex",
};
assert.deepEqual(loadConfig(validEnv), {
  host: "0.0.0.0", port: 8646, apiKey: "bridge-secret",
  publicModel: "supermemory-codex", codexModel: "gpt-5.3-codex",
  codexHome: "/var/lib/codex", codexCwd: "/workspace",
  timeoutMs: 120_000, maxBodyBytes: 2_097_152, maxConcurrency: 2,
});
assert.throws(() => loadConfig({ ...validEnv, BRIDGE_API_KEY: "" }), /BRIDGE_API_KEY/);
assert.throws(() => loadConfig({ ...validEnv, CODEX_MODEL: "" }), /CODEX_MODEL/);
assert.throws(() => loadConfig({ ...validEnv, BRIDGE_MAX_CONCURRENCY: "0" }), /positive integer/);
```

Required environment variables are `BRIDGE_API_KEY`, `CODEX_MODEL`, and `CODEX_HOME`. Optional variables and defaults are `BRIDGE_HOST=0.0.0.0`, `BRIDGE_PORT=8646`, `BRIDGE_PUBLIC_MODEL=supermemory-codex`, `CODEX_CWD=/workspace`, `BRIDGE_TIMEOUT_MS=120000`, `BRIDGE_MAX_BODY_BYTES=2097152`, and `BRIDGE_MAX_CONCURRENCY=2`.

- [ ] **Step 3: Run the configuration test to verify it fails**

Run: `cd services/supermemory-codex-bridge && npm test -- tests/config.test.ts`

Expected: FAIL because `src/config.ts` does not exist.

- [ ] **Step 4: Implement exact parsing**

Define `BridgeConfig`, `ConfigurationError`, an internal `required(name)` helper, and a `positiveInteger(name, fallback)` helper. Trim required strings, reject empty values and non-integer/out-of-range ports, and return exactly the object exercised by the test. Error messages must name the invalid environment variable but never include its value.

- [ ] **Step 5: Verify tests and compile**

Run: `cd services/supermemory-codex-bridge && npm test && npm run build`

Expected: all configuration tests PASS and TypeScript emits no diagnostics.

- [ ] **Step 6: Commit package and configuration**

```bash
git add services/supermemory-codex-bridge
git commit -m "feat: configure dedicated codex bridge"
```

### Task 3: Parse the supported OpenAI request surface and build prompts

**Files:**
- Create: `services/supermemory-codex-bridge/src/openai.ts`
- Create: `services/supermemory-codex-bridge/src/prompt.ts`
- Create: `services/supermemory-codex-bridge/tests/openai.test.ts`
- Create: `services/supermemory-codex-bridge/tests/prompt.test.ts`

**Interfaces:**
- Consumes: JSON-decoded HTTP bodies and configured public model alias.
- Produces: `parseChatCompletionRequest(value: unknown, publicModel: string): ChatCompletionRequest`, `buildCodexInvocation(request: ChatCompletionRequest): CodexInvocation`, `createChatCompletion(model: string, result: CodexResult): ChatCompletionResponse`, and `ApiError`.

- [ ] **Step 1: Write failing parser tests for the accepted surface**

Cover plain string content, OpenAI text parts, role preservation, and structured output:

```ts
const parsed = parseChatCompletionRequest({
  model: "supermemory-codex",
  messages: [
    { role: "system", content: "Return facts." },
    { role: "user", content: [{ type: "text", text: "Ada uses Rust." }] }
  ],
  response_format: {
    type: "json_schema",
    json_schema: { name: "facts", strict: true, schema: { type: "object", properties: { facts: { type: "array" } }, required: ["facts"], additionalProperties: false } }
  }
}, "supermemory-codex");
assert.equal(parsed.messages[1]?.content, "Ada uses Rust.");
assert.deepEqual(parsed.outputSchema, { type: "object", properties: { facts: { type: "array" } }, required: ["facts"], additionalProperties: false });
```

Also assert `response_format: {type: "json_object"}` maps to `{type:"object", additionalProperties:true}` and omitted `response_format` leaves `outputSchema` undefined.

- [ ] **Step 2: Write failing rejection and response-shape tests**

Assert HTTP-facing `ApiError` status/code pairs:

```ts
function assertApiError(action: () => unknown, status: number, code: string): void {
  assert.throws(action, (error: unknown) =>
    error instanceof ApiError && error.status === status && error.code === code
  );
}
assertApiError(() => parseChatCompletionRequest({ model: "other", messages: [] }, alias), 400, "unsupported_model");
assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], stream: true }, alias), 400, "unsupported_streaming");
assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], tools: [{}] }, alias), 400, "unsupported_tools");
assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [{ role: "tool", content: "x" }] }, alias), 400, "unsupported_message");
assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "x" } }] }] }, alias), 400, "unsupported_content");
```

Reject unknown `response_format` types and malformed schemas. Accept `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, and `n: 1` only as compatibility hints; reject `n` other than 1. Do not forward those hints to Codex because app-server has no matching per-turn controls.

Test that `createChatCompletion` returns `object: "chat.completion"`, alias model, one assistant choice, `finish_reason: "stop"`, and token totals; when usage is absent, all three token counters are zero.

- [ ] **Step 3: Run parser tests to verify they fail**

Run: `cd services/supermemory-codex-bridge && npx tsx --test tests/openai.test.ts tests/prompt.test.ts`

Expected: FAIL because the parser and prompt builder are missing.

- [ ] **Step 4: Implement request types and strict parsing**

Define these normalized types and implement structural validation without adding a schema library:

```ts
export type ChatRole = "system" | "developer" | "user" | "assistant";
export interface ChatMessage { role: ChatRole; content: string }
export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  outputSchema?: Record<string, unknown>;
}
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
```

Never include raw message content in thrown errors. `createChatCompletion` generates IDs with `crypto.randomUUID()`, `created` with `Math.floor(Date.now()/1000)`, and usage as `prompt_tokens`, `completion_tokens`, and their sum.

- [ ] **Step 5: Implement deterministic prompt construction**

`buildCodexInvocation` must preserve order and role boundaries with a fixed wrapper:

```text
You are a text inference engine used internally by Supermemory.
Follow the supplied messages exactly. Do not use tools, inspect files, browse, or add commentary.

<message role="system">
Return facts.
</message>
<message role="user">
Ada uses Rust.
</message>
```

Escape `&`, `<`, and `>` in content so a message cannot close the wrapper. Return the normalized `outputSchema` unchanged. Unit-test the exact generated prompt, including XML escaping.

- [ ] **Step 6: Verify parser, prompt, and build**

Run: `cd services/supermemory-codex-bridge && npm test && npm run build`

Expected: all tests PASS; supported and rejected request modes are explicit.

- [ ] **Step 7: Commit the compatibility contract**

```bash
git add services/supermemory-codex-bridge/src services/supermemory-codex-bridge/tests
git commit -m "feat: map chat completions to codex prompts"
```

### Task 4: Implement a robust line-delimited JSON-RPC transport

**Files:**
- Create: `services/supermemory-codex-bridge/src/json-rpc.ts`
- Create: `services/supermemory-codex-bridge/tests/json-rpc.test.ts`

**Interfaces:**
- Consumes: a Node `Readable` for app-server stdout and `Writable` for stdin.
- Produces: `JsonRpcClient.request<T>(method: string, params?: unknown, signal?: AbortSignal): Promise<T>`, `notify(method: string, params?: unknown): void`, `onNotification(listener: (method: string, params: unknown) => void): () => void`, `onRequest(listener: (id: string | number, method: string, params: unknown) => void): () => void`, `respondResult(id: string | number, result: unknown): void`, `respondError(id: string | number, code: number, message: string): void`, and `close(error?: Error): void`.

- [ ] **Step 1: Write failing transport tests using `PassThrough` streams**

Assert:

```ts
async function nextLine(stream: NodeJS.ReadableStream): Promise<string> {
  for await (const chunk of stream) {
    const line = String(chunk).split("\n")[0];
    if (line) return line;
  }
  throw new Error("stream ended before a JSON-RPC line arrived");
}
const request = client.request<{ thread: { id: string } }>("thread/start", { ephemeral: true });
assert.deepEqual(JSON.parse(await nextLine(stdin)), { jsonrpc: "2.0", id: 1, method: "thread/start", params: { ephemeral: true } });
stdout.write('{"jsonrpc":"2.0","id":1,"result":{"thread":{"id":"t1"}}}\n');
assert.equal((await request).thread.id, "t1");
```

Add cases for chunk-split lines, multiple lines in one chunk, notification dispatch, server-initiated request dispatch, JSON-RPC error conversion, cancellation that removes a pending request, malformed JSON closing all pending calls, EOF rejection, and ignoring a late response after cancellation.

- [ ] **Step 2: Run the transport test to verify it fails**

Run: `cd services/supermemory-codex-bridge && npx tsx --test tests/json-rpc.test.ts`

Expected: FAIL because `JsonRpcClient` is missing.

- [ ] **Step 3: Implement the transport**

Maintain a monotonically increasing numeric ID and maps of pending requests. Buffer stdout until newline; cap an individual protocol line at 4 MiB. Classify messages by `id` plus `result/error`, `method` without `id`, and `method` with `id`. Serialize one compact JSON object plus `\n`; never log raw protocol payloads. `close()` detaches listeners and rejects all pending promises with the same sanitized error.

- [ ] **Step 4: Verify transport isolation**

Run: `cd services/supermemory-codex-bridge && npm test && npm run build`

Expected: all tests PASS, including cancellation and malformed-input cases.

- [ ] **Step 5: Commit JSON-RPC transport**

```bash
git add services/supermemory-codex-bridge/src/json-rpc.ts services/supermemory-codex-bridge/tests/json-rpc.test.ts
git commit -m "feat: add codex app server transport"
```

### Task 5: Orchestrate the persistent Codex app-server safely

**Files:**
- Create: `services/supermemory-codex-bridge/src/codex-app-server.ts`
- Create: `services/supermemory-codex-bridge/tests/codex-app-server.test.ts`

**Interfaces:**
- Consumes: `BridgeConfig`, `JsonRpcClient`, `CodexInvocation`, and an injectable child-process factory.
- Produces: `CodexAppServer implements CodexRunner`, plus `CodexUpstreamError` with stable kinds `authentication`, `rate_limit`, `timeout`, `structured_output`, `forbidden_tool`, `unavailable`, and `upstream`.

- [ ] **Step 1: Write failing startup and success-flow tests with a fake RPC peer**

Assert the child command is `codex app-server --listen stdio://`, with `CODEX_HOME` set to the dedicated configured path and no API key env injection. Startup must send:

```json
{"method":"initialize","params":{"clientInfo":{"name":"supermemory-codex-bridge","title":"Supermemory Codex Bridge","version":"0.1.0"},"capabilities":{"experimentalApi":false}}}
{"method":"initialized"}
```

For each `run`, assert exact thread settings:

```ts
await rpc.request("thread/start", {
  model: config.codexModel,
  cwd: config.codexCwd,
  approvalPolicy: "never",
  sandbox: "readOnly",
  personality: "none",
  ephemeral: true,
  selectedCapabilityRoots: [],
  dynamicTools: [],
});
await rpc.request("turn/start", {
  threadId: "thread-1",
  input: [{ type: "text", text: invocation.prompt }],
  outputSchema: invocation.outputSchema,
});
```

Feed `item/completed` for an `agentMessage`, then `turn/completed`, and assert `run()` returns only the final agent text and mapped input/output token counts. Invoke twice and assert two distinct `thread/start` calls.

- [ ] **Step 2: Write failing policy and failure-mapping tests**

Cover all of these concrete cases:

- `commandExecution`, `fileChange`, `mcpToolCall`, `collabToolCall`, or `webSearch` item events cause `turn/interrupt` and `CodexUpstreamError("forbidden_tool")`;
- server requests whose method includes `requestApproval` receive a JSON-RPC error response and cause interruption;
- caller abort sends `turn/interrupt` once and returns `timeout` when the abort reason is the request deadline;
- process exit rejects the active request as `unavailable`, then a later `run` starts and initializes one replacement process;
- app-server errors mentioning login/credentials, 429/rate limit, and output-schema validation map to the stable kinds above without copying secrets into messages;
- concurrent `run` calls route notifications by `threadId`/`turnId` and cannot exchange outputs.

- [ ] **Step 3: Run orchestration tests to verify they fail**

Run: `cd services/supermemory-codex-bridge && npx tsx --test tests/codex-app-server.test.ts`

Expected: FAIL because `CodexAppServer` does not exist.

- [ ] **Step 4: Implement app-server lifecycle and event routing**

Spawn with stdio pipes, route stderr through a line-based sanitizer that records only event category and exit status, and serialize startup behind one promise. Keep per-turn state in a map keyed by returned turn ID with thread ID as a fallback during early events. Collect the latest completed `agentMessage`; resolve only after `turn/completed` reports success. If the process dies, clear its client/start promise so exactly one lazy restart is possible.

For a forbidden item, immediately mark the turn failed before sending `turn/interrupt`, so late events cannot turn the result into success. Implement `close()` to interrupt active turns, close RPC, send `SIGTERM`, wait 5 seconds, then `SIGKILL` only if necessary.

- [ ] **Step 5: Implement server-request denial**

Extend `JsonRpcClient` with `respondResult(id, result)` and `respondError(id, code, message)`. For every server-initiated request, return JSON-RPC code `-32601` and message `Interactive requests are disabled`; never auto-approve. Test the exact response written to stdin.

- [ ] **Step 6: Verify app-server policy and compile**

Run: `cd services/supermemory-codex-bridge && npm test && npm run build`

Expected: all lifecycle, isolation, forbidden-tool, approval, and restart tests PASS.

- [ ] **Step 7: Commit persistent Codex orchestration**

```bash
git add services/supermemory-codex-bridge/src services/supermemory-codex-bridge/tests
git commit -m "feat: run isolated codex app server threads"
```

### Task 6: Expose the authenticated private HTTP bridge

**Files:**
- Create: `services/supermemory-codex-bridge/src/server.ts`
- Create: `services/supermemory-codex-bridge/src/index.ts`
- Create: `services/supermemory-codex-bridge/tests/server.test.ts`

**Interfaces:**
- Consumes: `BridgeConfig`, `CodexRunner`, parser/prompt/response helpers.
- Produces: `createBridgeServer(config: BridgeConfig, codex: CodexRunner): http.Server` and the production lifecycle entrypoint.

- [ ] **Step 1: Write failing HTTP contract tests with a fake runner**

Listen on an ephemeral local port and assert:

- `GET /healthz` returns 503 until `codex.start()` succeeds, then 200 with `{"status":"ok"}`;
- all `/v1/*` calls without `Authorization: Bearer <BRIDGE_API_KEY>` return 401 and do not call the runner;
- key comparison uses `crypto.timingSafeEqual` over equal-length buffers;
- `POST /v1/chat/completions` returns the conventional response from Task 3;
- wrong method/path returns stable 404/405 JSON errors;
- invalid JSON returns 400 `invalid_json`;
- a body larger than `maxBodyBytes` returns 413 `body_too_large` and destroys/drains the request safely;
- the third simultaneous request waits while the configured two permits are occupied;
- after `timeoutMs`, the request signal aborts and the response is 504 `codex_timeout`;
- client disconnect aborts the runner but attempts no response write.

- [ ] **Step 2: Write failing stable error-mapping tests**

Map `CodexUpstreamError` kinds exactly:

```text
authentication     -> 503 codex_authentication_required
rate_limit         -> 429 codex_rate_limited
timeout            -> 504 codex_timeout
structured_output  -> 502 codex_structured_output_error
forbidden_tool      -> 502 codex_policy_violation
unavailable         -> 503 codex_unavailable
upstream            -> 502 codex_upstream_error
```

Error bodies use `{error:{message, type:"codex_bridge_error", code}}`; test that supplied secrets and raw prompts never appear in bodies or captured logs.

- [ ] **Step 3: Run HTTP tests to verify they fail**

Run: `cd services/supermemory-codex-bridge && npx tsx --test tests/server.test.ts`

Expected: FAIL because the server factory is missing.

- [ ] **Step 4: Implement HTTP handling, semaphore, and deadlines**

Use Node's built-in `http` module. Set `server.requestTimeout = timeoutMs + 5_000`, `headersTimeout = 10_000`, and `keepAliveTimeout = 5_000`. Implement a FIFO semaphore whose queued acquisition is also abortable. Read bodies as buffers while counting bytes. Never log headers, bodies, prompts, or responses; normal logs contain only generated request ID, route, status, duration, and stable error code.

- [ ] **Step 5: Implement the production lifecycle**

In `src/index.ts`, load config, create/start `CodexAppServer`, create the HTTP server, and listen only after Codex initialization succeeds. On `SIGTERM` or `SIGINT`, stop accepting HTTP, wait up to 30 seconds for in-flight requests, call `codex.close()`, and exit. Startup failures must emit only the error kind and exit nonzero.

- [ ] **Step 6: Verify the complete bridge package**

Run: `cd services/supermemory-codex-bridge && npm test && npm run build`

Expected: all tests PASS; build output contains `dist/src/index.js` and no test files are needed by production.

- [ ] **Step 7: Commit the HTTP bridge**

```bash
git add services/supermemory-codex-bridge/src services/supermemory-codex-bridge/tests
git commit -m "feat: expose private chat completions bridge"
```

### Task 7: Containerize Codex with a dedicated tool-free runtime

**Files:**
- Create: `services/supermemory-codex-bridge/.dockerignore`
- Create: `services/supermemory-codex-bridge/Dockerfile`
- Create: `deploy/supermemory/codex-config.toml`

**Interfaces:**
- Consumes: bridge package from Tasks 2–6.
- Produces: image `hephaistos-supermemory-codex-bridge:test`, port `8646`, `codex` CLI on `PATH`, persistent `/var/lib/codex`.

- [ ] **Step 1: Add a failing container smoke assertion**

Extend the bridge package's verification command with:

```bash
docker build -f services/supermemory-codex-bridge/Dockerfile \
  -t hephaistos-supermemory-codex-bridge:test services/supermemory-codex-bridge
docker run --rm --entrypoint codex hephaistos-supermemory-codex-bridge:test --version | grep -F 'codex-cli 0.146.0'
docker inspect hephaistos-supermemory-codex-bridge:test \
  --format '{{json .Config.ExposedPorts}} {{.Config.User}}' | grep -F '8646/tcp'
```

Run it before the Dockerfile exists and expect failure.

- [ ] **Step 2: Create the multi-stage bridge Dockerfile**

Use the pinned Node base in every stage. `npm ci`, run `npm test` and `npm run build` in the build stage, then install production dependencies only. Copy `dist/src`, `node_modules`, and `package.json` into the runtime image; symlink `/app/node_modules/.bin/codex` to `/usr/local/bin/codex`. Create `/workspace` and `/var/lib/codex`, chown both to `node`, set `CODEX_HOME=/var/lib/codex`, run as `node`, expose 8646, and add a Node-based `/healthz` healthcheck. Entrypoint is `node /app/dist/src/index.js`.

- [ ] **Step 3: Create the dedicated Codex config**

Create `deploy/supermemory/codex-config.toml` with no provider override and explicit tool removal:

```toml
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
personality = "none"

[features]
shell_tool = false
unified_exec = false
apply_patch_freeform = false
web_search = false
web_search_cached = false
web_search_request = false
standalone_web_search = false
tool_search = false
collab = false
```

Do not add MCP servers, skills, plugins, workspace instructions, model providers, or API keys. The runtime event interdiction from Task 5 remains mandatory defense in depth even if Codex ignores an unknown feature key.

- [ ] **Step 4: Verify image, ownership, and CLI version**

Run the smoke commands from Step 1 plus:

```bash
docker run --rm --entrypoint sh hephaistos-supermemory-codex-bridge:test \
  -c 'test -w /var/lib/codex && test -w /workspace && test "$(id -un)" = node'
```

Expected: all commands PASS.

- [ ] **Step 5: Commit bridge image and policy**

```bash
git add services/supermemory-codex-bridge deploy/supermemory/codex-config.toml
git commit -m "build: isolate codex bridge runtime"
```

### Task 8: Compose the private backend and exact Traefik routes

**Files:**
- Create: `deploy/supermemory/compose.yaml`
- Create: `deploy/supermemory/.env.example`
- Create: `deploy/supermemory/tests/test-compose-config.sh`

**Interfaces:**
- Consumes: both Dockerfiles, external network `traefik_default`, runtime env file.
- Produces: Compose services `codex-bridge` and `supermemory-server`, volumes `codex_home` and `supermemory_data`, private network `backend`.

- [ ] **Step 1: Write a failing rendered-config test**

The test creates a mode-600 temporary env file with inert test values, renders `docker compose --env-file "$env_file" -f deploy/supermemory/compose.yaml config --format json`, and asserts with a small inline Node expression:

- `codex-bridge.ports` is absent/empty and it joins only `backend`;
- `supermemory-server.ports` is absent/empty and it joins `backend` plus `traefik_default`;
- both services have `restart: unless-stopped`, health checks, and JSON-file rotation `max-size=10m`, `max-file=3`;
- Supermemory's `OPENAI_BASE_URL` is `http://codex-bridge:8646/v1`, all three model variables use `supermemory-codex`, and `SUPERMEMORY_SKIP_EMBEDDING_PREWARM=true`;
- bridge receives `BRIDGE_API_KEY`, not `OPENAI_API_KEY`;
- only Supermemory has Traefik labels;
- HTTPS routers include `sm-web`, `sm-docs`, and `sm-api`; priorities are respectively 10, 300, and 200;
- `sm-web` and `sm-docs` use `sm-basic-auth,sm-backend-key`; `sm-api` has no auth/header middleware;
- the service port label is `6767` and TLS resolver is `le`.

- [ ] **Step 2: Run the topology test to verify it fails**

Run: `bash deploy/supermemory/tests/test-compose-config.sh`

Expected: FAIL because `compose.yaml` does not exist.

- [ ] **Step 3: Implement services, dependencies, volumes, and networks**

Define `codex-bridge` with the bridge Dockerfile, `expose: ["8646"]`, the `codex_home` volume at `/var/lib/codex`, a read-only bind of `codex-config.toml` at `/var/lib/codex/config.toml`, and only `backend`. Keep the image's empty `/workspace` directory unmounted; the read-only root filesystem makes it immutable. Define Supermemory with `server.Dockerfile`, `expose: ["6767"]`, `supermemory_data:/var/lib/supermemory`, both networks, and `depends_on.codex-bridge.condition: service_healthy`. Do not add host `ports`.

Use `read_only: true`, `tmpfs: [/tmp]`, `security_opt: [no-new-privileges:true]`, and drop all Linux capabilities on the bridge. Keep Supermemory's root filesystem read-only only if the Task 1 image probe confirms all writes stay in its data volume and `/tmp`; otherwise document the exact required writable mount instead of weakening the bridge.

- [ ] **Step 4: Implement exact Traefik labels**

Use these logical rules and priorities:

```text
sm-http: Host(`persephone.cc`) on web -> sm-https-redirect
sm-web: Host(`persephone.cc`) on websecure, priority 10 -> sm-basic-auth,sm-backend-key
sm-docs: Host AND (PathPrefix(`/v4/reference`) OR PathPrefix(`/v4/openapi`)), priority 300 -> sm-basic-auth,sm-backend-key
sm-api: Host AND (PathPrefix(`/v3`) OR PathPrefix(`/v4`) OR PathPrefix(`/files`)), priority 200 -> no auth/header middleware
```

Configure `sm-basic-auth.basicauth.users=${SUPERMEMORY_BASIC_AUTH_USERS}` and `.removeheader=true`. Configure `sm-backend-key.headers.customrequestheaders.Authorization=Bearer ${SUPERMEMORY_API_KEY}`. All HTTPS routers set `tls=true`, `tls.certresolver=le`, and service `sm-service`; `sm-service.loadbalancer.server.port=6767`.

- [ ] **Step 5: Add a secret-free environment template**

Create `.env.example` containing names and safe non-secret defaults only:

```dotenv
CODEX_MODEL=gpt-5.3-codex
SUPERMEMORY_BASIC_AUTH_USERS=
SUPERMEMORY_API_KEY=
SUPERMEMORY_BRIDGE_API_KEY=
```

Ensure the real deployment file name `.env.runtime` is ignored by an existing ignore rule or add the narrow line `deploy/supermemory/.env.runtime` to the repository `.gitignore`.

- [ ] **Step 6: Verify rendered routing and Docker isolation**

Run: `bash deploy/supermemory/tests/test-compose-config.sh`

Expected: PASS with no secret value printed. Also run `docker compose --env-file <temporary-test-env> -f deploy/supermemory/compose.yaml config --quiet` and expect exit 0.

- [ ] **Step 7: Commit Compose topology**

```bash
git add deploy/supermemory/compose.yaml deploy/supermemory/.env.example deploy/supermemory/tests/test-compose-config.sh .gitignore
git commit -m "deploy: route supermemory through traefik"
```

### Task 9: Add secret-safe bootstrap and acceptance scripts

**Files:**
- Create: `deploy/supermemory/scripts/bootstrap.sh`
- Create: `deploy/supermemory/scripts/smoke.sh`

**Interfaces:**
- Consumes: a terminal operator, Docker Compose, persisted volumes, DNS configured by the user.
- Produces: mode-600 `.env.runtime`, BasicAuth hash, bridge key, dedicated Codex login, discovered Supermemory API key, and a repeatable acceptance command.

- [ ] **Step 1: Implement bootstrap preflight without secret output**

`bootstrap.sh` must use `set -euo pipefail`, resolve its own deployment directory, require an interactive TTY, check Docker/Compose, verify `traefik_default`, and refuse to overwrite an existing `.env.runtime` unless invoked with `--resume`. Check `getent ahostsv4 persephone.cc`; print the observed address and a clear warning if it is not `162.19.229.31`, but allow bootstrap to continue because DNS can propagate while local services are prepared.

- [ ] **Step 2: Generate the two deployment credentials safely**

Prompt for the BasicAuth password with `read -r -s` so it never appears in argv or logs. Generate the bcrypt entry by piping the password to a disposable `httpd:2.4-alpine` container running `htpasswd -niB titagram`; unset the plaintext variable immediately. Generate `SUPERMEMORY_BRIDGE_API_KEY` with `openssl rand -hex 32`. Write `.env.runtime` with `umask 077` and `chmod 600`; initially set `SUPERMEMORY_API_KEY=sm_bootstrap_pending`. Do not enable shell tracing.

- [ ] **Step 3: Build images and authenticate the dedicated Codex volume**

Run the image/config tests, then build both services. Copy `codex-config.toml` into the named `codex_home` volume through a one-shot container only if the read-only bind design in Task 8 does not expose it correctly. Authenticate the dedicated volume with:

```bash
docker compose --env-file .env.runtime -f compose.yaml run --rm \
  --entrypoint codex codex-bridge login --device-auth
```

After login, run `codex login status` in the same one-shot service and fail unless it reports authenticated. This uses Codex subscription/device authentication; do not request or store an OpenAI Platform API key.

- [ ] **Step 4: Start privately and discover Supermemory's generated key**

Start `codex-bridge` and wait for its Docker health state. Start Supermemory; capture its logs into a shell variable, not stdout, and extract exactly one token matching `sm_[A-Za-z0-9_-]+`. Fail if zero or multiple distinct candidates are found. Replace only the `SUPERMEMORY_API_KEY=` line in `.env.runtime` using a temporary mode-600 file and atomic `mv`, then recreate Supermemory so the Traefik header middleware receives the real key. Explicitly unset shell key variables.

On `--resume`, preserve existing keys, re-run Codex status, and use `docker compose up -d --build` without regenerating credentials.

- [ ] **Step 5: Implement the smoke script with no embedded secrets**

`smoke.sh` reads `.env.runtime` after checking mode 600, accepts `--local` or `--public`, and uses a trap to remove temporary response files. It must assert:

```text
local: codex-bridge and supermemory-server healthy
local: Supermemory root HTML contains "supermemory · local"
local: bridge has no HostConfig.PortBindings and no Traefik labels
public: HTTP redirects to HTTPS
public: unauthenticated / returns 401 with WWW-Authenticate: Basic
public: unauthenticated /v4/reference returns the same Basic challenge
public: /v4/memories (or another documented API endpoint) without Bearer returns 401/403, never a Basic challenge
public: TLS certificate covers persephone.cc
```

Prompt for the BasicAuth password during public UI tests rather than storing it. Read the Supermemory API key from `.env.runtime` for API calls, suppress curl command echo, and redact response bodies on failure unless a `--debug-safe` flag is set.

- [ ] **Step 6: Shell-check behavior and dry-run failure paths**

Run `bash -n deploy/supermemory/scripts/bootstrap.sh deploy/supermemory/scripts/smoke.sh`. Exercise bootstrap with a temporary mocked `PATH` for missing Docker, missing network, non-TTY, existing env without `--resume`, and DNS mismatch; assert each produces the documented status without revealing test secrets.

- [ ] **Step 7: Commit operational scripts**

```bash
git add deploy/supermemory/scripts
git commit -m "ops: bootstrap dedicated supermemory deployment"
```

### Task 10: Deploy and prove Milestone 1 end to end

**Files:**
- Create: `deploy/supermemory/README.md`
- Runtime only, never commit: `deploy/supermemory/.env.runtime`

**Interfaces:**
- Consumes: Tasks 1–9, DNS A record, BasicAuth password supplied interactively, Codex device login.
- Produces: working `https://persephone.cc`, persisted Supermemory data, and recorded non-secret acceptance evidence.

- [ ] **Step 1: Document exact operator workflow and rollback**

The README must contain:

```bash
cd deploy/supermemory
./scripts/bootstrap.sh
./scripts/smoke.sh --local
./scripts/smoke.sh --public
docker compose --env-file .env.runtime -f compose.yaml logs --tail=100
docker compose --env-file .env.runtime -f compose.yaml restart
```

Document that DNS A `persephone.cc` must be `162.19.229.31`, no AAAA record should point elsewhere, the BasicAuth username is `titagram`, credentials live only in `.env.runtime`/`codex_home`, and backups must cover `supermemory_data` plus `codex_home`. Rollback is `docker compose down` without `-v`; explicitly warn that `down -v` destroys persistent memory/auth and is not part of rollback.

- [ ] **Step 2: Run all local quality gates before deployment**

Run:

```bash
cd services/supermemory-codex-bridge && npm ci && npm test && npm run build
cd ../.. && bash deploy/supermemory/tests/test-server-image.sh
bash deploy/supermemory/tests/test-compose-config.sh
bash -n deploy/supermemory/scripts/*.sh
git diff --check
```

Expected: every command passes. Inspect `git status --short` and confirm `.env.runtime`, Codex auth, hashes, and generated keys are absent.

- [ ] **Step 3: Confirm or wait for DNS, then bootstrap**

Run `getent ahostsv4 persephone.cc` and continue to public TLS only when it returns `162.19.229.31`. Run `deploy/supermemory/scripts/bootstrap.sh`, complete the device login, and verify both containers become healthy. Confirm `docker port` reports no published bindings for either project container and `docker inspect` shows only Supermemory attached to `traefik_default`.

- [ ] **Step 4: Validate web and API authentication boundaries**

Run `smoke.sh --public`. In a browser, confirm `/` displays Traefik's BasicAuth popup and then the built-in `supermemory · local` UI. Confirm `/v4/reference` and `/v4/openapi` work after BasicAuth. With curl, confirm a generic `/v4` endpoint receives native Bearer behavior and never accepts the Basic credential as API authorization.

- [ ] **Step 5: Trace one real ingestion without content logging**

Generate a unique marker such as `persephone-memory-<UTC timestamp>`. Call the documented add endpoint from `/v4/openapi` with `Authorization: Bearer $SUPERMEMORY_API_KEY`, a short sentence containing the marker, and a test container tag. While it runs, record only bridge request ID, accepted request field names/types, status, duration, and Codex error category; do not record the sentence, generated output, headers, or key.

If the trace reveals an unsupported but necessary Chat Completions field, stop deployment acceptance, add one failing unit test to Task 3's parser suite, implement the smallest explicit mapping, and rerun all bridge tests before retrying. Never silently drop tools, images, streaming, multiple choices, or an unknown response format.

- [ ] **Step 6: Poll processing and prove retrieval**

Use the document/status endpoint shown by the deployed OpenAPI reference until processing reports complete or a 5-minute deadline expires. Search using the same container tag and unique marker. Assert at least one returned document or extracted memory contains/references the marker. Inspect sanitized bridge logs to prove Codex handled the request and inspect environment/container config to prove no `OPENAI_API_KEY` and no Hermes endpoint were used.

- [ ] **Step 7: Prove persistence across restart**

Run `docker compose --env-file .env.runtime -f compose.yaml restart`, wait for both health checks, rerun the marker search, and assert the same result remains available. Re-run `codex login status` inside the bridge service to prove authentication persisted in `codex_home`.

- [ ] **Step 8: Record non-secret acceptance evidence and commit documentation**

Append to the README a dated checklist containing only PASS/FAIL, image/tag/checksum, container image IDs, DNS IP, TLS issuer/expiry, restart result, and the hashed or truncated test marker. Do not include keys, htpasswd hashes, auth tokens, document text, or model output.

```bash
git add deploy/supermemory/README.md
git commit -m "docs: operate supermemory codex deployment"
```

- [ ] **Step 9: Mark Milestone 1 complete only after all ten design checks pass**

The completion note must state: built-in UI confirmed; HTTPS and authentication boundary confirmed; bridge private; document added; Codex extraction observed; search succeeded; restart persistence succeeded; Hermes/Hades untouched. If any statement is unproven, leave the milestone open and report the exact failed check.

## Milestone 2 handoff (not implemented in this plan)

After Milestone 1 passes, create a separate spec and plan for `plugins/memory/supermemory`. That work must introduce one configurable Supermemory base URL used by both the SDK client and conversations request, preserve the current cloud default, and test both cloud and `https://persephone.cc`. It must not alter Hermes's LLM providers; the dedicated Codex adapter remains solely behind Supermemory.

## Reference material

- Approved design: `docs/superpowers/specs/2026-08-02-supermemory-codex-bridge-design.md`
- Supermemory server release: `https://github.com/supermemoryai/supermemory/releases/tag/server-v0.0.6`
- Codex app-server protocol: `https://developers.openai.com/codex/app-server/`
- Codex authentication: `https://developers.openai.com/codex/auth/`
- Traefik BasicAuth middleware: `https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/basicauth/`
- Traefik Headers middleware: `https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/headers/`

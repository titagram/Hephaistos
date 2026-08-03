import assert from "node:assert/strict";
import test from "node:test";

import {
  ApiError,
  createChatCompletion,
  parseChatCompletionRequest,
} from "../src/openai.js";

const alias = "supermemory-codex";

function assertApiError(action: () => unknown, status: number, code: string): void {
  assert.throws(action, (error: unknown) =>
    error instanceof ApiError && error.status === status && error.code === code,
  );
}

test("parses string and text-part messages while preserving supported roles", () => {
  const parsed = parseChatCompletionRequest({
    model: alias,
    messages: [
      { role: "system", content: "Return facts." },
      { role: "developer", content: "Use concise language." },
      { role: "user", content: [{ type: "text", text: "Ada uses Rust." }] },
      { role: "assistant", content: "Noted." },
    ],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "facts",
        strict: true,
        schema: {
          type: "object",
          properties: { facts: { type: "array" } },
          required: ["facts"],
          additionalProperties: false,
        },
      },
    },
  }, alias);

  assert.deepEqual(parsed.messages, [
    { role: "system", content: "Return facts." },
    { role: "developer", content: "Use concise language." },
    { role: "user", content: "Ada uses Rust." },
    { role: "assistant", content: "Noted." },
  ]);
  assert.deepEqual(parsed.outputSchema, {
    type: "object",
    properties: { facts: { type: "array" } },
    required: ["facts"],
    additionalProperties: false,
  });
});

test("maps json_object output and omits schemas when no response format is supplied", () => {
  assert.deepEqual(
    parseChatCompletionRequest({ model: alias, messages: [], response_format: { type: "json_object" } }, alias)
      .outputSchema,
    { type: "object", additionalProperties: true },
  );
  assert.equal(parseChatCompletionRequest({ model: alias, messages: [] }, alias).outputSchema, undefined);
});

test("accepts documented compatibility hints without forwarding them", () => {
  const parsed = parseChatCompletionRequest({
    model: alias,
    messages: [],
    temperature: 0.2,
    top_p: 0.8,
    max_tokens: 50,
    max_completion_tokens: 50,
    n: 1,
    serviceTier: "flex",
    service_tier: "flex",
  }, alias);

  assert.deepEqual(parsed, {
    model: alias,
    messages: [],
    outputSchema: undefined,
    tools: [],
    toolChoice: "none",
  });
});

test("parses symbolic tools and correlated assistant/tool history", () => {
  const parsed = parseChatCompletionRequest({
    model: alias,
    messages: [
      { role: "user", content: "Remember Ada." },
      {
        role: "assistant",
        content: null,
        tool_calls: [{
          id: "call_1",
          type: "function",
          function: { name: "add_memory", arguments: "{\"memory\":\"Ada\"}" },
        }],
      },
      { role: "tool", tool_call_id: "call_1", content: "{\"stored\":true}" },
    ],
    tools: [{
      type: "function",
      function: {
        name: "add_memory",
        description: "Store a memory",
        strict: false,
        parameters: {
          type: "object",
          properties: { memory: { type: "string" } },
          required: ["memory"],
          additionalProperties: false,
        },
      },
    }],
    tool_choice: "auto",
  }, alias);

  assert.equal(parsed.toolChoice, "auto");
  assert.equal(parsed.tools[0]?.name, "add_memory");
  assert.equal(parsed.tools[0]?.strict, false);
  assert.equal(parsed.messages[1]?.role, "assistant");
  assert.equal(parsed.messages[2]?.role, "tool");
});

test("rejects malformed tool protocol without silently dropping fields", () => {
  const tool = { type: "function", function: { name: "remember", parameters: { type: "object" } } };
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    tools: [tool, tool],
  }, alias), 400, "invalid_tools");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    tools: [{ ...tool, caller_secret: true }],
  }, alias), 400, "invalid_tools");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [{
      role: "assistant",
      content: null,
      tool_calls: [{ id: "call_1", type: "function", function: { name: "remember", arguments: "{}" } }],
    }],
    tools: [tool],
  }, alias), 400, "invalid_tool_history");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [{ role: "tool", tool_call_id: "missing", content: "x" }],
    tools: [tool],
  }, alias), 400, "invalid_tool_history");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    serviceTier: "priority",
  }, alias), 400, "invalid_request");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    service_tier: "priority",
  }, alias), 400, "invalid_request");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    serviceTier: "flex",
    service_tier: "default",
  }, alias), 400, "invalid_request");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [
      {
        role: "assistant",
        content: null,
        tool_calls: [{ id: "call_1", type: "function", function: { name: "remember", arguments: "{}" } }],
      },
      { role: "tool", tool_call_id: "call_1", content: "not validated" },
    ],
    tools: [{ type: "function", function: {
      name: "remember",
      strict: true,
      parameters: {
        type: "object",
        properties: { memory: { type: "string" } },
        required: ["memory"],
        additionalProperties: false,
      },
    } }],
  }, alias), 400, "invalid_tools");
});

test("rejects unsupported models, modes, and message content", () => {
  assertApiError(() => parseChatCompletionRequest({ model: "other", messages: [] }, alias), 400, "unsupported_model");
  assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], stream: true }, alias), 400, "unsupported_streaming");
  assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], tools: [{}] }, alias), 400, "invalid_tools");
  assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], n: 2 }, alias), 400, "unsupported_n");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [{ role: "tool", content: "x" }],
  }, alias), 400, "invalid_tool_history");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "x" } }] }],
  }, alias), 400, "unsupported_content");
});

test("rejects unlisted and tool-related top-level fields", () => {
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    metadata: { internal: true },
  }, alias), 400, "unsupported_field");
  for (const field of ["parallel_tool_calls", "function_call", "functions"]) {
    assertApiError(() => parseChatCompletionRequest({
      model: alias,
      messages: [],
      [field]: "unsupported",
    }, alias), 400, "unsupported_tools");
  }
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    stream_options: { include_usage: true },
  }, alias), 400, "unsupported_streaming");
});

test("rejects malformed assistant tool metadata instead of dropping it", () => {
  for (const field of ["tool_calls", "function_call"]) {
    assertApiError(() => parseChatCompletionRequest({
      model: alias,
      messages: [{ role: "assistant", content: "private response", [field]: [] }],
    }, alias), 400, "invalid_tool_history");
  }
});

test("rejects unknown response formats and malformed JSON schemas", () => {
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    response_format: { type: "text" },
  }, alias), 400, "unsupported_response_format");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [],
    response_format: { type: "json_schema", json_schema: { name: "facts", schema: [] } },
  }, alias), 400, "invalid_response_format");
});

test("returns an OpenAI chat completion with usage from the Codex result", () => {
  const request = parseChatCompletionRequest({ model: alias, messages: [] }, alias);
  const completion = createChatCompletion(request, {
    text: '{"facts":["Ada uses Rust."]}',
    usage: { inputTokens: 12, outputTokens: 7 },
  });

  assert.equal(completion.object, "chat.completion");
  assert.equal(completion.model, alias);
  assert.match(completion.id, /^chatcmpl-/);
  assert.equal(typeof completion.created, "number");
  assert.deepEqual(completion.choices, [{
    index: 0,
    message: { role: "assistant", content: '{"facts":["Ada uses Rust."]}' },
    finish_reason: "stop",
  }]);
  assert.deepEqual(completion.usage, {
    prompt_tokens: 12,
    completion_tokens: 7,
    total_tokens: 19,
  });
});

test("reports zero usage when Codex does not provide token counts", () => {
  const request = parseChatCompletionRequest({ model: alias, messages: [] }, alias);
  assert.deepEqual(createChatCompletion(request, { text: "ok" }).usage, {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  });
});

test("maps validated symbolic tool results to OpenAI tool calls", () => {
  const request = parseChatCompletionRequest({
    model: alias,
    messages: [{ role: "user", content: "Remember Ada." }],
    tools: [{ type: "function", function: {
      name: "add_memory",
      parameters: { type: "object", properties: { memory: { type: "string" } }, required: ["memory"] },
    } }],
    tool_choice: "required",
  }, alias);
  const completion = createChatCompletion(request, {
    text: JSON.stringify({ content: "", tool_calls: [{ name: "add_memory", arguments: "{\"memory\":\"Ada\"}" }] }),
  });

  assert.equal(completion.choices[0]?.finish_reason, "tool_calls");
  assert.equal(completion.choices[0]?.message.content, null);
  assert.match(completion.choices[0]?.message.tool_calls?.[0]?.id ?? "", /^call_/);
  assert.deepEqual(completion.choices[0]?.message.tool_calls?.[0]?.function, {
    name: "add_memory",
    arguments: "{\"memory\":\"Ada\"}",
  });
  const finalAnswer = createChatCompletion({ ...request, toolChoice: "auto" }, {
    text: JSON.stringify({ content: "Nothing to store.", tool_calls: [] }),
  });
  assert.deepEqual(finalAnswer.choices[0], {
    index: 0,
    message: { role: "assistant", content: "Nothing to store." },
    finish_reason: "stop",
  });
  assertApiError(() => createChatCompletion(request, { text: "not json" }), 502, "codex_structured_output_error");
  assertApiError(() => createChatCompletion(request, {
    text: JSON.stringify({ content: "", tool_calls: [{ name: "unknown", arguments: "{}" }] }),
  }), 502, "codex_structured_output_error");
  for (const text of [
    JSON.stringify({ content: "", tool_calls: [], extra: "secret" }),
    JSON.stringify({ content: "", tool_calls: [{ name: "add_memory", arguments: "{}", extra: true }] }),
    JSON.stringify({ content: "must not be dropped", tool_calls: [{ name: "add_memory", arguments: "{}" }] }),
  ]) {
    assertApiError(() => createChatCompletion({ ...request, toolChoice: "auto" }, { text }), 502, "codex_structured_output_error");
  }
});

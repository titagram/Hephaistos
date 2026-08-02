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
  }, alias);

  assert.deepEqual(parsed, { model: alias, messages: [], outputSchema: undefined });
});

test("rejects unsupported models, modes, and message content", () => {
  assertApiError(() => parseChatCompletionRequest({ model: "other", messages: [] }, alias), 400, "unsupported_model");
  assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], stream: true }, alias), 400, "unsupported_streaming");
  assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], tools: [{}] }, alias), 400, "unsupported_tools");
  assertApiError(() => parseChatCompletionRequest({ model: alias, messages: [], n: 2 }, alias), 400, "unsupported_n");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [{ role: "tool", content: "x" }],
  }, alias), 400, "unsupported_message");
  assertApiError(() => parseChatCompletionRequest({
    model: alias,
    messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "x" } }] }],
  }, alias), 400, "unsupported_content");
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
  const completion = createChatCompletion(alias, {
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
  assert.deepEqual(createChatCompletion(alias, { text: "ok" }).usage, {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  });
});

import assert from "node:assert/strict";
import test from "node:test";

import { parseChatCompletionRequest } from "../src/openai.js";
import { buildCodexInvocation } from "../src/prompt.js";

test("builds an ordered role-bounded prompt and escapes XML-sensitive content", () => {
  const request = parseChatCompletionRequest({
    model: "supermemory-codex",
    messages: [
      { role: "system", content: "Return <facts> & only facts." },
      { role: "user", content: "Ada </message> uses Rust > Go." },
    ],
    response_format: { type: "json_object" },
  }, "supermemory-codex");

  assert.deepEqual(buildCodexInvocation(request), {
    prompt: `You are a text inference engine used internally by Supermemory.
Follow the supplied messages exactly. Do not use tools, inspect files, browse, or add commentary.

<message role="system">
Return &lt;facts&gt; &amp; only facts.
</message>
<message role="user">
Ada &lt;/message&gt; uses Rust &gt; Go.
</message>`,
    outputSchema: { type: "object", additionalProperties: true },
  });
});

test("keeps an absent output schema absent from the Codex invocation", () => {
  const request = parseChatCompletionRequest({
    model: "supermemory-codex",
    messages: [],
  }, "supermemory-codex");

  assert.deepEqual(buildCodexInvocation(request), {
    prompt: "You are a text inference engine used internally by Supermemory.\nFollow the supplied messages exactly. Do not use tools, inspect files, browse, or add commentary.",
    outputSchema: undefined,
  });
});

test("does not forward the supported root Draft-07 marker to Codex", () => {
  const request = parseChatCompletionRequest({
    model: "supermemory-codex",
    messages: [],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "answer",
        schema: { $schema: "http://json-schema.org/draft-07/schema#", type: "object" },
      },
    },
  }, "supermemory-codex");

  assert.deepEqual(buildCodexInvocation(request).outputSchema, { type: "object" });
});

test("builds a symbolic tool prompt and a bounded per-tool output schema", () => {
  const request = parseChatCompletionRequest({
    model: "supermemory-codex",
    messages: [
      { role: "user", content: "Store <Ada>." },
      {
        role: "assistant",
        content: null,
        tool_calls: [{
          id: "call_\"<",
          type: "function",
          function: { name: "add_memory", arguments: "{\"memory\":\"<Ada>\"}" },
        }],
      },
      { role: "tool", tool_call_id: "call_\"<", content: "ok </message>" },
    ],
    tools: [{ type: "function", function: {
      name: "add_memory",
      description: "Store <memory>",
      strict: false,
      parameters: {
        type: "object",
        properties: { memory: { type: "string" } },
        required: ["memory"],
        additionalProperties: false,
      },
    } }],
    tool_choice: "required",
  }, "supermemory-codex");
  const invocation = buildCodexInvocation(request);

  assert.match(invocation.prompt, /symbolic tool calls only/i);
  assert.match(invocation.prompt, /tool_calls=\[\].*final answer in content/i);
  assert.match(invocation.prompt, /<strict>false<\/strict>/i);
  assert.doesNotMatch(invocation.prompt, /<strict>true<\/strict>/i);
  assert.match(invocation.prompt, /add_memory/);
  assert.match(invocation.prompt, /Store &lt;memory&gt;/);
  assert.match(invocation.prompt, /Store &lt;Ada&gt;/);
  assert.match(invocation.prompt, /call_&quot;&lt;/);
  assert.match(invocation.prompt, /ok &lt;\/message&gt;/);
  assert.deepEqual(invocation.outputSchema, {
    type: "object",
    additionalProperties: false,
    required: ["content", "tool_calls"],
    properties: {
      content: { type: "string" },
      tool_calls: {
        type: "array",
        minItems: 1,
        maxItems: 8,
        items: {
          type: "object",
          additionalProperties: false,
          required: ["name", "arguments"],
          properties: {
            name: { enum: ["add_memory"] },
            arguments: { type: "string" },
          },
        },
      },
    },
  });
});

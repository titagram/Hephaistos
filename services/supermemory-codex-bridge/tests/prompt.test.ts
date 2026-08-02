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

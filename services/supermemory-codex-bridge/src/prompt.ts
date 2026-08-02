import type { ChatCompletionRequest, CodexInvocation } from "./openai.js";

const instructions = "You are a text inference engine used internally by Supermemory.\nFollow the supplied messages exactly. Do not use tools, inspect files, browse, or add commentary.";
const toolInstructions = "You are a text inference engine used internally by Supermemory.\nChoose symbolic tool calls only; never execute tools. Return arguments as a JSON-object string matching the supplied parameter schema. When calling tools, set content='' and populate tool_calls. When no call is needed, return tool_calls=[] and put the final answer in content. Do not inspect files, browse, or add commentary.";

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&apos;");
}

export function buildCodexInvocation(request: ChatCompletionRequest): CodexInvocation {
  const messages = request.messages.map((message) => {
    const metadata = message.toolCallId ? ` tool_call_id="${escapeXml(message.toolCallId)}"` : "";
    const calls = message.toolCalls?.map((call) =>
      `<tool_call id="${escapeXml(call.id)}" name="${escapeXml(call.name)}">${escapeXml(call.arguments)}</tool_call>`,
    ).join("\n");
    const content = message.content === null ? "" : escapeXml(message.content);
    return `<message role="${message.role}"${metadata}>\n${content}${calls ? `\n${calls}` : ""}\n</message>`;
  });

  if (request.tools.length > 0) {
    const definitions = request.tools.map((tool) =>
      `<tool name="${escapeXml(tool.name)}">\n<description>${escapeXml(tool.description ?? "")}</description>\n<strict>${String(tool.strict ?? false)}</strict>\n<parameters>${escapeXml(JSON.stringify(tool.parameters))}</parameters>\n</tool>`,
    );
    return {
      prompt: `${toolInstructions}\nTool choice: ${request.toolChoice}.\n\n<tools>\n${definitions.join("\n")}\n</tools>${messages.length === 0 ? "" : `\n\n${messages.join("\n")}`}`,
      outputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["content", "tool_calls"],
        properties: {
          content: { type: "string" },
          tool_calls: {
            type: "array",
            ...(request.toolChoice === "required" ? { minItems: 1 } : {}),
            maxItems: request.toolChoice === "none" ? 0 : 8,
            items: {
              type: "object",
              additionalProperties: false,
              required: ["name", "arguments"],
              properties: {
                name: { enum: request.tools.map((tool) => tool.name) },
                arguments: { type: "string" },
              },
            },
          },
        },
      },
    };
  }

  return {
    prompt: messages.length === 0
      ? instructions
      : `${instructions}\n\n${messages.join("\n")}`,
    outputSchema: request.outputSchema,
  };
}

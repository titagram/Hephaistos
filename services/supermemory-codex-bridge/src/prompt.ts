import type { ChatCompletionRequest, CodexInvocation } from "./openai.js";

const instructions = "You are a text inference engine used internally by Supermemory.\nFollow the supplied messages exactly. Do not use tools, inspect files, browse, or add commentary.";

function escapeXml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

export function buildCodexInvocation(request: ChatCompletionRequest): CodexInvocation {
  const messages = request.messages.map((message) =>
    `<message role="${message.role}">\n${escapeXml(message.content)}\n</message>`,
  );

  return {
    prompt: messages.length === 0
      ? instructions
      : `${instructions}\n\n${messages.join("\n")}`,
    outputSchema: request.outputSchema,
  };
}

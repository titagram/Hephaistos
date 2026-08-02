export type ChatRole = "system" | "developer" | "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  outputSchema?: Record<string, unknown>;
}

export interface CodexInvocation {
  prompt: string;
  outputSchema?: Record<string, unknown>;
}

export interface CodexResult {
  text: string;
  usage?: {
    inputTokens: number;
    outputTokens: number;
  };
}

export interface ChatCompletionResponse {
  id: string;
  object: "chat.completion";
  created: number;
  model: string;
  choices: Array<{
    index: number;
    message: { role: "assistant"; content: string };
    finish_reason: "stop";
  }>;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const supportedRoles = new Set<ChatRole>(["system", "developer", "user", "assistant"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalidRequest(message: string): never {
  throw new ApiError(400, "invalid_request", message);
}

function parseMessages(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) {
    invalidRequest("messages must be an array.");
  }

  return value.map((message) => {
    if (!isRecord(message) || typeof message.role !== "string") {
      invalidRequest("Each message must include a supported role and content.");
    }
    if (!supportedRoles.has(message.role as ChatRole)) {
      throw new ApiError(400, "unsupported_message", "The message role is not supported.");
    }

    return { role: message.role as ChatRole, content: parseContent(message.content) };
  });
}

function parseContent(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (!Array.isArray(value)) {
    invalidRequest("Message content must be a string or text-part array.");
  }

  return value.map((part) => {
    if (!isRecord(part) || part.type !== "text" || typeof part.text !== "string") {
      throw new ApiError(400, "unsupported_content", "Only text message content is supported.");
    }
    return part.text;
  }).join("");
}

function parseOutputSchema(value: unknown): Record<string, unknown> | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!isRecord(value) || typeof value.type !== "string") {
    throw new ApiError(400, "invalid_response_format", "response_format must be a supported object.");
  }
  if (value.type === "json_object") {
    return { type: "object", additionalProperties: true };
  }
  if (value.type !== "json_schema") {
    throw new ApiError(400, "unsupported_response_format", "The response_format type is not supported.");
  }

  const jsonSchema = value.json_schema;
  if (
    !isRecord(jsonSchema)
    || typeof jsonSchema.name !== "string"
    || jsonSchema.name.trim() === ""
    || !isRecord(jsonSchema.schema)
    || (jsonSchema.strict !== undefined && typeof jsonSchema.strict !== "boolean")
  ) {
    throw new ApiError(400, "invalid_response_format", "json_schema must include a name and object schema.");
  }

  return jsonSchema.schema;
}

function parseCompatibilityHints(body: Record<string, unknown>): void {
  for (const name of ["temperature", "top_p"]) {
    const value = body[name];
    if (value !== undefined && (typeof value !== "number" || !Number.isFinite(value))) {
      invalidRequest(`${name} must be a finite number.`);
    }
  }
  for (const name of ["max_tokens", "max_completion_tokens"]) {
    const value = body[name];
    if (value !== undefined && (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)) {
      invalidRequest(`${name} must be a non-negative integer.`);
    }
  }
  if (body.n !== undefined && body.n !== 1) {
    throw new ApiError(400, "unsupported_n", "Only n: 1 is supported.");
  }
}

export function parseChatCompletionRequest(value: unknown, publicModel: string): ChatCompletionRequest {
  if (!isRecord(value)) {
    invalidRequest("The request body must be an object.");
  }
  if (typeof value.model !== "string") {
    invalidRequest("model must be a string.");
  }
  if (value.model !== publicModel) {
    throw new ApiError(400, "unsupported_model", "The requested model is not supported.");
  }
  if (value.stream === true) {
    throw new ApiError(400, "unsupported_streaming", "Streaming is not supported.");
  }
  if (value.stream !== undefined && value.stream !== false) {
    invalidRequest("stream must be a boolean.");
  }
  if (value.tools !== undefined) {
    throw new ApiError(400, "unsupported_tools", "Tools are not supported.");
  }

  parseCompatibilityHints(value);
  return {
    model: publicModel,
    messages: parseMessages(value.messages),
    outputSchema: parseOutputSchema(value.response_format),
  };
}

export function createChatCompletion(model: string, result: CodexResult): ChatCompletionResponse {
  const inputTokens = result.usage?.inputTokens ?? 0;
  const outputTokens = result.usage?.outputTokens ?? 0;

  return {
    id: `chatcmpl-${crypto.randomUUID()}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1_000),
    model,
    choices: [{
      index: 0,
      message: { role: "assistant", content: result.text },
      finish_reason: "stop",
    }],
    usage: {
      prompt_tokens: inputTokens,
      completion_tokens: outputTokens,
      total_tokens: inputTokens + outputTokens,
    },
  };
}

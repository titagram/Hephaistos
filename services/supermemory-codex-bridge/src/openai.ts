export type ChatRole = "system" | "developer" | "user" | "assistant" | "tool";

export interface ChatToolCall {
  id: string;
  name: string;
  arguments: string;
}

export interface ChatMessage {
  role: ChatRole;
  content: string | null;
  toolCalls?: ChatToolCall[];
  toolCallId?: string;
}

export interface ChatTool {
  name: string;
  description?: string;
  parameters: Record<string, unknown>;
  strict?: boolean;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  outputSchema?: Record<string, unknown>;
  tools: ChatTool[];
  toolChoice: "auto" | "required" | "none";
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
    message: {
      role: "assistant";
      content: string | null;
      tool_calls?: Array<{
        id: string;
        type: "function";
        function: { name: string; arguments: string };
      }>;
    };
    finish_reason: "stop" | "tool_calls";
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

const supportedRoles = new Set<ChatRole>(["system", "developer", "user", "assistant", "tool"]);
const supportedRequestFields = new Set([
  "model",
  "messages",
  "response_format",
  "temperature",
  "top_p",
  "max_tokens",
  "max_completion_tokens",
  "n",
  "stream",
  "tools",
  "tool_choice",
  "serviceTier",
]);
const toolRequestFields = new Set(["tool_choice", "parallel_tool_calls", "function_call", "functions"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyFields(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const fields = new Set(allowed);
  return Object.keys(value).every((field) => fields.has(field));
}

function invalidRequest(message: string): never {
  throw new ApiError(400, "invalid_request", message);
}

function rejectUnsupportedRequestFields(body: Record<string, unknown>): void {
  for (const field of Object.keys(body)) {
    if (supportedRequestFields.has(field)) {
      continue;
    }
    if (field === "stream_options") {
      throw new ApiError(400, "unsupported_streaming", "Streaming options are not supported.");
    }
    if (toolRequestFields.has(field)) {
      throw new ApiError(400, "unsupported_tools", "Tools are not supported.");
    }
    throw new ApiError(400, "unsupported_field", "The request contains an unsupported field.");
  }
}

function parseMessages(value: unknown, tools: ChatTool[]): ChatMessage[] {
  if (!Array.isArray(value)) {
    invalidRequest("messages must be an array.");
  }

  const toolNames = new Set(tools.map((tool) => tool.name));
  const pendingCalls = new Map<string, string>();
  const parsed = value.map((message) => {
    if (!isRecord(message) || typeof message.role !== "string") {
      invalidRequest("Each message must include a supported role and content.");
    }
    if (!supportedRoles.has(message.role as ChatRole)) {
      throw new ApiError(400, "unsupported_message", "The message role is not supported.");
    }
    if (message.role !== "tool" && pendingCalls.size > 0) {
      throw new ApiError(400, "invalid_tool_history", "Every tool call must be followed by its tool result.");
    }
    if (message.role === "tool") {
      if (!hasOnlyFields(message, ["role", "content", "tool_call_id"])) {
        throw new ApiError(400, "invalid_tool_history", "Tool messages contain unsupported fields.");
      }
      if (typeof message.tool_call_id !== "string" || !pendingCalls.has(message.tool_call_id)) {
        throw new ApiError(400, "invalid_tool_history", "Tool messages must reference an earlier tool call.");
      }
      pendingCalls.delete(message.tool_call_id);
      return { role: "tool" as const, content: parseContent(message.content), toolCallId: message.tool_call_id };
    }
    if (message.role === "assistant" && message.tool_calls !== undefined) {
      if (!hasOnlyFields(message, ["role", "content", "tool_calls"])) {
        throw new ApiError(400, "invalid_tool_history", "Assistant tool messages contain unsupported fields.");
      }
      if (!Array.isArray(message.tool_calls) || message.tool_calls.length === 0 || message.tool_calls.length > 8) {
        throw new ApiError(400, "invalid_tool_history", "Assistant tool calls must be a non-empty bounded array.");
      }
      const toolCalls = message.tool_calls.map((value) => {
        if (!isRecord(value) || !hasOnlyFields(value, ["id", "type", "function"])
          || typeof value.id !== "string" || value.id.length === 0 || value.id.length > 128
          || value.type !== "function" || !isRecord(value.function)
          || !hasOnlyFields(value.function, ["name", "arguments"])
          || typeof value.function.name !== "string" || !toolNames.has(value.function.name)
          || typeof value.function.arguments !== "string" || value.function.arguments.length > 65_536
          || pendingCalls.has(value.id)) {
          throw new ApiError(400, "invalid_tool_history", "Assistant tool calls are malformed.");
        }
        parseArgumentsObject(value.function.arguments, 400, "invalid_tool_history");
        pendingCalls.set(value.id, value.function.name);
        return { id: value.id, name: value.function.name, arguments: value.function.arguments };
      });
      const content = message.content === null ? null : parseContent(message.content);
      return { role: "assistant" as const, content, toolCalls };
    }
    if (Object.hasOwn(message, "tool_calls") || Object.hasOwn(message, "function_call")) {
      throw new ApiError(400, "invalid_tool_history", "Tool metadata is malformed.");
    }
    if (!hasOnlyFields(message, ["role", "content"])) {
      throw new ApiError(400, "unsupported_message", "The message contains unsupported fields.");
    }
    return { role: message.role as ChatRole, content: parseContent(message.content) };
  });
  if (pendingCalls.size > 0) {
    throw new ApiError(400, "invalid_tool_history", "Every tool call must be followed by its tool result.");
  }
  return parsed;
}

function parseTools(value: unknown): ChatTool[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 32) {
    throw new ApiError(400, "invalid_tools", "tools must be a bounded array.");
  }
  const names = new Set<string>();
  return value.map((entry) => {
    if (!isRecord(entry) || !hasOnlyFields(entry, ["type", "function"])
      || entry.type !== "function" || !isRecord(entry.function)
      || !hasOnlyFields(entry.function, ["name", "description", "parameters", "strict"])) {
      throw new ApiError(400, "invalid_tools", "Only function tools are supported.");
    }
    const { name, description, parameters, strict } = entry.function;
    if (typeof name !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(name) || names.has(name)
      || (description !== undefined && (typeof description !== "string" || description.length > 1_024))
      || (strict !== undefined && typeof strict !== "boolean")
      || !isRecord(parameters) || JSON.stringify(parameters).length > 32_768) {
      throw new ApiError(400, "invalid_tools", "Function tool definitions are malformed.");
    }
    names.add(name);
    return {
      name,
      ...(description === undefined ? {} : { description }),
      parameters,
      ...(strict === undefined ? {} : { strict }),
    };
  });
}

function parseToolChoice(value: unknown, tools: ChatTool[]): "auto" | "required" | "none" {
  if (value === undefined) return tools.length === 0 ? "none" : "auto";
  if ((value === "auto" || value === "required" || value === "none")
    && (tools.length > 0 || value === "none")) return value;
  throw new ApiError(400, "invalid_tool_choice", "tool_choice must be auto, required, or none.");
}

function parseArgumentsObject(value: string, status: number, code: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed)) throw new Error("not object");
    return parsed;
  } catch {
    throw new ApiError(status, code, "Tool arguments must be a JSON object.");
  }
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
  if (body.serviceTier !== undefined && body.serviceTier !== "flex") {
    invalidRequest("serviceTier must be flex.");
  }
}

export function parseChatCompletionRequest(value: unknown, publicModel: string): ChatCompletionRequest {
  if (!isRecord(value)) {
    invalidRequest("The request body must be an object.");
  }
  rejectUnsupportedRequestFields(value);
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
  parseCompatibilityHints(value);
  const tools = parseTools(value.tools);
  const toolChoice = parseToolChoice(value.tool_choice, tools);
  if (tools.length > 0 && value.response_format !== undefined) {
    invalidRequest("response_format cannot be combined with tools.");
  }
  return {
    model: publicModel,
    messages: parseMessages(value.messages, tools),
    outputSchema: parseOutputSchema(value.response_format),
    tools,
    toolChoice,
  };
}

export function createChatCompletion(request: ChatCompletionRequest, result: CodexResult): ChatCompletionResponse {
  const inputTokens = result.usage?.inputTokens ?? 0;
  const outputTokens = result.usage?.outputTokens ?? 0;

  let message: ChatCompletionResponse["choices"][number]["message"] = { role: "assistant", content: result.text };
  let finishReason: "stop" | "tool_calls" = "stop";
  if (request.tools.length > 0) {
    let value: unknown;
    try {
      value = JSON.parse(result.text);
    } catch {
      throw new ApiError(502, "codex_structured_output_error", "Codex returned invalid structured output.");
    }
    if (!isRecord(value) || !hasOnlyFields(value, ["content", "tool_calls"])
      || typeof value.content !== "string" || !Array.isArray(value.tool_calls)
      || value.tool_calls.length > 8 || (request.toolChoice === "required" && value.tool_calls.length === 0)
      || (request.toolChoice === "none" && value.tool_calls.length > 0)
      || (value.tool_calls.length > 0 && value.content !== "")) {
      throw new ApiError(502, "codex_structured_output_error", "Codex returned invalid structured output.");
    }
    const names = new Set(request.tools.map((tool) => tool.name));
    const toolCalls = value.tool_calls.map((call) => {
      if (!isRecord(call) || !hasOnlyFields(call, ["name", "arguments"])
        || typeof call.name !== "string" || !names.has(call.name)
        || typeof call.arguments !== "string" || call.arguments.length > 65_536) {
        throw new ApiError(502, "codex_structured_output_error", "Codex returned invalid tool calls.");
      }
      parseArgumentsObject(call.arguments, 502, "codex_structured_output_error");
      return {
        id: `call_${crypto.randomUUID()}`,
        type: "function" as const,
        function: { name: call.name, arguments: call.arguments },
      };
    });
    if (toolCalls.length > 0) {
      message = { role: "assistant", content: null, tool_calls: toolCalls };
      finishReason = "tool_calls";
    } else {
      message = { role: "assistant", content: value.content };
    }
  }

  return {
    id: `chatcmpl-${crypto.randomUUID()}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1_000),
    model: request.model,
    choices: [{
      index: 0,
      message,
      finish_reason: finishReason,
    }],
    usage: {
      prompt_tokens: inputTokens,
      completion_tokens: outputTokens,
      total_tokens: inputTokens + outputTokens,
    },
  };
}

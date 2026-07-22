import type { EngineRequest, EngineResponse } from "../protocol.js";

export async function dispatch(
  request: EngineRequest,
): Promise<EngineResponse> {
  return {
    protocolVersion: 1,
    requestId: request.requestId,
    status: "inconclusive",
    output: {},
    diagnostics: [
      {
        code: "handler_not_implemented",
        message: `No handler is installed for ${request.command}`,
      },
    ],
  };
}

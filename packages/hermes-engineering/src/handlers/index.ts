import type { EngineRequest, EngineResponse } from "../protocol.js";
import { CaptureTargetError, captureTarget } from "./capture-target.js";

export async function dispatch(
  request: EngineRequest,
): Promise<EngineResponse> {
  if (request.command === "capture-target") {
    try {
      const output = await captureTarget(request);
      const incomplete = output.skippedFiles.length > 0;
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: incomplete ? "inconclusive" : "passed",
        output: { ...output },
        diagnostics: incomplete
          ? [
              {
                code: "capture_incomplete",
                message: `${output.skippedFiles.length} file(s) could not be fully captured`,
              },
            ]
          : [],
      };
    } catch (cause) {
      if (cause instanceof CaptureTargetError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "failed",
          output: cause.output ? { ...cause.output } : {},
          diagnostics: [{ code: cause.code, message: cause.message }],
        };
      }
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "failed",
          output: {},
          diagnostics: [{ code: "invalid_target", message: cause.message }],
        };
      }
      throw cause;
    }
  }
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

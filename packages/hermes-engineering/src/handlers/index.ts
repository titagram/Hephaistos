import type { EngineRequest, EngineResponse } from "../protocol.js";
import { buildPrompts } from "./build-prompts.js";
import { runBuildTest } from "./build-test.js";
import { CaptureTargetError, captureTarget } from "./capture-target.js";
import { checkCoverage } from "./check-coverage.js";
import { composeReview } from "./compose-review.js";
import {
  ReviewerEvidenceUnavailableError,
  resolveFindingAnchors,
} from "./resolve-anchors.js";
import { runTestEfficacy } from "./test-efficacy.js";

export async function dispatch(
  request: EngineRequest,
): Promise<EngineResponse> {
  if (request.command === "resolve-anchors") {
    try {
      const output = await resolveFindingAnchors(request);
      const incomplete = output.unresolvedFindings.length > 0;
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: incomplete ? "inconclusive" : "passed",
        output: { ...output },
        diagnostics: incomplete
          ? [
              {
                code: "unresolved_anchors",
                message: `${output.unresolvedFindings.length} finding anchor(s) could not be resolved`,
              },
            ]
          : [],
      };
    } catch (cause) {
      if (cause instanceof ReviewerEvidenceUnavailableError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "reviewer_evidence_unavailable", message: cause.message },
          ],
        };
      }
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "failed",
          output: {},
          diagnostics: [{ code: "invalid_findings", message: cause.message }],
        };
      }
      throw cause;
    }
  }
  if (request.command === "compose-review") {
    try {
      const output = await composeReview(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status:
          output.event === "APPROVE"
            ? "passed"
            : output.event === "REQUEST_CHANGES"
              ? "failed"
              : "inconclusive",
        output: { ...output },
        diagnostics: [],
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "failed",
          output: {},
          diagnostics: [
            { code: "invalid_review_facts", message: cause.message },
          ],
        };
      }
      throw cause;
    }
  }
  if (request.command === "build-prompts") {
    try {
      const output = await buildPrompts(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: "passed",
        output: { ...output },
        diagnostics: [],
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "invalid_build_prompts_input", message: cause.message },
          ],
        };
      }
      throw cause;
    }
  }
  if (request.command === "check-coverage") {
    try {
      const result = await checkCoverage(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: result.status,
        output: { ...result.output },
        diagnostics: result.diagnostics,
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "invalid_check_coverage_input", message: cause.message },
          ],
        };
      }
      throw cause;
    }
  }
  if (request.command === "build-test") {
    try {
      const result = await runBuildTest(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: result.status,
        output: { ...result.output },
        diagnostics: result.diagnostics,
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "invalid_build_test_input", message: cause.message },
          ],
        };
      }
      throw cause;
    }
  }
  if (request.command === "test-efficacy") {
    try {
      const result = await runTestEfficacy(request);
      return {
        protocolVersion: 1,
        requestId: request.requestId,
        status: result.status,
        output: { ...result.output },
        diagnostics: result.diagnostics,
      };
    } catch (cause) {
      if (cause instanceof TypeError) {
        return {
          protocolVersion: 1,
          requestId: request.requestId,
          status: "inconclusive",
          output: {},
          diagnostics: [
            { code: "invalid_test_efficacy_input", message: cause.message },
          ],
        };
      }
      throw cause;
    }
  }
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

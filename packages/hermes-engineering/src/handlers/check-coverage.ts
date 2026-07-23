import { lstatSync, readFileSync, realpathSync, statSync } from "node:fs";
import { resolve } from "node:path";

import type { CheckStatus, EngineRequest } from "../protocol.js";
import {
  describeOmittedSpecialists,
  effortLimits,
  selectReviewRoster,
  type BuildPromptsOutput,
  type OmittedSpecialist,
  type ReviewEffort,
  type ReviewPlan,
} from "./build-prompts.js";
import {
  coverageFromTranscripts,
  readRecordedPrompts,
  readTranscripts,
  requiredAgents,
  TranscriptsUnavailableError,
  type CoverageFromTranscripts,
} from "../shims/qwenReviewRuntime.js";

export interface ExactCoverage extends CoverageFromTranscripts {
  exactPromptMismatches: string[];
}

export interface CheckCoverageOutput {
  coverage: ExactCoverage;
  omittedSpecialists: OmittedSpecialist[];
}

export interface CheckCoverageResult {
  status: CheckStatus;
  output: CheckCoverageOutput | Record<string, never>;
  diagnostics: Array<{ code: string; message: string }>;
}

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

const validatedPlanPath = (request: EngineRequest): string => {
  const input = asRecord(request.input, "input");
  const unknown = Object.keys(input).find((key) => key !== "planPath");
  if (unknown !== undefined) {
    throw new TypeError(`unknown check-coverage input field: ${unknown}`);
  }
  if (typeof input.planPath !== "string" || input.planPath.length === 0) {
    throw new TypeError("planPath must be a non-empty string");
  }
  const artifactRoot = realpathSync(request.artifactRoot);
  const planPath = realpathSync(resolve(input.planPath));
  if (planPath !== resolve(artifactRoot, "plan.json")) {
    throw new TypeError("planPath must be the run's canonical plan.json");
  }
  const stat = lstatSync(planPath);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new TypeError("planPath must be a real file");
  }
  return planPath;
};

const promptsFor = (
  artifactRoot: string,
  planPath: string,
): BuildPromptsOutput => {
  const promptsPath = resolve(artifactRoot, "prompts.json");
  const stat = lstatSync(promptsPath);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new TypeError("prompts.json must be a real file");
  }
  const parsed = asRecord(
    JSON.parse(readFileSync(promptsPath, "utf8")) as unknown,
    "prompts.json",
  );
  if (parsed.planPath !== planPath || !Array.isArray(parsed.prompts)) {
    throw new TypeError("prompts.json does not belong to this review plan");
  }
  if (!Array.isArray(parsed.omittedSpecialists)) {
    throw new TypeError("prompts.json has no omittedSpecialists array");
  }
  return parsed as unknown as BuildPromptsOutput;
};

const sameJson = (left: unknown, right: unknown): boolean =>
  JSON.stringify(left) === JSON.stringify(right);

const validatedPromptPlan = (
  artifactRoot: string,
  planPath: string,
): BuildPromptsOutput => {
  const promptPlan = promptsFor(artifactRoot, planPath);
  if (!(promptPlan.effort in effortLimits)) {
    throw new TypeError("prompts.json has an invalid effort");
  }
  const effort = promptPlan.effort as ReviewEffort;
  const plan = asRecord(
    JSON.parse(readFileSync(planPath, "utf8")) as unknown,
    "plan",
  ) as ReviewPlan;
  const upstream = requiredAgents(plan);
  const expected = selectReviewRoster(plan, effort);
  const omitted = describeOmittedSpecialists(expected.omitted);
  if (
    !sameJson(
      promptPlan.upstreamRequiredAgentKeys,
      upstream.map((agent) => agent.key),
    ) ||
    !sameJson(
      promptPlan.prompts.map((prompt) => prompt.key),
      expected.selected.map((agent) => agent.key),
    ) ||
    !sameJson(promptPlan.omittedSpecialists, omitted)
  ) {
    throw new TypeError(
      "prompts.json does not match the deterministic effort roster",
    );
  }
  return promptPlan;
};

const effectiveCoverage = (
  raw: CoverageFromTranscripts,
  omitted: readonly OmittedSpecialist[],
  exactPromptMismatches: readonly string[],
): ExactCoverage => {
  const omittedSelectors = new Set(omitted.map((entry) => entry.selector));
  const omittedSubjects = new Set(omitted.map((entry) => entry.label));
  const missingRoles: string[] = [];
  const missingRoleSelectors: string[] = [];
  const selectorsArePaired =
    raw.missingRoles.length === raw.missingRoleSelectors.length;
  for (let index = 0; index < raw.missingRoles.length; index++) {
    const selector = raw.missingRoleSelectors[index];
    if (
      selectorsArePaired &&
      selector !== undefined &&
      omittedSelectors.has(selector)
    ) {
      continue;
    }
    missingRoles.push(raw.missingRoles[index]!);
    if (selector !== undefined) missingRoleSelectors.push(selector);
  }
  const disclosures = raw.disclosures.filter(
    (entry) => !omittedSubjects.has(entry.subject),
  );
  const ok =
    raw.blindAgents.length === 0 &&
    raw.idleAgents.length === 0 &&
    raw.unopenedAgents.length === 0 &&
    raw.rewrittenPrompts.length === 0 &&
    missingRoles.length === 0 &&
    raw.unreadBriefs.length === 0 &&
    raw.uncoverableChunks.length === 0 &&
    raw.missingChunks.length === 0 &&
    exactPromptMismatches.length === 0;
  return {
    ...raw,
    ok,
    missingRoles,
    missingRoleSelectors,
    disclosures,
    exactPromptMismatches: [...exactPromptMismatches],
  };
};

const exactPromptMismatches = (
  promptPlan: BuildPromptsOutput,
  planPath: string,
  env: NodeJS.ProcessEnv,
): string[] => {
  const plan = asRecord(
    JSON.parse(readFileSync(planPath, "utf8")) as unknown,
    "plan",
  );
  if (typeof plan.diffPathAbsolute !== "string") {
    throw new TypeError("plan.diffPathAbsolute must be a string");
  }
  const recorded = readRecordedPrompts(planPath);
  const transcripts = readTranscripts(
    statSync(planPath).mtimeMs,
    env,
    plan.diffPathAbsolute,
  );
  const mismatches: string[] = [];
  for (const prompt of promptPlan.prompts) {
    const built = recorded.get(prompt.key);
    if (built !== prompt.text) {
      throw new TypeError(
        `recorded prompt ${prompt.key} does not match immutable prompts.json`,
      );
    }
    if (!transcripts.some((record) => record.launchPrompt === built)) {
      mismatches.push(prompt.key);
    }
  }
  return mismatches;
};

export async function checkCoverage(
  request: EngineRequest,
): Promise<CheckCoverageResult> {
  const planPath = validatedPlanPath(request);
  const artifactRoot = realpathSync(request.artifactRoot);
  const promptPlan = validatedPromptPlan(artifactRoot, planPath);
  const env = {
    ...process.env,
    QWEN_CODE_PROJECT_DIR: artifactRoot,
    QWEN_CODE_SESSION_ID: "reviewers",
  };
  try {
    const exactMismatches = exactPromptMismatches(promptPlan, planPath, env);
    const coverage = effectiveCoverage(
      coverageFromTranscripts(planPath, env),
      promptPlan.omittedSpecialists,
      exactMismatches,
    );
    return {
      status: coverage.ok ? "passed" : "failed",
      output: { coverage, omittedSpecialists: promptPlan.omittedSpecialists },
      diagnostics: coverage.ok
        ? []
        : [
            {
              code: "coverage_failed",
              message: "required reviewer evidence is absent or unverifiable",
            },
          ],
    };
  } catch (cause) {
    if (cause instanceof TranscriptsUnavailableError) {
      return {
        status: "inconclusive",
        output: {},
        diagnostics: [
          { code: "transcripts_unavailable", message: cause.message },
        ],
      };
    }
    throw cause;
  }
}

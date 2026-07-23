import {
  chmodSync,
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { randomBytes } from "node:crypto";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "node:path";

import type { EngineRequest } from "../protocol.js";
import {
  BRIEFS,
  buildChunkAgentPrompt,
  buildChunkLaunchPrompt,
  buildRoleBrief,
  buildRoleLaunchPrompt,
  briefPath,
  isTerritoryFanOut,
  promptRecordDir,
  recordPrompt,
  requiredAgents,
  shellQuotePath,
  writeBrief,
  type RequiredAgent,
  type RoleId,
  type RosterPlan,
} from "../shims/qwenReviewRuntime.js";

export type ReviewEffort = "low" | "medium" | "high";

export const effortLimits = {
  low: { maxReviewers: 1, verifyFindings: false, reverseAudit: false },
  medium: { maxReviewers: 3, verifyFindings: true, reverseAudit: false },
  high: { maxReviewers: 24, verifyFindings: true, reverseAudit: true },
} as const;

export interface ReviewPrompt {
  key: string;
  role: RoleId | "chunk";
  chunk?: number;
  file?: string;
  wave: number;
  text: string;
}

export interface OmittedSpecialist {
  key: string;
  role: RoleId;
  file?: string;
  label: string;
  selector: string;
}

export interface ReviewPromptWave {
  number: number;
  promptKeys: string[];
}

export interface BuildPromptsOutput {
  runId: string;
  planPath: string;
  diffPath: string;
  promptsPath: string;
  effort: ReviewEffort;
  limits: (typeof effortLimits)[ReviewEffort];
  upstreamRequiredAgentKeys: string[];
  prompts: ReviewPrompt[];
  waves: ReviewPromptWave[];
  omittedSpecialists: OmittedSpecialist[];
}

interface BuildPromptsInput {
  planPath: string;
  effort: ReviewEffort;
  rules?: string;
  worktreePath?: string;
}

export type ReviewPlan = RosterPlan & {
  diffPathAbsolute?: unknown;
  hermes?: unknown;
  chunks?: Array<{
    id?: unknown;
    startLine?: unknown;
    endLine?: unknown;
    lines?: unknown;
    chars?: unknown;
    maxLineChars?: unknown;
    oversized?: unknown;
    files?: unknown;
  }>;
  files?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

const PROMPTS_NAME = "prompts.json";
const RUN_ID = /^[A-Za-z0-9_-]+$/u;
const MAX_RULES_BYTES = 256 * 1024;

const asRecord = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

const within = (root: string, candidate: string): boolean => {
  const rel = relative(root, candidate);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
};

const validatePlanPath = (request: EngineRequest, raw: unknown): string => {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new TypeError("planPath must be a non-empty string");
  }
  const artifactRoot = realpathSync(request.artifactRoot);
  const planPath = realpathSync(resolve(raw));
  if (planPath !== join(artifactRoot, "plan.json")) {
    throw new TypeError("planPath must be the run's canonical plan.json");
  }
  const stat = lstatSync(planPath);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new TypeError("planPath must be a real file");
  }
  return planPath;
};

const parseInput = (request: EngineRequest): BuildPromptsInput => {
  const input = asRecord(request.input, "input");
  const unknown = Object.keys(input).find(
    (key) => !["planPath", "effort", "rules", "worktreePath"].includes(key),
  );
  if (unknown !== undefined) {
    throw new TypeError(`unknown build-prompts input field: ${unknown}`);
  }
  if (
    !(
      input.effort === "low" ||
      input.effort === "medium" ||
      input.effort === "high"
    )
  ) {
    throw new TypeError("effort must be low, medium, or high");
  }
  if (
    input.rules !== undefined &&
    (typeof input.rules !== "string" ||
      Buffer.byteLength(input.rules, "utf8") > MAX_RULES_BYTES)
  ) {
    throw new TypeError("rules must be a string no larger than 256 KiB");
  }
  let worktreePath: string | undefined;
  if (input.worktreePath !== undefined) {
    if (
      typeof input.worktreePath !== "string" ||
      input.worktreePath.length === 0
    ) {
      throw new TypeError("worktreePath must be a non-empty string");
    }
    const path = realpathSync(resolve(input.worktreePath));
    const stat = lstatSync(path);
    if (!stat.isDirectory() || stat.isSymbolicLink()) {
      throw new TypeError("worktreePath must be a real directory");
    }
    worktreePath = path;
  }
  return {
    planPath: validatePlanPath(request, input.planPath),
    effort: input.effort,
    ...(input.rules === undefined ? {} : { rules: input.rules }),
    ...(worktreePath === undefined ? {} : { worktreePath }),
  };
};

const selectorOf = (agent: RequiredAgent): string => {
  if (agent.role === "chunk") return `--chunk ${agent.chunk}`;
  return agent.file
    ? `--role ${agent.role} --file ${shellQuotePath(agent.file)}`
    : `--role ${agent.role}`;
};

const labelOf = (agent: RequiredAgent): string => {
  if (agent.role === "chunk") return `chunk ${agent.chunk}`;
  const label = BRIEFS[agent.role].label;
  return agent.file ? `${label} — ${agent.file}` : label;
};

const normalizePlan = (
  raw: unknown,
  worktreePath: string | undefined,
): ReviewPlan => {
  const plan = asRecord(raw, "plan") as ReviewPlan;
  const hermes = asRecord(plan.hermes, "plan.hermes");
  const targetKind = hermes.targetKind;
  if (targetKind === "local" || targetKind === "file") {
    plan.untrackedFiles ??= [];
  } else if (targetKind === "pr") {
    const context = asRecord(hermes.prContext, "plan.hermes.prContext");
    plan.ownerRepo = context.ownerRepo;
    plan.prNumber = context.number;
    plan.worktreePath = worktreePath ?? plan.worktreePath;
  } else if (targetKind === "range" && worktreePath !== undefined) {
    plan.worktreePath = worktreePath;
  }
  return plan;
};

export const selectReviewRoster = (
  plan: ReviewPlan,
  effort: ReviewEffort,
): { selected: RequiredAgent[]; omitted: RequiredAgent[] } => {
  const required = requiredAgents(plan);
  const source = isTerritoryFanOut(plan)
    ? required.filter((agent) => agent.role === "chunk")
    : required.filter((agent) => agent.role === "1a");
  if (source.length === 0) {
    throw new TypeError("the upstream roster did not provide source coverage");
  }
  const chosen = new Set(source.map((agent) => agent.key));
  const limit = effortLimits[effort].maxReviewers;
  if (chosen.size < limit) {
    for (const agent of required) {
      if (chosen.has(agent.key)) continue;
      chosen.add(agent.key);
      if (chosen.size >= limit) break;
    }
  }
  const selected = [
    ...source,
    ...required.filter(
      (agent) =>
        chosen.has(agent.key) &&
        !source.some((entry) => entry.key === agent.key),
    ),
  ];
  const omitted = required.filter((agent) => !chosen.has(agent.key));
  if (omitted.some((agent) => agent.role === "chunk")) {
    throw new Error(
      "internal error: effort selection omitted required chunk coverage",
    );
  }
  return { selected, omitted };
};

export const describeOmittedSpecialists = (
  omitted: readonly RequiredAgent[],
): OmittedSpecialist[] =>
  omitted.map((agent) => {
    if (agent.role === "chunk") {
      throw new Error(
        "internal error: a chunk cannot be an omitted specialist",
      );
    }
    return {
      key: agent.key,
      role: agent.role,
      ...(agent.file === undefined ? {} : { file: agent.file }),
      label: labelOf(agent),
      selector: selectorOf(agent),
    };
  });

const promptFor = (
  plan: ReviewPlan,
  planPath: string,
  runId: string,
  agent: RequiredAgent,
  rules: string | undefined,
): { brief: string; text: string } => {
  let brief: string;
  let launch: string;
  if (agent.role === "chunk") {
    const chunk = agent.chunk;
    if (chunk === undefined)
      throw new Error(`chunk roster entry ${agent.key} has no id`);
    brief = buildChunkAgentPrompt(plan, chunk, rules);
    launch = buildChunkLaunchPrompt(
      plan,
      chunk,
      briefPath(planPath, agent.key),
    );
  } else {
    brief = buildRoleBrief(plan, agent.role, {
      ...(rules === undefined ? {} : { rules }),
      ...(agent.file === undefined ? {} : { file: agent.file }),
      planPath,
    });
    launch = buildRoleLaunchPrompt(
      plan,
      agent.role,
      briefPath(planPath, agent.key),
      {
        ...(agent.file === undefined ? {} : { file: agent.file }),
      },
    );
  }
  const markers = `Hermes-Review-Run: ${runId}\nHermes-Review-Plan: ${planPath}`;
  return { brief, text: `${markers}\n${launch}` };
};

const promptRecordPath = (planPath: string, key: string): string =>
  join(promptRecordDir(planPath), `${encodeURIComponent(key)}.txt`);

const assertImmutable = (path: string, contents: string): void => {
  if (!existsSync(path)) return;
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error(`immutable review artifact is not a real file: ${path}`);
  }
  if (readFileSync(path, "utf8") !== contents) {
    throw new Error(`refusing to rewrite immutable review artifact: ${path}`);
  }
};

const writeExclusive = (path: string, contents: string): void => {
  if (existsSync(path)) {
    assertImmutable(path, contents);
    return;
  }
  mkdirSync(dirname(path), { recursive: true });
  const descriptor = openSync(path, "wx", 0o600);
  try {
    writeFileSync(descriptor, contents);
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
  chmodSync(path, 0o600);
};

const atomicReplaceIfChanged = (path: string, contents: string): void => {
  if (readFileSync(path, "utf8") === contents) return;
  const temporary = join(
    dirname(path),
    `.${basename(path)}.${randomBytes(12).toString("hex")}.tmp`,
  );
  let descriptor: number | undefined;
  try {
    descriptor = openSync(temporary, "wx", 0o600);
    writeFileSync(descriptor, contents);
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporary, path);
    chmodSync(path, 0o600);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    try {
      unlinkSync(temporary);
    } catch {
      // A successful rename consumed the temporary name.
    }
  }
};

const ensurePromptDirectory = (
  planPath: string,
  artifactRoot: string,
): void => {
  const directory = promptRecordDir(planPath);
  if (!existsSync(directory))
    mkdirSync(directory, { recursive: true, mode: 0o700 });
  const stat = lstatSync(directory);
  const canonical = realpathSync(directory);
  if (
    !stat.isDirectory() ||
    stat.isSymbolicLink() ||
    !within(artifactRoot, canonical)
  ) {
    throw new Error(
      "review prompt directory must be a real directory inside artifactRoot",
    );
  }
  chmodSync(canonical, 0o700);
};

export async function buildPrompts(
  request: EngineRequest,
): Promise<BuildPromptsOutput> {
  const input = parseInput(request);
  const artifactRoot = realpathSync(request.artifactRoot);
  const plan = normalizePlan(
    JSON.parse(readFileSync(input.planPath, "utf8")) as unknown,
    input.worktreePath,
  );
  const hermes = asRecord(plan.hermes, "plan.hermes");
  const runId = hermes.runId;
  if (
    typeof runId !== "string" ||
    !RUN_ID.test(runId) ||
    runId !== basename(artifactRoot)
  ) {
    throw new TypeError("plan.hermes.runId must match the artifact root name");
  }
  if (typeof plan.diffPathAbsolute !== "string") {
    throw new TypeError("plan.diffPathAbsolute must be inside artifactRoot");
  }
  const diffPath = realpathSync(resolve(plan.diffPathAbsolute));
  const diffStat = lstatSync(diffPath);
  if (
    !within(artifactRoot, diffPath) ||
    !diffStat.isFile() ||
    diffStat.isSymbolicLink()
  ) {
    throw new TypeError(
      "plan.diffPathAbsolute must be a real file inside artifactRoot",
    );
  }
  plan.diffPathAbsolute = diffPath;

  const upstreamRoster = requiredAgents(plan);
  const { selected, omitted } = selectReviewRoster(plan, input.effort);
  const maxReviewers = effortLimits[input.effort].maxReviewers;
  const built = selected.map((agent, index) => {
    const material = promptFor(plan, input.planPath, runId, agent, input.rules);
    return {
      agent,
      brief: material.brief,
      prompt: {
        key: agent.key,
        role: agent.role,
        ...(agent.chunk === undefined ? {} : { chunk: agent.chunk }),
        ...(agent.file === undefined ? {} : { file: agent.file }),
        wave: Math.floor(index / maxReviewers) + 1,
        text: material.text,
      } satisfies ReviewPrompt,
    };
  });
  const prompts = built.map(({ prompt }) => prompt);
  const waves: ReviewPromptWave[] = [];
  for (const prompt of prompts) {
    const wave = waves[prompt.wave - 1];
    if (wave) wave.promptKeys.push(prompt.key);
    else waves.push({ number: prompt.wave, promptKeys: [prompt.key] });
  }
  const omittedSpecialists = describeOmittedSpecialists(omitted);
  const promptsPath = join(artifactRoot, PROMPTS_NAME);
  const output: BuildPromptsOutput = {
    runId,
    planPath: input.planPath,
    diffPath,
    promptsPath,
    effort: input.effort,
    limits: effortLimits[input.effort],
    upstreamRequiredAgentKeys: upstreamRoster.map((agent) => agent.key),
    prompts,
    waves,
    omittedSpecialists,
  };
  const serialized = `${JSON.stringify(output, null, 2)}\n`;

  ensurePromptDirectory(input.planPath, artifactRoot);
  for (const { agent, brief, prompt } of built) {
    assertImmutable(briefPath(input.planPath, agent.key), brief);
    assertImmutable(promptRecordPath(input.planPath, agent.key), prompt.text);
  }
  assertImmutable(promptsPath, serialized);

  hermes.reviewPrompts = {
    effort: input.effort,
    limits: effortLimits[input.effort],
    upstreamRequiredAgentKeys: output.upstreamRequiredAgentKeys,
    selectedAgentKeys: prompts.map((prompt) => prompt.key),
    omittedSpecialists,
    waves,
  };
  atomicReplaceIfChanged(input.planPath, `${JSON.stringify(plan, null, 2)}\n`);

  for (const { agent, brief, prompt } of built) {
    const writtenBrief = writeBrief(input.planPath, agent.key, brief);
    if (readFileSync(writtenBrief, "utf8") !== brief) {
      throw new Error(`failed to record reviewer brief ${agent.key}`);
    }
    chmodSync(writtenBrief, 0o600);
    recordPrompt(input.planPath, agent.key, prompt.text);
    const recordedPath = promptRecordPath(input.planPath, agent.key);
    if (readFileSync(recordedPath, "utf8") !== prompt.text) {
      throw new Error(`failed to record reviewer prompt ${agent.key}`);
    }
    chmodSync(recordedPath, 0o600);
  }
  writeExclusive(promptsPath, serialized);
  return output;
}

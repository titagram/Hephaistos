export type ExecutionMode = "local" | "sandbox" | "denied";

export interface ExecutionPolicy {
  mode: ExecutionMode;
  allowed: boolean;
  sanitizedEnv: NodeJS.ProcessEnv;
  network: boolean;
  reason: string;
  backend: string | null;
}

const SAFE_ENV = new Set([
  "PATH",
  "HOME",
  "USERPROFILE",
  "HOMEDRIVE",
  "HOMEPATH",
  "APPDATA",
  "LOCALAPPDATA",
  "PROGRAMDATA",
  "SYSTEMROOT",
  "WINDIR",
  "COMSPEC",
  "PATHEXT",
  "TMP",
  "TEMP",
  "TMPDIR",
  "LANG",
  "LANGUAGE",
]);

const record = (value: unknown, label: string): Record<string, unknown> => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
};

export const parseExecutionPolicy = (value: unknown): ExecutionPolicy => {
  const policy = record(value, "execution");
  const allowedKeys = new Set([
    "mode",
    "allowed",
    "sanitizedEnv",
    "network",
    "reason",
    "backend",
  ]);
  const unknown = Object.keys(policy).find((key) => !allowedKeys.has(key));
  if (unknown !== undefined)
    throw new TypeError(`unknown execution field: ${unknown}`);
  if (!["local", "sandbox", "denied"].includes(String(policy.mode))) {
    throw new TypeError("execution.mode is invalid");
  }
  if (typeof policy.allowed !== "boolean")
    throw new TypeError("execution.allowed must be a boolean");
  if (typeof policy.network !== "boolean")
    throw new TypeError("execution.network must be a boolean");
  if (typeof policy.reason !== "string" || policy.reason.length === 0) {
    throw new TypeError("execution.reason must be a non-empty string");
  }
  if (policy.backend !== null && typeof policy.backend !== "string") {
    throw new TypeError("execution.backend must be a string or null");
  }
  const rawEnv = record(policy.sanitizedEnv, "execution.sanitizedEnv");
  if (Object.keys(rawEnv).length > 64)
    throw new TypeError("execution.sanitizedEnv has too many entries");
  const sanitizedEnv: NodeJS.ProcessEnv = {};
  for (const [name, raw] of Object.entries(rawEnv)) {
    if (
      (!SAFE_ENV.has(name) && !name.startsWith("LC_")) ||
      typeof raw !== "string" ||
      raw.length > 32_768 ||
      raw.includes("\0")
    ) {
      throw new TypeError(
        `execution.sanitizedEnv contains unsafe entry: ${name}`,
      );
    }
    sanitizedEnv[name] = raw;
  }
  const mode = policy.mode as ExecutionMode;
  if (policy.allowed !== (mode !== "denied")) {
    throw new TypeError("execution.allowed contradicts execution.mode");
  }
  if (mode === "sandbox" && policy.network) {
    throw new TypeError("sandbox execution must disable network");
  }
  if (mode !== "sandbox" && policy.backend !== null) {
    throw new TypeError("only sandbox execution may name a backend");
  }
  return {
    mode,
    allowed: policy.allowed,
    sanitizedEnv,
    network: policy.network,
    reason: policy.reason,
    backend: policy.backend as string | null,
  };
};

export const deniedExecutionResult = (policy: ExecutionPolicy) => ({
  status: "inconclusive" as const,
  diagnostics: [
    {
      code: "untrusted_execution_not_authorized",
      message: `repository code was not executed: ${policy.reason}`,
    },
  ],
});

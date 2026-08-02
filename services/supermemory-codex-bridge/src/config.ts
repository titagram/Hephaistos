export interface BridgeConfig {
  host: string;
  port: number;
  apiKey: string;
  publicModel: string;
  codexModel: string;
  codexHome: string;
  codexCwd: string;
  timeoutMs: number;
  maxBodyBytes: number;
  maxConcurrency: number;
}

export class ConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigurationError";
  }
}

export function loadConfig(env: NodeJS.ProcessEnv): BridgeConfig {
  const required = (name: string): string => {
    const value = env[name]?.trim();

    if (!value) {
      throw new ConfigurationError(`${name} is required.`);
    }

    return value;
  };

  const positiveInteger = (name: string, fallback: number): number => {
    const rawValue = env[name];

    if (rawValue === undefined) {
      return fallback;
    }

    const value = rawValue.trim();
    if (!/^[1-9]\d*$/.test(value)) {
      throw new ConfigurationError(`${name} must be a positive integer.`);
    }

    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed)) {
      throw new ConfigurationError(`${name} must be a positive integer.`);
    }

    return parsed;
  };

  const port = positiveInteger("BRIDGE_PORT", 8646);
  if (port > 65_535) {
    throw new ConfigurationError("BRIDGE_PORT must be between 1 and 65535.");
  }

  return {
    host: env.BRIDGE_HOST?.trim() || "0.0.0.0",
    port,
    apiKey: required("BRIDGE_API_KEY"),
    publicModel: env.BRIDGE_PUBLIC_MODEL?.trim() || "supermemory-codex",
    codexModel: required("CODEX_MODEL"),
    codexHome: required("CODEX_HOME"),
    codexCwd: env.CODEX_CWD?.trim() || "/workspace",
    timeoutMs: positiveInteger("BRIDGE_TIMEOUT_MS", 120_000),
    maxBodyBytes: positiveInteger("BRIDGE_MAX_BODY_BYTES", 2_097_152),
    maxConcurrency: positiveInteger("BRIDGE_MAX_CONCURRENCY", 2),
  };
}

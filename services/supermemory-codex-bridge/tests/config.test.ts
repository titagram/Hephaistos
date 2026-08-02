import assert from "node:assert/strict";
import test from "node:test";

import { loadConfig } from "../src/config.js";

const validEnv: NodeJS.ProcessEnv = {
  BRIDGE_API_KEY: "bridge-secret",
  CODEX_MODEL: "gpt-5.3-codex",
  CODEX_HOME: "/var/lib/codex",
};

test("loads safe defaults", () => {
  assert.deepEqual(loadConfig(validEnv), {
    host: "0.0.0.0",
    port: 8646,
    apiKey: "bridge-secret",
    publicModel: "supermemory-codex",
    codexModel: "gpt-5.3-codex",
    codexHome: "/var/lib/codex",
    codexCwd: "/workspace",
    timeoutMs: 120_000,
    maxBodyBytes: 2_097_152,
    maxConcurrency: 2,
  });
});

test("rejects invalid required values and concurrency", () => {
  assert.throws(() => loadConfig({ ...validEnv, BRIDGE_API_KEY: "" }), /BRIDGE_API_KEY/);
  assert.throws(() => loadConfig({ ...validEnv, CODEX_MODEL: "" }), /CODEX_MODEL/);
  assert.throws(
    () => loadConfig({ ...validEnv, BRIDGE_MAX_CONCURRENCY: "0" }),
    /positive integer/,
  );
});

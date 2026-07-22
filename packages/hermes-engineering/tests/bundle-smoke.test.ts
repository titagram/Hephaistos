import { execFileSync, spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

const repositoryRoot = resolve(import.meta.dirname, "../../..");
const bundle = resolve(
  repositoryRoot,
  "hermes_cli/engineering_dist/hermes-engineering.mjs",
);
const notice = resolve(
  repositoryRoot,
  "hermes_cli/engineering_dist/NOTICE.qwen-code",
);
const isolatedDirectory = mkdtempSync(join(tmpdir(), "hermes-engineering-"));
const isolatedBundle = join(isolatedDirectory, basename(bundle));

const request = JSON.stringify({
  protocolVersion: 1,
  requestId: "smoke-1",
  command: "capture-target",
  workspace: isolatedDirectory,
  artifactRoot: join(isolatedDirectory, "artifacts"),
  input: {},
});

beforeAll(() => {
  execFileSync(process.execPath, ["scripts/build_engineering_review.mjs"], {
    cwd: repositoryRoot,
    stdio: "pipe",
  });
  copyFileSync(bundle, isolatedBundle);
  copyFileSync(notice, join(isolatedDirectory, basename(notice)));
});

afterAll(() => rmSync(isolatedDirectory, { recursive: true, force: true }));

describe("standalone engineering review bundle", () => {
  it("has no non-Node runtime imports", () => {
    const source = readFileSync(isolatedBundle, "utf8");
    const imports = [...source.matchAll(/\bfrom\s+["']([^"']+)["']/g)].map(
      (match) => match[1],
    );
    expect(imports.every((specifier) => specifier?.startsWith("node:"))).toBe(
      true,
    );
  });

  it("runs with only the bundle and notice available", () => {
    const environment = { ...process.env };
    delete environment.NODE_PATH;
    const result = spawnSync(process.execPath, [isolatedBundle], {
      cwd: isolatedDirectory,
      env: environment,
      input: request,
      encoding: "utf8",
    });

    expect(result.status).toBe(0);
    expect(result.stderr).toBe("");
    expect(result.stdout.endsWith("\n")).toBe(true);
    expect(result.stdout.trimEnd().split("\n")).toHaveLength(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      protocolVersion: 1,
      requestId: "smoke-1",
      status: "inconclusive",
      diagnostics: [{ code: "handler_not_implemented" }],
    });
  });

  it("returns exit code 2 and a typed response for invalid input", () => {
    const result = spawnSync(process.execPath, [isolatedBundle], {
      cwd: isolatedDirectory,
      env: { PATH: process.env.PATH ?? "" },
      input: '{"protocolVersion":2}',
      encoding: "utf8",
    });

    expect(result.status).toBe(2);
    expect(JSON.parse(result.stdout)).toMatchObject({
      protocolVersion: 1,
      status: "failed",
      diagnostics: [{ code: "invalid_request" }],
    });
  });
});

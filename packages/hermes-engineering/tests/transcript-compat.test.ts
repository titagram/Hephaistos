import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

import { afterEach, describe, expect, it } from "vitest";

import { readTranscripts } from "../../../third_party/qwen-code/packages/cli/src/commands/review/lib/transcripts.js";

const repositoryRoot = resolve(import.meta.dirname, "../../..");
const temporaryRoots: string[] = [];

function pythonExecutable(): string {
  const candidates = [
    process.env["PYTHON"],
    resolve(repositoryRoot, ".venv/bin/python"),
    resolve(repositoryRoot, "venv/bin/python"),
    resolve(repositoryRoot, ".venv/Scripts/python.exe"),
  ];
  return (
    candidates.find((candidate) => candidate && existsSync(candidate)) ??
    "python3"
  );
}

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    rmSync(root, { force: true, recursive: true });
  }
});

describe("Hermes reviewer transcript compatibility", () => {
  it("is consumed directly by Qwen readTranscripts", () => {
    const testRoot = mkdtempSync(
      resolve(tmpdir(), "hermes-review-transcript-"),
    );
    temporaryRoots.push(testRoot);
    const script = String.raw`
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from agent.review_evidence import write_reviewer_transcript
from hermes_cli.engineering_review.runs import ReviewRun

root = Path(sys.argv[1])
workspace = root / "workspace"
workspace.mkdir()
run = ReviewRun.create(workspace, target="local", effort="medium", session_id="parent")
diff = root / "review.diff"
brief = root / "chunk-1.brief.md"
diff.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
brief.write_text("Review the chunk.", encoding="utf-8")
run.atomic_artifact("plan.json", json.dumps({"diffPathAbsolute": str(diff)}).encode())
prompt = f"Hermes-Review-Run: {run.run_id}\nHermes-Review-Plan: {run.root / 'plan.json'}\nRead {brief} and {diff}."
child = SimpleNamespace(_delegate_role="reviewer", _subagent_goal=prompt, _subagent_id="compat")
result = {
    "final_response": "No findings.",
    "messages": [
        {"role": "assistant", "tool_calls": [
            {"id": "diff", "function": {"name": "read_file", "arguments": json.dumps({"file_path": str(diff), "offset": 1, "limit": 3})}},
            {"id": "brief", "function": {"name": "read_file", "arguments": json.dumps({"file_path": str(brief)})}},
            {"id": "denied", "function": {"name": "read_file", "arguments": json.dumps({"file_path": "/denied"})}},
            {"id": "cancelled", "function": {"name": "read_file", "arguments": json.dumps({"file_path": "/cancelled"})}},
        ]},
        {"role": "tool", "tool_call_id": "diff", "content": "two\nthree\nfour"},
        {"role": "tool", "tool_call_id": "brief", "content": "Review the chunk."},
        {"role": "tool", "tool_call_id": "denied", "content": "Denied by the approval policy"},
        {"role": "tool", "tool_call_id": "cancelled", "content": "[Tool execution cancelled — read_file was skipped due to user interrupt]"},
    ],
}
path = write_reviewer_transcript("parent", child, result)
if path is None:
    raise SystemExit("transcript was not written")
print(json.dumps({"runRoot": str(run.root), "diff": str(diff), "brief": str(brief), "prompt": prompt}))
`;
    const proc = spawnSync(pythonExecutable(), ["-c", script, testRoot], {
      cwd: repositoryRoot,
      encoding: "utf8",
      env: { ...process.env, HERMES_HOME: resolve(testRoot, "hermes-home") },
    });
    expect(proc.status, proc.stderr).toBe(0);
    const fixture = JSON.parse(proc.stdout) as {
      runRoot: string;
      diff: string;
      brief: string;
      prompt: string;
    };

    const records = readTranscripts(
      undefined,
      {
        QWEN_CODE_PROJECT_DIR: fixture.runRoot,
        QWEN_CODE_SESSION_ID: "reviewers",
      },
      fixture.diff,
    );
    expect(records).toHaveLength(1);
    const record = records[0];
    if (!record) throw new Error("Python transcript was not parsed");

    expect(record.launchPrompt).toBe(fixture.prompt);
    expect(record.successfulToolCalls).toBe(2);
    expect(record.diffToolCalls).toBe(1);
    expect(record.diffReads).toEqual([[2, 4]]);
    expect(record.successfulCallArgs).toContain(
      JSON.stringify({ file_path: fixture.brief }),
    );
    expect(record.finalText).toBe("No findings.");
  });
});

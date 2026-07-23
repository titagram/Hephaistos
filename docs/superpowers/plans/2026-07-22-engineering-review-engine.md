# Hermes Engineering Review Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an autonomous `hermes review` workflow that reuses a narrow Apache-2.0 TypeScript slice from Qwen Code for deterministic diff planning, review coverage, test efficacy, anchor resolution, and verdict composition while Hermes retains control of models, evidence, approvals, and publication.

**Architecture:** Selected Qwen review sources remain TypeScript under `third_party/qwen-code` and are bundled with Hermes-owned adapters into one Node 22 ESM file. A typed Python facade invokes that bundle through one-request/one-response JSON subprocesses; the existing Hermes agent and `delegate_task(role="reviewer")` perform model work and write harness-authored evidence consumed by the upstream coverage logic.

**Tech Stack:** Python 3.11–3.13, TypeScript, Node 22, esbuild, Vitest, pytest, Git worktrees, `gh`, JSON over stdio, setuptools package data.

## Global Constraints

- Use Qwen Code commit `d064bd7dcf98e0255283068a775f6e49d70db8aa` as the initial upstream baseline.
- Retain Apache-2.0 headers, license text, copyright, provenance, and modified-file notices for every reused file.
- Runtime requires Node 22 but must not require Qwen Code, npm, TypeScript, esbuild, `node_modules`, Qwen credentials, or network fetching.
- The checked-in release bundle is `hermes_cli/engineering_dist/hermes-engineering.mjs`; only release/build tooling regenerates it.
- Do not add a core model tool or mutate the tool schema/system prompt during a conversation.
- `hermes review` and its skill never post, approve, push, or merge. Existing GitHub flows own explicitly authorized publication.
- The Node engine is deterministic and never calls a model.
- Local review includes staged, unstaged, and untracked files without changing the user's index or working tree.
- Remote PR code runs only in a configured sandbox or after explicit unsandboxed-execution consent; subprocess environments contain no provider secrets.
- Every check returns `passed`, `failed`, or `inconclusive`; infrastructure/compile/import failures never become `passed`.
- Coverage fails closed when required harness evidence is absent or unverifiable.
- Store no hidden reasoning and upload no raw prompts, transcripts, secrets, or environment content to Hades.
- `review.retention_runs` is a `config.yaml` setting with default `30`; active runs are never pruned.
- High effort stops after two consecutive dry reverse-audit rounds or five total rounds.

---

## File and module map

| Path | Responsibility |
|---|---|
| `scripts/sync_qwen_engineering.py` | Reproduce the allowlisted upstream extraction and provenance manifest |
| `third_party/qwen-code/UPSTREAM.json` | Pin source commit, exact copied paths, hashes, shims, and patch order |
| `third_party/qwen-code/packages/cli/src/commands/review/` | Unmodified Qwen-derived deterministic source and selected upstream tests |
| `packages/hermes-engineering/src/protocol.ts` | Runtime validation and versioned request/response types |
| `packages/hermes-engineering/src/handlers/` | Hermes adapters around upstream operations |
| `packages/hermes-engineering/src/main.ts` | One-request/one-response stdin/stdout executable |
| `hermes_cli/engineering_dist/hermes-engineering.mjs` | Prebuilt runtime bundle shipped in wheels/images |
| `hermes_cli/engineering_review/bridge.py` | Safe Node resolution, spawning, timeout, and response validation |
| `hermes_cli/engineering_review/runs.py` | Profile-local run/artifact lifecycle, permissions, and retention |
| `hermes_cli/engineering_review/pytest_probe.py` | Structured pytest collection and assertion-outcome adapter |
| `agent/review_evidence.py` | Harness-authored Qwen-compatible reviewer transcript writer |
| `hermes_cli/engineering_review/command.py` | Public command orchestration and chat handoff |
| `hermes_cli/subcommands/review.py` | `argparse` registration for `hermes review` |
| `skills/software-development/requesting-code-review/SKILL.md` | Model orchestration using deterministic engine operations |

## Stable interfaces used across tasks

Python request/result contracts:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

CheckStatus = Literal["passed", "failed", "inconclusive"]

@dataclass(frozen=True)
class EngineRequest:
    protocol_version: int
    request_id: str
    command: str
    workspace: Path
    artifact_root: Path
    input: Mapping[str, Any]

@dataclass(frozen=True)
class EngineResponse:
    protocol_version: int
    request_id: str
    status: CheckStatus
    output: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
```

TypeScript equivalents:

```ts
export type CheckStatus = 'passed' | 'failed' | 'inconclusive';

export interface EngineRequest {
  protocolVersion: 1;
  requestId: string;
  command: EngineCommand;
  workspace: string;
  artifactRoot: string;
  input: Record<string, unknown>;
}

export interface EngineResponse {
  protocolVersion: 1;
  requestId: string;
  status: CheckStatus;
  output: Record<string, unknown>;
  diagnostics: Array<{ code: string; message: string }>;
}
```

The initial `EngineCommand` union is:

```ts
export type EngineCommand =
  | 'capture-target'
  | 'build-prompts'
  | 'build-test'
  | 'test-efficacy'
  | 'check-coverage'
  | 'resolve-anchors'
  | 'compose-review'
  | 'cleanup';
```

---

### Task 1: Reproducible Qwen source extraction and licensing

**Files:**
- Create: `scripts/sync_qwen_engineering.py`
- Create: `scripts/qwen_engineering_allowlist.json`
- Create: `third_party/qwen-code/LICENSE`
- Create: `third_party/qwen-code/NOTICE`
- Create: `third_party/qwen-code/UPSTREAM.json`
- Create: `third_party/qwen-code/packages/cli/src/commands/review/**` (only allowlisted files)
- Create: `third_party/qwen-code/packages/cli/src/services/review-worktree-lease.ts`
- Create: `third_party/qwen-code/packages/cli/src/services/review-worktree-lease.test.ts`
- Create: `tests/scripts/test_sync_qwen_engineering.py`
- Modify: `MANIFEST.in`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: a local Qwen checkout and an exact Git commit.
- Produces: `sync(source: Path, destination: Path, ref: str, allowlist: Path) -> dict[str, object]` and a deterministic `UPSTREAM.json` consumed by Task 2 and CI.

- [ ] **Step 1: Write failing provenance and escape tests**

```python
def test_sync_copies_only_allowlisted_files_and_hashes_them(tmp_path):
    upstream = make_git_upstream(tmp_path, {
        "packages/cli/src/commands/review/lib/diff-plan.ts": HEADER + "export const x = 1;\n",
        "packages/cli/src/commands/review/submit.ts": HEADER + "export const forbidden = 1;\n",
        "LICENSE": "Apache License Version 2.0\n",
    })
    out = tmp_path / "out"
    manifest = sync(upstream, out, git_head(upstream), ALLOWLIST)
    assert (out / "packages/cli/src/commands/review/lib/diff-plan.ts").is_file()
    assert not (out / "packages/cli/src/commands/review/submit.ts").exists()
    assert manifest["upstreamCommit"] == git_head(upstream)
    assert manifest["files"][0]["sha256"] == sha256_file(
        out / "packages/cli/src/commands/review/lib/diff-plan.ts"
    )

def test_sync_rejects_path_traversal_and_missing_apache_header(tmp_path):
    with pytest.raises(SyncError, match="relative POSIX path"):
        validate_allowlist(["../outside.ts"])
    with pytest.raises(SyncError, match="SPDX-License-Identifier: Apache-2.0"):
        validate_typescript_header("export const x = 1")
```

- [ ] **Step 2: Run the tests and confirm they fail before the module exists**

Run: `scripts/run_tests.sh tests/scripts/test_sync_qwen_engineering.py -q`

Expected: FAIL during import of `scripts.sync_qwen_engineering`.

- [ ] **Step 3: Implement the exact allowlist and synchronization script**

`scripts/qwen_engineering_allowlist.json` must contain exact paths, not directory globs:

```json
{
  "repository": "https://github.com/QwenLM/qwen-code.git",
  "files": [
    "LICENSE",
    "packages/cli/src/commands/review/agent-prompt.ts",
    "packages/cli/src/commands/review/build-test.ts",
    "packages/cli/src/commands/review/capture-local.ts",
    "packages/cli/src/commands/review/check-coverage.ts",
    "packages/cli/src/commands/review/cleanup.ts",
    "packages/cli/src/commands/review/compose-review.ts",
    "packages/cli/src/commands/review/fetch-pr.ts",
    "packages/cli/src/commands/review/plan-diff.ts",
    "packages/cli/src/commands/review/resolve-anchors.ts",
    "packages/cli/src/commands/review/test-efficacy.ts",
    "packages/cli/src/commands/review/agent-prompt.test.ts",
    "packages/cli/src/commands/review/build-test.test.ts",
    "packages/cli/src/commands/review/capture-local.test.ts",
    "packages/cli/src/commands/review/check-coverage.test.ts",
    "packages/cli/src/commands/review/cleanup.test.ts",
    "packages/cli/src/commands/review/compose-review.test.ts",
    "packages/cli/src/commands/review/fetch-pr.test.ts",
    "packages/cli/src/commands/review/plan-diff.test.ts",
    "packages/cli/src/commands/review/resolve-anchors.test.ts",
    "packages/cli/src/commands/review/test-efficacy.integration.test.ts",
    "packages/cli/src/commands/review/test-efficacy.test.ts",
    "packages/cli/src/commands/review/lib/agent-briefs.ts",
    "packages/cli/src/commands/review/lib/anchors.ts",
    "packages/cli/src/commands/review/lib/coverage.ts",
    "packages/cli/src/commands/review/lib/diff-flags.ts",
    "packages/cli/src/commands/review/lib/diff-plan.ts",
    "packages/cli/src/commands/review/lib/gh.ts",
    "packages/cli/src/commands/review/lib/git.ts",
    "packages/cli/src/commands/review/lib/heavy.ts",
    "packages/cli/src/commands/review/lib/inline-counts.ts",
    "packages/cli/src/commands/review/lib/local-diff.ts",
    "packages/cli/src/commands/review/lib/merge-base.ts",
    "packages/cli/src/commands/review/lib/path-rules.ts",
    "packages/cli/src/commands/review/lib/paths.ts",
    "packages/cli/src/commands/review/lib/prompt-record.ts",
    "packages/cli/src/commands/review/lib/report.ts",
    "packages/cli/src/commands/review/lib/roster.ts",
    "packages/cli/src/commands/review/lib/shell-quote.ts",
    "packages/cli/src/commands/review/lib/transcripts.ts",
    "packages/cli/src/commands/review/lib/workspaces.ts",
    "packages/cli/src/commands/review/lib/anchors.test.ts",
    "packages/cli/src/commands/review/lib/diff-plan.integration.test.ts",
    "packages/cli/src/commands/review/lib/diff-plan.test.ts",
    "packages/cli/src/commands/review/lib/gh.test.ts",
    "packages/cli/src/commands/review/lib/git.integration.test.ts",
    "packages/cli/src/commands/review/lib/git.test.ts",
    "packages/cli/src/commands/review/lib/local-diff.integration.test.ts",
    "packages/cli/src/commands/review/lib/merge-base.test.ts",
    "packages/cli/src/commands/review/lib/path-rules.test.ts",
    "packages/cli/src/commands/review/lib/paths.test.ts",
    "packages/cli/src/commands/review/lib/prompt-record.test.ts",
    "packages/cli/src/commands/review/lib/report.test.ts",
    "packages/cli/src/commands/review/lib/roster.test.ts",
    "packages/cli/src/commands/review/lib/transcripts.test.ts",
    "packages/cli/src/commands/review/lib/workspaces.test.ts",
    "packages/cli/src/services/review-worktree-lease.ts",
    "packages/cli/src/services/review-worktree-lease.test.ts"
  ]
}
```

The script must use `git show <ref>:<path>` rather than copy a mutable working tree, validate every destination with `PurePosixPath`, require the Apache header for `.ts`, write atomically, and delete only previously manifested files that are no longer allowlisted. It statically resolves relative TypeScript imports (including `.js` specifiers that map to `.ts`) and fails when an import targets an upstream file outside the allowlist; package imports must appear in an explicit dependency-or-shim table, so an upstream update cannot silently widen the slice.

```python
def sync(source: Path, destination: Path, ref: str, allowlist: Path) -> dict[str, object]:
    commit = run_git(source, "rev-parse", f"{ref}^{{commit}}")
    paths = validate_allowlist(json.loads(allowlist.read_text())["files"])
    records = []
    for rel in paths:
        data = subprocess.run(
            ["git", "-C", str(source), "show", f"{commit}:{rel}"],
            check=True, capture_output=True,
        ).stdout
        if rel.endswith(".ts"):
            validate_typescript_header(data.decode("utf-8"))
        atomic_write(destination / rel, data)
        records.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "schemaVersion": 1,
        "repository": "https://github.com/QwenLM/qwen-code.git",
        "upstreamCommit": commit,
        "files": records,
        "hermesShims": [],
        "patches": [],
    }
    atomic_write(destination / "UPSTREAM.json", canonical_json(manifest))
    return manifest
```

- [ ] **Step 4: Extract the pinned source and preserve license material**

Run:

```bash
python scripts/sync_qwen_engineering.py \
  --source /tmp/qwen-code-target-ggiKGQ/source \
  --ref d064bd7dcf98e0255283068a775f6e49d70db8aa \
  --destination third_party/qwen-code
```

Expected: `UPSTREAM.json` lists only the exact allowlist, every hash verifies, and `submit.ts` is absent. Add `third_party/qwen-code/NOTICE` stating the source URL, commit, copied subpaths, Apache-2.0 status, and that Hermes adapter files are separate MIT code except for explicitly attributed Qwen-derived shims.

- [ ] **Step 5: Package the required license/provenance files**

Add to `MANIFEST.in`:

```text
include third_party/qwen-code/LICENSE
include third_party/qwen-code/NOTICE
include third_party/qwen-code/UPSTREAM.json
recursive-include third_party/qwen-code/packages/cli/src/commands/review *.ts
recursive-include third_party/qwen-code/packages/cli/src/services review-worktree-lease*.ts
```

Add the Apache license to `pyproject.toml`:

```toml
license-files = ["LICENSE", "third_party/qwen-code/LICENSE"]
```

- [ ] **Step 6: Verify and commit**

Run:

```bash
scripts/run_tests.sh tests/scripts/test_sync_qwen_engineering.py -q
python scripts/sync_qwen_engineering.py --verify --destination third_party/qwen-code
git diff --check
```

Expected: all commands exit 0 and `--verify` reports the pinned commit and zero hash mismatches.

Commit:

```bash
git add scripts/sync_qwen_engineering.py scripts/qwen_engineering_allowlist.json \
  third_party/qwen-code MANIFEST.in pyproject.toml tests/scripts/test_sync_qwen_engineering.py
git commit -m "build: vendor Qwen review engine sources"
```

---

### Task 2: TypeScript protocol, upstream shims, and self-contained bundle

**Files:**
- Create: `packages/hermes-engineering/package.json`
- Create: `packages/hermes-engineering/tsconfig.json`
- Create: `packages/hermes-engineering/vitest.config.ts`
- Create: `packages/hermes-engineering/src/protocol.ts`
- Create: `packages/hermes-engineering/src/main.ts`
- Create: `packages/hermes-engineering/src/handlers/index.ts`
- Create: `packages/hermes-engineering/src/shims/stdioHelpers.ts`
- Create: `packages/hermes-engineering/src/shims/qwenCore.ts`
- Create: `packages/hermes-engineering/tests/protocol.test.ts`
- Create: `packages/hermes-engineering/tests/bundle-smoke.test.ts`
- Create: `scripts/build_engineering_review.mjs`
- Create: `hermes_cli/engineering_dist/NOTICE.qwen-code`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `third_party/qwen-code/UPSTREAM.json`

**Interfaces:**
- Consumes: vendored functions from Task 1.
- Produces: `parseRequest(value: unknown): EngineRequest`, `dispatch(req): Promise<EngineResponse>`, and `hermes_cli/engineering_dist/hermes-engineering.mjs`.

- [ ] **Step 1: Add failing protocol tests**

```ts
it('rejects unknown protocol versions without dispatching', () => {
  expect(() => parseRequest({ protocolVersion: 2 })).toThrow(/protocolVersion 1/);
});

it('returns a typed inconclusive response while a valid handler is not installed', async () => {
  const response = await dispatch(validRequest({ command: 'capture-target' }));
  expect(response).toMatchObject({
    protocolVersion: 1,
    status: 'inconclusive',
    diagnostics: [{ code: 'handler_not_implemented' }],
  });
});
```

- [ ] **Step 2: Run Vitest and confirm failure**

Run: `npm test --workspace packages/hermes-engineering -- protocol.test.ts`

Expected: FAIL because the workspace and protocol module do not exist.

- [ ] **Step 3: Add the isolated workspace and strict protocol parser**

`packages/hermes-engineering/package.json`:

```json
{
  "name": "@hermes/engineering-review",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "build": "node ../../scripts/build_engineering_review.mjs",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": { "yargs": "17.7.2" },
  "devDependencies": {
    "@types/node": "^24.13.2",
    "esbuild": "^0.28.1",
    "typescript": "^6.0.3",
    "vitest": "^4.1.5"
  }
}
```

Add `packages/hermes-engineering` to the root `workspaces` array and implement `parseRequest` with explicit key/type checks, `resolve()` of both paths, a 1 MiB request ceiling, and the exact command union in the Stable Interfaces section. The development dependency ranges intentionally match the repository's existing lockfile toolchain; `yargs` stays on Qwen's compatible 17.7.2 API. Configure Vitest to include the selected upstream tests under `third_party/qwen-code` as well as Hermes adapter tests.

- [ ] **Step 4: Add the stdio shim and bundle entry point**

```ts
export const writeStdoutLine = (line: string): void => process.stdout.write(`${line}\n`);
export const writeStderrLine = (line: string): void => process.stderr.write(`${line}\n`);
```

Both Vitest and esbuild resolve Qwen's `../../utils/stdioHelpers.js` imports to this shim. They resolve `@qwen-code/qwen-code-core` to `qwenCore.ts`, which exposes only the imported `unquoteCStylePath` and a no-op `createDebugLogger`. Preserve the upstream `unquoteCStylePath` algorithm and Apache header in that shim, record its source path/hash under `hermesShims` in `UPSTREAM.json`, and cover quoted UTF-8/octal/control-character paths with upstream tests. This keeps the selected upstream files byte-identical and prevents an accidental dependency on the full Qwen core.

`main.ts` must read stdin to EOF, reject more than 1 MiB, dispatch once, write exactly `JSON.stringify(response) + '\n'` to stdout, and set exit code `0` for valid check results or `2` for invalid protocol/input. Unhandled errors become a typed `inconclusive` response and exit `3`; stack traces go only to stderr when explicitly enabled in development tests.

- [ ] **Step 5: Build a single runtime file and assert it has no external imports**

```js
await build({
  entryPoints: ['packages/hermes-engineering/src/main.ts'],
  outfile: 'hermes_cli/engineering_dist/hermes-engineering.mjs',
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node22',
  packages: 'bundle',
  sourcemap: false,
  legalComments: 'eof',
});
```

Copy the Qwen attribution into `NOTICE.qwen-code`. Add a test that copies only the generated `.mjs` and notice into an empty directory, runs Node with `NODE_PATH` unset, and receives a valid response.

- [ ] **Step 6: Verify and commit**

Run:

```bash
npm install
npm run typecheck --workspace packages/hermes-engineering
npm test --workspace packages/hermes-engineering
npm run build --workspace packages/hermes-engineering
```

Expected: typecheck and tests pass; `bundle-smoke.test.ts` proves that a copy of the generated file prints one valid JSON line with `NODE_PATH` unset and no `node_modules` available.

Commit:

```bash
git add package.json package-lock.json packages/hermes-engineering scripts/build_engineering_review.mjs \
  hermes_cli/engineering_dist third_party/qwen-code/UPSTREAM.json
git commit -m "build: add autonomous engineering review bundle"
```

---

### Task 3: Python bridge and packaged-bundle contract

**Files:**
- Create: `hermes_cli/engineering_review/__init__.py`
- Create: `hermes_cli/engineering_review/protocol.py`
- Create: `hermes_cli/engineering_review/bridge.py`
- Create: `tests/hermes_cli/engineering_review/test_bridge.py`
- Modify: `pyproject.toml`
- Modify: `MANIFEST.in`

**Interfaces:**
- Consumes: Task 2 bundle and `hermes_constants.find_node_executable("node")`.
- Produces: `EngineeringReviewBridge.invoke(request: EngineRequest, timeout: float, cancel_event: threading.Event | None = None) -> EngineResponse` and `bundle_path() -> Path`.

- [ ] **Step 1: Write failing bridge tests**

```python
def test_bridge_uses_managed_node_and_exactly_one_json_document(monkeypatch, tmp_path):
    node = make_fake_node(tmp_path, stdout='{"protocolVersion":1,"requestId":"r1","status":"passed","output":{},"diagnostics":[]}\n')
    monkeypatch.setattr(bridge, "find_node_executable", lambda name: str(node))
    response = EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(request(tmp_path), timeout=2)
    assert response.status == "passed"

def test_bridge_rejects_extra_stdout(monkeypatch, tmp_path):
    node = make_fake_node(tmp_path, stdout='noise\n{}\n')
    monkeypatch.setattr(bridge, "find_node_executable", lambda name: str(node))
    with pytest.raises(EngineProtocolError, match="exactly one JSON document"):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(request(tmp_path), timeout=2)
```

Add parallel fixtures for timeout, an already-set and mid-flight cancellation event, malformed JSON, mismatched request ID/protocol version, nonzero exit, and stdout/stderr beyond their configured caps. Each must terminate the child process group and preserve the real typed failure rather than returning `passed`.

- [ ] **Step 2: Run tests and verify import failure**

Run: `scripts/run_tests.sh tests/hermes_cli/engineering_review/test_bridge.py -q`

Expected: FAIL importing `hermes_cli.engineering_review.bridge`.

- [ ] **Step 3: Implement immutable dataclasses and strict response parsing**

Use the Python contracts in Stable Interfaces. `EngineRequest.to_wire()` must emit camelCase keys, reject non-absolute workspace/artifact paths, and reject an artifact root outside `get_hermes_home() / "reviews"`. `EngineResponse.from_wire()` must reject booleans/numbers in string fields, unknown statuses, more than 200 diagnostics, and mismatched request IDs.

- [ ] **Step 4: Implement safe process execution**

```python
process = subprocess.Popen(
    [node, str(self.bundle)],
    stdin=subprocess.PIPE,
    stdout=bounded_stdout_file,
    stderr=bounded_stderr_file,
    cwd=request.workspace,
    env=sanitized_engine_env(with_hermes_node_path()),
    start_new_session=(os.name != "nt"),
    creationflags=windows_process_group_flags(),
)
```

Write the canonical request bytes, close stdin, and poll at a bounded interval for completion, timeout, or `cancel_event`. On timeout/cancellation terminate then kill the whole process group after a short grace period. Capture stdout/stderr in temporary files rather than unbounded pipes; reject stdout above 4 MiB and stderr above 1 MiB before reading. `sanitized_engine_env` keeps only platform/runtime essentials (`PATH`, `HOME`/Windows profile variables, temp variables, locale) and explicitly removes provider/API/token variables, `NODE_OPTIONS`, `NODE_PATH`, `PYTHONPATH`, Git credential helpers, and proxy credentials. Never use `shell=True`.

- [ ] **Step 5: Include the bundle in wheels and sdists**

Add package data:

```toml
hermes_cli = [
  "web_dist/**/*",
  "tui_dist/**/*",
  "engineering_dist/*.mjs",
  "engineering_dist/NOTICE.qwen-code",
  "scripts/install.sh",
  "scripts/install.ps1"
]
```

Add `recursive-include hermes_cli/engineering_dist *.mjs NOTICE.qwen-code` to `MANIFEST.in`.

- [ ] **Step 6: Verify and commit**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_bridge.py -q
python -m build --wheel --sdist
python -c 'import zipfile,glob; z=zipfile.ZipFile(glob.glob("dist/*.whl")[-1]); assert any(n.endswith("engineering_dist/hermes-engineering.mjs") for n in z.namelist())'
```

Expected: bridge tests pass and both distributions contain the bundle and Qwen notice.

Commit:

```bash
git add hermes_cli/engineering_review pyproject.toml MANIFEST.in tests/hermes_cli/engineering_review/test_bridge.py
git commit -m "feat: add engineering review Node bridge"
```

---

### Task 4: Review run lifecycle, artifact safety, and retention

**Files:**
- Create: `hermes_cli/engineering_review/runs.py`
- Create: `tests/hermes_cli/engineering_review/test_runs.py`
- Modify: `hermes_cli/config.py`
- Modify: `tests/hermes_cli/test_config.py`

**Interfaces:**
- Consumes: profile-aware `get_hermes_home()` and `review.retention_runs`.
- Produces: `ReviewRun.create(workspace, target, effort, session_id)`, `ReviewRun.load(run_id, session_id)`, `atomic_artifact(name, data)`, `mark_complete()`, and `prune_completed_runs(home, keep)`.

- [ ] **Step 1: Write failing path, permission, and retention tests**

```python
def test_run_root_is_profile_local_private_and_atomic(fake_home, tmp_path):
    run = ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1")
    assert run.root.parent == fake_home / "reviews" / "s1"
    assert stat.S_IMODE(run.root.stat().st_mode) == 0o700
    run.atomic_artifact("plan.json", b"{}")
    assert stat.S_IMODE((run.root / "plan.json").stat().st_mode) == 0o600

def test_prune_keeps_30_completed_and_every_active(fake_home):
    runs = make_runs(fake_home, completed=32, active=2)
    removed = prune_completed_runs(fake_home, keep=30)
    assert len(removed) == 2
    assert all(run.root.exists() for run in runs.active)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `scripts/run_tests.sh tests/hermes_cli/engineering_review/test_runs.py -q`

Expected: FAIL importing `ReviewRun`.

- [ ] **Step 3: Implement canonical run identity and state transitions**

Use `secrets.token_urlsafe(18)` for unguessable run IDs. Persist `run.json` with schema version, canonical workspace, target, effort, session ID, timestamps, status (`active|complete|cleanup_failed`), and bundle/provenance hashes. `load` must reject symlinks, ownership/root escapes, unknown schema/status, and session mismatch.

```python
@dataclass(frozen=True)
class ReviewRun:
    run_id: str
    root: Path
    workspace: Path
    target: str
    effort: Literal["low", "medium", "high"]
    session_id: str
```

- [ ] **Step 4: Add the configuration default**

Insert into `DEFAULT_CONFIG`:

```python
"review": {
    "retention_runs": 30,
    "default_effort": "medium",
    "engine_timeout_seconds": 120,
    "test_timeout_seconds": 900,
},
```

Coerce invalid/negative retention to `30`; `0` retains no completed runs but still never deletes active runs.

- [ ] **Step 5: Verify and commit**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_runs.py tests/hermes_cli/test_config.py -q
```

Expected: all tests pass, including a race test where two pruners run concurrently without deleting an active run.

Commit:

```bash
git add hermes_cli/engineering_review/runs.py hermes_cli/config.py \
  tests/hermes_cli/engineering_review/test_runs.py tests/hermes_cli/test_config.py
git commit -m "feat: persist bounded engineering review runs"
```

---

### Task 5: Local, Git-range, and pull-request target capture

**Files:**
- Create: `packages/hermes-engineering/src/handlers/capture-target.ts`
- Create: `packages/hermes-engineering/tests/capture-target.integration.test.ts`
- Modify: `packages/hermes-engineering/src/handlers/index.ts`
- Modify: `packages/hermes-engineering/src/protocol.ts`

**Interfaces:**
- Consumes: upstream `captureLocalDiff`, `buildDiffPlan`, `resolveMergeBase`, Git/worktree helpers, and a validated `ReviewRun` artifact root.
- Produces: `CaptureTargetOutput` with `targetKind`, `baseRef`, `headRef`, `diffPath`, `planPath`, `worktreePath`, `skippedFiles`, `files`, and `chunks`.

- [ ] **Step 1: Add failing real-Git integration tests**

```ts
it('captures staged, unstaged, and untracked files without changing git state', async () => {
  const repo = await fixtureRepo();
  await repo.write('staged.ts', 'staged\n');
  await repo.git('add', 'staged.ts');
  await repo.write('tracked.ts', 'unstaged\n');
  await repo.write('new.ts', 'untracked\n');
  const before = await repo.statusPorcelain();
  const out = await captureTarget(localRequest(repo));
  expect(await readFile(out.diffPath, 'utf8')).toContain('new.ts');
  expect(await repo.statusPorcelain()).toBe(before);
});

it('removes a PR worktree but never its source checkout', async () => {
  const repo = await fixtureRepoWithRef('refs/pull/7/head');
  const out = await captureTarget(prRequest(repo, 7));
  expect(out.worktreePath).not.toBe(repo.path);
  await cleanupCapture(out);
  expect(await exists(repo.path)).toBe(true);
  expect(await exists(out.worktreePath)).toBe(false);
});
```

Add real-Git cases for a commit, `A..B`, `A...B`, one file, rename, deletion, binary change, symlink/path escape, and a deliberately oversized untracked file. Assert correct merge-base identity, explicit skip metadata, and byte-identical porcelain state for every case.

- [ ] **Step 2: Run and confirm the handler is missing**

Run: `npm test --workspace packages/hermes-engineering -- capture-target.integration.test.ts`

Expected: FAIL resolving `handlers/capture-target.ts`.

- [ ] **Step 3: Implement target discrimination and safe capture**

The input union is exact:

```ts
type CaptureInput =
  | { kind: 'local' }
  | { kind: 'file'; path: string; base?: string }
  | { kind: 'range'; range: string }
  | { kind: 'pr'; number: number; ownerRepo: string };
```

Validate repository membership and relative file paths before calling upstream code. Local capture writes the combined diff into the run root. Range capture resolves its merge base and uses stable Git diff flags. PR capture fetches the exact head SHA, creates a sibling disposable worktree, and records both requested and resolved identities. Never call `git checkout`, `git reset`, or mutate the current index.

- [ ] **Step 4: Write and validate a shared plan artifact**

Use upstream `buildPlanReport`/`stringifyPlanReport`; add Hermes fields under a namespaced object rather than modifying upstream fields:

```ts
const report = {
  ...buildPlanReport(plan, postImageLines),
  hermes: {
    schemaVersion: 1,
    runId,
    targetKind,
    baseRef,
    headRef,
    diffSha256,
  },
  diffPathAbsolute: diffPath,
};
```

Reject empty diffs with a typed `failed/no_changes` result. Oversized/binary/untracked skips appear explicitly and cap the final verdict rather than disappearing.

- [ ] **Step 5: Verify and commit**

Run:

```bash
npm run typecheck --workspace packages/hermes-engineering
npm test --workspace packages/hermes-engineering -- capture-target.integration.test.ts
npm run build --workspace packages/hermes-engineering
```

Expected: all tests pass and every fixture's porcelain status is byte-identical before/after capture.

Commit:

```bash
git add packages/hermes-engineering hermes_cli/engineering_dist/hermes-engineering.mjs
git commit -m "feat: capture review targets without workspace mutation"
```

---

### Task 6: Test-runner contract and preserved Vitest efficacy probe

**Files:**
- Create: `packages/hermes-engineering/src/runners/types.ts`
- Create: `packages/hermes-engineering/src/runners/vitest.ts`
- Create: `packages/hermes-engineering/src/handlers/build-test.ts`
- Create: `packages/hermes-engineering/src/handlers/test-efficacy.ts`
- Create: `packages/hermes-engineering/tests/build-test.integration.test.ts`
- Create: `packages/hermes-engineering/tests/vitest-efficacy.integration.test.ts`
- Modify: `packages/hermes-engineering/src/handlers/index.ts`

**Interfaces:**
- Consumes: upstream `planTestEfficacy`, `classifyProbeRun`, and safe worktree cleanup.
- Produces: `TestRunner` and `TestEfficacyOutput` grouped into `unreachable`, `gated`, `inert`, and `inconclusive` per changed test file.

- [ ] **Step 1: Write failing classifications against real Vitest fixtures**

```ts
it.each([
  ['effective.test.ts', 'gated'],
  ['inert.test.ts', 'inert'],
  ['outside-workspace.test.ts', 'unreachable'],
  ['compile-error.test.ts', 'inconclusive'],
])('classifies %s as %s', async (file, verdict) => {
  const result = await runFixtureProbe(file);
  expect(result.tests.find((t) => t.path === file)?.verdict).toBe(verdict);
});
```

- [ ] **Step 2: Run and confirm missing runner failure**

Run: `npm test --workspace packages/hermes-engineering -- vitest-efficacy.integration.test.ts`

Expected: FAIL importing the runner.

- [ ] **Step 3: Define the runner boundary and adapt upstream code without copying its algorithm**

```ts
export interface TestRunner {
  readonly id: 'vitest' | 'pytest';
  detect(workspace: string, plan: DiffPlan): Promise<'yes' | 'no' | 'ambiguous'>;
  collectedFiles(workspace: string): Promise<Set<string>>;
  runFile(workspace: string, relativePath: string, timeoutMs: number): Promise<TestRun>;
}
```

The Vitest adapter retains Qwen's npm workspace membership and revert-probe logic. Move only process launching behind `runFile`; preserve the upstream rules that compile/import errors are inconclusive, fixture directories are not production revert targets, cleanup cannot escape the probe worktree, and classification is per file.

- [ ] **Step 4: Make runner choice deterministic**

`test-efficacy` accepts `runner: "auto" | "vitest" | "pytest"`. Auto succeeds only when exactly one runner returns `yes`; zero yields `inconclusive/no_runner`, and multiple yield `inconclusive/ambiguous_runner` with the explicit retry choices.

Adapt upstream `build-test` behind the same runner/process boundary. `build-test` executes only commands already discovered and recorded in `plan.json`, uses argument arrays or the repository's package-manager executable without interpolating model text, applies the execution timeout, and maps compilation/import/infrastructure failure to `inconclusive` rather than success. `build-test.integration.test.ts` covers a passing build, a genuine failing test, a compile/import failure, and timeout.

- [ ] **Step 5: Verify and commit**

Run:

```bash
npm test --workspace packages/hermes-engineering -- build-test.integration.test.ts vitest-efficacy.integration.test.ts
npm run typecheck --workspace packages/hermes-engineering
npm run build --workspace packages/hermes-engineering
```

Expected: all four classifications pass, the source checkout is unchanged, and the probe worktree is absent afterward even on timeout.

Commit:

```bash
git add packages/hermes-engineering hermes_cli/engineering_dist/hermes-engineering.mjs
git commit -m "feat: preserve Qwen Vitest efficacy checks"
```

---

### Task 7: Structured pytest adapter

**Files:**
- Create: `hermes_cli/engineering_review/pytest_probe.py`
- Create: `packages/hermes-engineering/src/runners/pytest.ts`
- Create: `tests/hermes_cli/engineering_review/test_pytest_probe.py`
- Create: `packages/hermes-engineering/tests/pytest-efficacy.integration.test.ts`
- Modify: `packages/hermes-engineering/src/runners/types.ts`

**Interfaces:**
- Consumes: `python -m hermes_cli.engineering_review.pytest_probe` and the Task 6 runner contract.
- Produces: JSON collection/run results without a new PyPI dependency and pytest efficacy classifications identical to Vitest semantics.

- [ ] **Step 1: Add failing pytest hook tests**

```python
def test_probe_distinguishes_assertion_from_import_error(tmp_path):
    write_pytest_fixture(tmp_path)
    asserted = run_probe(tmp_path, "run", "tests/test_assert.py")
    imported = run_probe(tmp_path, "run", "tests/test_import_error.py")
    assert asserted["outcome"] == "assertion_failed"
    assert imported["outcome"] == "collection_or_import_error"

def test_collect_reports_canonical_relative_files(tmp_path):
    write_pytest_fixture(tmp_path)
    result = run_probe(tmp_path, "collect")
    assert result["files"] == ["tests/test_assert.py", "tests/test_ok.py"]
```

- [ ] **Step 2: Run and confirm module absence**

Run: `scripts/run_tests.sh tests/hermes_cli/engineering_review/test_pytest_probe.py -q`

Expected: FAIL importing `pytest_probe`.

- [ ] **Step 3: Implement an internal pytest plugin that emits one JSON result**

```python
class ProbePlugin:
    def __init__(self, root: Path):
        self.root = root
        self.files: set[str] = set()
        self.call_failures: list[str] = []
        self.non_call_failures: list[str] = []

    def pytest_collection_finish(self, session):
        for item in session.items:
            self.files.add(Path(str(item.path)).resolve().relative_to(self.root).as_posix())

    def pytest_runtest_logreport(self, report):
        if report.failed and report.when == "call":
            self.call_failures.append(report.nodeid)
        elif report.failed:
            self.non_call_failures.append(report.nodeid)
```

Invoke `pytest.main` in this subprocess only. `run` returns `assertion_failed` only when at least one call-phase assertion fails; collection, import, setup, teardown, internal error, interruption, or timeout are structured inconclusive outcomes.

- [ ] **Step 4: Implement the Node pytest runner and integration fixtures**

For a single-file probe, spawn `[python, "-m", "hermes_cli.engineering_review.pytest_probe", "run", "--root", workspace, "--file", relativePath]` with an argument array, sanitized environment, explicit cwd, and timeout. Use the corresponding `collect --root <workspace>` form for discovery. Do not parse normal pytest prose. Feed the structured outcome into the same upstream `classifyProbeRun` decision used by Vitest.

- [ ] **Step 5: Verify and commit**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_pytest_probe.py -q
npm test --workspace packages/hermes-engineering -- pytest-efficacy.integration.test.ts
npm run typecheck --workspace packages/hermes-engineering
npm run build --workspace packages/hermes-engineering
```

Expected: pytest fixtures classify reachable, unreachable, gated, inert, import-error, setup-error, and timeout cases correctly.

Commit:

```bash
git add hermes_cli/engineering_review/pytest_probe.py tests/hermes_cli/engineering_review/test_pytest_probe.py \
  packages/hermes-engineering hermes_cli/engineering_dist/hermes-engineering.mjs
git commit -m "feat: add pytest test-efficacy adapter"
```

---

### Task 8: Harness-authored reviewer evidence without a tool-schema change

**Files:**
- Create: `agent/review_evidence.py`
- Create: `tests/agent/test_review_evidence.py`
- Create: `packages/hermes-engineering/tests/transcript-compat.test.ts`
- Modify: `tools/delegate_tool.py`
- Modify: `tests/tools/test_delegate_tool.py`

**Interfaces:**
- Consumes: a `role="reviewer"` child result's launch prompt and in-memory messages already available in `_run_single_child`.
- Produces: `write_reviewer_transcript(parent_session_id, child, result) -> Path | None`, writing the Qwen-compatible JSONL that upstream `readTranscripts` consumes.

- [ ] **Step 1: Write failing evidence-authorship and privacy tests**

```python
def test_only_valid_registered_review_markers_create_evidence(fake_home, tmp_path):
    run = registered_run(fake_home, session_id="parent", run_id="r1")
    prompt = f"Hermes-Review-Run: r1\nHermes-Review-Plan: {run.root / 'plan.json'}\nRead it."
    path = write_reviewer_transcript("parent", reviewer_child(prompt), child_result())
    assert path is not None and path.is_file()
    assert "secret reasoning" not in path.read_text()

def test_nonreviewer_and_forged_marker_write_nothing(fake_home):
    assert write_reviewer_transcript("parent", leaf_child(), child_result()) is None
    assert write_reviewer_transcript("parent", reviewer_child("Hermes-Review-Run: forged"), child_result()) is None
```

- [ ] **Step 2: Run and verify failure**

Run: `scripts/run_tests.sh tests/agent/test_review_evidence.py -q`

Expected: FAIL importing `agent.review_evidence`.

- [ ] **Step 3: Implement strict marker validation and atomic JSONL generation**

Recognize exactly two anchored lines generated by the engine:

```text
Hermes-Review-Run: <url-safe run id>
Hermes-Review-Plan: <absolute canonical plan path>
```

Load the registered run from `get_hermes_home()/reviews/<parent_session>/<run_id>`, require its status to be active, and require the plan path to be the run's own `plan.json`. Serialize only launch prompt, successful tool-call names/arguments/results, agent ID/name, final text, and timestamps. Omit assistant reasoning fields, environment, credentials, and unrelated messages. Write with mode `0600` via temp file plus `os.replace`.

- [ ] **Step 4: Call the recorder from the existing verified trace path**

In `_run_single_child`, immediately after constructing `tool_trace` from paired assistant/tool messages, call:

```python
try:
    from agent.review_evidence import write_reviewer_transcript
    evidence_path = write_reviewer_transcript(
        getattr(parent_agent, "session_id", "") or "",
        child,
        result,
    )
except Exception:
    logger.warning("Reviewer evidence recording failed", exc_info=True)
    evidence_path = None
```

Return only a relative `review_evidence_ref` in the harness-authored result entry. Do not add any `delegate_task` schema argument and do not accept an evidence path from the model.

- [ ] **Step 5: Verify Qwen transcript compatibility**

Add a Node fixture test that points upstream `readTranscripts` at the Python-produced directory and asserts launch prompt, successful call count, diff reads with offset/limit, brief reads, and final text. Run:

```bash
scripts/run_tests.sh tests/agent/test_review_evidence.py tests/tools/test_delegate_tool.py -q
npm test --workspace packages/hermes-engineering -- transcript-compat.test.ts
```

Expected: all pass; a denied/error tool call does not count as a successful read.

- [ ] **Step 6: Commit**

```bash
git add agent/review_evidence.py tools/delegate_tool.py tests/agent/test_review_evidence.py \
  tests/tools/test_delegate_tool.py packages/hermes-engineering/tests/transcript-compat.test.ts
git commit -m "feat: record reviewer coverage evidence"
```

---

### Task 9: Prompt construction and fail-closed coverage

**Files:**
- Create: `packages/hermes-engineering/src/handlers/build-prompts.ts`
- Create: `packages/hermes-engineering/src/handlers/check-coverage.ts`
- Create: `packages/hermes-engineering/tests/review-coverage.integration.test.ts`
- Modify: `packages/hermes-engineering/src/handlers/index.ts`

**Interfaces:**
- Consumes: upstream `requiredAgents`, `buildRoleLaunchPrompt`, `buildChunkLaunchPrompt`, prompt recording, and `coverageFromTranscripts` plus Task 8 evidence layout.
- Produces: `prompts.json` with exact immutable prompts and a `CoverageFromTranscripts` result.

- [ ] **Step 1: Write failing roster and coverage tests**

```ts
it('builds exactly the required medium roster and records byte-identical prompts', async () => {
  const out = await buildPrompts(mediumPlan());
  expect(out.prompts).toHaveLength(3);
  for (const prompt of out.prompts) {
    expect(prompt.text).toContain(`Hermes-Review-Run: ${out.runId}`);
    expect(await readRecordedPrompt(out.planPath, prompt.key)).toBe(prompt.text);
  }
});

it('fails closed for a missing, idle, rewritten, or unopened reviewer', async () => {
  for (const fixture of ['missing', 'idle', 'rewritten', 'unopened']) {
    expect((await coverageFixture(fixture)).status).toBe('failed');
  }
});
```

- [ ] **Step 2: Run and verify handlers are missing**

Run: `npm test --workspace packages/hermes-engineering -- review-coverage.integration.test.ts`

Expected: FAIL resolving the new handlers.

- [ ] **Step 3: Implement effort-aware roster mapping**

Keep upstream risk/chunk roster logic, with a Hermes cap:

```ts
const effortLimits = {
  low: { maxReviewers: 1, verifyFindings: false, reverseAudit: false },
  medium: { maxReviewers: 3, verifyFindings: true, reverseAudit: false },
  high: { maxReviewers: 24, verifyFindings: true, reverseAudit: true },
} as const;
```

If upstream requires more roles than the cap, preserve source/chunk coverage first and list omitted specialists in the plan. Omitted required chunk coverage is not allowed; split execution into bounded waves instead.

- [ ] **Step 4: Point upstream coverage at harness-authored evidence**

Set the upstream transcript environment only inside the Node child:

```ts
const env = {
  ...process.env,
  QWEN_CODE_PROJECT_DIR: runArtifactRoot,
  QWEN_CODE_SESSION_ID: 'reviewers',
};
```

Task 8 stores transcripts at `<runArtifactRoot>/subagents/reviewers/agent-<id>.jsonl`. Call `coverageFromTranscripts(planPath, env)` through a minimal patch/shim if the upstream signature requires `process.env`; record that patch in `UPSTREAM.json`. Missing transcript infrastructure is `inconclusive`, while missing/idle/rewritten/unopened required work is `failed` and cannot certify the diff.

- [ ] **Step 5: Verify and commit**

Run:

```bash
npm test --workspace packages/hermes-engineering -- review-coverage.integration.test.ts transcript-compat.test.ts
npm run typecheck --workspace packages/hermes-engineering
npm run build --workspace packages/hermes-engineering
```

Expected: complete evidence passes; every deliberately defective fixture fails with the documented disclosure.

Commit:

```bash
git add packages/hermes-engineering third_party/qwen-code/UPSTREAM.json \
  hermes_cli/engineering_dist/hermes-engineering.mjs
git commit -m "feat: enforce deterministic review coverage"
```

---

### Task 10: Finding verification, anchors, reverse audit, and computed verdict

**Files:**
- Create: `packages/hermes-engineering/src/handlers/resolve-anchors.ts`
- Create: `packages/hermes-engineering/src/handlers/compose-review.ts`
- Create: `packages/hermes-engineering/src/reverse-audit.ts`
- Create: `packages/hermes-engineering/tests/verdict.integration.test.ts`
- Modify: `packages/hermes-engineering/src/handlers/index.ts`

**Interfaces:**
- Consumes: upstream `resolveAnchors`, `composeReview`, coverage output, deterministic checks, and verified finding JSON.
- Produces: stable `findings.json`, `verdict.json`, and Markdown report with no publication side effects.

- [ ] **Step 1: Add failing anchor, dedupe, and verdict tests**

```ts
it('reanchors by quoted code after line shifts and deduplicates identical findings', async () => {
  const result = await composeFixture('shifted-duplicate');
  expect(result.findings).toHaveLength(1);
  expect(result.findings[0]).toMatchObject({ path: 'src/x.ts', line: 19 });
});

it.each([
  ['missing-coverage', 'COMMENT'],
  ['inert-test', 'REQUEST_CHANGES'],
  ['clean', 'APPROVE'],
])('computes %s without trusting a caller verdict', async (fixture, event) => {
  expect((await composeFixture(fixture)).event).toBe(event);
});
```

- [ ] **Step 2: Run and verify failure**

Run: `npm test --workspace packages/hermes-engineering -- verdict.integration.test.ts`

Expected: FAIL because the handlers are absent.

- [ ] **Step 3: Implement strict finding input validation and anchor resolution**

Accepted finding shape:

```ts
interface VerifiedFinding {
  id: string;
  severity: 'blocker' | 'high' | 'medium' | 'low';
  title: string;
  body: string;
  path: string;
  quotedCode: string;
  sourceReviewerIds: string[];
  verification: 'confirmed' | 'rejected' | 'uncertain';
}
```

Reject absolute/traversing paths, findings without quoted code, unknown reviewer IDs, and unbounded text. Resolve locations from the captured diff; never trust a reviewer-supplied line number. Deduplicate on canonical path, resolved range, normalized title, and quoted-code hash while retaining all source reviewer IDs.

- [ ] **Step 4: Implement bounded reverse-audit state**

```ts
export interface ReverseAuditState {
  round: number;
  consecutiveDryRounds: number;
  complete: boolean;
}

export function nextReverseAudit(state: ReverseAuditState, newConfirmed: number): ReverseAuditState {
  const round = state.round + 1;
  const consecutiveDryRounds = newConfirmed === 0 ? state.consecutiveDryRounds + 1 : 0;
  return { round, consecutiveDryRounds, complete: consecutiveDryRounds >= 2 || round >= 5 };
}
```

Only high effort uses this state. Hitting round five with fewer than two dry rounds caps the verdict at `COMMENT` and discloses residual uncertainty.

- [ ] **Step 5: Compute rather than accept the verdict**

The caller supplies facts, never `event` or `approved`. `compose-review` derives the event from confirmed severity, coverage, test/build state, skipped diff content, CI state, and reverse-audit completion. Remove/disable upstream submit wiring; assert the bundle contains no `gh pr review`, HTTP mutation, push, or merge path.

- [ ] **Step 6: Verify and commit**

Run:

```bash
npm test --workspace packages/hermes-engineering -- verdict.integration.test.ts
npm run typecheck --workspace packages/hermes-engineering
npm run build --workspace packages/hermes-engineering
if rg -n "gh pr review|git push|git merge|submitReview" hermes_cli/engineering_dist/hermes-engineering.mjs; then
  echo "forbidden remote-mutation surface found in bundle" >&2
  exit 1
fi
```

Expected: all tests pass and the mutation-surface scan finds nothing.

Commit:

```bash
git add packages/hermes-engineering hermes_cli/engineering_dist/hermes-engineering.mjs
git commit -m "feat: compute evidence-backed review verdicts"
```

---

### Task 11: Public CLI and review skill orchestration

**Files:**
- Create: `hermes_cli/subcommands/review.py`
- Create: `hermes_cli/engineering_review/command.py`
- Create: `hermes_cli/engineering_review/internal_cli.py`
- Create: `tests/hermes_cli/engineering_review/test_command.py`
- Create: `tests/skills/test_requesting_code_review_engine.py`
- Modify: `hermes_cli/main.py`
- Modify: `pyproject.toml`
- Modify: `skills/software-development/requesting-code-review/SKILL.md`
- Modify: `tests/hermes_cli/test_subcommands_batch.py`

**Interfaces:**
- Consumes: Tasks 3–10 and existing `cmd_chat`/skill preloading.
- Produces: `hermes review [target] --effort low|medium|high`, internal `hermes-review-engine`, and a fail-closed skill workflow.

- [ ] **Step 1: Write failing parser and orchestration tests**

```python
def test_review_parser_defaults_to_medium_and_accepts_all_targets():
    parser = parser_with_review()
    ns = parser.parse_args(["review", "https://github.com/o/r/pull/42"])
    assert ns.target.endswith("/pull/42")
    assert ns.effort == "medium"

def test_public_review_preloads_skill_and_preserves_approvals(monkeypatch):
    seen = {}
    monkeypatch.setattr(command, "launch_review_chat", lambda **kw: seen.update(kw))
    assert review_command(args(target="local")) == 0
    assert seen["skills"] == ["requesting-code-review"]
    assert seen["auto_approve"] is False
```

- [ ] **Step 2: Run and confirm failures**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_command.py \
  tests/skills/test_requesting_code_review_engine.py tests/hermes_cli/test_subcommands_batch.py -q
```

Expected: FAIL because the parser/command and new skill contract are absent.

- [ ] **Step 3: Register the public command and internal executable**

`build_review_parser` exposes exactly the approved public surface: optional target plus `--effort low|medium|high`. It sets hidden/default attributes required by `cmd_chat`, including `runner="auto"`; runner ambiguity is resolved later by the normal clarification flow, while sandbox selection and model/provider routing come from existing Hermes configuration. Add `review` to `_BUILTIN_SUBCOMMANDS`, import/build it beside other extracted subcommands, and add:

```toml
hermes-review-engine = "hermes_cli.engineering_review.internal_cli:main"
```

The internal executable accepts an operation and a request JSON path and is
documented as unstable/internal. It is only a short-lived proxy to the live
`ReviewAuthority` owned by the public Hermes process; it must never call
`ReviewRun.create`, load a run capability from disk, accept a bundle path, or
invoke `EngineeringReviewBridge` directly. Its `start` operation validates
`${HERMES_SESSION_ID}`, contacts that session's authority, and prints the
already-created run ID and prospective plan path. Operations in the stable
`EngineCommand` union forward the exact caller request to the authority and
print exactly one response JSON. No proxy operation may commit reviewer
evidence. Absence of the live authority fails closed.

- [ ] **Step 4: Launch the normal Hermes chat path, not oneshot/yolo**

`review_command` hands a generated query to the existing `cmd_chat` path with
classic interactive callbacks, `pass_session_id=True`, and
`requesting-code-review` preloaded. `AIAgent` creates the session before skill
preprocessing exposes `${HERMES_SESSION_ID}`. Immediately after that session
exists, the same long-lived public Hermes process creates and serves one
`ReviewAuthority(workspace, target, effort, session_id)` for the entire chat
lifecycle. The authority exclusively owns the run capability, exact reviewer
manifest, and executable bundle snapshot; the delegate writer commits evidence
directly in that process. Every engine operation, including `capture-target`,
`build-prompts`, `build-test`, and `test-efficacy`, executes inside the authority
against the registered workspace/run and the captured bundle bytes. On POSIX,
the bytes execute through an inherited anonymous descriptor, never by reopening
the bundle path after hashing. Platforms without safe descriptor execution or
kernel-authenticated local-peer ownership fail closed until an equivalent
backend is implemented.

Authority cleanup first cancels active bridge work and closes the private proxy
socket, then completes the run and destroys the HMAC capability, exact evidence
manifest, and executable snapshot. Do not create a review run before the
session exists, and do not call `run_oneshot`; it deliberately enables
`HERMES_YOLO_MODE`.

The generated user message contains only target/effort and a direction to execute the preloaded skill. It does not inject system content, invent a run ID or plan path, or rebuild the toolset.

- [ ] **Step 5: Rewrite the skill around deterministic operations**

The skill must explicitly direct the agent to:

1. call `hermes-review-engine start --session-id ${HERMES_SESSION_ID}` and treat the returned run ID plus plan path from the live parent authority as the accepted run marker; never retry by creating/loading a run directly when the authority is absent;
2. call `hermes-review-engine capture-target` for the requested target only after the run exists;
3. call `hermes-review-engine build-prompts` and deliver each printed prompt verbatim with `delegate_task(role="reviewer")`;
4. use only configured logical routes, never caller-selected provider/model IDs;
5. run low/medium/high rosters as defined by the plan;
6. call deterministic build/test and test-efficacy operations;
7. call coverage and relaunch only missing/idle/unopened work;
8. send candidate findings to fresh verifier reviewers;
9. run high-effort reverse-audit rounds with the 2-dry/5-round cap;
10. call anchor resolution and verdict composition;
11. present the report without automatically fixing, committing, posting, pushing, or merging, and use the existing explicitly authorized GitHub review skill only if the user separately requests publication.

Add structure tests for each invariant rather than a full Markdown snapshot.

- [ ] **Step 6: Verify and commit**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_command.py \
  tests/skills/test_requesting_code_review_engine.py tests/hermes_cli/test_subcommands_batch.py -q
python -m hermes_cli.main review --help
```

Expected: tests pass; help shows the public flags but not individual engine operations.

Commit:

```bash
git add hermes_cli/subcommands/review.py hermes_cli/engineering_review/command.py \
  hermes_cli/engineering_review/internal_cli.py hermes_cli/main.py pyproject.toml \
  skills/software-development/requesting-code-review/SKILL.md tests/hermes_cli/engineering_review/test_command.py \
  tests/skills/test_requesting_code_review_engine.py tests/hermes_cli/test_subcommands_batch.py
git commit -m "feat: add autonomous hermes review workflow"
```

---

### Task 12: Untrusted-PR execution policy and cleanup recovery

**Files:**
- Create: `hermes_cli/engineering_review/execution_policy.py`
- Create: `tests/hermes_cli/engineering_review/test_execution_policy.py`
- Create: `packages/hermes-engineering/src/handlers/cleanup.ts`
- Create: `packages/hermes-engineering/tests/cleanup.integration.test.ts`
- Modify: `hermes_cli/engineering_review/command.py`
- Modify: `packages/hermes-engineering/src/handlers/build-test.ts`
- Modify: `packages/hermes-engineering/src/handlers/test-efficacy.ts`
- Modify: `packages/hermes-engineering/src/handlers/index.ts`

**Interfaces:**
- Consumes: target kind, terminal backend/sandbox configuration, and explicit interactive approval through the existing Hermes consent callback.
- Produces: `ExecutionDecision(mode, allowed, sanitized_env, network, reason)` and cleanup recovery instructions.

- [ ] **Step 1: Write failing policy tests**

```python
def test_remote_pr_without_sandbox_or_consent_cannot_execute_code():
    decision = decide_execution(target_kind="pr", sandbox=None, allow_local=False)
    assert decision.allowed is False
    assert decision.reason == "untrusted_remote_code_requires_sandbox_or_consent"

def test_sandboxed_pr_strips_secrets_and_disables_network_by_default():
    decision = decide_execution(target_kind="pr", sandbox="docker", allow_local=False)
    assert decision.allowed is True
    assert decision.network is False
    assert not any("TOKEN" in key or "KEY" in key for key in decision.sanitized_env)
```

- [ ] **Step 2: Run and confirm failure**

Run: `scripts/run_tests.sh tests/hermes_cli/engineering_review/test_execution_policy.py -q`

Expected: FAIL importing `execution_policy`.

- [ ] **Step 3: Implement policy before any build/test spawn**

Planning/diff inspection remains read-only and may proceed. Before `build-test` or `test-efficacy`, require `ExecutionDecision.allowed`. If denied, emit `inconclusive/untrusted_execution_not_authorized`; continue static review but cap claims about tests. For configured Docker/remote backends, route through the existing terminal environment abstraction rather than constructing a second container runner.

- [ ] **Step 4: Add cleanup-failure state and recovery command**

If upstream cleanup reports a retained worktree/lease, mark the run `cleanup_failed`, retain artifacts, and print one exact recovery command:

```text
hermes-review-engine cleanup --run <run-id>
```

The cleanup operation resolves the registered run and its recorded worktree; it never accepts an arbitrary deletion path.

- [ ] **Step 5: Verify and commit**

Run:

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_execution_policy.py -q
npm test --workspace packages/hermes-engineering -- cleanup.integration.test.ts
```

Expected: remote code never executes in the denial fixture; cleanup cannot delete the workspace or an external symlink target.

Commit:

```bash
git add hermes_cli/engineering_review packages/hermes-engineering \
  hermes_cli/engineering_dist/hermes-engineering.mjs tests/hermes_cli/engineering_review/test_execution_policy.py
git commit -m "feat: gate untrusted review execution"
```

---

### Task 13: Installed-artifact and end-to-end acceptance gates

**Files:**
- Create: `tests/integration/test_engineering_review_e2e.py`
- Create: `tests/integration/test_engineering_review_wheel.py`
- Create: `tests/fixtures/engineering_review/` fixture projects
- Create: `scripts/verify_qwen_engineering_vendor.py`
- Modify: `package.json`
- Modify: `.github/workflows/typecheck.yml`
- Modify: `.github/workflows/tests.yml`
- Modify: `scripts/ci/classify_changes.py`
- Modify: `tests/scripts/test_classify_changes.py`
- Modify: `README.md`
- Modify: `website/docs/guides/github-pr-review-agent.md`
- Modify: `website/docs/reference/cli-commands.md`
- Modify: `website/docs/user-guide/configuration.md`

**Interfaces:**
- Consumes: the packaged bundle, public CLI, internal engine, skill contract, Node 22, and fixture repositories.
- Produces: release gates proving the design's acceptance scenario and a documented user workflow.

- [ ] **Step 1: Add a failing installed-wheel test**

```python
@pytest.mark.integration
def test_wheel_review_engine_runs_without_source_tree_or_node_modules(tmp_path):
    wheel = build_wheel(tmp_path)
    env = install_wheel_in_fresh_venv(tmp_path, wheel)
    result = env.run("hermes-review-engine", "capture-target", "--request", fixture_request(tmp_path))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["protocolVersion"] == 1
    assert not (env.root / "node_modules").exists()
```

- [ ] **Step 2: Add the mixed pytest/Vitest acceptance fixture**

Provide parallel pytest and Vitest fixture projects. Each contains a production change, one effective test, one inert sibling test, an untracked source file, and enough diff content for medium reviewers. The E2E test stubs model responses but uses real Git, the packaged Node bundle, the applicable real test process, and real artifact paths. It asserts unchanged porcelain status, correct test classifications, failed coverage for a deliberately unread chunk, deduped findings, no remote writes, successful worktree cleanup, byte-stable system prompt/tool schemas across the conversation, strict role alternation, and no newly registered core model tool. Add a third fixture where both runners apply and assert `inconclusive/ambiguous_runner` until the clarification response selects one explicitly.

- [ ] **Step 3: Add supply-chain and mutation-surface verification**

`scripts/verify_qwen_engineering_vendor.py` must:

```python
def verify() -> None:
    verify_manifest_hashes()
    verify_apache_headers()
    verify_no_unmanifested_vendor_files()
    verify_bundle_notice_and_hash()
    verify_no_forbidden_imports(("provider", "telemetry", "submit"))
    verify_no_remote_mutation_strings(("gh pr review", "git push", "git merge"))
```

It runs offline in CI and fails on any mismatch.

Extend `scripts/ci/classify_changes.py` so changes under `packages/hermes-engineering/` and `third_party/qwen-code/` select both the frontend/Node and Python packaging lanes; cover both mappings in `tests/scripts/test_classify_changes.py`. Add `packages/hermes-engineering` to the Node workspace typecheck matrix in `.github/workflows/typecheck.yml`, and add a dedicated engine test/build job in `.github/workflows/tests.yml` that runs the vendor verifier, Vitest, typecheck, and bundle build.

- [ ] **Step 4: Document behavior and limitations**

Document Node 22, supported targets, `low|medium|high`, pytest/Vitest support, artifact retention, untrusted-PR consent/sandbox behavior, read-only remote policy, `passed|failed|inconclusive`, and the fact that the final verdict is local until separately published. Attribute Qwen Code and link Apache-2.0 provenance.

- [ ] **Step 5: Run focused and full verification**

Run:

```bash
python scripts/verify_qwen_engineering_vendor.py
npm run typecheck --workspace packages/hermes-engineering
npm test --workspace packages/hermes-engineering
npm run build --workspace packages/hermes-engineering
scripts/run_tests.sh tests/hermes_cli/engineering_review tests/agent/test_review_evidence.py \
  tests/skills/test_requesting_code_review_engine.py tests/integration/test_engineering_review_e2e.py -q
scripts/run_tests.sh
python -m build --sdist --wheel
```

Expected: every command exits 0; the full Python suite reports zero failures; Node tests report zero failures; wheel/sdist contain bundle, notice, license, and provenance.

- [ ] **Step 6: Run the manual acceptance check**

From the fixture repository:

```bash
hermes review --effort medium
```

Confirm the displayed report includes the untracked file, effective and inert test classifications, reviewer coverage, deduplicated finding, computed verdict, residual uncertainty, artifact path, and cleanup result. Confirm `git status --porcelain=v1` is identical before and after and no GitHub review/comment exists.

- [ ] **Step 7: Commit**

```bash
git add tests/integration/test_engineering_review_e2e.py tests/integration/test_engineering_review_wheel.py \
  tests/fixtures/engineering_review scripts/verify_qwen_engineering_vendor.py \
  package.json .github/workflows/typecheck.yml .github/workflows/tests.yml \
  scripts/ci/classify_changes.py tests/scripts/test_classify_changes.py README.md \
  website/docs/guides/github-pr-review-agent.md website/docs/reference/cli-commands.md \
  website/docs/user-guide/configuration.md
git commit -m "test: gate autonomous engineering review releases"
```

---

## Final implementation verification

Before claiming completion, run all commands from Task 13 Step 5 on a fresh checkout or implementation worktree. Then inspect:

```bash
git status --short
git log --oneline --decorate -15
git diff origin/main...HEAD --stat
git diff origin/main...HEAD --check
```

The implementation is complete only when the worktree is clean, every task has its own passing test evidence and commit, the bundled runtime works without `node_modules`, the vendored-source verification is clean, and the full acceptance scenario produces no unauthorized external mutation.

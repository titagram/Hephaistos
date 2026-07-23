# Hermes Engineering Review Engine design

**Status:** approved design for implementation planning
**Upstream baseline:** Qwen Code `d064bd7dcf98e0255283068a775f6e49d70db8aa`

## 1. Outcome and boundary

Hermes will gain a deterministic engineering-review engine derived from the
useful parts of Qwen Code's review implementation. The engine will prepare and
partition diffs, run build and test checks, probe whether changed tests gate the
changed behavior, verify review coverage from harness evidence, resolve finding
anchors, deduplicate findings, and compute a review verdict.

Hermes remains autonomous. It does not install or invoke Qwen Code, use Qwen
credentials, depend on Qwen providers, or delegate control of agents to the
Qwen runtime. Selected Apache-2.0 TypeScript sources are vendored, compiled into
a self-contained JavaScript bundle, and executed with the Node 22 runtime that
the Hermes installer manages. Hermes Python code communicates with that bundle
through a small versioned JSON protocol.

The engine is deterministic: it does not call models and it does not publish to
GitHub. A Hermes skill coordinates reviewers and gives the engine their
evidence. Existing GitHub workflows retain responsibility for any remote write
and require explicit user authorization.

This design does not implement strict read-before-write enforcement, new LSP
query operations, path-gated skill activation, multi-model Arena, or Qwen Agent
Team. Those are independent follow-up designs. It also does not add a new core
model tool; the capability follows the Footprint Ladder as a CLI command plus a
skill.

## 2. Alternatives considered

| Approach | Benefit | Rejected cost |
|---|---|---|
| Invoke an installed `qwen` binary | no source extraction | makes Hermes depend on Qwen installation, configuration, providers, internal CLI behavior, and credentials |
| Translate all selected code to Python | one runtime and native imports | rewrites already tested logic, loses easy upstream comparison, and creates a permanent behavioral fork |
| **Vendor a narrow TypeScript slice behind a JSON subprocess boundary** | preserves upstream code and tests while keeping Hermes autonomous | requires Node 22 and a maintained protocol/adapter layer |

Hermes uses the third approach. Node 22 is an accepted dependency. Production
distributions contain a prebuilt bundle, so runtime use does not require npm,
TypeScript, esbuild, or `node_modules`.

## 3. User-facing behavior

The public entry point is:

```text
hermes review [target] [--effort low|medium|high]
```

The target may be omitted for the current local changes, a commit or Git range,
a pull-request number or URL, or a specific file. Local review includes staged,
unstaged, and untracked files. Pull requests and Git ranges are inspected in an
isolated worktree. The default effort is `medium`.

Effort controls only reviewer orchestration, not deterministic safety checks:

| Effort | Review orchestration |
|---|---|
| `low` | one independent reviewer plus all applicable deterministic checks |
| `medium` | three independent review perspectives plus finding verification |
| `high` | risk-aware chunk reviewers, applicable specialists, finding verification, and reverse audit until two consecutive dry rounds, with a maximum of five rounds |

The command produces a human summary and a machine-readable artifact set. It
does not post comments, approve a pull request, push, merge, or mutate the
reviewed branch. A later explicitly authorized GitHub skill may consume the
final report.

## 4. Architecture

```text
Hermes CLI / review skill
          |
          | versioned JSON on stdin/stdout
          v
Hermes Engineering Engine (Node 22, one process per request)
          |
          +-- vendored Qwen-derived deterministic review modules
          +-- Hermes artifact, transcript, and test-runner adapters
          +-- Git/worktree and report artifacts
```

### 4.1 Vendored upstream slice

The repository retains the original upstream-relative layout under:

```text
third_party/qwen-code/
  LICENSE
  NOTICE
  UPSTREAM.json
  packages/cli/src/commands/review/...
```

The allowlist includes only the review implementation needed for:

- local diff capture, merge-base resolution, and worktree management;
- diff planning, risk/size partitioning, roster and brief generation;
- build/test planning;
- test-efficacy planning and probes;
- finding anchors, coverage, aggregation, and verdict composition;
- the upstream unit and integration tests for those modules.

Provider code, model clients, authentication, telemetry, UI, memory, general
session state, Qwen configuration, remote review submission, and unrelated CLI
commands are excluded. Read-only GitHub context retrieval may reuse narrow
upstream helpers only when they can run through the existing authenticated `gh`
CLI without importing Qwen runtime state.

Files under `third_party/qwen-code` are not edited by hand. Their existing
license headers are retained. A compatibility shim may occupy an allowlisted
upstream path when an imported utility is trivial and unrelated to review, but
the shim is recorded as Hermes-owned in the provenance manifest.

### 4.2 Hermes TypeScript adapter package

Hermes-specific behavior lives outside the vendored directory:

```text
packages/hermes-engineering/
  src/
    main.ts
    protocol.ts
    adapters/
      artifact-store.ts
      hermes-transcripts.ts
      pytest-runner.ts
      vitest-runner.ts
  tests/
  dist/hermes-engineering.mjs
```

The adapter package translates protocol requests into upstream operations,
normalizes Hermes/Hades transcript evidence, selects an applicable test-runner
adapter, and emits versioned results. It may wrap upstream functions but does
not duplicate their algorithms.

esbuild compiles the adapter entry point and its selected upstream dependencies
into one Node-22-targeted ESM bundle. Runtime dependencies are bundled. Release
packaging includes the bundle and its checksum. Source checkouts provide one
documented build command that regenerates it.

### 4.3 Python facade and skill

The Python facade resolves the managed or system Node 22 executable, validates
workspace and artifact paths, launches one bundle process per operation, sends
the JSON request, enforces timeouts, parses exactly one JSON response, and maps
typed results to the Hermes CLI.

The review skill owns model work. It consumes the deterministic plan, launches
the configured number and kind of Hermes/Hades reviewers, records their
immutable outputs and harness evidence, calls the coverage and aggregation
operations, and renders the final report. The vendored code does not select
models, alter logical role routing, bypass direct-parent authority, or publish
artifacts to a backend.

## 5. Data flow

1. **Resolve target.** The Python facade validates the requested local diff,
   file, commit/range, or pull request and creates a unique local run ID.
2. **Acquire an isolated snapshot.** Pull requests and committed ranges use a
   disposable worktree. A local review captures staged, unstaged, and untracked
   state without changing the user's index or working tree.
3. **Build the deterministic plan.** The engine records base/head identity,
   file topology, chunks, risk markers, applicable build/test commands, changed
   tests, and the exact required review roster.
4. **Run deterministic checks.** Build/test and test-efficacy checks execute
   through the selected runner and produce `passed`, `failed`, or
   `inconclusive` evidence.
5. **Run reviewers.** The skill launches reviewers with generated briefs and
   exact diff ranges. Their returns and harness transcripts are stored locally.
6. **Prove coverage.** The engine verifies which planned ranges and briefs were
   actually inspected. Missing, idle, blind, or unprovable reviewer work cannot
   certify the diff.
7. **Verify and aggregate.** Hermes verifies candidate findings; the engine
   resolves anchors from quoted code, deduplicates findings, and applies the
   effort-specific reverse-audit policy.
8. **Compute the verdict.** The engine combines findings, deterministic checks,
   coverage, CI state when available, and residual uncertainty into a stable
   report. Publication remains a separate authorized action.
9. **Clean up.** Worktrees and temporary leases are removed in a `finally`
   path. The bounded report artifacts remain available for inspection and
   resume according to the retention policy.

## 6. JSON protocol

Every invocation is a one-request, one-response subprocess. The request is
written to stdin. Stdout contains one JSON document and no prose; diagnostics
go to stderr.

All requests include:

```json
{
  "protocolVersion": 1,
  "requestId": "review-run-id:operation",
  "command": "test-efficacy",
  "workspace": "/absolute/repository/path",
  "artifactRoot": "/absolute/profile-local/run/path",
  "input": {}
}
```

All responses include the same protocol version and request ID, a status of
`passed`, `failed`, or `inconclusive`, structured command-specific output, and a
bounded list of diagnostics. Protocol/validation failures are typed errors and
use nonzero process exit codes. A valid `failed` review check is not a transport
error and still returns a parseable response.

The initial protocol exposes only the operations required by the public review
flow: target capture/planning, prompt/brief generation, build-test planning,
test efficacy, anchor resolution, coverage checking, report composition, and
cleanup. Internal operations are not model tools and are not added to the slash
command registry individually.

Python rejects an unknown protocol version, mismatched request ID, extra stdout
content, malformed or oversized JSON, missing required fields, and inconsistent
exit status. Large diffs and transcripts are referenced by validated artifact
paths rather than embedded repeatedly in JSON.

## 7. Test-runner adapters and efficacy semantics

The upstream Vitest/npm behavior is retained behind a runner contract. The
contract identifies changed test files, determines whether the normal project
test command collects them, runs selected tests in an isolated probe worktree,
and classifies their relationship to changed production code.

The first release supports:

- npm workspaces with Vitest, preserving Qwen's existing implementation; and
- pytest, through a Hermes adapter using pytest collection and structured test
  outcomes.

A changed test is:

- `unreachable` when the repository's normal test configuration does not
  collect it;
- `gated` only when the test passes with the change and fails with a genuine
  assertion after the relevant production change is reverted;
- `inert` when it still passes after that revert; or
- `inconclusive` when collection, import, compilation, fixture setup, timeout,
  or infrastructure failure prevents a trustworthy conclusion.

Compilation or import failure after the revert is never evidence that a test
gates behavior. Classification remains per changed test file so one effective
test cannot conceal an inert sibling.

Runner detection is deterministic and visible in the plan. Ambiguous projects
require an explicit runner selection; the engine does not guess and report a
false success. Additional runners are later adapters, not changes to the core
algorithm.

## 8. Artifact and evidence contract

Each run stores a bounded, profile-local artifact directory containing:

- immutable target identity and captured diff;
- plan and reviewer roster;
- generated briefs or their hashes;
- deterministic check results;
- reviewer final returns and harness-derived evidence needed for coverage;
- verified/deduplicated findings;
- final verdict and cleanup state.

Raw hidden reasoning is not required, stored, or published. Backend-bound Hades
payloads receive only accepted structured evidence and never raw prompts,
transcripts, secrets, or environment content. Local artifacts are written with
restrictive permissions, use atomic replacement where appropriate, and obey a
bounded retention setting in `config.yaml`, not a new environment variable.
The default retains the 30 most recent completed runs plus every active run;
cleanup removes older completed runs from oldest to newest and never removes an
active run.

Coverage is derived from harness evidence, not from an agent's unsupported
claim that it read a range. If transcripts are unavailable because of an
environment failure, the result is `inconclusive` and the review cannot claim
complete coverage.

## 9. Security and failure behavior

The facade canonicalizes workspace, worktree, and artifact paths and rejects
path traversal, symlink escapes, and writes outside approved roots. Commands
are launched with argument arrays rather than shell strings. The child process
receives a minimal environment and no model/provider secrets.

Builds and tests execute repository code. Worktree isolation is not an OS
sandbox. For an untrusted remote pull request, Hermes uses a configured
container or sandbox with secrets removed and network disabled unless the test
contract explicitly requires it. Unsandboxed local execution of remote code
requires explicit user consent. Local user changes follow the existing Hermes
terminal approval policy.

Timeout, cancellation, malformed output, missing tools, test-runner crashes,
worktree drift, and cleanup failures retain their real state. They do not become
successful checks. Cleanup is retried safely and any retained worktree/lease is
reported with a recovery command. The engine never mutates the user's current
branch, index, or working tree.

The review verdict fails closed on missing mandatory coverage. Test efficacy
can be inconclusive without fabricating a blocker, but the final report must
surface that uncertainty and cannot describe the test as proven effective.

## 10. Upstream provenance and synchronization

`UPSTREAM.json` records the Qwen repository URL, pinned commit, upstream paths,
copied-file hashes, Hermes-owned shims, and ordered patch files. A synchronization
command accepts an explicit upstream ref and:

1. obtains that exact Qwen source revision;
2. copies only the reviewed allowlist;
3. retains and validates Apache-2.0 headers and notices;
4. updates hashes and provenance;
5. applies the small explicit patch queue, if any;
6. reports new transitive source imports instead of copying them implicitly;
7. builds the bundle; and
8. runs upstream, adapter, protocol, and integration tests.

Normal builds and runtime use do not fetch Qwen or access the network. Updating
the upstream pin is an intentional reviewed change. If an upstream update
requires extensive patching, maintainers either narrow the imported slice or
hold the previous audited version; they do not silently fork the implementation.

## 11. Required verification

The implementation is accepted only when tests prove:

- vendored upstream unit tests pass against the extracted source;
- every vendored file and applicable notice matches `UPSTREAM.json`;
- the release bundle runs with Node 22 without `node_modules`;
- Python/Node contract behavior for success, verified failure, inconclusive
  checks, timeout, cancellation, malformed output, protocol mismatch, and
  oversized output;
- real temporary Git repositories cover staged, unstaged, untracked, renamed,
  binary, and deleted files without changing the user's simulated worktree;
- merge-base resolution and disposable worktree creation/cleanup work through
  real Git imports and subprocesses;
- Vitest and pytest fixtures each demonstrate reachable, unreachable, gated,
  inert, import/compile-error, and timeout outcomes;
- missing, idle, blind, malformed, and complete reviewer evidence drives the
  correct coverage state;
- finding anchor resolution and deduplication remain stable when line numbers
  shift;
- low, medium, and high effort produce the documented roster while preserving
  the same mandatory deterministic gates;
- untrusted-PR execution strips secrets and follows sandbox/consent policy;
- no new core model tool is registered and no review path can publish, push, or
  merge without the existing separate authorization flow; and
- the installed CLI and skill complete an end-to-end local review using the
  packaged bundle, not a development-only source runner.

## 12. Acceptance scenario

In a temporary mixed fixture repository, a developer modifies production code,
adds one effective pytest or Vitest test, adds one inert sibling test, leaves an
untracked source file, and requests a medium local review. Hermes captures all
changes without modifying the working tree, plans the whole diff, identifies
the effective and inert tests correctly, launches the documented reviewers,
refuses to certify any deliberately unread chunk, verifies and deduplicates the
findings, and emits a structured verdict with explicit residual uncertainty.

The same packaged installation then reviews a pull-request fixture in an
isolated worktree, removes the worktree afterward, and produces no remote write.
The run is complete only when the artifact manifest, deterministic checks,
coverage evidence, verdict, and cleanup state are internally consistent and
independently inspectable.

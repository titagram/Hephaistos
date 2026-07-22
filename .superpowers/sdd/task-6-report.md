# Task 6 report — test-runner contract and preserved Vitest efficacy probe

## Status

Complete. The engineering engine now has a typed test-runner/process boundary,
a real Vitest adapter, deterministic runner selection, captured build/test
command plans, and structured `build-test` and `test-efficacy` handlers.

## Implementation

- Added `TestRunner`, `ProcessRunner`, `TestRun`, runner detection, and a bounded
  argument-array `NodeProcessRunner`. POSIX process groups and Windows
  `taskkill /T` contain timed-out package-manager child processes.
- Added the Vitest adapter with local-only module resolution, structured JSON
  collection/run output, disabled caches, no shell, explicit cwd, and path
  containment checks.
- Reused Qwen's `planTestEfficacy`, `classifyProbeRun`, `safeRmWithin`, npm
  workspace reading, affected-workspace selection, and dependency/dependent
  build-set algorithms through the existing typed shim. No Qwen algorithm was
  copied into Hermes.
- `capture-target` now records Qwen-scoped argument-array commands under
  `plan.hermes.buildTest`. Non-npm or unmodeled layouts record no executable
  plan and therefore remain inconclusive instead of being guessed.
- `build-test` executes only the commands recorded in that plan, only through
  the recorded package-manager executable, and rejects arbitrary package-manager
  `exec` arguments. Passing commands, assertion failures, compile/import errors,
  missing structure, and timeouts map to typed outcomes.
- `test-efficacy` supports `auto | vitest | pytest`; auto selects only one
  definite runner, reports `no_runner` for zero, and reports
  `ambiguous_runner` with explicit retry choices for multiple runners. The
  pytest implementation is intentionally left to Task 7.
- Every changed test file appears in one of `unreachable`, `gated`, `inert`, or
  `inconclusive`. Classification is per file; test-only diffs remain explicitly
  inconclusive because there is no production source to revert.
- Baseline and reverted tests run only in a disposable nested Git worktree.
  Revert paths normalize both separator styles before containment, reject dot
  segments, exclude Qwen fixture directories, and use Qwen's symlink-safe
  removal. The source checkout remains byte-identical and cleanup runs after
  failures and timeouts, including partial worktree creation.

## TDD evidence

The first real-fixture test run failed at the intended missing-module boundary:

```text
FAIL tests/vitest-efficacy.integration.test.ts
Error: Cannot find module '../src/handlers/test-efficacy.js'
Test Files 1 failed (1)
```

The real fixtures cover gated, inert, outside-workspace, compile/import-error,
test-only, mixed per-file, timeout, fixture-directory, and backslash traversal
behavior. The build fixture covers a passing Qwen-scoped plan, a real assertion
failure, import failure, process-tree timeout, and package-manager argument
injection rejection.

During green/refactor verification, the source-porcelain assertion caught
Vitest writing `.vite` cache state; collection/run now use the runner config
loader with caching disabled. Full-suite testing also caught Vitest's optional
`--json` filename parsing; the adapter places bare `--json` last so output stays
on stdout.

## Independent review

An independent Task 6 review found four issues, all fixed and re-reviewed:

1. Windows-style backslash traversal could be normalized after containment and
   escape Qwen's safe removal.
2. A partially created worktree could be omitted from cleanup if `worktree add`
   threw before returning.
3. Qwen intentionally schedules no probe for a test-only diff, which initially
   omitted that changed test from Hermes' required per-file output.
4. The first build-test cut consumed a synthesized command plan that real
   capture did not produce.

The final re-review reported no remaining findings and marked the task ready.

## Fresh verification

```text
npm test --workspace packages/hermes-engineering -- \
  build-test.integration.test.ts vitest-efficacy.integration.test.ts
# 2 files passed, 16 tests passed

npm run typecheck --workspace packages/hermes-engineering
# exit 0

npm run build --workspace packages/hermes-engineering
# exit 0

npm test --workspace packages/hermes-engineering
# 32 files passed, 691 tests passed

prettier --check <Task 6 TypeScript/JSON paths>
# all matched files use Prettier style

git diff --check
# exit 0
```

No Task 6 temporary fixture directory, Vitest cache, or efficacy worktree was
left after verification. `third_party/qwen-code` is unchanged. The checked-in
Node bundle was regenerated successfully.

## Concerns and preserved state

- No blocking concern remains.
- Task 7 must supply the pytest adapter before explicit `runner=pytest` can run.
- Task 12 remains responsible for untrusted-PR execution authorization and the
  final secret-stripping/network policy around these repository-code processes.
- Pre-existing changes to `.superpowers/sdd/progress.md` and
  `.superpowers/sdd/task-4-report.md` were preserved and excluded from this
  task's commit.

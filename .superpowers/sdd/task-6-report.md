# Task 6 review — test-runner contract and preserved Vitest efficacy

## Review identity

- Base: `3f145def6`
- Head: `0f398eac0`
- Scope: Task 6 brief and the exact base-to-head diff

## Findings

### Important — single-package Vitest tests are falsely classified as unreachable

`runTestEfficacy` always feeds `readWorkspaceGlobs(workspace)` into Qwen's
workspace-specific `planTestEfficacy` and immediately turns its `unreachable`
set into final findings (`src/handlers/test-efficacy.ts:335-340`). In an ordinary
single-package Vitest repository, the workspace list is empty, so Qwen's npm
workspace membership predicate rejects every root-level changed test. The
adapter never consults `runner.collectedFiles`, even when Vitest itself collects
the file.

This was reproduced with the committed single-package build-test fixture:

```text
vitest list ...
  compile-error.test.ts
  failing.test.ts

test-efficacy for failing.test.ts
  status: failed
  verdict: unreachable
  detail: the changed test is outside every npm workspace
```

The runner contract is intended to distinguish actual collection reachability;
this result is a false blocker for every non-workspace Vitest repository. Keep
Qwen's membership behavior for npm-workspace repositories, but special-case a
root package or make successful runner collection authoritative before emitting
`unreachable`. Add a real single-package fixture regression test.

### Important — genuine assertion failures are inconclusive for the normal source-only plan

`discoverBuildTestPlan` records a package test command for an affected source
workspace but only attaches `testFiles` when the diff itself changes test files
(`src/handlers/build-test.ts:145-157`). `classifyCommand` recognizes an assertion
failure only by passing that optional list through the Vitest efficacy
classifier (`src/handlers/build-test.ts:340-356`). With no changed test file, any
non-zero test run falls through to "failed without structured per-file assertion
evidence" and becomes `inconclusive` (`src/handlers/build-test.ts:358-368`).

The built engine was run with the committed real assertion-failure fixture and
a recorded source-only test command. The process exited `1`, but the engine
returned:

```text
status: inconclusive
outcome: inconclusive
detail: the test command failed without structured per-file assertion evidence
```

This misses the common case Task 6's "genuine failing test" requirement is meant
to cover: production code changes while existing tests catch the regression. It
also means non-Vitest package scripts remain inconclusive even when their native
runner clearly reports assertion failures. Build-test needs runner-aware or
otherwise structured test-result classification that does not depend on the
changed-test list. Add a capture-produced source-only plan regression test; the
current failing-test test uses a hand-authored plan with `testFiles`, masking the
defect.

## Contract audit

- Qwen efficacy planning, per-file probe classification, safe removal, and npm
  workspace/build-set helpers are imported through the shim rather than copied.
- Process, Git, Vitest, and package-manager invocations use argument arrays with
  `shell: false` at the process boundary.
- Auto runner selection is typed for zero and multiple matches; pytest execution
  is correctly deferred.
- Compile/import/spawn/timeout probe outcomes are kept inconclusive.
- Probe worktrees are nested, partial creation reaches `finally`, and the focused
  timeout/porcelain/cleanup tests pass.
- Capture deterministically records the build/test command array derived from
  the captured file scope, and build-test emits the exact executed arrays. No
  model-provided command field was added.

## Verification

```text
npm test --workspace packages/hermes-engineering -- \
  build-test.integration.test.ts vitest-efficacy.integration.test.ts
# 2 files passed, 16 tests passed

npm run typecheck --workspace packages/hermes-engineering
# exit 0

npm run build --workspace packages/hermes-engineering
# exit 0; checked-in bundle remained byte-identical

npm test --workspace packages/hermes-engineering
# 32 files passed, 691 tests passed

git diff --check 3f145def6..0f398eac0
# exit 0
```

No efficacy worktree or temporary fixture remained. The only pre-existing
worktree changes were `.superpowers/sdd/progress.md` and
`.superpowers/sdd/task-4-report.md`; this review changes only this requested
report.

## Verdict

- Critical: **0**
- Important: **2**
- Minor: **0**
- Spec verdict: **FAIL**
- Quality verdict: **FAIL**

Task 6 is not ready to proceed until both false-classification paths are fixed
and covered by integration tests.

---

## Remediation — root Vitest reachability and source-only assertion evidence

Both Important findings above are resolved.

### Root/single-package Vitest

When the selected runner is Vitest and the root package declares no npm
workspaces, changed root tests are now candidates for the runner's real
`collectedFiles()` result rather than being finalized from Qwen's
workspace-only membership predicate. Qwen's original membership rules remain
unchanged for repositories that do declare workspaces, so tests outside those
workspaces remain unreachable.

A real single-package fixture proves that a root-level changed test is
collected, passes at the changed head, fails by assertion after the production
source revert, and is classified `gated`. The source checkout's porcelain state
is byte-identical afterward and the disposable probe worktree is absent.

### Capture-produced source-only assertion failures

`build-test` now derives the actual test-result files from Vitest's structured
JSON output and passes those exact file identities through Qwen's
`classifyProbeRun`. This removes the dependency on an optional changed-test
list while retaining Qwen's assertion-versus-import/compile distinction.
Compile/import failures still have no assertion-bearing file result and remain
inconclusive.

The regression drives the checked-in Node bundle end to end:

1. a real Git repository starts with an existing passing Vitest test;
2. only production source is changed so that test assertion fails;
3. bundled `capture-target` creates the real source-only plan;
4. bundled `build-test` executes the captured npm command; and
5. the response is `failed` with a failed command outcome, while Git porcelain
   remains unchanged.

### RED evidence

Before the fixes, the two new tests failed exactly at the reported outcomes:

```text
root-package efficacy:
  expected gated, received unreachable

capture -> bundled build-test:
  expected failed, received inconclusive

Test Files 2 failed
Tests 2 failed | 16 passed
```

### Fresh verification after remediation

```text
npm run typecheck --workspace packages/hermes-engineering
# exit 0

npm run build --workspace packages/hermes-engineering
# exit 0

npm test --workspace packages/hermes-engineering -- \
  vitest-efficacy.integration.test.ts build-test.integration.test.ts \
  capture-target.integration.test.ts
# 3 files passed, 34 tests passed

npm test --workspace packages/hermes-engineering
# 32 files passed, 693 tests passed

prettier --check <remediation paths>
# all matched files use Prettier style

git diff --check
# exit 0
```

No temporary fixture directory, cache directory, or efficacy worktree remained.
The remediation introduces no network, model, provider, pytest, or core-tool
surface. The pre-existing changes to `.superpowers/sdd/progress.md` and
`.superpowers/sdd/task-4-report.md` remain untouched.

### Remediation verdict

- Critical: **0**
- Important: **0**
- Minor: **0**
- Spec verdict: **PASS**
- Quality verdict: **PASS**

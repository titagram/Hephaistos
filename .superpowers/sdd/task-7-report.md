# Task 7 — structured pytest adapter

## Outcome

Implemented the pytest efficacy adapter as an isolated Python subprocess plus a
Node `TestRunner`. The Python side uses pytest hooks and emits one JSON document;
the Node side validates that document, preserves phase-specific outcomes, and
adapts only passed/assertion-failed results into Qwen's existing per-file
`classifyProbeRun` decision.

The default efficacy runner set now includes pytest. Pytest collection is the
reachability authority even when an unrelated npm workspace declaration exists,
while the existing Vitest npm-workspace behavior remains unchanged.

## Behavior covered

- Canonical repository-relative collection paths.
- Call-phase failures classify as `assertion_failed`.
- Collection/import, setup/teardown, internal error, interruption, empty run,
  malformed probe output, spawn failure, and timeout remain inconclusive.
- Reachable, unreachable, gated, inert, import-error, setup-error, and timeout
  efficacy fixtures.
- Mixed Python/npm repositories do not misclassify pytest files through npm
  workspace membership.
- Probe launches use argument arrays, explicit `cwd`, bounded timeouts, a
  credential-free environment allowlist, and repository-relative path checks.
- The generated `hermes-engineering.mjs` bundle includes the adapter.

## TDD evidence

Initial Python tests failed with empty stdout/`JSONDecodeError` because
`hermes_cli.engineering_review.pytest_probe` did not exist. After implementing
the plugin, the assertion case passed and the import case initially surfaced as
`interrupted`; changing structured outcome precedence made the import case
correctly report `collection_or_import_error`.

The initial Node integration test failed to import `src/runners/pytest.js`.
After the adapter was added, the first real efficacy run exposed project-root
import isolation in the subprocess; explicitly adding the validated project
root after safely importing the Hermes probe fixed baseline execution.

The mixed-repository regression then failed with an empty gated set because npm
workspace membership overrode pytest discovery. Making pytest collection
authoritative turned it green.

## Fresh verification

```text
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_pytest_probe.py -q
# 2 passed, 0 failed

npm test --workspace packages/hermes-engineering -- pytest-efficacy.integration.test.ts
# 1 file passed, 8 tests passed

npm run typecheck --workspace packages/hermes-engineering
# exit 0

npm run build --workspace packages/hermes-engineering
# exit 0

npm test --workspace packages/hermes-engineering
# 33 files passed, 701 tests passed

prettier --check <Task 7 TypeScript paths>
# all matched files use Prettier style

ruff check <Task 7 Python paths>
# all checks passed

ruff format --check <Task 7 Python paths>
# 2 files already formatted

git diff --check
# exit 0
```

The full Node suite prints expected stderr from subprocess failure-path fixtures;
Vitest still reports all 701 tests passing. No temporary pytest efficacy trees
remain. Pre-existing edits to `.superpowers/sdd/progress.md` and
`.superpowers/sdd/task-4-report.md` were preserved and are not part of this task.

## Reviewer remediation — honor configured discovery roots

Fix commit:
`b12333580328edbf06ed33c4db1bd9e80a47e334`

The collection probe previously supplied the repository root as an explicit
pytest positional target. That overrides configured `testpaths` and could make a
correctly named test outside the project's normal discovery roots appear
reachable. The probe now runs `--collect-only` without a positional target,
while retaining the validated `cwd` and `--rootdir` boundary.

The integration fixture now uses `checks/test_not_collected.py`, which matches
pytest's normal filename pattern but sits outside `testpaths = tests`. Before
the fix the regression failed because the file was classified `inert`; after
the fix it is `unreachable`.

Fresh remediation verification:

```text
scripts/run_tests.sh tests/hermes_cli/engineering_review/test_pytest_probe.py -q
# 2 passed, 0 failed

npm test --workspace packages/hermes-engineering -- pytest-efficacy.integration.test.ts
# 1 file passed, 8 tests passed

npm run typecheck --workspace packages/hermes-engineering
# exit 0

npm run build --workspace packages/hermes-engineering
# exit 0

prettier --check packages/hermes-engineering/tests/pytest-efficacy.integration.test.ts
# all matched files use Prettier style

ruff check <Task 7 Python paths>
# all checks passed

ruff format --check <Task 7 Python paths>
# 2 files already formatted

git diff --check
# exit 0
```

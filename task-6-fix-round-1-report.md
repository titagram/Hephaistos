# Task 6 fix round 1 report

## Prior scope

- Reused one process-tracked Evolution job manager for the dashboard's
  server-owned organism root, including job polling and router shutdown.
- Preserved `EvolutionJobConflict` through the service and exposed it as a
  stable HTTP 409 response.
- Replaced the permissive Telos draft document dictionary with the exact,
  recursively strict Telos revision and item models.

## Prior TDD evidence

The initial focused adapter run was red: production-root initialization and
job submission/polling failed because no manager was retained for the default
root. The conflict and strict-document regressions were added before the
implementation. The first post-change run also exposed the fixture's unsafe
world-readable Hermes home; changing that fixture to represent the real
private server storage made the lifecycle test exercise the intended boundary.

## Prior verification

- `scripts/run_tests.sh tests/plugins/test_evolution_dashboard_plugin.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-plugin` — 18 passed
- `scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_service.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-service` — 53 passed
- `scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_jobs.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-jobs` — 26 passed
- `scripts/run_tests.sh tests/plugins/test_plugin_dashboard_auth_contract.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-plugin-auth` — 3 passed
- `scripts/run_tests.sh tests/hermes_cli/test_dashboard_auth_gate.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-auth-gate` — 22 passed, 1 skipped
- `scripts/run_tests.sh tests/hermes_cli/test_dashboard_auth_middleware.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-auth-middleware` — 33 passed
- `git diff --check` — clean

## UI follow-up

## Delivered

- Research handoff now announces `Research brief copied — paste it in Chat.` in a live status region before navigating to `/chat`.
- The navigation is delayed by 750 ms, is cancellable on replacement and unmount, and clipboard failures leave the user on the pipeline with the error displayed.
- Added a shared `ExpandableText` component. Long audit and blueprint-authorization summaries now render a bounded preview with an explicit, accessible show-full/show-less control.
- Audit and authorization history preserve the server-provided order.
- Rebuilt `plugins/evolution/dashboard/dist/index.js`.

## Test-first evidence

- RED: interaction tests initially failed because the handoff scheduler did not exist and authorization history was reordered.
- GREEN: the focused interaction suite passes, covering the visible handoff message, clipboard failure, fake-timer cancellation/single navigation, and summary expansion/order.

## Validation

- `npm --workspace web test` — 17 files, 74 tests passed.
- `npm --workspace web run check:evolution` — TypeScript check, evolution bundle rebuild, and bundle syntax check passed.
- `npm --workspace web run build` — production web build passed. Vite emitted its existing large-chunk advisory only.

## Scope note

- The pre-existing untracked `task-7-report.md` was left untouched.

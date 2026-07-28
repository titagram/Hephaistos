# Task 6 fix round 1 report

## Scope

- Reused one process-tracked Evolution job manager for the dashboard's
  server-owned organism root, including job polling and router shutdown.
- Preserved `EvolutionJobConflict` through the service and exposed it as a
  stable HTTP 409 response.
- Replaced the permissive Telos draft document dictionary with the exact,
  recursively strict Telos revision and item models.

## TDD evidence

The initial focused adapter run was red: production-root initialization and
job submission/polling failed because no manager was retained for the default
root. The conflict and strict-document regressions were added before the
implementation. The first post-change run also exposed the fixture's unsafe
world-readable Hermes home; changing that fixture to represent the real
private server storage made the lifecycle test exercise the intended boundary.

## Verification

- `scripts/run_tests.sh tests/plugins/test_evolution_dashboard_plugin.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-plugin`
  - 18 passed
- `scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_service.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-service`
  - 53 passed
- `scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_jobs.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-jobs`
  - 26 passed
- `scripts/run_tests.sh tests/plugins/test_plugin_dashboard_auth_contract.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-plugin-auth`
  - 3 passed
- `scripts/run_tests.sh tests/hermes_cli/test_dashboard_auth_gate.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-auth-gate`
  - 22 passed, 1 skipped
- `scripts/run_tests.sh tests/hermes_cli/test_dashboard_auth_middleware.py -q --basetemp=/private/tmp/ecc-task6-fix1-final-auth-middleware`
  - 33 passed
- `git diff --check`
  - clean

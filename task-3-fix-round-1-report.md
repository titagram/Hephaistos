# Task 3 Fix Round 1 Report

## Scope

- Bind `pipeline(attempt_id=...)` to one attempt instead of mixing it with a
  global attempt page.
- Keep dashboard lifecycle verification, blueprint proof reads, and evolution
  directory discovery within fixed read budgets.
- Fail closed on a budget exceed; do not present a verified prefix as healthy.

## RED evidence

All focused RED commands used `scripts/run_tests.sh` with a task-scoped
`TMPDIR` and `--basetemp` because this checkout's default pytest cleanup path
recurses through a stale temporary tree.

| Regression | RED observation |
| --- | --- |
| Selected attempt scope | `test_pipeline_selected_attempt_uses_only_the_selected_attempt_scope` returned a second, unrelated attempt UUID with `limit=2`. |
| Bounded verifier | `test_bounded_chain_verifier_rejects_an_oversized_valid_chain` failed with missing `verify_chain_bounded`. |
| Oversized valid chain | `test_dashboard_blocks_an_oversized_valid_lifecycle_chain` returned pipeline state `ready`, proving the dashboard used the unbounded verifier. |
| Blueprint event proof | `test_blueprint_event_checks_cap_the_one_event_proof_at_one_plus_one_rows` observed two unrestricted `SELECT * FROM lifecycle_events WHERE attempt_id = ?` queries. |
| Evolution directory | `test_dashboard_bounds_the_evolution_directory_probe_before_materialization` reported `not_ready` after enumerating all 66 entries. |

## GREEN implementation

- Attempt detail views select and count only the requested attempt, with
  `total_attempts=1` and `attempts_truncated=false`.
- `EvolutionLedger.verify_chain_bounded(max_events=...)` uses `LIMIT cap + 1`
  and raises `lifecycle_event_read_limit` before any prefix is accepted.
  `verify_chain()` remains the full authoritative verifier.
- Dashboard pipeline, audit, and generation probes use the fixed 256-event
  budget. A budget exceed is `blocked`; an invalid complete chain is `corrupt`.
  Snapshot generation reads retain the stable `lifecycle_unavailable`
  diagnostic for the blocked case.
- Blueprint repository reads and idempotent blueprint replay retain their
  exactly-one-event invariant using `LIMIT 2`.
- `evolution_state_kind()` no longer materializes an unrestricted directory
  listing. Dashboard calls use a 64-member budget and block after observing
  entry 65; default callers retain the same empty / lock-only / existing
  classification using at most two entries.

## Regression evidence

Each suite used its own `--basetemp` directory.

| Command target | Result |
| --- | --- |
| `tests/hermes_cli/evolution/test_dashboard_service.py` | 39 passed |
| `tests/hermes_cli/evolution/test_ledger.py` | 43 passed |
| `tests/hermes_cli/evolution/test_bootstrap_matrix.py` | 11 passed |
| `tests/hermes_cli/evolution/test_blueprint_repository.py` | 94 passed |
| `tests/hermes_cli/evolution/test_blueprint_contract.py` | 63 passed |
| `tests/hermes_cli/evolution/test_command.py` | 11 passed |

# Task 5 — Fix Round 1

Status: DONE

## Delivered

- Telos revision writes and host-approved pointer publication now retain
  verified directory descriptors for `organism → telos → revisions`.  Every
  mutable operation uses no-follow, descriptor-relative file operations and
  rechecks the linked hierarchy before publication.  Platforms without the
  required atomic primitives fail closed.
- Dashboard initialization acquires an initialization-safe ancestor lock,
  then checks absence, valid identity, and corrupt roots before creating the
  organism hierarchy.
- Dashboard mutations reject stale snapshots before taking the
  diagnostic-writing lifecycle lock, then repeat the full proof under that
  lock to reject races safely.
- Telos draft parent checks use a non-creating store binding before the
  mutation lease, preventing a stale request from recreating a missing Telos
  tree.

## TDD coverage

Added deterministic regressions for:

1. `telos/revisions` replacement during a Telos revision write.
2. `telos/revisions` replacement during dashboard confirmation publication.
3. Concurrent valid and corrupt organism-root creation during initialization.
4. Stale mutations preserving lifecycle-lock diagnostic bytes.
5. A valid concurrent snapshot change between optimistic precheck and lease.
6. A stale Telos parent request with a missing Telos directory being a full
   filesystem no-op.

The new contracts were observed red before the implementation and are green
with the fix.

## Verification

All commands used unique `--basetemp` roots to avoid the test runner's
per-file parallel subprocesses sharing duplicate pytest test-directory names.

- `test_telos_contract_and_store.py`: 5 passed
- `test_dashboard_confirmations.py`: 5 passed
- `test_dashboard_service.py`: 53 passed
- `test_locking.py`: 27 passed
- `test_lifecycle_p0.py`: 9 passed
- `test_global_lifecycle_migration.py`: 9 passed
- `test_telos_cli_approval.py`: 56 passed
- Ruff and `git diff --check`: passed

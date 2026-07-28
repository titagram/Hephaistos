# Foundation Final Fix Round 1

## Result

Implemented and committed the requested Foundation fixes in
`2741edb5d1e7a09b16e83de6bde4fe1c107a9444`
(`fix(evolution): harden local organism foundation`). No push was performed.

## Changes

- `OrganismRevisionStore` now keeps reads non-mutating, rejects symlink and
  non-regular store artifacts, and creates/repairs storage permissions only
  during publication (`0700` directories and `0600` files).
- Legacy profile pointers are treated as unreadable when they are symlinks,
  whether their target exists or is dangling.
- Source collection opens only an existing backend database using immutable
  read-only SQLite access; it never initializes schemas, enables WAL, or
  creates a missing database.
- A lifecycle transition that needs authorization now requires a grant for the
  transition's actual kind, so a research grant cannot approve a build or
  promotion transition.
- Backend documentation now states that organism artifacts never enter remote
  synchronization.

## TDD evidence

Each gap was driven with a focused regression first and then the smallest
implementation change.

| Gap | RED command and result | GREEN command and result |
| --- | --- | --- |
| Organism storage safety and permissions | `./scripts/run_tests.sh tests/hermes_cli/test_gnothi_store.py -q --basetemp=/private/tmp/ecc-foundation-fix1-store-red-20260728b` — **5 failed, 10 passed**: default directories were `0755`; store-root, pointer, revision, and legacy-pointer symlinks were accepted. | `./scripts/run_tests.sh tests/hermes_cli/test_gnothi_store.py -q --basetemp=/private/tmp/ecc-foundation-fix1-store-green-20260728a` — **15 passed**. Final isolated rerun also passed: **15 passed**. |
| Read-only backend inspection | `./scripts/run_tests.sh tests/hermes_cli/test_gnothi_collectors.py -q --basetemp=/private/tmp/ecc-foundation-fix1-source-red-20260728a` — **1 failed, 11 passed**: an empty existing `hades_backend.db` changed bytes because the normal connection initialized it. | `./scripts/run_tests.sh tests/hermes_cli/test_gnothi_collectors.py -q --basetemp=/private/tmp/ecc-foundation-fix1-collector-wal-green-XXXXXX` — **13 passed**. Coverage snapshots database, `-wal`, and `-shm` bytes for both uninitialized and legacy live-WAL stores. |
| Research cannot grant build/promotion | `./scripts/run_tests.sh tests/hermes_cli/evolution/test_research_policy.py -q --basetemp=/private/tmp/ecc-foundation-fix1-policy-red-20260728a` — **1 failed, 8 passed**: the build transition accepted a research grant. | `./scripts/run_tests.sh tests/hermes_cli/evolution/test_research_policy.py tests/hermes_cli/evolution/test_state_machine.py tests/hermes_cli/evolution/test_ledger.py tests/hermes_cli/evolution/test_authorization.py -q --basetemp=/private/tmp/ecc-foundation-fix1-policy-green-20260728b` — **478 passed**. Final isolated rerun of the same four files: **471 passed** (the focused policy test was streamlined to two behavior tests). |

The policy test exercises real toolset resolution and real authorization
request/grant/ledger transitions. It does not assert source text or prose.

## Final verification

- Final focused loop, one fresh `mktemp -d` base per file and `umask 077` for
  ledger/authorization-sensitive tests: **499 passed** across
  `test_gnothi_store.py` (15), `test_gnothi_collectors.py` (13),
  `test_research_policy.py` (2), `test_state_machine.py` (333),
  `test_ledger.py` (42), and `test_authorization.py` (94).
- Proportional Foundation sweep: **56/56 test files passed**, each launched
  separately with its own fresh base directory: the eight Gnothi/backend
  Foundation files plus all 48 files in `tests/hermes_cli/evolution/`.
- `git diff --check` passed before staging.

One initial proportional-sweep run exposed a timing-sensitive failure in
`test_two_connections_that_preflight_v1_concurrently_both_open_v2`
(`invalid_ledger_database` from one of two concurrent migration openers).
The changed code does not run on that initialization path. Five subsequent
fresh, isolated complete-file runs of `test_ledger_migrations.py` were green
(**42 passed** each), so no unrelated migration change was made.

## Files changed

- `hermes_cli/gnothi/store.py`
- `hermes_cli/gnothi/collectors/source.py`
- `hermes_cli/evolution/state_machine.py`
- `hermes_cli/evolution/ledger.py`
- `tests/hermes_cli/test_gnothi_store.py`
- `tests/hermes_cli/test_gnothi_collectors.py`
- `tests/hermes_cli/evolution/test_research_policy.py`
- `docs/hades/backend.md`

## Residual risks

- Immutable SQLite reads deliberately ignore uncheckpointed WAL content. A
  binding that exists only in a live WAL therefore degrades to no binding
  rather than risking WAL/SHM mutation; the source collector reports partial
  graph coverage instead of changing backend state.
- The concurrent migration test had one non-reproducing timing failure during
  the sweep; repeated fresh runs were green, but it remains a test-infrastructure
  flake worth monitoring independently of this fix.
- Ruff is not installed in the shared test virtual environment; focused and
  proportional behavioral tests plus `git diff --check` were used for this
  round.

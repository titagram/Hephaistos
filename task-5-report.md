# Task 5 — Evolution Control Center API report

## RED evidence

- `tests/hermes_cli/evolution/test_dashboard_service.py` and
  `tests/hermes_cli/evolution/test_dashboard_confirmations.py` initially failed
  for the absent mutation and confirmation APIs: 39 existing tests passed, with
  9 service-contract and 4 confirmation-contract failures.
- The public pipeline suggestion-digest contract was then added with a focused
  failing test: the response raised `KeyError: 'suggestion_digest'` before the
  digest was exposed.

## GREEN evidence

- `./scripts/run_tests.sh --basetemp=.../green-final3 tests/hermes_cli/evolution/test_dashboard_service.py tests/hermes_cli/evolution/test_dashboard_confirmations.py -q`
  — 52 passed, 0 failed.
- Lifecycle and job regressions: dashboard jobs (26), lifecycle P0 (9),
  locking (27), and global lifecycle migration (9) all passed.
- Telos regressions: contract/store (4), adversarial (14), approval security
  (2), host approval (10), and CLI approval (56) all passed.
- Observer/Blueprint regressions: proposal service (28), blueprint repository
  (94), blueprint contract (63), blueprint command (11), observer policy (3),
  observer degraded mode (5), observer ingestion (11), and suggestion notices
  (2) all passed.

## Delivered contract

- Dashboard writes authenticate a full organism UUID and expected full snapshot
  digest while holding the lifecycle lock; mismatches fail before domain-state
  writes.
- Initialization fails closed for an existing or corrupt organism. Observer
  pause/resume, fixed rebuild/scan jobs, Telos draft validation, and Blueprint
  creation are wired to their existing lifecycle services.
- Pipeline suggestions expose their canonical digest so a client can supply the
  required optimistic-concurrency value for Blueprint creation.
- Telos activation/rollback uses a host-owned, in-memory, one-time
  confirmation context. The public payload contains only the exact public
  confirmation fields; host approval context and session reference remain
  server-side. Every confirm attempt consumes its context, and confirmation
  revalidates organism, snapshot, active/target digests, action, phrase, and
  expiry before the real Telos transition.

## Non-blocking hardening debt

- `lifecycle_lock` refreshes its operational `.lifecycle.lock` diagnostic even
  when a stale mutation is rejected. Atomicity tests intentionally exclude this
  lock artifact while asserting no organism/lifecycle domain artifact changes.
- `scripts/run_tests.sh` runs input files in parallel while forwarding one
  `--basetemp`; multiple files can race over that directory and cause fixture
  setup/cleanup errors. Each verification file was therefore given a distinct
  temporary base. This is test-runner infrastructure debt, not a Task 5
  functional failure.

## Dashboard UI delivery

Implemented the Telos control-center view with structured, bounded form fields
for the complete public Telos document schema. Draft saves use the active digest
as their displayed and serialized parent, remain inert, and surface local
validation plus server rejection feedback.

The view exposes truthful bounded revision history and semantic field-level
diffs. Activate and rollback share one accessible strong-confirmation dialog,
but describe distinct consequences. The dialog fetches fresh mutation context
before prepare and confirmation, displays the server-issued organism, digests,
action, expiry, and exact phrase, and permits only one confirmation attempt. A
409 closes the dialog, refreshes current state, shows a warning, and does not
retry.

Validation run:

- `npm run test -- evolution-telos.test.ts evolution-api.test.ts evolution-plugin.test.ts evolution-graph.test.ts` — 29 passing tests
- `npm run check:evolution` — TypeScript check, rebuilt dashboard bundle, and JavaScript syntax check passed

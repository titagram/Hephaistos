# Whole-branch reliability fix report

Date: 2026-07-28

Baseline: `89c106ffc`
Scope: local-first Kanban remote lifecycle hardening. No deployment and no live
resume of session `20260727_191342_7b2122` were performed in this fix wave.

## Outcome

The reviewed whole-branch defects are addressed with durable, binding-scoped
state and cross-process coordination. The final selected regression suite
completed with:

- 874 tests
- 0 failures
- 0 errors
- 0 skipped
- 40.372 seconds

The JUnit evidence is
`/private/tmp/kanban-fix-final-suite2.xml`.

## Issue mapping

### C1 — terminal result hook and durable outbox

- `complete_task`, `block_task`, and internal give-up transitions enqueue their
  remote terminal result in the same SQLite transaction as the local terminal
  state.
- Immediate delivery is best effort and occurs only after the local commit.
- A process interruption between commit and delivery leaves a replayable
  outbox row.
- The dashboard and CLI paths inherit the same central transition behavior.
- Org-run links carry an immutable `org_run_gated` publication policy; their
  generic terminal hook cannot publish before the independent
  evidence/integration gate.

Tests cover completion, blocking, internal give-up, CLI restart recovery,
dashboard restart recovery, and the org-run integration-ready gate.

### C2 — remote heartbeat

- A successful local worker heartbeat now also attempts the exact linked remote
  lease heartbeat.
- Transport failures preserve the acquired lease and record a redacted,
  retryable state.
- Permanent/expired lease failures clear the token and persist an expired
  lease state so the next admission must reacquire.
- The local heartbeat remains authoritative and is not rolled back by a remote
  outage.

### C3 — cross-process sync ownership and atomic materialization

- Per-board/per-binding sync ownership is stored in SQLite rather than process
  memory.
- Remote materialization of a work item is enclosed by one outer write
  transaction, so concurrent processes cannot create duplicate local tasks or
  split task/link persistence.
- A multiprocessing test exercises two independent SQLite connections and
  verifies exclusive sync ownership.
- An adversarial concurrent materialization test verifies one task and one
  remote link.

### I1 — binding-scoped outbox

- Outbox identity includes project and workspace binding.
- Counts, claims, drains, retries, and terminal result publication are scoped to
  the resolved binding.
- Rebinding cannot drain another binding's queued result.

### I2 — client ownership

- Sync-created clients are borrowed by outbox draining and closed once by their
  owner.
- Standalone delivery continues to own and close its own client.
- Injected test factories retain compatibility with both context-aware and
  legacy zero-argument callables.

### I3 — outbox CAS

- Delivery first atomically claims a row with a random claim token.
- Rows use `inflight` plus an expiry for crash recovery.
- Sent, retry, and dead-letter transitions require the matching claim token.
- Concurrent drainers therefore produce one remote mutation, and a late
  failure cannot resurrect an already-sent/dead-letter row.
- Existing databases receive additive migrations for claim columns.

### I4 — read-only `sync --status`

- `hades kanban sync --status` reads only durable local sync/outbox state.
- It does not construct a backend client, pull remote work, drain the outbox, or
  mutate retry state.
- Plain `hades kanban sync` remains the explicit mutating operation.
- Operations documentation now states this contract.

### I5 — durable retry backoff

- Sync failures persist failure count, redacted error, and exponential
  `next_attempt_at`.
- Automatic triggers respect the persisted timestamp across process restarts.
- Explicit plain sync uses `force=True`, preserving operator control.

### I6 — structured admission outcomes

- Transport unavailability defers linked work without consuming an attempt.
- Authentication, validation, malformed-claim, binding mismatch, and permanent
  identity failures are persisted and routed as typed supersede outcomes.
- Error storage uses secret redaction.
- Already-acquired valid leases remain dispatchable while the backend is
  temporarily offline.

### I7 — exact offline identity and lease semantics

- Offline context resolution retains the stored project and workspace binding;
  it no longer silently reclassifies a linked card as generic local-only.
- A linked card with a persisted acquired lease remains admissible without
  constructing a client.
- An unleased linked card while offline is deferred, not falsely superseded for
  identity mismatch.

### Minor repairs

- Malformed legacy `remote-kanban:` markers produce an idempotent migration
  diagnostic event instead of disappearing silently.
- Old outbox schemas migrate claim fields and the sync-lock table additively.
- Re-entrant local write transactions allow atomic higher-level operations
  without nested `BEGIN` failures.

## TDD evidence

Initial focused RED:

```text
tests/hermes_cli/test_kanban_remote_reliability.py
14 failed
```

The focused file was expanded while implementing sibling paths and finished:

```text
17 passed in 1.49s
```

The first integrated regression run exposed six failures:

```text
321 passed, 6 failed
```

Five were obsolete expectations after the intended local-first semantics
changed. One was a real regression: the central terminal hook could publish an
org-run result before its integration gate. Adding the typed
`org_run_gated` publication policy fixed that behavior rather than weakening
the test.

Core database/sync/coordination rerun:

```text
261 passed in 10.50s
```

Final selected regression command:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -qq \
  tests/hermes_cli/test_kanban_remote_reliability.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/hermes_cli/test_hades_kanban_sync.py \
  tests/hermes_cli/test_kanban_backend.py \
  tests/hermes_cli/test_kanban_cli.py \
  tests/hermes_cli/test_hades_coordination.py \
  tests/gateway/test_kanban_auto_decompose_live.py \
  tests/plugins/test_kanban_dashboard_plugin.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/hermes_cli/test_hades_backend_status.py \
  tests/hermes_cli/test_web_server.py \
  --basetemp=/private/tmp/kanban-fix-final-suite2 \
  --junitxml=/private/tmp/kanban-fix-final-suite2.xml
```

Result: `874 passed`, with zero failures/errors/skips.

Additional checks:

```text
git diff --check: clean
python -m py_compile (all changed Python production modules): passed
```

## Self-review

- Local completion remains authoritative; backend failures cannot roll it back.
- Durable remote intent is committed before any network call.
- Binding identity is explicit in persisted links, outbox keys, and drain
  selection.
- Admission does not consume attempts for retryable backend outages.
- Status inspection is observational.
- The implementation extends the existing SQLite/CLI/plugin architecture and
  adds no core model tool.

## Remaining validation boundary

The reliability behavior is covered by unit, integration, concurrent-thread,
and multiprocessing tests. A browser DOM run of the localhost dashboard was
not available in this wave; dashboard route/plugin and web-server tests are
green. Per task ownership, deployment and resuming the live Hades session are
left to the parent orchestration step after this commit.

## Continuation — final re-review blockers

Date: 2026-07-28

This continuation addresses the final two Critical and one Important
re-review findings without deploying or resuming a live session.

### Chain-aware remote failure classification

- Kanban remote admission and heartbeat now reuse the existing proven
  `_is_transport_failure` traversal used by backend sync.
- The traversal follows both `__cause__` and `__context__`, detects cycles, and
  recognizes `httpx.TransportError` in addition to standard connection,
  timeout, and OS failures.
- A production-shaped `HadesBackendError` caused by
  `httpx.ConnectError` is therefore retryable transport, not an identity
  rejection.
- Transport heartbeat failures preserve the acquired lease. Permanent lease
  results still expire the lease, but terminal completion now always creates a
  durable ordinary-link intent.
- Delivery with an absent/expired lease performs no backend mutation and
  returns the outbox row to `pending` with a reconciliation-required reason.
  Reacquiring a lease makes that same idempotent intent deliverable.

Regressions use the real exception wrapping shape, including transport failure
during client construction, rather than a bare `ConnectionError`.

### Immutable publication policy

- `kanban_remote_links.publication_policy` is a structured enum with
  `ordinary` as the default and `org_run_gated` for integration-gated work.
- The field is distinct from mutable lease and synchronization status. Public
  link APIs reject attempts to change it after creation.
- The additive migration maps the historical `sync_status='org_run_gated'`
  encoding and recognizable OrgRun execution nodes into the new policy, then
  normalizes mutable sync status.
- OrgRun graph creation now runs in one outer SQLite write transaction and
  creates each execution node and gated remote link together. A simulated
  interruption after link insertion rolls back the complete graph and link,
  proving there is no ordinary-policy crash window.
- Successful claim, generic admission, and heartbeat update lease/sync state
  without changing policy. Generic completion creates no outbox entry for a
  gated link; specialized evidence-gated publication remains the only result
  path.

### Structured OrgRun claim

- `claim_remote_work_item_outcome` and
  `claim_org_run_remote_task_outcome` return the same
  `DispatchAdmission` shape used by generic dispatch.
- Transport returns `defer`; authorization, validation, malformed claim,
  mapping, and identity failures return stable typed `supersede` reasons for
  operator action.
- Persisted diagnostics are bounded/redacted, and public reasons contain no
  backend exception text or credentials.
- Existing tuple callers remain supported by thin compatibility adapters over
  the structured result.

### Continuation TDD evidence

Initial production-shape RED:

```text
tests/hermes_cli/test_kanban_remote_reliability.py
4 failed, 15 passed
```

The coordination suite additionally failed collection because the new typed
OrgRun outcome API did not yet exist. A sibling-path test then reproduced a
wrapped transport failure during client construction as one additional RED.

GREEN progression:

```text
Reliability + coordination:                  35 passed in 1.94s
DB + reliability + coordination:            272 passed in 10.90s
OrgRun portfolio/distributed/CLI consumers:  25 passed in 0.98s
Client-construction and gate focus:           3 passed in 0.30s
```

Final selected regression suite plus relevant OrgRun consumers:

```text
912 tests
0 failures
0 errors
0 skipped
40.696 seconds
```

JUnit evidence:
`/private/tmp/kanban-cont-final-suite.xml`.

Final static checks:

```text
git diff --check: clean
python -m py_compile (all changed production modules): passed
```

The remaining validation boundary is unchanged: dashboard routes/plugins and
web-server tests are green, while browser DOM validation, deployment, and live
Hades session resume belong to the parent orchestration step.

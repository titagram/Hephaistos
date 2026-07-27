# Task 10 report — local-first Kanban regression and operations handoff

## Delivered

- Added a local-only board E2E: a fresh board dispatches and completes a card
  with no backend database, token, workspace binding, or network setup.
- Added a project-link regression proving that a local `project_id` alone does
  not make a card remote or require a remote lease.
- Documented the local-first contract, `hades kanban serve`, `hades kanban
  sync --status`, `local_only`, remote lease deferral, durable terminal-result
  retry, and the independence of `memory.provider`.

## TDD evidence

The new local-only E2E was written before changing any production code. Its
first run failed because the isolated test had no spawnable `leaf` profile;
after the test provided that existing dispatch prerequisite, it exposed the
current `DispatchResult.spawned` tuple contract. The final focused regression
passed. The project-link test likewise initially exposed that its old
standalone SQLite fixture used a different dispatcher lock path; it was made a
real isolated board fixture, then the intended local dispatch behavior passed.

No production change was needed in this task: the previous implementation
tasks already provided the required behavior, and these tests lock it in.

## Verification

Fresh, distinct `--basetemp` directories were used for every command to avoid
the repository's known numbered-temp cleanup recursion.

```text
test_kanban_db.py                                  233 passed
test_hades_kanban_sync.py                           18 passed
test_kanban_backend.py                               3 passed
test_kanban_cli.py                                  54 passed
test_kanban_core_functionality.py                  180 passed, 1 skipped
test_hades_backend_sync_runner.py                   60 passed
test_hades_backend_memory_provider_sync.py          10 passed
test_hades_backend_conversation_sync.py              1 passed
test_kanban_project_link.py                          5 passed
test_kanban_auto_decompose_live.py                   9 passed
```

`tests/plugins/test_kanban_dashboard_plugin.py` completed 74 tests with no
failure before the local runner stopped without a pytest summary during its
known teardown behavior. The next test at that boundary,
`test_home_subscribe_flips_subscribed_flag_in_subsequent_get`, passed when run
individually. This is recorded as incomplete runner evidence, not as a claim
that the full dashboard file passed.

The one excluded core test is pre-existing and unrelated to this task:
`test_dispatch_once_integrates_stale_detection` sets fake PID `99999`; the
test live-system guard correctly blocks the resulting `os.kill(99999,
SIGTERM)` outside the test subtree. Its full-suite attempt reached `487
passed, 1 skipped` before that guard failure.

### Isolated worktree smoke

Using the worktree entrypoint (not the installed `hades`) with a new
`HERMES_HOME`:

```text
create_exit=0 sync_exit=0
{
  "state": "local_only",
  "workspace_binding_id": null,
  "pulled": 0,
  "created": 0,
  "existing": 0,
  "delivered": 0,
  "deferred": 0,
  "failed": 0,
  "outbox_pending": 0,
  "error": null
}
```

This proves the board and `sync --status --json` need no backend database or
token in a fresh home.

### Localhost launcher evidence

An active-board launcher attempt used the worktree entrypoint with
`--host 127.0.0.1 --port 0 --no-open`. The sandbox denied access before any
bind attempt, so no retry loop was run:

```text
kanban: could not initialize database: [Errno 1] Operation not permitted:
'/Users/gabriele/.hermes/kanban/boards/ariadne/kanban.db.init.lock'
```

No live board task, deployment, or historical session was resumed or mutated.

### Integrity

- `python -m compileall -q` for the Task 1–9 Python surfaces: exit 0.
- `git diff --check`: exit 0.
- Ruff could not run in the available virtualenv: `No module named ruff`; no
  package was installed or environment changed to work around that limitation.

## Self-review

- The documentation describes only the implemented local-first behavior and
  reuses the existing dashboard rather than promising a new frontend.
- The E2E uses a named board and real dispatch/complete calls; the project
  regression verifies that a first-class local project association remains
  distinct from a `kanban_remote_links` record.
- The task touched only its documentation, regression tests, and this handoff;
  it does not deploy, resume, or claim completion of the live Ariadne chain.

# Task 9 report — memory/backend sync separation

## Delivered

- Added `run_backend_sync(..., include_memory: bool | None = None)`.
  Memory snapshot/proposal work now runs only for the active
  `memory.provider: hades_backend`, unless explicitly requested.
- Preserved jobs, artifacts, logbook, inbox, and awareness sync regardless of
  the selected memory provider.
- Made `HadesBackendMemoryProvider.sync_turn()` explicitly request memory sync
  for its exact workspace binding.
- Scoped background state to `background_sync:<workspace_binding_id>` for a
  single selected binding; manual/all-binding runs retain the legacy aggregate
  key for compatibility.
- Made current-workspace status read its scoped state, count unrelated failed
  bindings separately, filter a foreign `last_sync_error`, and redact exposed
  background errors.

## TDD evidence

RED was observed for the new contracts before their implementation:

- no module-level `load_config_readonly` gate;
- no `background_sync_state_key` API;
- memory provider omitted `include_memory=True`.

The binding-status test additionally reproduced the real global-error leak:
an error recorded for binding B made binding A degraded.

## Verification

Passed (with an explicit fresh pytest base directory because the repository's
default pytest numbered-temp cleanup currently recurses independently of these
changes):

```text
python -m pytest tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/agent/test_hades_backend_memory_provider_sync.py \
  tests/agent/test_hades_backend_conversation_sync.py \
  tests/agent/test_turn_finalizer_cleanup_guard.py -q --basetemp=<fresh-temp>
78 passed
```

`scripts/run_tests.sh` executed all 76 selected tests successfully, but its
per-file subprocesses then exited nonzero during pytest's numbered-directory
teardown with a pre-existing `RecursionError` in `shutil.rmtree`; the output
showed every selected test passing before that teardown failure.

## Self-review

- `git diff --check` clean.
- No Kanban Task 10 documentation or live-validation files changed.

## Fix Round 1

Addressed three P1 review findings with RED/GREEN regressions:

- Status now selects the most-specific binding containing the requested cwd
  without default-agent preference; the default agent is used only when no cwd
  binding exists.
- A selected binding no longer falls back to the legacy aggregate
  `background_sync` state. The aggregate remains available only when there is
  no selected binding, so a historical/all-binding failure cannot degrade a
  healthy current workspace.
- Exceptions thrown by a background sync runner now persist a redacted,
  binding-scoped failed state with exponential backoff and always release the
  in-memory running guard. Backoff expiry is retry-eligible rather than being
  masked by the normal success interval.

Verification:

```text
python -m pytest tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/hermes_cli/test_hades_backend_status.py \
  tests/agent/test_hades_backend_memory_provider_sync.py \
  tests/agent/test_hades_backend_conversation_sync.py \
  tests/agent/test_turn_finalizer_cleanup_guard.py -q --basetemp=<fresh-temp>
92 passed
```

# O5 — Information-only Persephone worker

## Scope delivered

- Added `hermes_cli/hades_information_worker.py` with the public
  `InformationRequest`, `InformationResponse`, `PolicyDenied`,
  `validate_information_capability()`, `run_information_request()`, and
  `execute_stored_information_request()` interfaces.
- Added an exact, deny-by-default allowlist for `source_slice`,
  `source_search`, `symbol_lookup`, `git_metadata`, `artifact_metadata`, and
  `project_memory_search`.
- Bound execution to the durable request's exact linked project, target agent,
  and workspace binding. There is no fallback to another workspace.
- Implemented all v1 capabilities as direct bounded local reads. The optional
  `agent_factory` is never invoked, so the worker cannot acquire terminal,
  browser, delegation, write, build, test, or mutation tools.
- Added bounded evidence envelopes with summary, evidence references,
  truncation state, residual uncertainty, path/secret redaction, binary and
  symlink exclusion, and a payload budget below the O1 wire limit.
- Added atomic O2 response persistence: a successful or safely summarized
  operational result moves `processing -> processed -> responded` while the
  outbox response and durable request link are committed together.
- Added an optional receiver execution hook. It is called only after O4 has
  classified a request as auto-accepted and resolved its exact route. Human,
  mutating, ambiguous, and unknown requests remain in
  `waiting_human_approval` and never instantiate the worker. Lifecycle wiring
  remains intentionally deferred to O6.

## TDD evidence

1. Initial RED: 10 tests failed with `ModuleNotFoundError` because the worker
   did not exist.
2. First GREEN: 10 tests passed after the minimal direct handlers, authority
   validation, and atomic response path were implemented.
3. Receiver RED: 2 tests failed because `information_executor` and
   `response_id_factory` were not yet accepted.
4. Receiver GREEN: the worker was dispatched only for auto-accepted requests,
   and the integration test observed a durable `information_response`.
5. Hardening RED/GREEN covered recursive symlink escape, `.git` symlink
   escape, oversized cached-memory evidence, secret-bearing keys, absolute
   local paths, and safely summarized handler errors.
6. Atomicity RED/GREEN proved response-envelope construction failure leaves
   the request in `processing` instead of stranding it in `processed` without
   a durable response.

## Verification

```text
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_persephone_messages.py \
  tests/hermes_cli/test_hades_persephone_store.py \
  tests/hermes_cli/test_hades_persephone_transport.py \
  tests/hermes_cli/test_hades_persephone_receiver.py \
  tests/hermes_cli/test_hades_information_worker.py -q
164 passed

.venv/bin/python -m ruff check \
  hermes_cli/hades_information_worker.py \
  hermes_cli/hades_persephone_receiver.py \
  tests/hermes_cli/test_hades_information_worker.py \
  tests/hermes_cli/test_hades_persephone_receiver.py
All checks passed!

.venv/bin/python -m py_compile \
  hermes_cli/hades_information_worker.py \
  hermes_cli/hades_persephone_receiver.py
passed

git diff --check
passed
```

## Deferred by plan boundary

- Starting/stopping the receiver and selecting the execution hook from gateway
  lifecycle configuration is O6.
- Blackboard/DAG interrupts and remote Kanban projection are O7/O8.
- No backend mutation or remote deployment was performed.

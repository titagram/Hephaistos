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
7. Authority-boundary RED/GREEN proved the exported worker rejects expired
   requests even when called outside the receiver path.

## Verification

```text
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_persephone_messages.py \
  tests/hermes_cli/test_hades_persephone_store.py \
  tests/hermes_cli/test_hades_persephone_transport.py \
  tests/hermes_cli/test_hades_persephone_receiver.py \
  tests/hermes_cli/test_hades_information_worker.py -q
165 passed

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

## Critical review remediation

Follow-up review findings were addressed with additional RED/GREEN cycles:

- Sensitive paths are denied without echoing their names or contents. The
  policy covers `.env` variants, credential/secret/token/auth/provider stores,
  Hades/Hermes metadata, `.git` internals, SSH/private-key names, and
  PEM/certificate/key containers. Directory pruning is top-down.
- Evidence redaction is recursive, case-insensitive, cycle/depth/node bounded,
  and replaces entire values for password, passphrase, secret, token, API and
  access keys, private keys, authorization, credentials, cookies, sessions,
  client secrets, and AWS secrets. Text detection covers ENV, JSON, YAML,
  Bearer, PEM, AWS access-key and JWT forms.
- Source search uses `os.walk(topdown=True, followlinks=False)` with hard
  directory, entry, file, per-file byte, aggregate byte, result, and monotonic
  deadline budgets before reading. It never sorts/materializes the full tree.
  Project-memory matching and response cleaning have independent bounded
  node/depth/string/deadline traversal and do not stringify whole objects.
- Inbox rows now persist bounded attempts, next-attempt time, and sanitized
  error codes. Stale auto-information workers recover selectively; unrelated
  or human-approved work is not reset.
- `persist_response_for_request()` now supports direct atomic
  `processing -> responded`: global response identity, outbox row and inbox
  link share one SQLite transaction. A trigger-induced link failure proves the
  outbox and identity roll back while the request remains processing.
- Worker reads require durable `processing` state. Operational/construction
  failures return the request to `received` until the bounded attempt cap;
  expiry is durable. Response IDs are deterministic, and redelivery produces
  one response/outbox row without re-execution after completion.
- A receiver without an executor leaves allowed work in durable `received`
  and does not advance its queue cursor. Terminal duplicate delivery repairs a
  crash-gap cursor without re-running the worker. `run_backend_sync()` now
  supplies the safe information executor; its integration test executes a
  real source-search request exactly once and persists the response.

Focused critical-remediation verification:

```text
97 passed
Ruff: All checks passed!
py_compile: passed
git diff --check: passed
```

Post-commit audit added two further guards: retry/recovery rejects non-information
`processing` rows, and project-memory cache size is checked in SQLite before JSON
materialization. Known standalone Bearer, GitHub, OpenAI-style, Slack, AWS, JWT,
and PEM token forms are redacted. Follow-up focused verification: `99 passed`.

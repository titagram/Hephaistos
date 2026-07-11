# O7 implementation report — delegated DAG coordination

## Outcome

Implemented leaf manifests, direct-parent authority, relevance-based addressed
blackboard routing, and safe-boundary delivery for delegated agents.

## TDD evidence

RED was captured before implementation:

```text
ModuleNotFoundError: No module named 'hermes_cli.hades_agent_coordination'
ImportError: cannot import name 'apply_pending_coordination_to_tool_results'
```

Final focused suite:

```text
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_agent_coordination.py \
  tests/agent/test_tool_executor.py \
  tests/tools/test_delegate.py -q
175 tests collected; exit 0
```

O1–O7, delegation, prompt-cache, and role-alternation regression set:

```text
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_agent_coordination.py \
  tests/hermes_cli/test_hades_coordination.py \
  tests/hermes_cli/test_hades_information_worker.py \
  tests/hermes_cli/test_hades_persephone_messages.py \
  tests/hermes_cli/test_hades_persephone_receiver.py \
  tests/hermes_cli/test_hades_persephone_store.py \
  tests/hermes_cli/test_hades_persephone_transport.py \
  tests/gateway/test_hades_persephone_lifecycle.py \
  tests/agent/test_tool_executor.py \
  tests/agent/test_prompt_builder.py \
  tests/agent/test_turn_finalizer_interrupt_alternation.py \
  tests/tools/test_delegation_contract.py \
  tests/tools/test_delegation_capacity.py \
  tests/tools/test_delegation_evidence.py \
  tests/tools/test_delegation_routing.py \
  tests/tools/test_delegate.py -q
731 tests collected; exit 0 (one pre-existing skip)
```

Quality gates passed:

- Ruff on all O7 implementation/test files (the existing malformed `# noqa`
  warning in `run_agent.py:107` remains non-blocking).
- `py_compile` on all O7 Python implementation files.
- `git diff --check`.

## Design and invariants

- `LeafManifest` exposes only identity, role, objective, scope, dependencies,
  interfaces, and produced artifacts needed to prove relevance.
- Batch children receive stable sibling awareness before their first model call;
  the system prefix is therefore byte-stable for the child's whole lifetime.
- Only the direct parent can change a child contract. The root can inspect any
  descendant, but cannot command a grandchild through the authority API.
- A sibling request requires a dependency, shared interface/scope, produced
  artifact, or named blocker. Missing proof routes to the direct parent.
- Bare sibling IDs cannot bypass relevance checks. Broadcast is restricted to
  `blocker` and `interface_change` events.
- Blackboard rows are append-only and bounded by recipient count, summary size,
  fetch limit, and TTL. Durable per-recipient cursors provide ack/dedup/replay
  behavior without a B-tree or second scheduler.
- Delivery mutates only the newest tool result from the current batch. It never
  inserts a user message or edits old context, preserving role alternation.
- The event block is appended before the first SessionDB flush. The cursor is
  acknowledged only after that flush succeeds; a crash/failure replays the
  event at the next safe tool boundary.
- Aggregate tool-output budget enforcement preserves the bounded coordination
  suffix even when it replaces a large underlying tool result, so an already
  acknowledged wakeup cannot become invisible to the model.
- The low-level addressed event helper is also available to the existing Kanban
  swarm and Hades coordination surfaces. No remote Kanban projection was added
  (reserved for O8), and no model-visible core tool was introduced.

## Files

- Added `hermes_cli/hades_agent_coordination.py`.
- Added `tests/hermes_cli/test_hades_agent_coordination.py`.
- Added `tests/agent/test_tool_executor.py`.
- Updated `tools/delegate_tool.py`, `agent/agent_runtime_helpers.py`,
  `agent/tool_executor.py`, `run_agent.py`, `hermes_cli/kanban_swarm.py`, and
  `hermes_cli/hades_coordination.py`.
- Updated `tests/tools/test_delegate.py` with sibling-awareness integration
  coverage.

## Post-review hardening

The first review identified six classes of issues. All were addressed in a
second strict-TDD pass:

- Runtime events are now represented as trusted sidecars. Tool output is never
  searched for a coordination marker. A 1 MB forged-marker regression proves
  ordinary tool data is compacted while the runtime sidecar remains bounded.
- Sidecar rendering has independent 20-event/8 KiB limits and composition
  reserves bytes inside the aggregate tool-result budget.
- Posting an event and advancing its recipient generation occur in one owned
  `BEGIN IMMEDIATE` transaction. The runtime checks dirty state at trailing
  tool boundaries before the next model call; terminal children atomically
  coalesce pending generations to their direct parent without new message
  roles or system-prompt mutation.
- No-SessionDB delivery is never acknowledged as durable. It remains dirty,
  avoids duplicate attachment in the live process, and replays after restart.
  A post racing between drain and ack leaves the newer generation dirty.
- `DelegationAuthority` owns a durable manifest registry. Posting accepts actor
  and recipient IDs and resolves relevance from that registry. Root queries are
  information-only, while contract/manifest mutation remains direct-parent
  only. Manifests carry status, task/contract versions, interfaces, and
  produced artifacts from the structured task contract/runtime.
- Event IDs are unique/idempotent under concurrent retries and restart.
  Recipient-indexed rows and indexes support bounded `LIMIT` queries; evidence,
  render, recipient, TTL, and cleanup limits are hard bounds.
- Coordination schema is part of the normal Hades DB migration. Every
  coordination API owns its connection/transaction; the Kanban adapter never
  executes or commits its caller's connection. Concurrent posts while an
  unrelated Kanban transaction rolls back are covered.

Final post-review evidence:

- O1–O7/delegation/cache/role suite: 737 tests collected, exit 0, one
  pre-existing skip.
- Hades backend DB migration suite: 5 passed.
- Run-agent conversation regression plus coordination hardening: exit 0.

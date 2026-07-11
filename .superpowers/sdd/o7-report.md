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

## Final namespace and late-arrival remediation

The follow-up review found that the first hardening pass still treated agent
IDs as globally unique and relied on process-local message identity after an
early SessionDB flush. The final pass closes those gaps:

- Every manifest, event, recipient row, cursor/state row, dirty generation,
  completion handoff, and delivery is scoped by the composite
  `(root_id, project_id)` namespace. Identical agent/event IDs can coexist in
  independent trees without observation, routing, or acknowledgement leakage.
- Unnamespaced or partially upgraded coordination tables are quarantined before
  the normal schema/index pass. No ambiguous legacy row is silently assigned to
  a project or delegation root.
- Registration cannot replace a root/parent or mutate an existing manifest.
  Contract changes use a single SQLite transaction with task+contract version
  compare-and-swap; both versions are monotonic and stale writers fail closed.
- The canonical event request includes namespace, event ID, actor, requested
  recipient, type, summary, evidence, artifact, blocker, and TTL. Its durable
  fingerprint makes retries immutable and preserves the originally committed
  route even if child-completion state changes before a retry.
- Root inspection and questions remain namespace-scoped and information-only;
  mutation is direct-parent-only. Existing ancestry cannot be hijacked through
  re-registration.
- Runtime composition refuses non-string/multimodal targets instead of coercing
  or corrupting content. The stale sibling-awareness assertion now checks the
  versioned manifest format actually placed in the byte-stable prompt.
- Late coordination arriving after the base tool result was flushed is written
  back to the exact durable SessionDB row by `(session_id, tool_call_id)` before
  acknowledgement. This lookup survives process restart; the in-memory object
  identity map is only a flush optimization and is no longer accepted as proof
  of persistence. If no durable target exists, the event remains dirty for
  replay.

Final evidence:

- Focused O7/DB/tool/delegate suite: `196 passed`.
- O1-O7, delegation, prompt-cache, and role-alternation suite:
  `753 passed, 1 skipped`.
- Ruff: all checks passed (the pre-existing malformed `# noqa` warning at
  `run_agent.py:107` remains non-blocking).
- `py_compile` and `git diff --check`: passed.

## Final operational surface and manifest-bound remediation

The final integration audit required a supported model-facing path into the
already durable coordination runtime. This was added as bounded actions on the
existing `delegate_task` tool, avoiding a new unconditional core tool:

- `coordination_post`, `coordination_status`, and `coordination_inspect` are
  discoverable in the stable delegation schema. Sender, root, and project are
  taken exclusively from the active delegated runtime context; the schema has
  no spoofable identity or namespace fields.
- Leaves and reviewers retain the delegation toolset for these coordination
  actions, but runtime authorization rejects attempts to spawn children. Only
  an orchestrator may use the normal `delegate` action below the root.
- Addressed question/answer delivery uses the existing relevance and authority
  checks. The E2E test proves a relevant leaf-to-leaf question, safe-boundary
  wakeup, answer routing, unauthorized inspection denial, and leaf fan-out
  denial.
- The hierarchical-development skill now documents the exact operational
  actions, runtime-bound identity rule, relevance proof, safe-boundary wakeup,
  and root/direct-parent read-only semantics.
- Manifest objectives, list counts, list-item sizes, identities, status, and
  total registry size have hard limits. Awareness is limited by sibling count
  and UTF-8 bytes, omits objectives entirely, hides irrelevant sibling details,
  and exposes concise structured scope/interface/dependency/artifact metadata
  plus exact relevance reasons and task/contract versions.

Final integration evidence:

- Focused coordination/delegation/dispatch suite: `210 passed`.
- Broad O1-O7/delegation/cache/role suite: `775 passed, 1 skipped`.
- Ruff, `py_compile`, and `git diff --check`: passed.

## Awareness parity and retry-id remediation

The last audit aligned the human/model awareness view with the exact runtime
router and made the supported posting surface retry-idempotent:

- Relevance now reports an artifact path whenever either side produces the
  named class of artifact, distinguishing `target_produces` from
  `source_produces`. Artifact-only sibling relevance is therefore visible
  before a post and agrees with `is_relevant_request`.
- Relevant siblings are ordered first and always receive a bounded entry.
  Identity/reason values have UTF-8 byte slices; every metadata class has a
  fixed item/value slice; each entry has a hard byte cap. Oversized valid
  manifests retain identity and relevance with an explicit
  `[details omitted/truncated]` marker rather than disappearing. A bounded
  global marker reports every sibling omitted by count or byte budget.
- `coordination_post` no longer generates a fresh UUID on each supported call.
  The dispatcher threads the trusted runtime tool-call ID outside the model
  schema, and the action derives its event ID from that operation ID plus the
  bound root/project/actor namespace. Replaying the same tool call after a
  retry/restart produces one event and one dirty generation; a distinct tool
  call produces a distinct event. Missing trusted identity fails closed.

Evidence:

- Focused awareness/dispatcher tests: `31 passed`.
- Broad O1-O7/delegation/cache/role suite: `775 passed, 1 skipped`.
- Ruff, `py_compile`, and `git diff --check`: passed.

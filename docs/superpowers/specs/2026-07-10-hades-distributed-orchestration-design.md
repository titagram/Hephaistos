# Hades distributed orchestration design

## Goal

Coordinate Hades Agent instances through the shared backend while preserving
project isolation, local workspace authority, durable delivery, hierarchical
responsibility, and bounded token use.

## Authority model

The backend is the source of truth for shared project memory, project identity,
the project-manager Kanban, instance presence, logical OrgRuns, and
project-scoped communication queues. A project-manager card remains the
original versioned mandate even when its natural-language description is
ambiguous or incomplete.

Each Hades Agent is the sole authority for its local workspace. It compiles a
versioned remote mandate into a validated local task contract and execution
DAG. No peer may directly mutate another instance's workspace, local DAG,
agent state, or child contracts. Questions, decisions, responses, proposals,
and verified evidence are appended as separate records; agents do not rewrite
the original remote card.

A remote mandate version change marks affected local contracts, DAG nodes, and
evidence packets stale. The affected subtree pauses until its orchestrator
proposes and receives a local reconciliation decision.

## Coordination topology

Authority is a tree: every node has exactly one responsible parent. Work is a
DAG: tasks may depend on several tasks or artifacts. Communication is an
append-only event log plus addressed queues.

The root may observe any descendant, request information, and interrupt for
safety, but cannot change a leaf contract by bypassing its orchestrator. Only
an orchestrator commands its direct leaves. Every parent validates the
evidence of its direct children. A dedicated reviewer is optional escalation
for high-risk, conflicted, or insufficiently evidenced work.

A logical child hosted on another machine is not a remotely controlled process.
The parent instance sends a request to the target Hades Agent. The target makes
a local policy or human decision, creates and owns any local child, and returns
an opaque handle, status, and evidence.

## Identity and routing

Every envelope contains these identifiers:

- `project_id`: backend project UUID and mandatory security boundary;
- `sender_agent_id`: authenticated sending Hades Agent;
- `target_agent_id`: explicit receiving Hades Agent;
- `target_workspace_binding_id`: mandatory for source, Git, artifact, or other
  workspace-scoped information;
- `message_id`: globally unique delivery and deduplication key;
- `correlation_id`: request/decision/response conversation key;
- `causation_id`: immediately preceding message or event;
- `remote_task_id` and `remote_task_version` when the exchange derives from a
  project-manager card.

The backend rejects cross-project delivery. The receiving client independently
revalidates project, authenticated sender, target agent, binding ownership,
message schema, expiry, effect class, and requested capability. It never falls
back to another workspace when the requested binding is missing or unlinked.

A receiver is profile-scoped and multi-project, not current-directory scoped.
It subscribes to every backend project linked to the active profile and updates
subscriptions when bindings are added, removed, or relinked.

## Persephone transport

Persephone is the single transport. Existing message creation, persistent
inbox, polling, and local event storage are extended rather than replaced by a
second broker.

The canonical envelope schema is `hades.persephone.agent-message.v1` and has
these message types:

- `information_request`;
- `local_decision` with `accepted`, `rejected`, or `waiting_human_approval`;
- `information_response`;
- `status_query` and `status_response`;
- `cancel_request`, which is advisory and requires a local decision.

Delivery follows this durable state machine:

```text
outbox_pending -> sent -> delivered
  -> accepted | rejected | waiting_human_approval
  -> responded -> acknowledged
  -> expired | dead_letter
```

Delivery is at-least-once. `message_id` and correlation state make handlers
idempotent. Outbox insertion is committed before network send. Inbox insertion
is committed before dispatch. Acknowledgment occurs only after durable local
processing. Retry uses bounded exponential backoff with jitter; expired or
repeatedly invalid messages enter a visible dead-letter state.

The Hades service owns an SSE receiver per linked project. SSE reconnects from
a durable cursor. Polling with the same cursor is the fallback and is also used
by occasional CLI sessions. Hades must be running for realtime delivery;
offline agents retain server-side messages until delivery or expiry.

## Receiver and local dispatch

After persisting an event, the receiver resolves its target binding and passes
the envelope to a policy dispatcher.

`information_read` is the only auto-accepted capability class. It means
information retrieval through structured, allow-listed operations: bounded
file/source-slice reads, indexed search, symbol and code-graph lookup, Git
metadata inspection, artifact metadata, and shared project-memory lookup.

`information_read` excludes generic terminal access, tests, builds, formatters,
package managers, browser actions, configuration changes, Git mutations,
database mutations, cache-producing validation, and any tool with an uncertain
side effect. These requests produce `waiting_human_approval` or rejection.

An accepted information request runs in an isolated local task with only the
allow-listed read-only capabilities. A mutating request approved by a human
becomes a new locally owned task; the sender still gains no direct control.
Responses contain bounded summaries and evidence references, never raw
reasoning or unrestricted source payloads.

## Local DAG and blackboard

Each orchestrator owns a directory manifest for its leaves. A manifest exposes
only leaf ID, objective, read/write scope, interfaces, dependencies, produced
artifacts, status, and parent; it does not expose hidden reasoning or full
conversation history.

A leaf may address a sibling only when the runtime proves relevance through a
declared dependency, shared scope/interface, produced artifact, or blocker that
names the target task. Relevant requests route automatically. Ambiguous or
unrelated requests go to the orchestrator for a routing decision.

Blackboard events are append-only, addressed, cursor-based, and acknowledged.
Broadcast is reserved for subtree-wide blockers or interface changes. A write
sets `blackboard_dirty` for explicit recipients. Delivery is cooperative: the
recipient finishes its current tool, reads new events at the next safe model
boundary, and advances its cursor only after processing. Events never rewrite
past context or inject a synthetic user message mid-loop. If the target has
already completed, its orchestrator decides whether to create a new responder.

Cross-instance blackboard needs are converted into Persephone exchanges and
mediated by the owning Hades Agent. A leaf never opens a direct network channel
to a remote leaf.

## Evidence and token economy

Every child returns a versioned evidence packet: task-contract version, base
commit, result commit or patch reference, diff hash, covered files, verification
commands/results, bounded conclusion, dependencies, and residual risks.
Parents reuse valid packets instead of consuming child transcripts. Contract,
commit, covered-file, dependency, or verification-input changes invalidate the
packet. Full model reasoning and transcripts are neither cached as evidence nor
uploaded to the backend.

Adaptive capacity controls local and remote fan-out. The runtime combines
machine pressure, active processes/agents, provider rate state, remaining
tree/token budget, task complexity, and user ceilings. Outcomes are `allow`,
`queue`, `degrade_to_leaf`, or `replan`.

## Kanban projection

The remote project-manager Kanban is authoritative for mandates and human
status. The local Kanban owns the detailed execution DAG, leases, workspace
state, evidence, integration, and review. Sync is not a row-for-row replica.

Remote cards are imported by stable ID and version. Local decomposition remains
a derived projection. Agents publish append-only clarification questions,
decisions, bounded progress summaries, and verified completion proposals.
Only a backend or human-owned workflow changes the authoritative card.

## Scheduling and storage

The scheduler uses DAG readiness, dependency completion, priority, capacity,
and critical-path hints. It does not implement a B-tree. Database engines may
use ordinary indexes for project, recipient, state, cursor, correlation, and
expiry lookups; that storage detail is independent from agent topology.

## Security and privacy

Remote text is untrusted data, never system instruction. Structured schemas,
size limits, effect classification, capability allowlists, authenticated
project membership, target binding checks, expiry, rate limits, and audit logs
apply before any model sees a payload.

Messages and evidence must not include secrets, raw reasoning, transcripts,
unbounded source, private keys, cookies, tokens, or unrelated absolute paths.
Backend and client both enforce cross-project denial.

## Verification

Contract tests cover envelope validation, correlation, idempotency, state
transitions, cursors, acknowledgment, expiry, retry, and dead letters. Receiver
tests cover SSE delivery, reconnect, polling fallback, offline replay, dynamic
multi-project subscriptions, missing bindings, and duplicate events.

Policy tests cover valid information reads, generic-terminal denial, tests/build
denial, human approval, prompt-injection payloads, cross-project denial, and
target-agent mismatch. DAG tests cover relevance routing, blackboard wakeups,
safe-boundary delivery, parent-only authority, remote logical children, stale
mandate reconciliation, and evidence invalidation. End-to-end tests use two
agents, two workspaces in one project, and a second project to prove both
successful communication and isolation.

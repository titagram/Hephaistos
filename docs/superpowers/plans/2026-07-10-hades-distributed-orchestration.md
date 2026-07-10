# Hades Distributed Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Persephone into a durable, project-scoped, multi-agent request/decision/response channel with a multi-project receiver, read-only auto-acceptance, local DAG coordination, and remote-Kanban projection.

**Architecture:** Persephone remains the only transport. The Hades service persists outbox/inbox state, streams SSE with polling fallback, validates project/agent/workspace routing, and dispatches only allow-listed information reads automatically. Authority remains local; remote messages produce requests and evidence, never direct workspace mutation.

**Tech Stack:** Python 3.11+, httpx streaming, SQLite, asyncio gateway lifecycle, dataclasses, JSON/OpenAPI contracts, pytest.

## Global Constraints

- Backend shared memory and project-manager Kanban remain the source of truth.
- `project_id`, `target_agent_id`, and workspace-scoped `target_workspace_binding_id` are mandatory routing boundaries.
- A peer cannot directly mutate another instance's workspace, DAG, task contract, or child.
- Only `information_read` is auto-accepted; terminal, tests, builds, browser actions, Git mutations, database mutations, and uncertain tools are excluded.
- Hades service must be active for realtime delivery; polling remains the fallback.
- Delivery is at-least-once and every handler is idempotent.
- Persist before send/dispatch; acknowledge only after durable processing.
- Remote payload text is untrusted data and never becomes system instruction.
- No secrets, raw reasoning, transcripts, or unbounded source enter Persephone.
- Do not claim live backend compatibility until `/capabilities` advertises `persephone_agent_queue_v1`.

## File map

- Create `hermes_cli/hades_persephone_messages.py`: canonical envelope and validation.
- Create `hermes_cli/hades_persephone_store.py`: durable outbox/inbox/cursor state over the existing backend DB.
- Create `hermes_cli/hades_persephone_transport.py`: SSE parser, polling fallback, retry, and outbox sender.
- Create `hermes_cli/hades_persephone_receiver.py`: multi-project subscription manager and policy dispatcher.
- Create `hermes_cli/hades_information_worker.py`: isolated allow-listed information retrieval.
- Create `hermes_cli/hades_agent_coordination.py`: manifests, relevance checks, addressed blackboard events, and safe-boundary delivery.
- Modify `hermes_cli/hades_backend_client.py`: target-aware Persephone APIs and SSE stream.
- Modify `hermes_cli/hades_backend_db.py`: idempotent schema migration and approval records.
- Modify `hermes_cli/hades_backend_sync.py`: reuse receiver ingestion during polling.
- Modify `gateway/run.py`: start and stop the receiver with the Hades service.
- Modify `agent/agent_runtime_helpers.py` and `agent/tool_executor.py`: append coordination events to new tool results at safe boundaries.
- Modify `hermes_cli/kanban_swarm.py`, `hermes_cli/hades_coordination.py`, and `hermes_cli/hades_kanban_sync.py`: DAG/blackboard and remote projection.
- Modify `docs/hades/openapi-hades-v1.json`: exact backend capability and envelope contract.

---

### Task 1: Freeze the project-scoped Persephone envelope contract

**Files:**
- Create: `hermes_cli/hades_persephone_messages.py`
- Create: `tests/hermes_cli/test_hades_persephone_messages.py`
- Modify: `docs/hades/openapi-hades-v1.json`
- Modify: `docs/hades/operations.md`

**Interfaces:**
- Produces: `AgentMessageEnvelope`, `MessageType`, `EffectClass`, `DecisionStatus`, `parse_envelope()`, and `make_response()`.
- Backend capability gate: `persephone_agent_queue_v1`.

- [ ] **Step 1: Write failing envelope tests**

```python
VALID = {
    "schema": "hades.persephone.agent-message.v1",
    "message_id": "msg_1",
    "correlation_id": "corr_1",
    "causation_id": None,
    "project_id": "proj_1",
    "sender_agent_id": "agent_a",
    "target_agent_id": "agent_b",
    "target_workspace_binding_id": "wb_b",
    "message_type": "information_request",
    "effect": "information_read",
    "capability": "source_search",
    "remote_task_id": "task_1",
    "remote_task_version": "7",
    "expires_at": 2_000_000_000,
    "payload": {"query": "Where is AuthService defined?"},
}

def test_workspace_request_requires_target_binding():
    raw = {**VALID, "target_workspace_binding_id": None}
    with pytest.raises(ValueError, match="target_workspace_binding_id"):
        parse_envelope(raw)

def test_cross_project_context_is_rejected():
    envelope = parse_envelope(VALID)
    with pytest.raises(ValueError, match="project"):
        envelope.validate_receiver(project_id="proj_2", agent_id="agent_b")
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_messages.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement strict immutable envelope parsing**

```python
class MessageType(StrEnum):
    INFORMATION_REQUEST = "information_request"
    LOCAL_DECISION = "local_decision"
    INFORMATION_RESPONSE = "information_response"
    STATUS_QUERY = "status_query"
    STATUS_RESPONSE = "status_response"
    CANCEL_REQUEST = "cancel_request"

class EffectClass(StrEnum):
    INFORMATION_READ = "information_read"
    MUTATING = "mutating"

@dataclass(frozen=True)
class AgentMessageEnvelope:
    schema: str
    message_id: str
    correlation_id: str
    causation_id: str | None
    project_id: str
    sender_agent_id: str
    target_agent_id: str
    target_workspace_binding_id: str | None
    message_type: MessageType
    effect: EffectClass
    capability: str
    remote_task_id: str | None
    remote_task_version: str | None
    expires_at: int
    payload: dict[str, Any]
```

Reject unknown fields that could alter authority, payloads above the documented byte limit, expired messages, blank IDs, and workspace capabilities without a binding. Redact errors with existing backend helpers.

- [ ] **Step 4: Update OpenAPI as the backend handoff contract**

Add top-level `target_agent_id`, `target_workspace_binding_id`, `message_id`, `correlation_id`, and `payload` requirements to `PersephoneMessageRequest`; add target/cursor query parameters to inbox/events; document `/capabilities` flag `persephone_agent_queue_v1`. Do not enable the live receiver when the capability is absent.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_messages.py tests/test_docs_hades_mvp.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_persephone_messages.py tests/hermes_cli/test_hades_persephone_messages.py docs/hades/openapi-hades-v1.json docs/hades/operations.md
git commit -m "feat(hades): define project-scoped agent messages"
```

---

### Task 2: Add durable outbox, inbox state, cursors, and approvals

**Files:**
- Create: `hermes_cli/hades_persephone_store.py`
- Create: `tests/hermes_cli/test_hades_persephone_store.py`
- Modify: `hermes_cli/hades_backend_db.py`

**Interfaces:**
- Produces: `enqueue_outbox`, `claim_due_outbox`, `record_inbox`, `transition_message`, `record_cursor`, `pending_human_requests`, and `approve_request`.
- Consumes: `AgentMessageEnvelope` JSON and existing `write_txn()`/connection helpers.

- [ ] **Step 1: Write failing state-machine tests**

```python
def test_persist_before_send_and_deduplicate(tmp_db):
    envelope = parse_envelope(VALID)
    enqueue_outbox(tmp_db, envelope)
    enqueue_outbox(tmp_db, envelope)
    assert len(claim_due_outbox(tmp_db, now=100, limit=10)) == 1

def test_ack_requires_processed_response(tmp_db):
    record_inbox(tmp_db, parse_envelope(VALID))
    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, "msg_1", "acknowledged")
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_store.py -q`

Expected: store module import fails.

- [ ] **Step 3: Add idempotent schema migration**

Create tables with unique `message_id`, indexed `(project_id, target_agent_id, state)`, indexed `next_attempt_at`, and per-project/agent SSE cursor. Store envelope JSON, attempt count, last error, received/updated timestamps, and human decision fields. Preserve the legacy `inbox_events` table for non-agent Persephone events.

```sql
CREATE TABLE IF NOT EXISTS persephone_outbox (
  message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, target_agent_id TEXT NOT NULL,
  envelope TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL, last_error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
```

- [ ] **Step 4: Implement guarded transitions and recovery**

Use an explicit transition map. `claim_due_outbox` changes `outbox_pending|retry` to `sending` inside one transaction. A startup recovery function returns abandoned `sending` rows to `retry` without duplicating `message_id`.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_store.py tests/hermes_cli/test_hades_backend_db.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_persephone_store.py hermes_cli/hades_backend_db.py tests/hermes_cli/test_hades_persephone_store.py tests/hermes_cli/test_hades_backend_db.py
git commit -m "feat(hades): persist agent message queues"
```

---

### Task 3: Implement SSE reception, polling fallback, and outbox delivery

**Files:**
- Create: `hermes_cli/hades_persephone_transport.py`
- Create: `tests/hermes_cli/test_hades_persephone_transport.py`
- Modify: `hermes_cli/hades_backend_client.py`
- Modify: `tests/hermes_cli/test_hades_backend_client.py`

**Interfaces:**
- Produces: `iter_persephone_events()`, `poll_persephone_events()`, `send_due_messages()`, and `RetryPolicy`.
- Consumes: target-aware client methods and durable store functions.

- [ ] **Step 1: Write failing streaming and retry tests**

```python
def test_sse_parser_resumes_from_cursor(mock_transport):
    client = HadesBackendClient(BASE, TOKEN, transport=mock_transport)
    events = list(client.iter_persephone_events(project_id="p", target_agent_id="a", cursor="42", limit=2))
    assert [event["id"] for event in events] == ["43", "44"]

def test_transient_send_failure_schedules_retry(store, fake_client):
    fake_client.create_inbox_message.side_effect = HadesBackendError("timeout")
    send_due_messages(store, fake_client, now=100, retry=RetryPolicy(base=2, maximum=60))
    assert outbox_state(store, "msg_1") == "retry"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_transport.py tests/hermes_cli/test_hades_backend_client.py -q`

Expected: missing streaming/transport APIs.

- [ ] **Step 3: Add a bounded synchronous SSE iterator**

Use `httpx.Client.stream("GET", ...)`, accept only `text/event-stream`, parse `id:` and `data:` lines, stop after server EOF/limit/stop event, and never log authorization headers or raw rejected payloads. Pass `project_id`, `target_agent_id`, `cursor`, and `limit` as query parameters.

- [ ] **Step 4: Implement fallback and durable retry**

Fallback to `list_inbox()` using the same cursor when SSE is unavailable or malformed. Retry uses exponential backoff with jitter and a maximum attempt count; terminal 4xx schema/auth errors become `dead_letter` immediately.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_transport.py tests/hermes_cli/test_hades_backend_client.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_persephone_transport.py hermes_cli/hades_backend_client.py tests/hermes_cli/test_hades_persephone_transport.py tests/hermes_cli/test_hades_backend_client.py
git commit -m "feat(hades): stream Persephone agent queues"
```

---

### Task 4: Build the profile-scoped multi-project receiver and policy gate

**Files:**
- Create: `hermes_cli/hades_persephone_receiver.py`
- Create: `tests/hermes_cli/test_hades_persephone_receiver.py`
- Modify: `hermes_cli/hades_backend_sync.py`

**Interfaces:**
- Produces: `PersephoneReceiver.start()`, `.stop()`, `.refresh_bindings()`, `.ingest_event()`, and `classify_request()`.
- Consumes: all linked bindings in the active profile, transport/store APIs, and backend capability discovery.

- [ ] **Step 1: Write failing routing tests**

```python
def test_receiver_routes_project_b_while_started_from_project_a(receiver, bindings):
    receiver.refresh_bindings(bindings)
    result = receiver.ingest_event(event_for(project="b", agent="agent_b", binding="wb_b"))
    assert result == "accepted"

def test_missing_binding_never_falls_back(receiver):
    result = receiver.ingest_event(event_for(binding="missing"))
    assert result == "target_binding_unavailable"

def test_mutating_request_waits_for_human(receiver):
    result = receiver.ingest_event(event_for(effect="mutating", capability="run_tests"))
    assert result == "waiting_human_approval"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_receiver.py -q`

Expected: receiver module import fails.

- [ ] **Step 3: Implement subscriptions and independent revalidation**

Create one worker per distinct `(project_id, agent_id)` and route workspace operations by backend binding ID. Recheck receiver project/agent, binding ownership/status, effect, capability, expiry, and message size after persistence. Capability absence leaves legacy inbox polling intact but disables agent dispatch.

- [ ] **Step 4: Reuse ingestion from manual/background sync**

Replace agent-message handling inside `_sync_inbox` with `receiver.ingest_event()` while leaving legacy events in `inbox_events`. This gives CLI polling the same validation and idempotency as SSE.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_persephone_receiver.py tests/hermes_cli/test_hades_backend_sync_runner.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_persephone_receiver.py hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_persephone_receiver.py tests/hermes_cli/test_hades_backend_sync_runner.py
git commit -m "feat(hades): receive multi-project agent messages"
```

---

### Task 5: Execute auto-accepted requests with information-only capabilities

**Files:**
- Create: `hermes_cli/hades_information_worker.py`
- Create: `tests/hermes_cli/test_hades_information_worker.py`
- Modify: `hermes_cli/hades_persephone_receiver.py`

**Interfaces:**
- Produces: `InformationRequest`, `InformationResponse`, `validate_information_capability()`, and `run_information_request()`.
- Allowed capabilities: `source_slice`, `source_search`, `symbol_lookup`, `git_metadata`, `artifact_metadata`, `project_memory_search`.

- [ ] **Step 1: Write deny-by-default tests**

```python
@pytest.mark.parametrize("capability", ["terminal", "run_tests", "build", "browser", "git_commit", "database_query"])
def test_mutating_or_uncertain_capabilities_are_not_auto_accepted(capability):
    with pytest.raises(PolicyDenied):
        validate_information_capability(capability)

def test_information_worker_never_receives_terminal_toolset(fake_agent_factory):
    run_information_request(valid_source_search(), agent_factory=fake_agent_factory)
    assert "terminal" not in fake_agent_factory.kwargs["enabled_toolsets"]
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_information_worker.py -q`

Expected: worker module import fails.

- [ ] **Step 3: Implement structured handlers before model fallback**

Use direct local APIs for source slices/search/symbol/Git metadata whenever possible. If a bounded synthesis model is needed, construct an ephemeral agent with only service-gated read/search tools, the target workspace, a fixed information-only system contract, no delegation, no terminal, no memory writes, and the configured leaf route.

- [ ] **Step 4: Return bounded response envelopes**

Responses include answer summary, evidence references, truncation flag, and residual uncertainty. Run secret/path redaction before enqueueing. Mutating requests remain in the human approval store and never instantiate the worker.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_information_worker.py tests/hermes_cli/test_hades_persephone_receiver.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_information_worker.py hermes_cli/hades_persephone_receiver.py tests/hermes_cli/test_hades_information_worker.py tests/hermes_cli/test_hades_persephone_receiver.py
git commit -m "feat(hades): answer bounded peer information requests"
```

---

### Task 6: Run the receiver with the Hades service lifecycle

**Files:**
- Modify: `gateway/run.py`
- Create: `tests/gateway/test_hades_persephone_lifecycle.py`
- Modify: `hermes_cli/hades_backend_status.py`
- Test: `tests/hermes_cli/test_hades_backend_status.py`

**Interfaces:**
- Consumes: `PersephoneReceiver`.
- Produces: one receiver owned by `GatewayRunner`, clean shutdown, and operator status.

- [ ] **Step 1: Write failing lifecycle tests**

```python
async def test_gateway_starts_and_stops_receiver(runner, fake_receiver):
    await runner._start_hades_persephone_receiver()
    fake_receiver.start.assert_called_once()
    await runner._stop_hades_persephone_receiver()
    fake_receiver.stop.assert_called_once()

def test_status_reports_queue_health():
    payload = build_status(persephone={"state": "connected", "projects": 2, "dead_letters": 0})
    assert payload["persephone"]["state"] == "connected"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/gateway/test_hades_persephone_lifecycle.py tests/hermes_cli/test_hades_backend_status.py -q`

Expected: lifecycle methods/status are absent.

- [ ] **Step 3: Wire startup and shutdown**

Start after workspace bindings and backend clients are available. Keep a strong receiver reference and tracked task(s); do not use fire-and-forget tasks without shutdown ownership. Stop accepting messages during gateway drain, flush bounded outbox work, persist cursors, then stop subscriptions before closing shared HTTP/SQLite resources.

- [ ] **Step 4: Expose degraded states without crashing the gateway**

Report `disabled_capability`, `polling`, `connected`, `backoff`, or `failed`; include linked project count, unread, pending approval, retry, and dead-letter counts without payload contents.

Run: `.venv/bin/python -m pytest tests/gateway/test_hades_persephone_lifecycle.py tests/hermes_cli/test_hades_backend_status.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add gateway/run.py hermes_cli/hades_backend_status.py tests/gateway/test_hades_persephone_lifecycle.py tests/hermes_cli/test_hades_backend_status.py
git commit -m "feat(hades): run Persephone receiver in service"
```

---

### Task 7: Add manifests, relevance routing, and cooperative blackboard wakeups

**Files:**
- Create: `hermes_cli/hades_agent_coordination.py`
- Create: `tests/hermes_cli/test_hades_agent_coordination.py`
- Modify: `hermes_cli/kanban_swarm.py`
- Modify: `hermes_cli/hades_coordination.py`
- Modify: `agent/agent_runtime_helpers.py`
- Modify: `agent/tool_executor.py`
- Create: `tests/agent/test_tool_executor.py`
- Test: `tests/tools/test_delegate.py`

**Interfaces:**
- Produces: `LeafManifest`, `CoordinationEvent`, `is_relevant_request()`, `post_addressed_event()`, `drain_addressed_events()`, and `apply_pending_coordination_to_tool_results()`.
- Consumes: task contracts, parent/subagent IDs, blackboard storage, and the existing pending-steer tool-result pattern.

- [ ] **Step 1: Write failing relevance and authority tests**

```python
def test_unrelated_leaf_request_is_routed_to_orchestrator():
    source = manifest("a", write_scope=("a/**",))
    target = manifest("b", write_scope=("b/**",))
    assert is_relevant_request(source, target, artifact=None) is False

def test_root_observation_cannot_change_leaf_contract(tree):
    with pytest.raises(AuthorityError):
        tree.update_contract(actor=tree.root, target=tree.leaf, patch={"objective": "different"})
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_agent_coordination.py tests/tools/test_delegate.py -q`

Expected: coordination module import fails.

- [ ] **Step 3: Implement addressed append-only blackboard events**

Persist sender, explicit recipients, parent, event type, summary, evidence refs, sequence, created time, and acknowledgment cursor. Allow broadcast only for `blocker` and `interface_change`. Prove relevance through dependency, shared interface/scope, produced artifact, or named blocker; otherwise target the orchestrator.

- [ ] **Step 4: Deliver only at a safe tool boundary**

Follow `apply_pending_steer_to_tool_results`: after tool execution has produced new, not-yet-sent tool result messages, append a bounded `HADES_COORDINATION_EVENTS` section to the newest result and advance the cursor only after the updated message is persisted. Never edit older messages and never inject a synthetic user message.

```python
def apply_pending_coordination_to_tool_results(agent, messages: list, num_tool_msgs: int) -> None:
    events = drain_addressed_events(agent._subagent_id, limit=20)
    if not events or num_tool_msgs <= 0:
        return
    target = messages[-1]
    target["content"] = append_coordination_block(target["content"], events)
```

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_agent_coordination.py tests/tools/test_delegate.py tests/agent/test_tool_executor.py -q`

Expected: all tests pass and message-role alternation remains valid.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_agent_coordination.py hermes_cli/kanban_swarm.py hermes_cli/hades_coordination.py agent/agent_runtime_helpers.py agent/tool_executor.py tests/hermes_cli/test_hades_agent_coordination.py tests/tools/test_delegate.py tests/agent/test_tool_executor.py
git commit -m "feat(hades): coordinate delegated agent DAGs"
```

---

### Task 8: Project remote Kanban state without making it a local-row replica

**Files:**
- Modify: `hermes_cli/hades_kanban_sync.py`
- Modify: `hermes_cli/kanban_portfolio.py`
- Modify: `hermes_cli/hades_coordination.py`
- Create: `tests/hermes_cli/test_hades_distributed_org_run.py`
- Modify: `docs/hades/org-run-operations.md`

**Interfaces:**
- Produces: stable remote mandate version mapping, stale-projection detection, clarification/decision/result proposals.
- Consumes: existing lease lifecycle, local OrgRun topology, Persephone envelopes, and evidence packets.

- [ ] **Step 1: Write failing projection tests**

```python
def test_remote_version_change_pauses_derived_subtree(org_run):
    org_run.import_mandate(remote_id="r1", version="1")
    result = org_run.reconcile_mandate(remote_id="r1", version="2")
    assert result.status == "stale"
    assert all(node.status == "blocked" for node in result.affected_nodes)

def test_agent_publishes_proposal_without_rewriting_remote_card(fake_client, org_run):
    org_run.publish_verified_result(fake_client, remote_id="r1", evidence=packet())
    fake_client.update_project_manager_card.assert_not_called()
    fake_client.create_inbox_message.assert_called_once()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_distributed_org_run.py -q`

Expected: mandate version/reconciliation APIs are absent.

- [ ] **Step 3: Persist stable remote ID/version and stale state**

Store remote task ID/version in topology and evidence. On version mismatch, block only dependent nodes, post a typed decision proposal, invalidate affected evidence, and require local reconciliation before resuming.

- [ ] **Step 4: Publish append-only bounded records**

Clarification questions, decisions, progress summaries, and verified completion proposals use Persephone; they never update the authoritative card. Keep claim/heartbeat/final-result lifecycle bounded and idempotent.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_distributed_org_run.py tests/hermes_cli/test_hades_kanban_sync.py tests/hermes_cli/test_kanban_portfolio.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_kanban_sync.py hermes_cli/kanban_portfolio.py hermes_cli/hades_coordination.py tests/hermes_cli/test_hades_distributed_org_run.py docs/hades/org-run-operations.md
git commit -m "feat(hades): project distributed OrgRuns safely"
```

---

### Task 9: Full verification and live Hades skill installation test

**Files:**
- Verify only; modify production files only if a failing test identifies a scoped defect.

**Interfaces:**
- Consumes: both implementation plans and the installed `hades` executable.
- Produces: recorded test output, installed bundled skill, and a real interactive skill invocation.

- [ ] **Step 1: Run the complete focused suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/tools/test_delegation_routing.py \
  tests/tools/test_delegation_contract.py \
  tests/tools/test_delegation_capacity.py \
  tests/tools/test_delegation_evidence.py \
  tests/tools/test_delegate.py \
  tests/hermes_cli/test_delegation_onboarding.py \
  tests/hermes_cli/test_hades_delegation_cmd.py \
  tests/hermes_cli/test_hades_persephone_messages.py \
  tests/hermes_cli/test_hades_persephone_store.py \
  tests/hermes_cli/test_hades_persephone_transport.py \
  tests/hermes_cli/test_hades_persephone_receiver.py \
  tests/hermes_cli/test_hades_information_worker.py \
  tests/hermes_cli/test_hades_agent_coordination.py \
  tests/hermes_cli/test_hades_distributed_org_run.py \
  tests/gateway/test_hades_persephone_lifecycle.py
```

Expected: all tests pass with no warnings attributable to these features.

- [ ] **Step 2: Verify the installed command and backend capability**

Run:

```bash
command -v hades
hades version
hades backend status --json
hades delegation --help
```

Expected: installed executable resolves; delegation help lists `setup` and `configure`; backend status contains no secrets. If `persephone_agent_queue_v1` is absent, report live distributed messaging as capability-gated rather than claiming it works.

- [ ] **Step 3: Sync the bundled skill into the active profile**

Run from the checkout:

```bash
.venv/bin/python tools/skills_sync.py
test -f "$HOME/.hermes/skills/software-development/hierarchical-development/SKILL.md"
```

Expected: sync succeeds and the installed `SKILL.md` exists. If the active profile opted out, run `hades skills opt-in --sync` only after confirming the active profile.

- [ ] **Step 4: Open Hades in a real PTY and reload the skill**

Run: `hades --cli` in an interactive terminal, then enter:

```text
/reload-skills
/hierarchical-development
```

Expected: Hades reports the skill as installed/loaded and does not report an unknown command.

- [ ] **Step 5: Exercise the skill without mutating the workspace**

In the same Hades session enter:

```text
Use hierarchical-development. Do not modify files or contact the backend. Explain what you must do when delegation routing is missing, when it already exists, and what contract an orchestrator requires.
```

Expected: the response names `hades delegation setup`, preserves existing routing, uses `hades delegation configure` only for requested changes, requires a structured orchestrator task contract, and assigns normal review to the parent.

- [ ] **Step 6: Exit cleanly and commit only scoped verification fixes**

Enter `/exit`. If no fixes were needed, do not create an empty commit. If a scoped defect was fixed, rerun the affected test and commit only those files with a message describing the defect.

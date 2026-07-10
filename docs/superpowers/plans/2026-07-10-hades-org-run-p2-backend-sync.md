# Hades Optional Backend Kanban Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optionally mirror mapped backend Kanban work into the local Kanban with late claim, bounded push, cancellation and restart-safe outbox.

**Architecture:** Keep remote mapping, lease and outbox in profile-scoped `hades_backend.db`; keep execution DAG in the existing Kanban DB. `off` performs no remote sync and locally freezes residual mapped cards, `pull_only` materializes non-dispatchable triage cards, and `mirror` supplies a dispatch-admission callback that claims remotely before spawn.

**Tech Stack:** Python, SQLite, httpx MockTransport, existing Hades plugin client, Kanban admission seam from P0, OrgRun materializer from P1, pytest.

## Global Constraints

- P0 and P1 release gates must be green.
- Existing `direct` worker behavior remains unchanged.
- Only explicitly mapped remote items produce outbound calls.
- Lease tokens stay in `hades_backend.db` and never enter Kanban payloads.
- `off` and `pull_only` must be side-effect-free outbound.
- Do not enable `mirror` unless backend capability `kanban_mirror_v1` is true.
- Endpoint absence is a stop condition, not permission to invent a fallback.

---

### Task P2-T01: Persist remote mapping and semantic outbox

**Files:**
- Modify: `hermes_cli/hades_backend_db.py` schema and accessors.
- Create: `tests/hermes_cli/test_hades_kanban_bridge_db.py`.

**Interfaces:**
- Produces `KanbanExternalTaskRef`, `KanbanSyncOutboxItem`.
- Produces mapping CRUD and outbox enqueue/list/mark-sent/mark-failed functions.

- [ ] **Step 1: Write schema round-trip tests**

Insert the same mapping twice and assert one row. Enqueue the same
`idempotency_key` twice and assert one pending row. Reopen the DB and prove
both survive restart. Use:

```python
MAPPING = {
    "provider": "hades_backend",
    "project_id": "proj_1",
    "work_item_id": "awi_1",
    "workspace_binding_id": "wb_1",
    "board": "default",
    "org_run_id": "org_1",
    "remote_task_id": "HD-101",
    "local_anchor_task_id": "t_anchor",
    "local_execution_task_id": "t_execute",
    "local_completion_task_id": "t_complete",
    "remote_revision": "rev_1",
    "sync_state": "mirrored",
    "last_remote_cursor": "cursor_1",
}
```

- [ ] **Step 2: Verify red state**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_bridge_db.py -q
```

Expected: failure naming the new mapping API.

- [ ] **Step 3: Add exact tables to `SCHEMA_SQL`**

```sql
CREATE TABLE IF NOT EXISTS kanban_external_task_refs (
    provider TEXT NOT NULL,
    project_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL,
    board TEXT NOT NULL,
    org_run_id TEXT NOT NULL,
    remote_task_id TEXT NOT NULL,
    local_anchor_task_id TEXT NOT NULL,
    local_execution_task_id TEXT NOT NULL,
    local_completion_task_id TEXT NOT NULL,
    remote_revision TEXT,
    sync_state TEXT NOT NULL,
    last_remote_cursor TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (provider, project_id, work_item_id, workspace_binding_id, board),
    UNIQUE (board, local_execution_task_id)
);

CREATE TABLE IF NOT EXISTS kanban_sync_outbox (
    idempotency_key TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kanban_sync_outbox_pending
    ON kanban_sync_outbox(status, created_at);
```

- [ ] **Step 4: Add dataclasses and accessors**

Allowed outbox statuses are exactly `pending`, `sent`, `failed`. Upsert updates
remote-owned fields without changing `created_at`. Failure increments attempts
and stores a redacted error capped at 500 characters.

- [ ] **Step 5: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_bridge_db.py tests/hermes_cli/test_hades_backend_db.py -q
git add hermes_cli/hades_backend_db.py tests/hermes_cli/test_hades_kanban_bridge_db.py
git commit -m "feat(hades): persist Kanban mappings and sync outbox"
```

Expected before commit: exit code `0`.

---

### Task P2-T02: Freeze backend capability and lifecycle client contract

**Files:**
- Modify: `docs/hades/openapi-hades-v1.json` only after backend deployment.
- Modify: `hermes_cli/hades_plugin_work_items_client.py`.
- Modify: `tests/hermes_cli/test_hades_plugin_work_items_client.py`.

**Interfaces:**
- Requires `capabilities.kanban_mirror_v1=true` from `auth/check`.
- Produces `progress_agent_work_item`, `block_agent_work_item`,
  `resume_agent_work_item`, `release_agent_work_item`.

- [ ] **Step 1: Verify backend capability before editing client code**

Required auth response fragment:

```json
{"capabilities":{"kanban_mirror_v1":true}}
```

If absent, stop with `backend_capability_missing`. `pull_only` can continue;
mirror tasks remain blocked.

- [ ] **Step 2: Add MockTransport tests for exact POST suffixes**

```text
/agent-work-items/awi_1/progress
/agent-work-items/awi_1/block
/agent-work-items/awi_1/resume
/agent-work-items/awi_1/release
```

Bodies contain `lease_token` plus respectively `progress`, `reason/evidence`,
nothing else, and `reason`. Existing client code adds `protocol_version=v1`.

- [ ] **Step 3: Implement thin methods and verify**

Each method calls `_request` exactly once. Retry belongs to the outbox, not the
client.

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_plugin_work_items_client.py -q
git add hermes_cli/hades_plugin_work_items_client.py tests/hermes_cli/test_hades_plugin_work_items_client.py
git commit -m "feat(hades): add mirrored work item lifecycle client"
```

Stage the OpenAPI file only if it changed from a verified deployed contract.

---

### Task P2-T03: Add strict sync configuration

**Files:**
- Create: `hermes_cli/hades_kanban_sync_config.py`.
- Create: `tests/hermes_cli/test_hades_kanban_sync_config.py`.
- Modify: `hermes_cli/hades_plugin_worker.py` at `run_plugin_worker_once`.
- Modify: `tests/hermes_cli/test_hades_plugin_worker.py`.
- Modify: `website/docs/user-guide/configuration.md` at backend config.

**Interfaces:**
- Produces `HadesKanbanSyncConfig` and `load_hades_kanban_sync_config`.

- [ ] **Step 1: Write table-driven config tests**

Defaults are `off`, `30`, `60`, `120`, `20`, and three `None` assignees.
Reject unknown modes, boolean integers, intervals below `5`, batch outside
`1..100`, and `mirror` without all three assignees.

- [ ] **Step 2: Implement exact public dataclass**

```python
@dataclass(frozen=True)
class HadesKanbanSyncConfig:
    mode: str = "off"
    pull_interval_seconds: int = 30
    push_interval_seconds: int = 60
    offline_grace_seconds: int = 120
    max_batch_size: int = 20
    execution_assignee: str | None = None
    reviewer_assignee: str | None = None
    integration_assignee: str | None = None
```

Read only `config.yaml`; never read environment variables.

- [ ] **Step 3: Make direct and mirror mutually exclusive**

At the start of `run_plugin_worker_once`, load sync config. If mode is
`mirror`, return exit `2` before constructing a client, listing or claiming;
the structured code is `mirror_mode_owns_workspace`. In `off` and `pull_only`,
the direct worker behavior remains unchanged. Add spy tests for all three
modes.

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_sync_config.py tests/hermes_cli/test_hades_plugin_worker.py -q
git add hermes_cli/hades_kanban_sync_config.py hermes_cli/hades_plugin_worker.py tests/hermes_cli/test_hades_kanban_sync_config.py tests/hermes_cli/test_hades_plugin_worker.py website/docs/user-guide/configuration.md
git commit -m "feat(hades): add optional Kanban sync config"
```

---

### Task P2-T04: Pull and materialize work items idempotently

**Files:**
- Create: `hermes_cli/hades_kanban_bridge.py`.
- Create: `tests/hermes_cli/test_hades_kanban_bridge.py`.

**Interfaces:**
- Produces `SyncAction`, `SyncPreview`, `HadesKanbanBridge.pull`.
- Consumes `create_org_run(..., activate=False)`.

- [ ] **Step 1: Write pull tests with a fake client**

Assert: two creates on first pull; zero creates on second; execution cards in
`triage`; no lifecycle calls; invalid/non-ready contract rejected without a
card; title never used as identity.

- [ ] **Step 2: Add result types**

```python
@dataclass(frozen=True)
class SyncAction:
    action: str
    work_item_id: str
    local_task_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class SyncPreview:
    mode: str
    cursor: str | None
    actions: tuple[SyncAction, ...]
```

- [ ] **Step 3: Implement pull in this fixed sequence**

1. In `off`, return before client/DB access.
2. List at most `max_batch_size`.
3. Validate with existing `kanban_task_contract_status`.
4. Group by project, repository and base commit.
   Derive `org_run_id` as `org_` plus the first 16 hex characters of SHA-256
   over project ID, repository ID, local base commit and sorted work-item IDs.
   Never use response order or task title.
5. Build portfolio using configured assignees. In `pull_only`, use the reserved
   inert assignee `hades-unassigned` because cards stay triage. When mode later
   changes to `mirror`, reassign execution/review/integration cards to the three
   verified configured profiles before promotion.
6. Call `create_org_run(..., activate=False)`.
7. Persist mapping only after topology exists.
8. Do not persist lease token in mapping.

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_bridge.py tests/hermes_cli/test_hades_kanban_bridge_db.py tests/hermes_cli/test_hades_kanban_task_contract.py -q
git add hermes_cli/hades_kanban_bridge.py tests/hermes_cli/test_hades_kanban_bridge.py
git commit -m "feat(hades): mirror backend tasks into local Kanban"
```

---

### Task P2-T05: Add deterministic dry-run CLI

**Files:**
- Modify: `hermes_cli/hades_backend_cmd.py` in `build_backend_parser` and
  `hades_backend_command`.
- Create: `hermes_cli/hades_kanban_sync_cmd.py`.
- Create: `tests/hermes_cli/test_hades_kanban_sync_cli.py`.

**Interfaces:**
- Produces `hades backend kanban-sync --pull --push --dry-run --json`.

- [ ] **Step 1: Test exact response shape**

```json
{
  "status": "ok",
  "mode": "pull_only",
  "dry_run": true,
  "creates": [],
  "updates": [],
  "cancellations": [],
  "conflicts": [],
  "outbound": []
}
```

Compare all DB rows before/after dry-run. `off` is a no-op exit `0`.
`pull_only --push` exits `2` with `push_disabled_in_pull_only`.

- [ ] **Step 2: Implement command semantics**

When neither `--pull` nor `--push` is supplied, select both. Never mutate in
`--dry-run`. Render JSON in sorted key order for stable tests.

- [ ] **Step 3: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_sync_cli.py -q
git add hermes_cli/hades_backend_cmd.py hermes_cli/hades_kanban_sync_cmd.py tests/hermes_cli/test_hades_kanban_sync_cli.py
git commit -m "feat(hades): add Kanban sync dry-run command"
```

---

### Task P2-T06: Claim remotely through dispatch admission

**Files:**
- Modify: `hermes_cli/hades_kanban_bridge.py`.
- Modify: `tests/hermes_cli/test_hades_kanban_bridge.py`.
- Modify: `gateway/kanban_watchers.py`.
- Create: `tests/gateway/test_hades_kanban_sync_watcher.py`.

**Interfaces:**
- Produces `HadesKanbanBridge.admit(task) -> DispatchAdmission`.

- [ ] **Step 1: Test all admission branches**

- unmapped local task: allow, zero network;
- mapped task in off: supersede with `backend_sync_disabled`, zero network;
- mapped pull-only task: supersede with `pull_only task is not executable`;
- backend unavailable/5xx: defer;
- structured `already_claimed`: supersede;
- successful claim: token only in existing plugin work-item row, then allow.

- [ ] **Step 2: Implement mapping and error policy**

Lookup by `(board, local_execution_task_id)`. Call remote claim once. Do not
retry inside admission. Auth/contract errors become safe defer with a redacted
reason. Claim conflict never increments local failure count.

- [ ] **Step 3: Wire gateway only in mirror mode**

Always compose a local mapping-mode policy. In `off` and `pull_only` it uses
only local DB state and never instantiates a network client. Add remote
`bridge.admit` only in `mirror`.

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_bridge.py tests/gateway/test_hades_kanban_sync_watcher.py tests/hermes_cli/test_kanban_dispatch_admission.py -q
git add hermes_cli/hades_kanban_bridge.py gateway/kanban_watchers.py tests/hermes_cli/test_hades_kanban_bridge.py tests/gateway/test_hades_kanban_sync_watcher.py
git commit -m "feat(hades): claim mirrored work at Kanban dispatch"
```

---

### Task P2-T07: Add outbox, heartbeat, revision and cancellation handling

**Files:**
- Modify: `hermes_cli/hades_kanban_bridge.py`.
- Modify: `tests/hermes_cli/test_hades_kanban_bridge.py`.
- Modify: `gateway/kanban_watchers.py`.

**Interfaces:**
- Produces `collect_local_events`, `flush_outbox`,
  `heartbeat_claimed_subtrees`, `apply_remote_updates`.

- [ ] **Step 1: Add restart and redaction tests**

Cover one-time enqueue across repeated scans, send after restart, retry after
5xx, terminal fail after five attempts, stale lease refusal, revision drift,
whole-subtree cancellation and recursive forbidden-payload scan.

- [ ] **Step 2: Use this exact idempotency key**

```text
{work_item_id}:{remote_revision}:{event_type}:{local_run_id_or_zero}
```

Outbound events are exactly `started`, `progress`, `blocked`, `completed`,
`failed_terminal`, `cancelled`, `residual_risk`.

- [ ] **Step 3: Implement runtime rules**

- throttle progress by `push_interval_seconds`;
- heartbeat only valid mapped leases;
- after outage grace, block subtree as `transient`, never fail;
- trigger remote completion only from mapped completion/publish node, which is
  downstream of global integration and org review;
- revision change before execution updates remote-owned fields;
- revision change during execution safe-stops and requires replan;
- cancellation stops execution, review and completion descendants.

- [ ] **Step 4: Run full P2 gate and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_kanban_bridge_db.py tests/hermes_cli/test_hades_kanban_bridge.py tests/hermes_cli/test_hades_plugin_work_items_client.py tests/hermes_cli/test_hades_plugin_worker.py tests/hermes_cli/test_hades_kanban_sync_cli.py tests/gateway/test_hades_kanban_sync_watcher.py -q
git diff --check
git add hermes_cli/hades_kanban_bridge.py gateway/kanban_watchers.py tests/hermes_cli/test_hades_kanban_bridge.py tests/gateway/test_hades_kanban_sync_watcher.py
git commit -m "feat(hades): sync bounded Kanban lifecycle through outbox"
```

## P2 Completion Checklist

- [ ] `off` creates no client, thread, timer or network call.
- [ ] `pull_only` creates triage cards and no outbound lifecycle.
- [ ] `mirror` refuses missing capability or assignee profile.
- [ ] Direct worker tests remain green.
- [ ] Only mapped tasks produce outbound events.
- [ ] Claim conflict consumes no failure budget.
- [ ] Lease token is absent from Kanban DB and command JSON.
- [ ] Outbox survives restart and deduplicates completion.
- [ ] Completion comes from completion/publish node downstream of org review,
  never anchor, execution fan-out or per-task integration-ready node.
- [ ] Cancellation reaches the whole local subtree.
- [ ] `git status --short` is empty.

# Agentic-Kanban and Local OrgRun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agentic-Kanban a backend-independent development runtime where hierarchical-development materializes versioned local plans through model-free OrgRun, executes them through real Hermes profiles, and derives a local per-task/run logbook.

**Architecture:** Preserve the existing SQLite Kanban kernel and dispatcher as the only scheduler. Add focused plan, OrgRun-store, materializer, amendment, and report-projection modules around it; disconnect every Agentic-Kanban call path from the dormant backend adapter without deleting legacy audit data. Expose the result through the existing Hades CLI and bundled Kanban dashboard.

**Tech Stack:** Python 3.11+, SQLite, argparse, FastAPI/Pydantic, bundled React-compatible dashboard JavaScript, pytest, Vitest-free dashboard bundle assertions.

## Global Constraints

- Agentic-Kanban must never construct a backend client, import remote cards, acquire remote leases, or publish remote lifecycle events.
- `hades kanban sync` and `hades org sync` must return the typed non-retryable code `agentic_kanban_has_no_remote_sync`.
- Existing `kanban_remote_links`, `kanban_sync_outbox`, and sync-state rows remain readable legacy data and are not deleted.
- OrgRun is deterministic and model-free; plan payloads contain logical roles, never provider/model/credentials.
- The only logical roles are `orchestrator`, `leaf`, and `reviewer`; no silent fallback to `default`.
- Worker launch uses the existing `hades`/`hermes`/module resolution chain and must never invoke `hermes-agent` or `hermes-review-engine`.
- OrgRun-managed cards bypass native auto-decompose and never invoke `kanban swarm`.
- Prompt, provider, model, and toolset remain byte-stable during a running conversation.
- New capability stays at the CLI/skill/plugin edge; do not add a core model tool.
- Migrations are additive and idempotent; materialization and amendments are single SQLite transactions.
- Local report JSON is canonical; Markdown is deterministically rendered from it.
- During subagent-driven execution, prefer `gpt-5.6-terra` for bounded
  documentation, dashboard, and test-only tasks; reserve `gpt-5.6-sol` for
  persistence, materialization, amendment, and final review gates.
- Do not modify or stage `README_MEMORY_COMMANDS.ms` or unrelated user changes.

---

### Task 1: Disconnect Agentic-Kanban from backend sync and legacy executors

**Files:**
- Modify: `hermes_cli/kanban_db.py`
- Modify: `hermes_cli/kanban.py`
- Modify: `gateway/kanban_watchers.py`
- Modify: `plugins/kanban/dashboard/plugin_api.py`
- Test: `tests/hermes_cli/test_kanban_cli.py`
- Test: `tests/hermes_cli/test_kanban_core_functionality.py`
- Test: `tests/hermes_cli/test_kanban_remote_reliability.py`
- Test: `tests/gateway/test_kanban_auto_decompose_live.py`
- Test: `tests/plugins/test_kanban_dashboard_plugin.py`

**Interfaces:**
- Consumes: existing `kanban_db.dispatch_once(conn, *, admission_fn=None, ...)`.
- Produces: `agentic_kanban_sync_disabled() -> dict[str, object]`; all ordinary completion, block, heartbeat, dispatcher, watch, serve, and dashboard paths are network-free.

- [ ] **Step 1: Write failing CLI and kernel isolation tests**

Add focused assertions:

```python
def test_kanban_sync_is_typed_non_retryable_local_boundary(kanban_home, capsys):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    kc.build_parser(sub)
    args = parser.parse_args(["kanban", "sync", "--json"])

    assert kc.kanban_command(args) == 2
    assert json.loads(capsys.readouterr().out) == {
        "state": "unsupported",
        "code": "agentic_kanban_has_no_remote_sync",
        "retryable": False,
    }


def test_local_completion_ignores_legacy_remote_link(kanban_home, monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.hades_kanban_sync.deliver_remote_terminal_for_task",
        lambda *_a, **_k: pytest.fail("remote delivery called"),
    )
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="local execution", assignee="leaf")
        kb.upsert_remote_link(
            conn, task_id=task_id, project_id="legacy-project",
            workspace_binding_id="legacy-binding",
            remote_work_item_id="legacy-work",
        )
        assert kb.complete_task(conn, task_id, summary="done")
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_sync_outbox WHERE task_id=?", (task_id,)
        ).fetchone()[0] == 0
```

Replace gateway/dashboard tests that expect optional sync with tests whose
backend entry points raise if touched.

- [ ] **Step 2: Run the isolation tests and verify current behavior fails**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_kanban_cli.py \
  tests/hermes_cli/test_kanban_core_functionality.py \
  tests/gateway/test_kanban_auto_decompose_live.py \
  tests/plugins/test_kanban_dashboard_plugin.py
```

Expected: failures show `_cmd_sync`, completion hooks, gateway ticks, and
dashboard reads/dispatch still reach sync code.

- [ ] **Step 3: Make Kanban lifecycle transitions purely local**

In `hermes_cli/kanban_db.py`, remove calls to
`_enqueue_remote_terminal_result_in_txn`,
`_fire_remote_terminal_delivery_hook`, and `_fire_remote_heartbeat_hook` from
task completion, block, failure-budget, reclaim, and heartbeat paths. Keep the
legacy helper/table functions importable for audit tests, but add no new caller.

The local transition must end like:

```python
_fire_kanban_lifecycle_hook(
    "kanban_task_completed",
    task_id,
    board=get_current_board(),
    assignee=_done_task.assignee if _done_task else None,
    run_id=run_id,
    summary=(summary if summary is not None else result),
)
return True
```

- [ ] **Step 4: Make CLI dispatch, daemon, and sync local-only**

In `hermes_cli/kanban.py`, implement:

```python
def agentic_kanban_sync_disabled() -> dict[str, object]:
    return {
        "state": "unsupported",
        "code": "agentic_kanban_has_no_remote_sync",
        "retryable": False,
    }


def _cmd_sync(args: argparse.Namespace) -> int:
    payload = agentic_kanban_sync_disabled()
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "Agentic-Kanban is local and does not synchronize remote cards.",
            file=sys.stderr,
        )
    return 2
```

Call `kb.dispatch_once(...)` without `_remote_dispatch_admission`; call
`kb.run_daemon(...)` without `sync_fn` or `admission_fn`. Delete the now-dead
private `_remote_dispatch_admission`.

- [ ] **Step 5: Remove backend composition from gateway and dashboard**

In `gateway/kanban_watchers.py`, remove lazy imports of
`kanban_backend`/`make_remote_admission` and reduce the tick to:

```python
return _kb.dispatch_once(
    conn,
    board=slug,
    max_spawn=max_spawn,
    max_in_progress=max_in_progress,
    failure_limit=failure_limit,
    stale_timeout_seconds=stale_timeout_seconds,
    default_assignee=default_assignee,
    max_in_progress_per_profile=max_in_progress_per_profile,
)
```

In `plugins/kanban/dashboard/plugin_api.py`, remove `_board_sync_payload`,
`_start_dashboard_sync`, sync/remote fields from `/board`, and backend admission
from `/dispatch`. A legacy remote link must not change the local card DTO.

- [ ] **Step 6: Prove worker argv never contains legacy process names**

Extend `tests/hermes_cli/test_kanban_db.py`:

```python
def test_worker_launcher_never_resolves_legacy_process_names(monkeypatch):
    seen = []
    monkeypatch.delenv("HERMES_BIN", raising=False)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: seen.append(name) or ("/bin/hades" if name == "hades" else None),
    )
    assert kb._resolve_hermes_argv() == ["/bin/hades"]
    assert seen == ["hades"]
    assert "hermes-agent" not in seen
    assert "hermes-review-engine" not in seen
```

- [ ] **Step 7: Run the focused regression suite**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_kanban_cli.py \
  tests/hermes_cli/test_kanban_core_functionality.py \
  tests/hermes_cli/test_kanban_remote_reliability.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/gateway/test_kanban_auto_decompose_live.py \
  tests/plugins/test_kanban_dashboard_plugin.py
```

Expected: PASS; no monkeypatched backend sentinel is called.

- [ ] **Step 8: Commit the local-only boundary**

```bash
git add hermes_cli/kanban_db.py hermes_cli/kanban.py \
  gateway/kanban_watchers.py plugins/kanban/dashboard/plugin_api.py \
  tests/hermes_cli/test_kanban_cli.py \
  tests/hermes_cli/test_kanban_core_functionality.py \
  tests/hermes_cli/test_kanban_remote_reliability.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/gateway/test_kanban_auto_decompose_live.py \
  tests/plugins/test_kanban_dashboard_plugin.py
git commit -m "fix(kanban): isolate Agentic-Kanban from backend sync"
```

### Task 2: Add the local implementation-plan contract

**Files:**
- Create: `hermes_cli/implementation_plan.py`
- Create: `tests/hermes_cli/test_implementation_plan.py`

**Interfaces:**
- Consumes: `tools.delegation_routing.ALLOWED_ROLES`.
- Produces:
  - `ImplementationTask`
  - `ImplementationPlan`
  - `PlanValidation`
  - `parse_implementation_plan(payload: Mapping[str, Any]) -> ImplementationPlan`
  - `validate_implementation_plan(plan: ImplementationPlan, *, repository: Path, profile_exists: Callable[[str], bool], role_route_exists: Callable[[str], bool]) -> PlanValidation`
  - `canonical_plan_json(plan: ImplementationPlan) -> str`

- [ ] **Step 1: Write failing parser, hash, DAG, scope, Git, and profile tests**

Use this fixture:

```python
def valid_payload() -> dict:
    return {
        "schema": "hades.implementation-plan.v1",
        "run_id": "local-run-001",
        "objective": "Ship an offline OrgRun",
        "base_commit": "a" * 40,
        "acceptance_criteria": ["All focused tests pass"],
        "independent_review": False,
        "tasks": [{
            "id": "runtime",
            "title": "Disconnect runtime sync",
            "role": "leaf",
            "risk": "high",
            "write_scope": ["hermes_cli/kanban.py"],
            "depends_on": [],
            "acceptance_criteria": ["No backend client is constructed"],
            "verification": ["pytest tests/hermes_cli/test_kanban_cli.py"],
            "independent_review": True,
        }],
    }
```

Tests must cover duplicate IDs, unknown/self/cyclic dependencies, `..` and
absolute scopes, empty acceptance/verification, unsupported roles, missing
profiles, missing delegation role routes, missing base commit, deterministic
hash, and deterministic serialization of exact-path overlaps.

- [ ] **Step 2: Run tests and verify the module is absent**

Run:

```bash
.venv/bin/python -m pytest -q tests/hermes_cli/test_implementation_plan.py
```

Expected: import failure for `hermes_cli.implementation_plan`.

- [ ] **Step 3: Implement immutable contract dataclasses and parsing**

Start with:

```python
IMPLEMENTATION_PLAN_SCHEMA = "hades.implementation-plan.v1"
RISK_LEVELS = frozenset({"low", "medium", "high"})


@dataclass(frozen=True)
class ImplementationTask:
    id: str
    title: str
    role: str
    risk: str
    write_scope: tuple[str, ...]
    depends_on: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    verification: tuple[str, ...]
    independent_review: bool


@dataclass(frozen=True)
class ImplementationPlan:
    schema: str
    run_id: str
    objective: str
    base_commit: str
    acceptance_criteria: tuple[str, ...]
    tasks: tuple[ImplementationTask, ...]
    independent_review: bool = False
    origin: str = "local"


@dataclass(frozen=True)
class PlanValidation:
    plan_hash: str
    ordered_dependencies: dict[str, tuple[str, ...]]
    conflicts: tuple[tuple[str, str, str], ...]
    resolved_profiles: dict[str, str]
    routed_roles: tuple[str, ...]
```

Parsing must reject provider/model/credential keys recursively.

- [ ] **Step 4: Implement deterministic validation**

Validate the commit without a shell:

```python
subprocess.run(
    ["git", "-C", str(repository), "cat-file", "-e", f"{plan.base_commit}^{{commit}}"],
    check=True, capture_output=True, text=True, timeout=10,
)
```

Resolve all three runtime roles with `profile_exists(role)` because
materialization always creates orchestrator/integration nodes and may create
review nodes. Require `role_route_exists(role)` for all three so a logical role
never falls through to the Hermes profile's default model. Serialize exact
write-scope overlaps by sorted task ID; do not infer prefix/glob overlap in v1.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest -q tests/hermes_cli/test_implementation_plan.py
```

Expected: PASS.

- [ ] **Step 6: Commit the plan contract**

```bash
git add hermes_cli/implementation_plan.py \
  tests/hermes_cli/test_implementation_plan.py
git commit -m "feat(org): define local implementation plan contract"
```

### Task 3: Add versioned OrgRun and report persistence

**Files:**
- Modify: `hermes_cli/kanban_db.py`
- Create: `hermes_cli/org_run_store.py`
- Modify: `tests/hermes_cli/test_kanban_db_init.py`
- Create: `tests/hermes_cli/test_org_run_store.py`

**Interfaces:**
- Consumes: canonical JSON/hash from Task 2.
- Produces:
  - `OrgRunRecord`
  - `OrgNodeRecord`
  - `KanbanReportRecord`
  - `insert_org_run(...)`
  - `get_org_run(conn, run_id) -> OrgRunRecord | None`
  - `list_org_nodes(conn, run_id) -> list[OrgNodeRecord]`
  - `set_org_run_state(conn, run_id, state) -> None`
  - `insert_plan_version(...)`
  - `insert_report(...) -> KanbanReportRecord`

- [ ] **Step 1: Write failing fresh-schema and upgrade tests**

Assert that fresh and reopened legacy databases contain:

```python
expected = {
    "kanban_org_runs",
    "kanban_org_plan_versions",
    "kanban_org_nodes",
    "kanban_reports",
}
assert expected <= {
    row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
}
```

Also test unique `(board_slug, run_id)`,
`(run_id, node_id)`, `(run_id, plan_version)`, and report idempotency.

- [ ] **Step 2: Run migration tests and verify missing tables**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_kanban_db_init.py \
  tests/hermes_cli/test_org_run_store.py
```

Expected: failures identify the four absent tables/module.

- [ ] **Step 3: Add additive schema**

Add to `SCHEMA_SQL`:

```sql
CREATE TABLE IF NOT EXISTS kanban_org_runs (
    run_id TEXT PRIMARY KEY,
    board_slug TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('local','backend')),
    state TEXT NOT NULL,
    anchor_task_id TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kanban_org_plan_versions (
    run_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id, plan_version)
);

CREATE TABLE IF NOT EXISTS kanban_org_nodes (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    task_id TEXT NOT NULL UNIQUE,
    node_kind TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    contract_hash TEXT NOT NULL,
    logical_role TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY(run_id, node_id)
);

CREATE TABLE IF NOT EXISTS kanban_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board_slug TEXT NOT NULL,
    report_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    terminal_run_id INTEGER,
    source_version INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    report_markdown TEXT NOT NULL,
    generated_at INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);
```

Add indexes for run state, node task lookup, and report subject/time.

- [ ] **Step 4: Implement the focused store**

`org_run_store.py` must own SQL for these tables, validate state against:

```python
ORG_RUN_STATES = frozenset({
    "draft", "validated", "materialized", "running", "integrating",
    "reviewing", "completed", "blocked", "cancelled",
})
```

It must never commit independently when `conn.in_transaction` is true.

- [ ] **Step 5: Run persistence tests**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_kanban_db_init.py \
  tests/hermes_cli/test_org_run_store.py
```

Expected: PASS, including migration idempotency.

- [ ] **Step 6: Commit persistence**

```bash
git add hermes_cli/kanban_db.py hermes_cli/org_run_store.py \
  tests/hermes_cli/test_kanban_db_init.py \
  tests/hermes_cli/test_org_run_store.py
git commit -m "feat(org): persist versioned local OrgRuns"
```

### Task 4: Materialize the simplified OrgRun DAG atomically

**Files:**
- Create: `hermes_cli/agentic_org_run.py`
- Create: `tests/hermes_cli/test_agentic_org_run.py`
- Modify: `hermes_cli/kanban_db.py`
- Modify: `hermes_cli/kanban_decompose.py`
- Modify: `tests/hermes_cli/test_kanban_decompose.py`

**Interfaces:**
- Consumes: `ImplementationPlan`, `PlanValidation`, and Task 3 store.
- Produces:
  - `OrgRunTopology`
  - `TaskNodeTopology`
  - `materialize_org_run(conn, plan, validation, *, board, activate=True) -> OrgRunTopology`
  - `load_org_run_topology(conn, run_id) -> OrgRunTopology | None`
  - `is_org_run_task(conn, task_id) -> bool`
  - `adopt_legacy_org_run(conn, run_id, *, board) -> OrgRunTopology`

- [ ] **Step 1: Write failing topology, idempotency, rollback, and profile tests**

Expected topology:

```python
assert kb.get_task(conn, topology.anchor_id).status == "done"
assert kb.get_task(conn, topology.tasks["runtime"].execution_id).assignee == "leaf"
assert kb.get_task(conn, topology.tasks["runtime"].review_id).assignee == "reviewer"
assert kb.parent_ids(conn, topology.integration_id) == [
    topology.tasks["runtime"].review_id
]
assert kb.get_task(conn, topology.integration_id).assignee == "orchestrator"
assert kb.get_task(conn, topology.finalization_id).assignee == "orchestrator"
```

Test no review node when `independent_review=False` and risk is not high; global
review exists when top-level review is true or any task is high risk. Force
`kb.create_task` to fail mid-materialization and assert zero run/node/task rows.
Materialize twice and assert stable topology/counts.

Create one legacy graph through the existing `kanban_portfolio.create_org_run`,
then assert adoption creates store/node provenance without increasing the
`tasks` count, changing completed events, or reading a backend binding.

- [ ] **Step 2: Run tests and verify missing module**

```bash
.venv/bin/python -m pytest -q tests/hermes_cli/test_agentic_org_run.py
```

Expected: import failure.

- [ ] **Step 3: Implement topology dataclasses and contract hashing**

Use:

```python
@dataclass(frozen=True)
class TaskNodeTopology:
    execution_id: str
    review_id: str | None


@dataclass(frozen=True)
class OrgRunTopology:
    run_id: str
    anchor_id: str
    tasks: dict[str, TaskNodeTopology]
    integration_id: str
    review_id: str | None
    finalization_id: str
```

Derive each `contract_hash` from canonical JSON containing only node kind,
logical role, task contract, dependency node IDs, plan version, and base commit.

- [ ] **Step 4: Implement one-transaction materialization**

Inside `with kb.write_txn(conn):`:

1. Reject same `run_id` with a different plan hash.
2. Return stored topology for an exact replay.
3. Create and immediately complete the anchor.
4. Create execution nodes and optional per-task review nodes.
5. Link dependencies to the prior task's terminal gate.
6. Create integration, optional global review, and finalization.
7. Insert run, version, and node rows.

Use keys:

```python
f"org-run:{plan.run_id}:anchor"
f"org-run:{plan.run_id}:task:{task.id}"
f"org-run:{plan.run_id}:task:{task.id}:review"
f"org-run:{plan.run_id}:integration"
f"org-run:{plan.run_id}:review"
f"org-run:{plan.run_id}:finalize"
```

Do not create remote anchors, readiness nodes, publish nodes, or remote links.

- [ ] **Step 5: Adopt old OrgRuns without recreating their cards**

`adopt_legacy_org_run` locates the anchor and nodes only through stable
idempotency keys:

```text
org-run:<run_id>:anchor
org-run:<run_id>:<task-id>:execute
org-run:<run_id>:<task-id>:review
org-run:<run_id>:<task-id>:ready
org-run:<run_id>:<task-id>:complete
org-run:<run_id>:integration
org-run:<run_id>:org-review
org-run:<run_id>:synthesis
```

Map execute/review/integration/org-review/synthesis to the new topology and
record old ready/complete nodes as required `legacy_gate` nodes. Use synthesis
as finalization. Repair only open legacy review cards whose exact old contract
is `assignee=default` plus `skills=["requesting-code-review"]`; assign them to
`reviewer` with `skills=["hierarchical-development"]`. Ignore remote-link rows
and preserve every task/event ID. A second adoption returns the same topology.

- [ ] **Step 6: Fail closed when an adopted role disappears**

For ready/review cards present in `kanban_org_nodes`, extend
`kanban_db.dispatch_once` preflight:

```python
if is_org_run_task(conn, row["id"]):
    if profile_exists is not None and not profile_exists(row_assignee):
        block_task(
            conn, row["id"],
            reason=f"profile_unavailable: {row_assignee}",
            kind="capability",
        )
        continue
    if _resolve_worker_role_route(row_assignee) is None:
        block_task(
            conn, row["id"],
            reason=f"role_route_unavailable: {row_assignee}",
            kind="capability",
        )
        continue
```

Ordinary control-plane lanes retain the existing
`skipped_nonspawnable` behavior. Add tests proving OrgRun nodes block once,
emit one typed event, and never fall back to `default`.

- [ ] **Step 7: Exclude managed cards from native decomposition**

In `kanban_decompose.decompose_task`, after loading the task:

```python
with kb.connect_closing() as conn:
    task = kb.get_task(conn, task_id)
    if task is not None:
        from hermes_cli.agentic_org_run import is_org_run_task
        if is_org_run_task(conn, task_id):
            return DecomposeOutcome(
                task_id, False, "OrgRun-managed task bypasses native decomposition"
            )
```

Make `list_triage_ids` exclude task IDs present in `kanban_org_nodes` so the
gateway does not spend the per-tick budget on them.

- [ ] **Step 8: Run topology and decomposition tests**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_agentic_org_run.py \
  tests/hermes_cli/test_kanban_decompose.py \
  tests/hermes_cli/test_kanban_decompose_db.py
```

Expected: PASS; task count is `1 + N + per-task-reviews + 1 integration +
optional global review + 1 finalization`.

- [ ] **Step 9: Commit materialization**

```bash
git add hermes_cli/agentic_org_run.py hermes_cli/kanban_db.py \
  hermes_cli/kanban_decompose.py \
  tests/hermes_cli/test_agentic_org_run.py \
  tests/hermes_cli/test_kanban_decompose.py
git commit -m "feat(org): materialize local Agentic-Kanban DAGs"
```

### Task 5: Add atomic amendments and derived OrgRun state

**Files:**
- Modify: `hermes_cli/implementation_plan.py`
- Modify: `hermes_cli/agentic_org_run.py`
- Modify: `hermes_cli/org_run_store.py`
- Create: `tests/hermes_cli/test_org_run_amendments.py`
- Modify: `tests/hermes_cli/test_agentic_org_run.py`

**Interfaces:**
- Produces:
  - `ImplementationAmendment`
  - `parse_implementation_amendment(payload) -> ImplementationAmendment`
  - `apply_org_run_amendment(conn, amendment, *, board, repository, profile_exists) -> OrgRunTopology`
  - `refresh_org_run_state(conn, run_id) -> str`

- [ ] **Step 1: Write failing amendment and state tests**

Use schema:

```python
{
    "schema": "hades.implementation-amendment.v1",
    "run_id": "local-run-001",
    "base_plan_version": 1,
    "reason": "Integration exposed a missing regression",
    "add_tasks": [valid_task_payload("regression")],
    "replace_tasks": [],
    "cancel_task_ids": [],
}
```

Test additive tasks, replacement of an unfinished task with a new task ID,
cancellation of an unfinished task, refusal to rewrite a done/running task,
stale `base_plan_version`, cycle/scope/profile failure rollback, no version
bump on failure, and exact state transitions from task statuses.

- [ ] **Step 2: Run tests and verify missing interfaces**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_org_run_amendments.py \
  tests/hermes_cli/test_agentic_org_run.py
```

Expected: imports or attribute lookups fail.

- [ ] **Step 3: Implement amendment parsing and validation**

Define:

```python
@dataclass(frozen=True)
class ReplacementTask:
    replaces: str
    task: ImplementationTask


@dataclass(frozen=True)
class ImplementationAmendment:
    schema: str
    run_id: str
    base_plan_version: int
    reason: str
    add_tasks: tuple[ImplementationTask, ...]
    replace_tasks: tuple[ReplacementTask, ...]
    cancel_task_ids: tuple[str, ...]
```

Reject empty amendments and repeated target IDs.

- [ ] **Step 4: Apply graph changes in one transaction**

Within one `kb.write_txn`:

- lock the current run/version by reading it after `BEGIN IMMEDIATE`;
- reject completed or running replacement/cancellation targets;
- archive cancelled/replaced task cards and mark their node rows `cancelled`;
- materialize new nodes with new IDs and versioned contract hashes;
- unlink cancelled gates from integration and link replacement/new gates;
- store the full resulting plan as `plan_version + 1`;
- update `kanban_org_runs.plan_hash`, version, and timestamp.

Any exception must roll back task, link, node, and version writes.

- [ ] **Step 5: Derive state only from durable Kanban rows**

Implement `refresh_org_run_state` with exact precedence:

```python
if run.state == "cancelled":
    return "cancelled"
if any_required_node_blocked:
    return "blocked"
if finalization_done and final_report_exists:
    return "completed"
if global_review_running_or_done:
    return "reviewing"
if integration_running_or_done:
    return "integrating"
if any_execution_started:
    return "running"
return "materialized"
```

Do not run a model or inspect worker prose to choose a state.

- [ ] **Step 6: Run amendment/state tests**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_org_run_amendments.py \
  tests/hermes_cli/test_agentic_org_run.py \
  tests/hermes_cli/test_org_run_store.py
```

Expected: PASS.

- [ ] **Step 7: Commit amendments**

```bash
git add hermes_cli/implementation_plan.py hermes_cli/agentic_org_run.py \
  hermes_cli/org_run_store.py \
  tests/hermes_cli/test_org_run_amendments.py \
  tests/hermes_cli/test_agentic_org_run.py
git commit -m "feat(org): version atomic OrgRun amendments"
```

### Task 6: Project terminal Kanban evidence into the local logbook

**Files:**
- Create: `hermes_cli/kanban_reports.py`
- Modify: `hermes_cli/kanban_db.py`
- Create: `tests/hermes_cli/test_kanban_reports.py`
- Modify: `tests/hermes_cli/test_agentic_org_run.py`

**Interfaces:**
- Produces:
  - `project_task_completion(conn, task_id, *, board) -> KanbanReportRecord | None`
  - `project_org_run_completion(conn, run_id, *, board) -> KanbanReportRecord | None`
  - `project_after_task_completion(conn, task_id, *, board) -> tuple[KanbanReportRecord, ...]`
  - `list_reports(conn, *, report_type=None, subject_id=None, run_id=None) -> list[KanbanReportRecord]`
  - `get_report(conn, report_id) -> KanbanReportRecord | None`

- [ ] **Step 1: Write failing deterministic report tests**

Complete a task with:

```python
metadata = {
    "changed_files": ["hermes_cli/kanban.py"],
    "tests_run": [{
        "command": "pytest tests/hermes_cli/test_kanban_cli.py",
        "status": "passed",
    }],
    "review": {"verdict": "pass", "findings": []},
    "regressions": [],
    "residual_risks": ["Legacy rows remain audit-only"],
}
```

Assert canonical JSON key order, Markdown headings, task/run/board provenance,
prior failed attempts, idempotent re-projection, versioned corrections, no
terminal report for blocked state, and no final OrgRun report before every
required gate is done.

- [ ] **Step 2: Run tests and verify missing projector**

```bash
.venv/bin/python -m pytest -q tests/hermes_cli/test_kanban_reports.py
```

Expected: import failure.

- [ ] **Step 3: Implement bounded evidence collection**

Canonical task JSON shape:

```python
payload = {
    "schema": "hades.kanban-task-report.v1",
    "board_slug": board,
    "task_id": task.id,
    "terminal_run_id": run.id,
    "title": task.title,
    "status": "completed",
    "summary": run.summary,
    "changed_files": metadata.get("changed_files", []),
    "tests": metadata.get("tests_run", []),
    "review": metadata.get("review"),
    "regressions": metadata.get("regressions", []),
    "residual_risks": metadata.get("residual_risks", []),
    "prior_attempts": prior_attempts,
    "generated_at": generated_at,
}
```

Cap free text and arrays using existing Kanban context bounds. Reject secrets
with the existing Hades redaction helper before persistence.

- [ ] **Step 4: Implement final OrgRun report and deterministic Markdown**

Final JSON schema `hades.org-run-report.v1` must aggregate task reports,
integration/final review evidence, plan/base-commit/version, test results,
regressions, blockers resolved, and residual risk. Markdown renderer must
produce:

```markdown
# Development report: <run_id>
## Objective
## Changes
## Verification
## Review
## Regressions and residual risk
## Provenance
```

No LLM call is permitted.

- [ ] **Step 5: Integrate projection after durable local completion**

After `complete_task` commits, invoke:

```python
try:
    from hermes_cli.kanban_reports import project_after_task_completion
    project_after_task_completion(conn, task_id, board=get_current_board())
except Exception as exc:
    from hermes_cli.hades_backend_client import redact_secret
    with write_txn(conn):
        _append_event(
            conn, task_id, "report_projection_failed",
            {"error": redact_secret(str(exc))[:500], "retryable": True},
            run_id=run_id,
        )
```

If final OrgRun projection fails, keep the OrgRun `blocked`/`reviewing`; do not
revert the already durable task completion.

- [ ] **Step 6: Run report and lifecycle tests**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_kanban_reports.py \
  tests/hermes_cli/test_agentic_org_run.py \
  tests/hermes_cli/test_kanban_core_functionality.py
```

Expected: PASS.

- [ ] **Step 7: Commit report projection**

```bash
git add hermes_cli/kanban_reports.py hermes_cli/kanban_db.py \
  tests/hermes_cli/test_kanban_reports.py \
  tests/hermes_cli/test_agentic_org_run.py
git commit -m "feat(kanban): derive local task and OrgRun reports"
```

### Task 7: Replace the Hades Org CLI with the local plan workflow

**Files:**
- Modify: `hermes_cli/hades_org_cmd.py`
- Modify: `tests/hermes_cli/test_hades_org_cli.py`

**Interfaces:**
- Consumes: Tasks 2–6 public functions.
- Produces:
  - `hades org validate <plan> --board <slug> --json`
  - `hades org materialize <plan> --board <slug> --json`
  - `hades org show <run_id> --board <slug> --json`
  - `hades org amend <amendment> --board <slug> --json`
  - `hades org list --board <slug> --json`
  - `hades org adopt-legacy <run_id> --board <slug> --json`
  - typed rejection for `hades org sync`

- [ ] **Step 1: Rewrite CLI tests for the new contract**

Assert exact output:

```python
assert valid == {
    "status": "valid",
    "schema": "hades.implementation-plan.v1",
    "run_id": "local-run-001",
    "task_count": 1,
    "conflict_count": 0,
    "plan_hash": valid["plan_hash"],
    "resolved_profiles": {
        "leaf": "leaf",
        "orchestrator": "orchestrator",
        "reviewer": "reviewer",
    },
    "routed_roles": ["leaf", "orchestrator", "reviewer"],
}
```

Also assert `show` returns state, plan version/hash, topology, blocked nodes,
dispatchable nodes, and report IDs; `sync` returns the same typed rejection as
Kanban without importing backend modules.

Create a legacy graph and assert `list` marks it `legacy_unadopted`, then
`adopt-legacy` returns `adopted` without changing the task count and subsequent
`list` returns the versioned run once.

- [ ] **Step 2: Run CLI tests and verify legacy output fails**

```bash
.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_org_cli.py
```

Expected: failures reference `hades.execution-portfolio.v1`, remote fields,
and `sync_kanban`.

- [ ] **Step 3: Replace legacy imports and command handlers**

Remove top-level imports from `hades_kanban_sync` and `kanban_backend`. Resolve
repository from the selected board's `default_workdir`; fail with
`board_workspace_missing` if it is absent or not a Git repository.

Use:

```python
def _repository_for_board(board: str | None) -> Path:
    slug = board or kb.get_current_board()
    raw = str(kb.read_board_metadata(slug).get("default_workdir") or "").strip()
    path = Path(raw).expanduser().resolve()
    if not raw or not (path / ".git").exists():
        raise ValueError("selected board has no Git default_workdir")
    return path
```

Build `role_route_exists` from the root read-only `config.yaml` via
`load_delegation_routing` and `resolve_role_profile`. Return only a boolean to
the validator/CLI result; provider and model values remain local and never
enter the plan or persisted card contract.

`list` unions versioned `kanban_org_runs` with unadopted
`org-run:*:anchor` idempotency keys. `adopt-legacy` calls the Task 4 adoption
function and never materializes replacement cards.

`org sync` remains a compatibility parser entry for one release but always
returns `agentic_kanban_has_no_remote_sync`; it performs no import beyond the
local CLI module.

- [ ] **Step 4: Add `list`, `adopt-legacy`, `amend`, and new `show`**

`show_org_run` reads `kanban_org_runs`, nodes, task states, and reports. It does
not reconstruct remote topology from swarm blackboard comments.

- [ ] **Step 5: Run CLI and import-isolation tests**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_hades_org_cli.py \
  tests/hermes_cli/test_implementation_plan.py \
  tests/hermes_cli/test_agentic_org_run.py
```

Expected: PASS; monkeypatch sentinels prove no backend client, sync, lease, or
remote-publication function was used.

- [ ] **Step 6: Commit CLI cutover**

```bash
git add hermes_cli/hades_org_cmd.py \
  tests/hermes_cli/test_hades_org_cli.py
git commit -m "feat(hades): expose local implementation plan OrgRuns"
```

### Task 8: Turn the dashboard into Agentic-Kanban with Board and Logbook

**Files:**
- Modify: `plugins/kanban/dashboard/manifest.json`
- Modify: `plugins/kanban/dashboard/plugin_api.py`
- Modify: `plugins/kanban/dashboard/dist/index.js`
- Modify: `plugins/kanban/dashboard/dist/style.css`
- Modify: `tests/plugins/test_kanban_dashboard_plugin.py`

**Interfaces:**
- Produces:
  - `GET /api/plugins/kanban/reports`
  - `GET /api/plugins/kanban/reports/{report_id}`
  - dashboard views `Board | Logbook`
  - settings label `Native triage decomposition`

- [ ] **Step 1: Write failing API and static-bundle tests**

API assertions:

```python
reports = client.get(
    "/api/plugins/kanban/reports?report_type=task_completion&subject_id=t_123"
)
assert reports.status_code == 200
assert reports.json()["reports"][0]["report_type"] == "task_completion"

detail = client.get(f"/api/plugins/kanban/reports/{report_id}")
assert detail.json()["report"]["report_markdown"].startswith("#")
```

Bundle assertions:

```python
assert '"Agentic-Kanban"' in bundle
assert '"Board"' in bundle and '"Logbook"' in bundle
assert '"Native triage decomposition"' in bundle
assert '"Orchestration settings"' not in bundle
assert '"Local only"' not in bundle
assert '"Backend synced"' not in bundle
assert 't.origin === "remote"' not in bundle
```

- [ ] **Step 2: Run dashboard tests and verify failures**

```bash
.venv/bin/python -m pytest -q tests/plugins/test_kanban_dashboard_plugin.py
```

Expected: missing endpoints and legacy labels/badges fail.

- [ ] **Step 3: Add report API endpoints**

Use `kanban_reports.list_reports/get_report`, validate report type against:

```python
{"task_completion", "org_run_final", "org_run_cancelled"}
```

Return parsed JSON plus Markdown; never expose worker logs, reasoning,
credentials, or legacy remote rows.

- [ ] **Step 4: Update dashboard identity and native-decomposition copy**

Set manifest label to `Agentic-Kanban`. In `dist/index.js`, render a primary
two-tab switch, preserve board state while viewing logbook, lazy-load reports,
and use the existing safe Markdown renderer for report detail.

Rename the settings panel and add this exact explanation:

```text
Applies only to manually created triage cards. OrgRun plans keep their own
versioned DAG, authority, and role routing.
```

Keep `orchestrator_profile`, `default_assignee`, `auto_decompose`, and profile
descriptions functional for native triage only.

- [ ] **Step 5: Add minimal tab/report styles**

Reuse existing dashboard tokens/classes. Add only scoped
`.hermes-kanban-tabs`, `.hermes-kanban-report-list`, and
`.hermes-kanban-report-detail` rules; no new frontend dependency or build step.

- [ ] **Step 6: Run dashboard tests**

```bash
.venv/bin/python -m pytest -q tests/plugins/test_kanban_dashboard_plugin.py
```

Expected: PASS.

- [ ] **Step 7: Commit dashboard**

```bash
git add plugins/kanban/dashboard/manifest.json \
  plugins/kanban/dashboard/plugin_api.py \
  plugins/kanban/dashboard/dist/index.js \
  plugins/kanban/dashboard/dist/style.css \
  tests/plugins/test_kanban_dashboard_plugin.py
git commit -m "feat(kanban): add Agentic-Kanban logbook view"
```

### Task 9: Align hierarchical-development and OrgRun operations documentation

**Files:**
- Modify: `skills/software-development/hierarchical-development/SKILL.md`
- Modify: `docs/hades/org-run-operations.md`
- Modify: `tests/skills/test_hierarchical_development_skill.py`
- Modify: `tests/test_docs_hades_mvp.py`

**Interfaces:**
- Consumes: final CLI and authority contract.
- Produces: one unambiguous documented flow:
  `hierarchical-development -> OrgRun -> Agentic-Kanban -> local reports`.

- [ ] **Step 1: Write failing documentation contract tests**

Require exact phrases:

```python
for phrase in [
    "Agentic-Kanban is local and never synchronizes cards with the backend",
    "OrgRun never calls a model",
    "The orchestrator authors the plan; OrgRun materializes the DAG",
    "Native triage decomposition does not apply to OrgRun cards",
    "Swarm is an explicit alternative, never an OrgRun stage",
    "Final Development Report",
]:
    assert phrase in skill
```

Reject legacy `pull_only`, `mirror`, remote lease, remote publish, and
`execution portfolio` instructions from the active operations guide.

- [ ] **Step 2: Run documentation tests and verify legacy guidance fails**

```bash
.venv/bin/python -m pytest -q \
  tests/skills/test_hierarchical_development_skill.py \
  tests/test_docs_hades_mvp.py
```

Expected: failures identify backend-centric instructions.

- [ ] **Step 3: Rewrite skill decision and execution protocol**

The skill must say:

```text
1. The orchestrator writes hades.implementation-plan.v1.
2. Run hades org validate, then hades org materialize with an explicit board.
3. OrgRun validates and writes the initial DAG atomically; do not create it
   card-by-card.
4. Leaf and reviewer work only through their direct-parent authority.
5. Changes to the plan use a versioned hades org amend operation.
6. Completion produces local task reports and one Final Development Report.
```

Remove automatic backend publication and remote-lease escalation.

- [ ] **Step 4: Rewrite operator guide**

Document validate/materialize/show/amend, state meanings, blocked recovery,
report inspection in the dashboard, native decomposition separation, swarm
separation, and `agentic_kanban_has_no_remote_sync`.

- [ ] **Step 5: Run documentation tests**

```bash
.venv/bin/python -m pytest -q \
  tests/skills/test_hierarchical_development_skill.py \
  tests/test_docs_hades_mvp.py
```

Expected: PASS.

- [ ] **Step 6: Commit documentation**

```bash
git add skills/software-development/hierarchical-development/SKILL.md \
  docs/hades/org-run-operations.md \
  tests/skills/test_hierarchical_development_skill.py \
  tests/test_docs_hades_mvp.py
git commit -m "docs(hades): align hierarchical development with local OrgRun"
```

### Task 10: Prove the offline end-to-end flow and resume the real Hades session

**Files:**
- Create: `tests/e2e/test_agentic_kanban_org_run.py`
- Runtime evidence only: board/session state for `20260727_191342_7b2122`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: reproducible offline E2E proof plus real-session completion reports.

- [ ] **Step 1: Write the failing offline E2E test**

The test must create a temporary Git repository, three real profile
directories, a named board, and a plan. Patch only subprocess execution, not
the plan/store/materializer/dispatcher/report path.

```python
def forbidden_backend(*_args, **_kwargs):
    raise AssertionError("Agentic-Kanban attempted backend access")

monkeypatch.setattr(
    "hermes_cli.hades_backend_client.HadesBackendClient",
    forbidden_backend,
)
```

Capture all `Popen` argv and assert no item contains `hermes-agent`,
`hermes-review-engine`, or backend credentials. Simulate task completions with
structured evidence, interrupt between task and integration, reopen the DB,
resume, and assert exactly one final report.

- [ ] **Step 2: Run the E2E test before final fixes**

```bash
.venv/bin/python -m pytest -q tests/e2e/test_agentic_kanban_org_run.py
```

Expected: fail on the first still-missing end-to-end invariant.

- [ ] **Step 3: Route any integration failure back to its owning task**

The E2E is an acceptance test, not a new implementation layer. If it fails,
classify the first failure and reopen exactly one owning task:

```text
backend/argv call       -> Task 1
plan validation         -> Task 2
missing/duplicate rows  -> Task 3
wrong DAG/decomposition -> Task 4
resume/version/state    -> Task 5
missing report          -> Task 6
CLI mismatch            -> Task 7
dashboard/API mismatch  -> Task 8
skill behavior          -> Task 9
```

Add the regression test and production fix to that task's exact files, rerun
that task's focused command, commit there, then rerun:

```bash
.venv/bin/python -m pytest -q tests/e2e/test_agentic_kanban_org_run.py
```

Expected final result: PASS without E2E-only production branches.

- [ ] **Step 4: Run the complete targeted suite**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_implementation_plan.py \
  tests/hermes_cli/test_org_run_store.py \
  tests/hermes_cli/test_agentic_org_run.py \
  tests/hermes_cli/test_org_run_amendments.py \
  tests/hermes_cli/test_kanban_reports.py \
  tests/hermes_cli/test_hades_org_cli.py \
  tests/hermes_cli/test_kanban_cli.py \
  tests/hermes_cli/test_kanban_core_functionality.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/hermes_cli/test_kanban_db_init.py \
  tests/hermes_cli/test_kanban_decompose.py \
  tests/gateway/test_kanban_auto_decompose_live.py \
  tests/plugins/test_kanban_dashboard_plugin.py \
  tests/skills/test_hierarchical_development_skill.py \
  tests/e2e/test_agentic_kanban_org_run.py
```

Expected: PASS.

- [ ] **Step 5: Run the broader Kanban/Hades regression set**

```bash
.venv/bin/python -m pytest -q \
  tests/hermes_cli/test_kanban_*.py \
  tests/hermes_cli/test_hades_*.py \
  tests/gateway/test_kanban_*.py \
  tests/plugins/test_kanban_*.py \
  tests/skills/test_hierarchical_development_skill.py
```

Expected: PASS except explicitly documented unrelated baseline failures, which
must be reproduced on the pre-change commit before exclusion.

- [ ] **Step 6: Commit E2E integration fixes**

```bash
git add tests/e2e/test_agentic_kanban_org_run.py
git commit -m "test(hades): prove offline Agentic-Kanban OrgRun flow"
```

Before committing, inspect `git diff --cached --name-only`; it must contain
only `tests/e2e/test_agentic_kanban_org_run.py`.

- [ ] **Step 7: Snapshot the real board before resuming**

Use the Hades skill/runtime, not direct SQLite mutation:

```bash
hades --resume 20260727_191342_7b2122
hades kanban --board ariadne list --json
hades org list --board ariadne --json
```

Save the command outputs outside the repository or in the generated local
report store. Record incomplete IDs, dependencies, current run IDs, historical
blocked reasons, and task counts.

If `hades org list` reports `legacy_unadopted`, run `hades org adopt-legacy`
with the exact `run_id` returned by that command, then run `hades org show`
with the same ID. Do not infer the OrgRun ID from the chat session ID.

- [ ] **Step 8: Resume and complete valid outstanding tasks**

Start the local dispatcher/watch flow with backend credentials unset or a
forbidden backend sentinel. Unblock only tasks whose historical blocker was the
removed legacy review authority or remote admission. Do not erase their
events. Let leaf/reviewer/orchestrator profiles execute according to the DAG:

```bash
hades kanban --board ariadne dispatch --json
hades kanban --board ariadne watch --interval 1
```

For every completion, verify the required tests/review evidence instead of
manually forcing `done`.

- [ ] **Step 9: Verify session acceptance evidence**

Assert:

```text
- zero backend calls;
- zero hermes-agent/hermes-review-engine subprocesses;
- no duplicate task idempotency keys;
- no repeated unblock/block loop;
- all valid outstanding tasks completed;
- historical failures still visible;
- one Task Completion Report per completed task terminal run;
- exactly one verified Final Development Report for the OrgRun.
```

If the old board has no versioned OrgRun record, preserve its card history and
create one local adoption/amendment run rather than rewriting old events.

- [ ] **Step 10: Final verification and handoff**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
hades org list --board ariadne --json
```

Run `hades org show` with the exact completed `run_id` returned by the list,
then report commit SHAs, test commands/results, real task/report IDs, residual
risks, and any unrelated dirty files. Do not claim completion if the final
report or session tasks are missing.

# Hades OrgRun P0-P1 Local Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a vetoable Kanban dispatch-admission seam and materialize validated, conflict-safe OrgRuns entirely in the local Kanban.

**Architecture:** Extend `kanban_db.dispatch_once` with an optional callback whose absence preserves legacy behavior. Represent OrgRun and remote-task topology using existing Kanban tasks, links, comments, events and idempotency keys; do not add a scheduler or `org_*` tables.

**Tech Stack:** Python 3.11+, dataclasses, stdlib JSON, SQLite, pytest, existing `hermes_cli.kanban_db` and `hermes_cli.kanban_swarm`.

## Global Constraints

- No network calls in this plan.
- No new dependencies, model tools, scheduler or `HERMES_*` variables.
- Preserve existing behavior when `admission_fn is None`.
- Every persisted scope path is repository-relative POSIX form.
- Stop on unexpected dirty files; never reset user changes.
- Execute one task and one commit at a time.

## Mandatory Execution Order

The physical section order in this document is not authoritative. Execute and
review exactly in this dependency order:

```text
P0-T01 -> P0-T02 -> P0-T03 -> P1-T01 -> P1-T02 -> P1-T03 -> P1-T04
```

Never start a task merely because its section appears next on screen. Locate
the heading matching the next ID in this sequence.

---

### Task P0-T01: Freeze the dispatcher baseline

**Files:**
- Create: `tests/hermes_cli/test_kanban_dispatch_admission.py`

**Interfaces:**
- Consumes: `kanban_db.dispatch_once(conn, spawn_fn=...)`.
- Produces: regression proof that no-admission dispatch still spawns once.

- [ ] **Step 1: Create the baseline test**

```python
from hermes_cli import kanban_db as kb


def test_dispatch_without_admission_preserves_legacy_spawn(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    spawned: list[str] = []
    try:
        task_id = kb.create_task(conn, title="legacy", assignee="worker")

        def spawn(task, workspace):
            spawned.append(task.id)
            return 43210

        result = kb.dispatch_once(conn, spawn_fn=spawn)
        assert spawned == [task_id]
        assert result.spawned[0][0] == task_id
        assert kb.get_task(conn, task_id).status == "running"
    finally:
        conn.close()
```

- [ ] **Step 2: Run and commit the baseline**

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_dispatch_admission.py -q
git add tests/hermes_cli/test_kanban_dispatch_admission.py
git commit -m "test(kanban): freeze dispatch admission baseline"
```

Expected before commit: `1 passed`.

---

### Task P0-T02: Add admission result and claim release primitive

**Files:**
- Modify: `hermes_cli/kanban_db.py` near `DispatchResult` and `claim_task`.
- Modify: `tests/hermes_cli/test_kanban_dispatch_admission.py`.

**Interfaces:**
- Produces: `DispatchAdmission`, `release_active_claim`.
- `DispatchAdmission.action`: exactly `allow`, `defer`, or `supersede`.
- `release_active_claim(conn, task_id, target_status, reason) -> bool`.

- [ ] **Step 1: Add failing tests**

```python
import pytest


def test_dispatch_admission_rejects_unknown_action():
    with pytest.raises(ValueError, match="unknown dispatch admission action"):
        kb.DispatchAdmission(action="invented", reason="bad")


def test_release_active_claim_closes_run_without_failure(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(conn, title="release", assignee="worker")
        assert kb.claim_task(conn, task_id) is not None
        assert kb.release_active_claim(
            conn,
            task_id,
            target_status="ready",
            reason="remote admission deferred",
        )
        task = kb.get_task(conn, task_id)
        assert task.status == "ready"
        assert task.claim_lock is None
        assert task.current_run_id is None
        assert task.consecutive_failures == 0
        run = kb.list_runs(conn, task_id)[-1]
        assert run.status == "released"
        assert run.outcome == "released"
        events = kb.list_events(conn, task_id)
        assert events[-1].kind == "admission_released"
    finally:
        conn.close()
```

- [ ] **Step 2: Verify red state**

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_dispatch_admission.py -q
```

Expected: FAIL because `DispatchAdmission` is absent. Any other failure means
`unexpected_red_state`; stop.

- [ ] **Step 3: Add `DispatchAdmission` immediately before `DispatchResult`**

```python
@dataclass(frozen=True)
class DispatchAdmission:
    action: str = "allow"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action not in {"allow", "defer", "supersede"}:
            raise ValueError(
                f"unknown dispatch admission action: {self.action!r}"
            )
```

- [ ] **Step 4: Add `release_active_claim` after `claim_task`**

```python
def release_active_claim(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    target_status: str,
    reason: str,
) -> bool:
    if target_status not in {"ready", "blocked"}:
        raise ValueError("target_status must be ready or blocked")
    now = int(time.time())
    with write_txn(conn):
        row = conn.execute(
            "SELECT status, current_run_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None or row["status"] != "running" or row["current_run_id"] is None:
            return False
        run_id = int(row["current_run_id"])
        conn.execute(
            "UPDATE task_runs SET status = 'released', outcome = 'released', "
            "ended_at = ?, summary = ? WHERE id = ? AND status = 'running'",
            (now, reason, run_id),
        )
        conn.execute(
            "UPDATE tasks SET status = ?, claim_lock = NULL, claim_expires = NULL, "
            "worker_pid = NULL, current_run_id = NULL WHERE id = ?",
            (target_status, task_id),
        )
        _append_event(
            conn,
            task_id,
            "admission_released",
            {"target_status": target_status, "reason": reason},
            run_id=run_id,
        )
    return True
```

- [ ] **Step 5: Verify and commit**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_kanban_dispatch_admission.py \
  tests/hermes_cli/test_kanban_reclaim_claim_lock_guard.py \
  -q
git add hermes_cli/kanban_db.py tests/hermes_cli/test_kanban_dispatch_admission.py
git commit -m "feat(kanban): add dispatch admission result"
```

Expected before commit: exit code `0`.

---

### Task P1-T03: Materialize OrgRun topology in Kanban

**Files:**
- Create: `hermes_cli/kanban_portfolio.py`.
- Create: `tests/hermes_cli/test_kanban_portfolio.py`.

**Interfaces:**
- Consumes: `ExecutionPortfolio`, `PortfolioValidation`.
- Produces: `RemoteTaskTopology`, `OrgRunCreated`, `create_org_run`.
- Uses only existing `kanban_db` and `kanban_swarm` persistence.

- [ ] **Step 1: Write the topology test**

```python
from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import create_org_run


def payload():
    return {
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": "org_demo_001",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "base_commit": "a" * 40,
        "tasks": [{
            "remote_task_id": "HD-101",
            "work_item_id": "awi_101",
            "title": "Change contract",
            "body": "Implement the bounded change.",
            "assignee": "marshal",
            "priority": 10,
            "risk": "high",
            "depends_on": [],
            "write_scope": ["hermes_cli/contracts.py"],
        }],
    }


def test_create_org_run_separates_anchor_execution_review_and_completion(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        remote = created.remote_tasks["HD-101"]
        assert kb.get_task(conn, created.anchor_id).status == "done"
        assert kb.get_task(conn, remote.anchor_id).status == "done"
        assert kb.get_task(conn, remote.execution_id).status == "ready"
        assert kb.get_task(conn, remote.review_id).status == "todo"
        assert kb.get_task(conn, remote.integration_ready_id).status == "todo"
        assert kb.get_task(conn, remote.completion_id).status == "todo"
        assert kb.parent_ids(conn, remote.execution_id) == [remote.anchor_id]
        assert kb.parent_ids(conn, remote.review_id) == [remote.execution_id]
        assert kb.parent_ids(conn, remote.integration_ready_id) == [remote.review_id]
        assert kb.parent_ids(conn, created.integration_id) == [remote.integration_ready_id]
        assert kb.parent_ids(conn, remote.completion_id) == [created.review_id]
    finally:
        conn.close()
```

- [ ] **Step 2: Verify module absence**

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_portfolio.py -q
```

Expected: collection error for `hermes_cli.kanban_portfolio`.

- [ ] **Step 3: Create immutable result types**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
import sqlite3

from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import ExecutionPortfolio, PortfolioValidation
from hermes_cli.kanban_swarm import post_blackboard_update


@dataclass(frozen=True)
class RemoteTaskTopology:
    anchor_id: str
    execution_id: str
    review_id: str
    integration_ready_id: str
    completion_id: str


@dataclass(frozen=True)
class OrgRunCreated:
    anchor_id: str
    remote_tasks: dict[str, RemoteTaskTopology]
    integration_id: str
    review_id: str
    synthesis_id: str
```

- [ ] **Step 4: Implement `create_org_run` with exact topology rules**

The function signature is:

```python
def create_org_run(
    conn: sqlite3.Connection,
    plan: ExecutionPortfolio,
    validation: PortfolioValidation,
    *,
    board: str | None = None,
    activate: bool = True,
) -> OrgRunCreated:
```

Implementation sequence:

1. Create OrgRun anchor with key `org-run:{org_run_id}:anchor` and complete it
   with `kind=hades_org_run_v1`.
   Before creating descendants, read `latest_blackboard`; if a complete valid
   topology already exists, reconstruct and return `OrgRunCreated` immediately.
2. For each task create anchor, execution, review, integration-ready and
   completion cards with
   idempotency keys
   `org-run:{org_run_id}:{remote_task_id}:{anchor|execute|review|ready|complete}`.
3. Complete only the remote anchor immediately.
4. Execution parent initially contains only its remote anchor. Pass
   `triage=not activate` to `kb.create_task`; this lets backend `pull_only`
   materialize visible but non-dispatchable cards.
5. Review parent is execution; integration-ready parent is review.
6. Apply every `ordered_dependencies[remote_id]` by linking the dependency's
   integration-ready node to the current execution node. Never link its remote
   completion/publish node here; that would create a cycle through org review.
7. Create integration with all remote integration-ready nodes as parents.
8. Create org review with integration as parent.
9. Create one remote completion/publish node per remote task, each with org
   review as parent. This is the `local_completion_task_id` used by P2.
10. Create synthesis with every remote completion/publish node as parent.
11. Post `portfolio`, `topology`, `conflicts` updates to the OrgRun anchor.

Every executable body ends with:

```python
def _protocol(org_run_id: str, remote_task_id: str) -> str:
    return (
        "\n\n## OrgRun protocol\n"
        f"- OrgRun: `{org_run_id}`.\n"
        f"- Remote task: `{remote_task_id}`.\n"
        "- Stay inside declared write_scope.\n"
        "- Complete with structured evidence or block with a typed reason.\n"
        "- Do not publish memory or contact the backend directly.\n"
    )
```

Store topology in the blackboard using:

```python
topology_payload = {
    "anchor_id": created.anchor_id,
    "remote_tasks": {
        key: asdict(value) for key, value in created.remote_tasks.items()
    },
    "integration_id": created.integration_id,
    "review_id": created.review_id,
    "synthesis_id": created.synthesis_id,
}
```

- [ ] **Step 5: Add idempotency and conflict-link tests**

Call `create_org_run` twice and assert equal results plus unchanged task count.
Add a second overlapping task and assert its execution parents contain the
first task's completion ID.

- [ ] **Step 6: Verify and commit**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_kanban_portfolio.py \
  tests/hermes_cli/test_kanban_swarm.py \
  -q
git add hermes_cli/kanban_portfolio.py tests/hermes_cli/test_kanban_portfolio.py
git commit -m "feat(kanban): materialize durable OrgRun portfolios"
```

Expected before commit: exit code `0`.

---

### Task P1-T04: Add local-only OrgRun CLI and restart E2E

**Files:**
- Modify: `hermes_cli/main.py` beside Kanban/project/backend parser registration
  and beside `cmd_kanban`/`cmd_project`/`cmd_backend`.
- Create: `hermes_cli/hades_org_cmd.py`.
- Create: `tests/hermes_cli/test_hades_org_cli.py`.
- Create: `tests/hermes_cli/test_hades_org_e2e.py`.

**Interfaces:**
- Produces:
  - `hades org validate <portfolio.json> --json`
  - `hades org materialize <portfolio.json> [--board NAME] --json`
  - `hades org show <org_run_id> [--board NAME] --json`

- [ ] **Step 1: Write command tests with temporary `HERMES_HOME`**

Assertions for `validate`:

```python
assert result == {
    "status": "valid",
    "schema": "hades.execution-portfolio.v1",
    "org_run_id": "org_demo_001",
    "task_count": 1,
    "conflict_count": 0,
}
```

Assertions for `materialize`: `status == "materialized"` and all topology IDs
are non-empty. Close the DB, call `show`, and assert identical topology.

- [ ] **Step 2: Verify unknown command failure**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_hades_org_cli.py \
  tests/hermes_cli/test_hades_org_e2e.py \
  -q
```

Expected: FAIL because `hades org` is absent.

- [ ] **Step 3: Implement file-only command helpers**

`hermes_cli/hades_org_cmd.py` exposes exactly three functions:

- `validate_portfolio_file(path: str) -> tuple[dict, int]`;
- `materialize_portfolio_file(path: str, *, board: str | None) -> tuple[dict, int]`;
- `show_org_run(org_run_id: str, *, board: str | None) -> tuple[dict, int]`.

It also exposes `build_parser(subparsers, *, cmd_org) -> None` and
`org_command(args) -> int`. In `main.py`, register the parser immediately after
the project parser and add `cmd_org` beside `cmd_project`; do not add cases to
the monolithic top-level argument parser elsewhere.

Rules:

- read UTF-8 JSON only;
- no YAML fallback;
- no network client import;
- exit `2` for validation errors;
- exit `1` for filesystem/DB errors;
- redact exception messages;
- output dictionaries, leaving JSON rendering to the caller;
- locate `show` anchor by idempotency key and read the structured blackboard.

- [ ] **Step 4: Run P0-P1 release gate**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_kanban_dispatch_admission.py \
  tests/hermes_cli/test_hierarchical_execution.py \
  tests/hermes_cli/test_kanban_portfolio.py \
  tests/hermes_cli/test_hades_org_cli.py \
  tests/hermes_cli/test_hades_org_e2e.py \
  tests/hermes_cli/test_kanban_swarm.py \
  tests/hermes_cli/test_kanban_core_functionality.py \
  -q
```

Expected: exit code `0`.

- [ ] **Step 5: Commit**

```bash
git diff --check
git add \
  hermes_cli/main.py \
  hermes_cli/hades_org_cmd.py \
  tests/hermes_cli/test_hades_org_cli.py \
  tests/hermes_cli/test_hades_org_e2e.py
git commit -m "feat(hades): expose local OrgRun commands"
```

## P0-P1 Completion Checklist

- [ ] No network client imported by local portfolio modules.
- [ ] No table named with prefix `org_` exists.
- [ ] No model tool or second scheduler exists.
- [ ] `admission_fn=None` baseline passes.
- [ ] Defer/supersede leave failure count unchanged.
- [ ] Anchor and completion IDs differ for every remote task.
- [ ] Every completion/publish node depends on org review, never directly on
  the per-task reviewer.
- [ ] Duplicate materialization creates zero additional cards.
- [ ] `activate=False` leaves every execution card in `triage`.
- [ ] Writer overlap produces a real dependency link.
- [ ] Restart/reconnect recovers topology.
- [ ] `git status --short` is empty.

---

### Task P1-T01: Define and parse `hades.execution-portfolio.v1`

**Files:**
- Create: `hermes_cli/hierarchical_execution.py`.
- Create: `tests/hermes_cli/test_hierarchical_execution.py`.

**Interfaces:**
- Produces: `PortfolioTask`, `ExecutionPortfolio`, `parse_execution_portfolio`.
- Schema: `hades.execution-portfolio.v1`.

- [ ] **Step 1: Write parser tests**

```python
import pytest

from hermes_cli.hierarchical_execution import (
    EXECUTION_PORTFOLIO_SCHEMA,
    parse_execution_portfolio,
)


def valid_payload():
    return {
        "schema": EXECUTION_PORTFOLIO_SCHEMA,
        "org_run_id": "org_demo_001",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "base_commit": "a" * 40,
        "tasks": [{
            "remote_task_id": "HD-101",
            "work_item_id": "awi_101",
            "title": "Change contract",
            "body": "Implement the bounded change.",
            "assignee": "marshal",
            "priority": 10,
            "risk": "high",
            "depends_on": [],
            "write_scope": ["hermes_cli/contracts.py"],
        }],
    }


def test_parse_execution_portfolio():
    plan = parse_execution_portfolio(valid_payload())
    assert plan.org_run_id == "org_demo_001"
    assert plan.tasks[0].remote_task_id == "HD-101"
    assert plan.tasks[0].write_scope == ("hermes_cli/contracts.py",)


def test_rejects_unknown_schema():
    payload = valid_payload()
    payload["schema"] = "invented.v9"
    with pytest.raises(ValueError, match="unsupported portfolio schema"):
        parse_execution_portfolio(payload)
```

- [ ] **Step 2: Verify collection failure**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hierarchical_execution.py -q
```

Expected: module absent.

- [ ] **Step 3: Implement public shapes and parser**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

EXECUTION_PORTFOLIO_SCHEMA = "hades.execution-portfolio.v1"
RISK_LEVELS = frozenset({"low", "medium", "high"})


@dataclass(frozen=True)
class PortfolioTask:
    remote_task_id: str
    work_item_id: str
    title: str
    body: str
    assignee: str
    priority: int
    risk: str
    depends_on: tuple[str, ...]
    write_scope: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionPortfolio:
    schema: str
    org_run_id: str
    project_id: str
    repository_id: str
    workspace_binding_id: str
    base_commit: str
    tasks: tuple[PortfolioTask, ...]


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _scope_path(value: Any) -> str:
    text = _text(value, "write_scope item").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"write_scope must be repository-relative: {text}")
    return path.as_posix()


def parse_execution_portfolio(payload: Mapping[str, Any]) -> ExecutionPortfolio:
    schema = _text(payload.get("schema"), "schema")
    if schema != EXECUTION_PORTFOLIO_SCHEMA:
        raise ValueError(f"unsupported portfolio schema: {schema}")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("tasks must be a non-empty list")
    tasks: list[PortfolioTask] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, Mapping):
            raise ValueError(f"tasks[{index}] must be an object")
        risk = _text(raw.get("risk"), f"tasks[{index}].risk")
        if risk not in RISK_LEVELS:
            raise ValueError(f"tasks[{index}].risk is invalid: {risk}")
        tasks.append(PortfolioTask(
            remote_task_id=_text(raw.get("remote_task_id"), f"tasks[{index}].remote_task_id"),
            work_item_id=_text(raw.get("work_item_id"), f"tasks[{index}].work_item_id"),
            title=_text(raw.get("title"), f"tasks[{index}].title"),
            body=_text(raw.get("body"), f"tasks[{index}].body"),
            assignee=_text(raw.get("assignee"), f"tasks[{index}].assignee"),
            priority=int(raw.get("priority", 0)),
            risk=risk,
            depends_on=tuple(_text(x, "depends_on item") for x in raw.get("depends_on", [])),
            write_scope=tuple(_scope_path(x) for x in raw.get("write_scope", [])),
        ))
    return ExecutionPortfolio(
        schema=schema,
        org_run_id=_text(payload.get("org_run_id"), "org_run_id"),
        project_id=_text(payload.get("project_id"), "project_id"),
        repository_id=_text(payload.get("repository_id"), "repository_id"),
        workspace_binding_id=_text(payload.get("workspace_binding_id"), "workspace_binding_id"),
        base_commit=_text(payload.get("base_commit"), "base_commit"),
        tasks=tuple(tasks),
    )
```

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hierarchical_execution.py -q
git add hermes_cli/hierarchical_execution.py tests/hermes_cli/test_hierarchical_execution.py
git commit -m "feat(hades): parse execution portfolio contract"
```

Expected before commit: exit code `0`.

---

### Task P1-T02: Validate graph and serialize write conflicts

**Files:**
- Modify: `hermes_cli/hierarchical_execution.py`.
- Modify: `tests/hermes_cli/test_hierarchical_execution.py`.

**Interfaces:**
- Produces: `PortfolioValidation`, `validate_execution_portfolio`.

- [ ] **Step 1: Add failing tests**

```python
from dataclasses import replace

from hermes_cli.hierarchical_execution import validate_execution_portfolio


def test_unknown_dependency_is_rejected():
    plan = parse_execution_portfolio(valid_payload())
    broken = replace(plan, tasks=(replace(plan.tasks[0], depends_on=("HD-999",)),))
    with pytest.raises(ValueError, match="unknown dependency HD-999"):
        validate_execution_portfolio(broken)


def test_write_overlap_is_serialized_by_priority_then_id():
    payload = valid_payload()
    payload["tasks"].append({
        "remote_task_id": "HD-102",
        "work_item_id": "awi_102",
        "title": "Second writer",
        "body": "Change the same file.",
        "assignee": "worker",
        "priority": 5,
        "risk": "medium",
        "depends_on": [],
        "write_scope": ["hermes_cli/contracts.py"],
    })
    result = validate_execution_portfolio(parse_execution_portfolio(payload))
    assert result.ordered_dependencies["HD-101"] == ()
    assert result.ordered_dependencies["HD-102"] == ("HD-101",)
    assert result.conflicts == (("HD-101", "HD-102", "hermes_cli/contracts.py"),)
```

- [ ] **Step 2: Verify missing function failure**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hierarchical_execution.py -q
```

Expected: import error for `validate_execution_portfolio`.

- [ ] **Step 3: Implement deterministic validation**

```python
@dataclass(frozen=True)
class PortfolioValidation:
    ordered_dependencies: dict[str, tuple[str, ...]]
    conflicts: tuple[tuple[str, str, str], ...]


def validate_execution_portfolio(plan: ExecutionPortfolio) -> PortfolioValidation:
    by_id: dict[str, PortfolioTask] = {}
    work_items: set[str] = set()
    for task in plan.tasks:
        if task.remote_task_id in by_id:
            raise ValueError(f"duplicate remote_task_id: {task.remote_task_id}")
        if task.work_item_id in work_items:
            raise ValueError(f"duplicate work_item_id: {task.work_item_id}")
        by_id[task.remote_task_id] = task
        work_items.add(task.work_item_id)
    dependencies = {task.remote_task_id: set(task.depends_on) for task in plan.tasks}
    for task_id, parents in dependencies.items():
        for parent in parents:
            if parent not in by_id:
                raise ValueError(f"unknown dependency {parent} for {task_id}")
            if parent == task_id:
                raise ValueError(f"self dependency: {task_id}")
    conflicts: list[tuple[str, str, str]] = []
    ordered = sorted(plan.tasks, key=lambda item: (-item.priority, item.remote_task_id))
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            for resource in sorted(set(first.write_scope) & set(second.write_scope)):
                dependencies[second.remote_task_id].add(first.remote_task_id)
                conflicts.append((first.remote_task_id, second.remote_task_id, resource))
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(f"portfolio dependency cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for parent in sorted(dependencies[task_id]):
            visit(parent)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(by_id):
        visit(task_id)
    return PortfolioValidation(
        ordered_dependencies={key: tuple(sorted(value)) for key, value in sorted(dependencies.items())},
        conflicts=tuple(conflicts),
    )
```

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hierarchical_execution.py -q
git add hermes_cli/hierarchical_execution.py tests/hermes_cli/test_hierarchical_execution.py
git commit -m "feat(hades): validate portfolio dependencies and scopes"
```

Expected before commit: exit code `0`.

---

### Task P0-T03: Gate spawn with `admission_fn`

**Files:**
- Modify: `hermes_cli/kanban_db.py` in `DispatchResult`, `dispatch_once` and `_dispatch_once_locked`.
- Modify: `tests/hermes_cli/test_kanban_dispatch_admission.py`.

**Interfaces:**
- Consumes: `Callable[[Task], DispatchAdmission]`.
- Produces: `admission_fn`, `admission_deferred`, `admission_superseded`.

- [ ] **Step 1: Add failing behavior tests**

```python
def test_defer_releases_claim_and_does_not_spawn(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    spawned: list[str] = []
    try:
        task_id = kb.create_task(conn, title="defer", assignee="worker")
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, workspace: spawned.append(task.id),
            admission_fn=lambda task: kb.DispatchAdmission("defer", "backend offline"),
        )
        assert spawned == []
        assert result.admission_deferred == [(task_id, "backend offline")]
        assert kb.get_task(conn, task_id).status == "ready"
        assert kb.get_task(conn, task_id).consecutive_failures == 0
    finally:
        conn.close()


def test_supersede_blocks_without_counting_failure(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(conn, title="lost", assignee="worker")
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, workspace: 99,
            admission_fn=lambda task: kb.DispatchAdmission("supersede", "claimed elsewhere"),
        )
        assert result.admission_superseded == [(task_id, "claimed elsewhere")]
        task = kb.get_task(conn, task_id)
        assert task.status == "blocked"
        assert task.consecutive_failures == 0
    finally:
        conn.close()
```

- [ ] **Step 2: Verify failure for unknown keyword**

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_dispatch_admission.py -q
```

Expected: FAIL mentioning `admission_fn`.

- [ ] **Step 3: Extend `DispatchResult` and both dispatcher signatures**

```python
admission_deferred: list[tuple[str, str]] = field(default_factory=list)
admission_superseded: list[tuple[str, str]] = field(default_factory=list)
```

Add `admission_fn=None` to `dispatch_once` and `_dispatch_once_locked`; forward
it through every call made by the lock wrapper.

- [ ] **Step 4: Insert the gate immediately after `claim_task`**

```python
        if admission_fn is not None:
            try:
                decision = admission_fn(claimed)
                if not isinstance(decision, DispatchAdmission):
                    raise TypeError("admission_fn must return DispatchAdmission")
            except Exception as exc:
                reason = f"admission callback error: {exc}"
                release_active_claim(
                    conn, claimed.id, target_status="ready", reason=reason
                )
                result.admission_deferred.append((claimed.id, reason))
                continue
            if decision.action == "defer":
                release_active_claim(
                    conn, claimed.id, target_status="ready", reason=decision.reason
                )
                result.admission_deferred.append((claimed.id, decision.reason))
                continue
            if decision.action == "supersede":
                release_active_claim(
                    conn, claimed.id, target_status="blocked", reason=decision.reason
                )
                result.admission_superseded.append((claimed.id, decision.reason))
                continue
```

- [ ] **Step 5: Verify no regression and commit**

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_kanban_dispatch_admission.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/hermes_cli/test_kanban_dispatch_lock.py \
  -q
git add hermes_cli/kanban_db.py tests/hermes_cli/test_kanban_dispatch_admission.py
git commit -m "feat(kanban): gate worker spawn with admission callback"
```

Expected before commit: exit code `0`.

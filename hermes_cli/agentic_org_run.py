"""Atomic local materialization and adoption for simplified OrgRun DAGs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
import time
from typing import Any

from hermes_cli import kanban_db as kb
from hermes_cli.implementation_plan import (
    IMPLEMENTATION_PLAN_SCHEMA,
    ImplementationPlan,
    ImplementationTask,
    PlanValidation,
    canonical_plan_json,
)
from hermes_cli.org_run_store import (
    get_org_run,
    insert_org_node,
    insert_org_run,
    insert_plan_version,
    list_org_nodes,
)


_PLAN_VERSION = 1
_CREATED_BY = "orchestrator"
_REVIEW_SKILLS = ["hierarchical-development"]
_LEGACY_REVIEW_SKILLS = ["requesting-code-review"]
_OPEN_REVIEW_STATUSES = {
    "triage",
    "todo",
    "ready",
    "review",
    "blocked",
    "scheduled",
}


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


@dataclass(frozen=True)
class _NodeSpec:
    node_id: str
    task_id: str
    node_kind: str
    logical_role: str
    task_contract: dict[str, Any]
    dependency_node_ids: tuple[str, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _contract_hash(
    *,
    node_kind: str,
    logical_role: str,
    task_contract: dict[str, Any],
    dependency_node_ids: tuple[str, ...],
    plan_version: int,
    base_commit: str,
) -> str:
    payload = {
        "node_kind": node_kind,
        "logical_role": logical_role,
        "task_contract": task_contract,
        "dependency_node_ids": sorted(dependency_node_ids),
        "plan_version": int(plan_version),
        "base_commit": base_commit,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _task_contract(task: ImplementationTask) -> dict[str, Any]:
    return asdict(task)


def _runnable_workspace_kwargs(board: str | None) -> dict[str, str]:
    board_slug = board if board is not None else kb.get_current_board()
    default_workdir = kb.read_board_metadata(board_slug).get("default_workdir")
    if isinstance(default_workdir, str) and default_workdir.strip():
        return {"workspace_kind": "dir"}
    return {}


def _profile(validation: PlanValidation, logical_role: str) -> str:
    try:
        return validation.resolved_profiles[logical_role]
    except KeyError as exc:
        raise ValueError(
            f"validated plan has no profile for logical role: {logical_role}"
        ) from exc


def _task_node_key(run_id: str, task_id: str) -> str:
    return f"org-run:{run_id}:task:{task_id}"


def _review_required(task: ImplementationTask) -> bool:
    return task.independent_review or task.risk == "high"


def _global_review_required(plan: ImplementationPlan) -> bool:
    return plan.independent_review or any(task.risk == "high" for task in plan.tasks)


def _planned_node_keys(plan: ImplementationPlan) -> tuple[str, ...]:
    keys = [f"org-run:{plan.run_id}:anchor"]
    for task in plan.tasks:
        keys.append(_task_node_key(plan.run_id, task.id))
        if _review_required(task):
            keys.append(f"{_task_node_key(plan.run_id, task.id)}:review")
    keys.append(f"org-run:{plan.run_id}:integration")
    if _global_review_required(plan):
        keys.append(f"org-run:{plan.run_id}:review")
    keys.append(f"org-run:{plan.run_id}:finalize")
    return tuple(keys)


def _reject_unowned_task_keys(
    conn: sqlite3.Connection,
    plan: ImplementationPlan,
) -> None:
    keys = _planned_node_keys(plan)
    placeholders = ",".join("?" for _ in keys)
    collision = conn.execute(
        f"SELECT idempotency_key FROM tasks "
        f"WHERE idempotency_key IN ({placeholders}) LIMIT 1",
        keys,
    ).fetchone()
    if collision is not None:
        raise ValueError(
            "pre-existing OrgRun task key requires explicit adoption: "
            f"{collision['idempotency_key']}"
        )


def _complete_anchor_in_txn(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    summary: str,
    metadata: dict[str, Any],
) -> int:
    """Complete a new anchor without firing observers before outer commit."""
    now = int(time.time())
    changed = conn.execute(
        "UPDATE tasks SET status='done', completed_at=?, claim_lock=NULL, "
        "claim_expires=NULL, worker_pid=NULL, block_kind=NULL, "
        "block_recurrences=0 WHERE id=? AND status='ready'",
        (now, task_id),
    )
    if changed.rowcount != 1:
        raise ValueError(f"new OrgRun anchor is not ready: {task_id}")
    run_id = kb._synthesize_ended_run(
        conn,
        task_id,
        outcome="completed",
        summary=summary,
        metadata=metadata,
    )
    kb._append_event(
        conn,
        task_id,
        "completed",
        {"result_len": 0, "summary": summary},
        run_id=run_id,
    )
    return run_id


def materialize_org_run(
    conn: sqlite3.Connection,
    plan: ImplementationPlan,
    validation: PlanValidation,
    *,
    board: str | None,
    activate: bool = True,
) -> OrgRunTopology:
    """Materialize a simplified OrgRun DAG in one SQLite transaction."""
    with kb.write_txn(conn):
        existing = get_org_run(conn, plan.run_id)
        if existing is not None:
            if existing.plan_hash != validation.plan_hash:
                raise ValueError(
                    f"OrgRun {plan.run_id} is already bound to a different plan hash"
                )
            topology = load_org_run_topology(conn, plan.run_id)
            if topology is None:
                raise ValueError(
                    f"OrgRun {plan.run_id} has incomplete stored topology"
                )
            return topology

        _reject_unowned_task_keys(conn, plan)
        runnable_kwargs = _runnable_workspace_kwargs(board)
        anchor_node_id = f"org-run:{plan.run_id}:anchor"
        anchor_id = kb.create_task(
            conn,
            title=f"OrgRun: {plan.run_id}",
            body=(
                "Local Agentic-Kanban OrgRun anchor.\n\n"
                f"Objective: {plan.objective}\nBase commit: {plan.base_commit}"
            ),
            assignee=_profile(validation, "orchestrator"),
            created_by=_CREATED_BY,
            idempotency_key=anchor_node_id,
            board=board,
        )
        anchor_summary = "Local OrgRun plan accepted for materialization."
        anchor_run_id = _complete_anchor_in_txn(
            conn,
            anchor_id,
            summary=anchor_summary,
            metadata={
                "kind": "agentic_org_run_anchor_v1",
                "run_id": plan.run_id,
                "base_commit": plan.base_commit,
            },
        )

        node_specs: list[_NodeSpec] = [
            _NodeSpec(
                node_id=anchor_node_id,
                task_id=anchor_id,
                node_kind="anchor",
                logical_role="orchestrator",
                task_contract={
                    "objective": plan.objective,
                    "acceptance_criteria": list(plan.acceptance_criteria),
                },
                dependency_node_ids=(),
            )
        ]
        tasks_by_id = {task.id: task for task in plan.tasks}
        execution_ids: dict[str, str] = {}
        review_ids: dict[str, str] = {}

        for task in plan.tasks:
            node_id = _task_node_key(plan.run_id, task.id)
            execution_ids[task.id] = kb.create_task(
                conn,
                title=task.title,
                body=(
                    f"{plan.objective}\n\n"
                    f"Acceptance criteria:\n- "
                    + "\n- ".join(task.acceptance_criteria)
                    + "\n\nVerification:\n- "
                    + "\n- ".join(task.verification)
                ),
                assignee=_profile(validation, task.role),
                created_by=_CREATED_BY,
                parents=[anchor_id],
                triage=not activate,
                idempotency_key=node_id,
                board=board,
                **runnable_kwargs,
            )

        for task in plan.tasks:
            if not _review_required(task):
                continue
            node_id = f"{_task_node_key(plan.run_id, task.id)}:review"
            review_ids[task.id] = kb.create_task(
                conn,
                title=f"Review: {task.title}",
                body=(
                    "Independently verify this implementation task, its declared "
                    "scope, acceptance criteria, and focused test evidence."
                ),
                assignee=_profile(validation, "reviewer"),
                created_by=_CREATED_BY,
                parents=[execution_ids[task.id]],
                skills=_REVIEW_SKILLS,
                idempotency_key=node_id,
                board=board,
                **runnable_kwargs,
            )

        terminal_task_ids = {
            task.id: review_ids.get(task.id, execution_ids[task.id])
            for task in plan.tasks
        }
        terminal_node_ids = {
            task.id: (
                f"{_task_node_key(plan.run_id, task.id)}:review"
                if task.id in review_ids
                else _task_node_key(plan.run_id, task.id)
            )
            for task in plan.tasks
        }
        for task in plan.tasks:
            dependency_node_ids = tuple(
                terminal_node_ids[parent_id]
                for parent_id in validation.ordered_dependencies.get(task.id, ())
            )
            for parent_id in validation.ordered_dependencies.get(task.id, ()):
                if parent_id not in tasks_by_id:
                    raise ValueError(
                        f"validated dependency {parent_id} is not in the plan"
                    )
                kb.link_tasks(
                    conn,
                    terminal_task_ids[parent_id],
                    execution_ids[task.id],
                )
            node_specs.append(
                _NodeSpec(
                    node_id=_task_node_key(plan.run_id, task.id),
                    task_id=execution_ids[task.id],
                    node_kind="execution",
                    logical_role=task.role,
                    task_contract=_task_contract(task),
                    dependency_node_ids=(anchor_node_id, *dependency_node_ids),
                )
            )
            if task.id in review_ids:
                node_specs.append(
                    _NodeSpec(
                        node_id=f"{_task_node_key(plan.run_id, task.id)}:review",
                        task_id=review_ids[task.id],
                        node_kind="task_review",
                        logical_role="reviewer",
                        task_contract={
                            "task_id": task.id,
                            "acceptance_criteria": list(task.acceptance_criteria),
                            "verification": list(task.verification),
                        },
                        dependency_node_ids=(
                            _task_node_key(plan.run_id, task.id),
                        ),
                    )
                )

        integration_node_id = f"org-run:{plan.run_id}:integration"
        integration_id = kb.create_task(
            conn,
            title=f"Integrate OrgRun {plan.run_id}",
            body=(
                "Integrate accepted task results in dependency order and verify "
                "the complete local objective."
            ),
            assignee=_profile(validation, "orchestrator"),
            created_by=_CREATED_BY,
            parents=[terminal_task_ids[key] for key in sorted(terminal_task_ids)],
            idempotency_key=integration_node_id,
            board=board,
            **runnable_kwargs,
        )
        integration_dependencies = tuple(
            terminal_node_ids[key] for key in sorted(terminal_node_ids)
        )
        node_specs.append(
            _NodeSpec(
                node_id=integration_node_id,
                task_id=integration_id,
                node_kind="integration",
                logical_role="orchestrator",
                task_contract={
                    "objective": plan.objective,
                    "acceptance_criteria": list(plan.acceptance_criteria),
                },
                dependency_node_ids=integration_dependencies,
            )
        )

        review_id: str | None = None
        final_parent_id = integration_id
        final_parent_node_id = integration_node_id
        if _global_review_required(plan):
            review_node_id = f"org-run:{plan.run_id}:review"
            review_id = kb.create_task(
                conn,
                title=f"Review integrated OrgRun {plan.run_id}",
                body=(
                    "Independently verify the integrated objective, acceptance "
                    "criteria, and regression evidence."
                ),
                assignee=_profile(validation, "reviewer"),
                created_by=_CREATED_BY,
                parents=[integration_id],
                skills=_REVIEW_SKILLS,
                idempotency_key=review_node_id,
                board=board,
                **runnable_kwargs,
            )
            node_specs.append(
                _NodeSpec(
                    node_id=review_node_id,
                    task_id=review_id,
                    node_kind="global_review",
                    logical_role="reviewer",
                    task_contract={
                        "objective": plan.objective,
                        "acceptance_criteria": list(plan.acceptance_criteria),
                    },
                    dependency_node_ids=(integration_node_id,),
                )
            )
            final_parent_id = review_id
            final_parent_node_id = review_node_id

        finalization_node_id = f"org-run:{plan.run_id}:finalize"
        finalization_id = kb.create_task(
            conn,
            title=f"Finalize OrgRun {plan.run_id}",
            body="Summarize verified outcomes, residual risks, and local evidence.",
            assignee=_profile(validation, "orchestrator"),
            created_by=_CREATED_BY,
            parents=[final_parent_id],
            idempotency_key=finalization_node_id,
            board=board,
            **runnable_kwargs,
        )
        node_specs.append(
            _NodeSpec(
                node_id=finalization_node_id,
                task_id=finalization_id,
                node_kind="finalization",
                logical_role="orchestrator",
                task_contract={
                    "objective": plan.objective,
                    "acceptance_criteria": list(plan.acceptance_criteria),
                },
                dependency_node_ids=(final_parent_node_id,),
            )
        )

        board_slug = board if board is not None else kb.get_current_board()
        insert_org_run(
            conn,
            run_id=plan.run_id,
            board_slug=board_slug,
            plan_version=_PLAN_VERSION,
            plan_hash=validation.plan_hash,
            base_commit=plan.base_commit,
            origin=plan.origin,
            state="running" if activate else "materialized",
            anchor_task_id=anchor_id,
        )
        insert_plan_version(
            conn,
            run_id=plan.run_id,
            plan_version=_PLAN_VERSION,
            plan_hash=validation.plan_hash,
            plan_json=canonical_plan_json(plan),
            reason="initial local materialization",
        )
        for spec in node_specs:
            insert_org_node(
                conn,
                run_id=plan.run_id,
                node_id=spec.node_id,
                task_id=spec.task_id,
                node_kind=spec.node_kind,
                plan_version=_PLAN_VERSION,
                contract_hash=_contract_hash(
                    node_kind=spec.node_kind,
                    logical_role=spec.logical_role,
                    task_contract=spec.task_contract,
                    dependency_node_ids=spec.dependency_node_ids,
                    plan_version=_PLAN_VERSION,
                    base_commit=plan.base_commit,
                ),
                logical_role=spec.logical_role,
            )

        topology = OrgRunTopology(
            run_id=plan.run_id,
            anchor_id=anchor_id,
            tasks={
                task.id: TaskNodeTopology(
                    execution_id=execution_ids[task.id],
                    review_id=review_ids.get(task.id),
                )
                for task in plan.tasks
            },
            integration_id=integration_id,
            review_id=review_id,
            finalization_id=finalization_id,
        )
    kb._fire_kanban_lifecycle_hook(
        "kanban_task_completed",
        anchor_id,
        board=board if board is not None else kb.get_current_board(),
        assignee=_profile(validation, "orchestrator"),
        run_id=anchor_run_id,
        summary=anchor_summary,
    )
    return topology


def _logical_task_id(run_id: str, node_id: str, node_kind: str) -> str | None:
    new_prefix = f"org-run:{run_id}:task:"
    legacy_prefix = f"org-run:{run_id}:"
    if node_kind == "execution":
        if node_id.startswith(new_prefix):
            return node_id[len(new_prefix):]
        if node_id.startswith(legacy_prefix) and node_id.endswith(":execute"):
            return node_id[len(legacy_prefix):-len(":execute")]
    if node_kind == "task_review":
        if node_id.startswith(new_prefix) and node_id.endswith(":review"):
            return node_id[len(new_prefix):-len(":review")]
        if node_id.startswith(legacy_prefix) and node_id.endswith(":review"):
            return node_id[len(legacy_prefix):-len(":review")]
    return None


def _validate_stored_node_provenance(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    plan_version: int,
    plan_hash: str,
    nodes: list,
) -> None:
    stored = conn.execute(
        "SELECT plan_hash, plan_json FROM kanban_org_plan_versions "
        "WHERE run_id=? AND plan_version=?",
        (run_id, int(plan_version)),
    ).fetchone()
    if stored is None or stored["plan_hash"] != plan_hash:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
    try:
        plan_payload = json.loads(stored["plan_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"OrgRun {run_id} has incomplete stored topology"
        ) from exc

    expected_node_ids: set[str]
    expected_records: set[tuple[str, str, str, str]] | None = None
    if plan_payload.get("schema") == IMPLEMENTATION_PLAN_SCHEMA:
        expected_node_ids = {f"org-run:{run_id}:anchor"}
        tasks = plan_payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
        for task in tasks:
            if not isinstance(task, dict) or not task.get("id"):
                raise ValueError(
                    f"OrgRun {run_id} has incomplete stored topology"
                )
            task_key = _task_node_key(run_id, str(task["id"]))
            expected_node_ids.add(task_key)
            if bool(task.get("independent_review")) or task.get("risk") == "high":
                expected_node_ids.add(f"{task_key}:review")
        expected_node_ids.add(f"org-run:{run_id}:integration")
        if bool(plan_payload.get("independent_review")) or any(
            isinstance(task, dict) and task.get("risk") == "high"
            for task in tasks
        ):
            expected_node_ids.add(f"org-run:{run_id}:review")
        expected_node_ids.add(f"org-run:{run_id}:finalize")
    elif plan_payload.get("schema") == "hades.legacy-org-run-adoption.v1":
        raw_nodes = plan_payload.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
        try:
            expected_records = {
                (
                    str(node["node_id"]),
                    str(node["task_id"]),
                    str(node["node_kind"]),
                    str(node["logical_role"]),
                )
                for node in raw_nodes
            }
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"OrgRun {run_id} has incomplete stored topology"
            ) from exc
        expected_node_ids = {record[0] for record in expected_records}
    else:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")

    actual_node_ids = {node.node_id for node in nodes}
    if actual_node_ids != expected_node_ids:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
    if expected_records is not None:
        actual_records = {
            (node.node_id, node.task_id, node.node_kind, node.logical_role)
            for node in nodes
        }
        if actual_records != expected_records:
            raise ValueError(f"OrgRun {run_id} has incomplete stored topology")


def load_org_run_topology(
    conn: sqlite3.Connection,
    run_id: str,
) -> OrgRunTopology | None:
    """Load a stored simplified topology without consulting backend state."""
    run = get_org_run(conn, run_id)
    if run is None:
        return None
    nodes = list_org_nodes(conn, run_id)
    _validate_stored_node_provenance(
        conn,
        run_id,
        plan_version=run.plan_version,
        plan_hash=run.plan_hash,
        nodes=nodes,
    )
    executions: dict[str, str] = {}
    reviews: dict[str, str] = {}
    integration_id: str | None = None
    review_id: str | None = None
    finalization_id: str | None = None
    anchor_task_ids: list[str] = []
    for node in nodes:
        logical_task_id = _logical_task_id(run_id, node.node_id, node.node_kind)
        if node.node_kind == "anchor":
            anchor_task_ids.append(node.task_id)
        elif node.node_kind == "execution" and logical_task_id is not None:
            executions[logical_task_id] = node.task_id
        elif node.node_kind == "task_review" and logical_task_id is not None:
            reviews[logical_task_id] = node.task_id
        elif node.node_kind == "integration":
            integration_id = node.task_id
        elif node.node_kind == "global_review":
            review_id = node.task_id
        elif node.node_kind == "finalization":
            finalization_id = node.task_id
    if (
        anchor_task_ids != [run.anchor_task_id]
        or integration_id is None
        or finalization_id is None
        or not executions
    ):
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
    return OrgRunTopology(
        run_id=run_id,
        anchor_id=run.anchor_task_id,
        tasks={
            task_id: TaskNodeTopology(
                execution_id=execution_id,
                review_id=reviews.get(task_id),
            )
            for task_id, execution_id in sorted(executions.items())
        },
        integration_id=integration_id,
        review_id=review_id,
        finalization_id=finalization_id,
    )


def is_org_run_task(conn: sqlite3.Connection, task_id: str) -> bool:
    """Return whether a Kanban card belongs to a stored OrgRun topology."""
    return conn.execute(
        "SELECT 1 FROM kanban_org_nodes WHERE task_id = ? LIMIT 1",
        (task_id,),
    ).fetchone() is not None


def _legacy_task_rows(
    conn: sqlite3.Connection,
    run_id: str,
) -> dict[str, sqlite3.Row]:
    prefix = f"org-run:{run_id}:"
    rows = conn.execute(
        "SELECT * FROM tasks WHERE idempotency_key IS NOT NULL"
    ).fetchall()
    return {
        str(row["idempotency_key"]): row
        for row in rows
        if str(row["idempotency_key"]).startswith(prefix)
    }


def _required_legacy_row(
    rows: dict[str, sqlite3.Row],
    key: str,
) -> sqlite3.Row:
    try:
        return rows[key]
    except KeyError as exc:
        raise ValueError(f"legacy OrgRun is missing card: {key}") from exc


def _repair_legacy_reviews(
    conn: sqlite3.Connection,
    review_rows: list[sqlite3.Row],
) -> None:
    for row in review_rows:
        try:
            skills = json.loads(row["skills"]) if row["skills"] else None
        except (TypeError, ValueError):
            skills = None
        if (
            row["status"] not in _OPEN_REVIEW_STATUSES
            or row["assignee"] != "default"
            or skills != _LEGACY_REVIEW_SKILLS
        ):
            continue
        conn.execute(
            "UPDATE tasks SET assignee='reviewer', skills=?, "
            "consecutive_failures=0, last_failure_error=NULL WHERE id=?",
            (json.dumps(_REVIEW_SKILLS), row["id"]),
        )
        kb._append_event(
            conn,
            row["id"],
            "contract_repaired",
            {
                "reason": "adopted_review_requires_durable_reviewer_route",
                "assignee": "reviewer",
                "skills": _REVIEW_SKILLS,
            },
        )


def _legacy_base_commit(anchor: sqlite3.Row) -> str:
    match = re.search(r"^Base commit:\s*(\S+)\s*$", anchor["body"] or "", re.MULTILINE)
    return match.group(1) if match else "legacy"


def _legacy_contract(row: sqlite3.Row) -> dict[str, Any]:
    try:
        skills = json.loads(row["skills"]) if row["skills"] else None
    except (TypeError, ValueError):
        skills = None
    return {
        "title": row["title"],
        "body": row["body"],
        "skills": skills,
    }


def adopt_legacy_org_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    board: str | None,
) -> OrgRunTopology:
    """Adopt legacy OrgRun cards in place using only stable task keys."""
    with kb.write_txn(conn):
        existing = get_org_run(conn, run_id)
        if existing is not None:
            topology = load_org_run_topology(conn, run_id)
            if topology is None:
                raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
            return topology

        rows = _legacy_task_rows(conn, run_id)
        anchor_key = f"org-run:{run_id}:anchor"
        integration_key = f"org-run:{run_id}:integration"
        global_review_key = f"org-run:{run_id}:org-review"
        finalization_key = f"org-run:{run_id}:synthesis"
        anchor = _required_legacy_row(rows, anchor_key)
        integration = _required_legacy_row(rows, integration_key)
        global_review = _required_legacy_row(rows, global_review_key)
        finalization = _required_legacy_row(rows, finalization_key)

        legacy_prefix = f"org-run:{run_id}:"
        task_ids: list[str] = []
        for ready_task_id in kb.parent_ids(conn, integration["id"]):
            ready_row = conn.execute(
                "SELECT idempotency_key FROM tasks WHERE id=?",
                (ready_task_id,),
            ).fetchone()
            ready_key = (
                str(ready_row["idempotency_key"])
                if ready_row is not None and ready_row["idempotency_key"]
                else ""
            )
            if not (
                ready_key.startswith(legacy_prefix)
                and ready_key.endswith(":ready")
            ):
                raise ValueError(
                    "legacy OrgRun integration has a parent without a stable "
                    f"ready key: {ready_task_id}"
                )
            task_ids.append(
                ready_key[len(legacy_prefix):-len(":ready")]
            )
        task_ids.sort()
        if not task_ids:
            raise ValueError(f"legacy OrgRun {run_id} has no execution cards")

        task_rows: dict[str, dict[str, sqlite3.Row]] = {}
        for task_id in task_ids:
            task_rows[task_id] = {
                kind: _required_legacy_row(
                    rows,
                    f"org-run:{run_id}:{task_id}:{suffix}",
                )
                for kind, suffix in (
                    ("execution", "execute"),
                    ("review", "review"),
                    ("ready", "ready"),
                    ("complete", "complete"),
                )
            }
        _repair_legacy_reviews(
            conn,
            [
                *(task["review"] for task in task_rows.values()),
                global_review,
            ],
        )
        repaired_rows = _legacy_task_rows(conn, run_id)
        global_review = _required_legacy_row(repaired_rows, global_review_key)
        for task_id, task in task_rows.items():
            task["review"] = _required_legacy_row(
                repaired_rows,
                f"org-run:{run_id}:{task_id}:review",
            )

        node_specs = [
            _NodeSpec(
                anchor_key,
                anchor["id"],
                "anchor",
                "orchestrator",
                _legacy_contract(anchor),
                (),
            )
        ]
        for task_id, task in task_rows.items():
            execution_key = f"org-run:{run_id}:{task_id}:execute"
            review_key = f"org-run:{run_id}:{task_id}:review"
            ready_key = f"org-run:{run_id}:{task_id}:ready"
            complete_key = f"org-run:{run_id}:{task_id}:complete"
            node_specs.extend(
                [
                    _NodeSpec(
                        execution_key,
                        task["execution"]["id"],
                        "execution",
                        "leaf",
                        _legacy_contract(task["execution"]),
                        (),
                    ),
                    _NodeSpec(
                        review_key,
                        task["review"]["id"],
                        "task_review",
                        "reviewer",
                        _legacy_contract(task["review"]),
                        (execution_key,),
                    ),
                    _NodeSpec(
                        ready_key,
                        task["ready"]["id"],
                        "legacy_gate",
                        "orchestrator",
                        _legacy_contract(task["ready"]),
                        (review_key,),
                    ),
                    _NodeSpec(
                        complete_key,
                        task["complete"]["id"],
                        "legacy_gate",
                        "orchestrator",
                        _legacy_contract(task["complete"]),
                        (global_review_key,),
                    ),
                ]
            )
        ready_keys = tuple(
            f"org-run:{run_id}:{task_id}:ready" for task_id in task_ids
        )
        complete_keys = tuple(
            f"org-run:{run_id}:{task_id}:complete" for task_id in task_ids
        )
        node_specs.extend(
            [
                _NodeSpec(
                    integration_key,
                    integration["id"],
                    "integration",
                    "orchestrator",
                    _legacy_contract(integration),
                    ready_keys,
                ),
                _NodeSpec(
                    global_review_key,
                    global_review["id"],
                    "global_review",
                    "reviewer",
                    _legacy_contract(global_review),
                    (integration_key,),
                ),
                _NodeSpec(
                    finalization_key,
                    finalization["id"],
                    "finalization",
                    "orchestrator",
                    _legacy_contract(finalization),
                    complete_keys,
                ),
            ]
        )
        base_commit = _legacy_base_commit(anchor)
        adopted_contract = {
            "schema": "hades.legacy-org-run-adoption.v1",
            "run_id": run_id,
            "base_commit": base_commit,
            "nodes": [
                {
                    "node_id": spec.node_id,
                    "task_id": spec.task_id,
                    "node_kind": spec.node_kind,
                    "logical_role": spec.logical_role,
                }
                for spec in node_specs
            ],
        }
        adopted_json = _canonical_json(adopted_contract)
        plan_hash = hashlib.sha256(adopted_json.encode("utf-8")).hexdigest()
        board_slug = board if board is not None else kb.get_current_board()
        insert_org_run(
            conn,
            run_id=run_id,
            board_slug=board_slug,
            plan_version=_PLAN_VERSION,
            plan_hash=plan_hash,
            base_commit=base_commit,
            origin="backend",
            state="running",
            anchor_task_id=anchor["id"],
        )
        insert_plan_version(
            conn,
            run_id=run_id,
            plan_version=_PLAN_VERSION,
            plan_hash=plan_hash,
            plan_json=adopted_json,
            reason="adopted legacy OrgRun cards",
        )
        for spec in node_specs:
            insert_org_node(
                conn,
                run_id=run_id,
                node_id=spec.node_id,
                task_id=spec.task_id,
                node_kind=spec.node_kind,
                plan_version=_PLAN_VERSION,
                contract_hash=_contract_hash(
                    node_kind=spec.node_kind,
                    logical_role=spec.logical_role,
                    task_contract=spec.task_contract,
                    dependency_node_ids=spec.dependency_node_ids,
                    plan_version=_PLAN_VERSION,
                    base_commit=base_commit,
                ),
                logical_role=spec.logical_role,
            )

        topology = load_org_run_topology(conn, run_id)
        if topology is None:
            raise ValueError(f"legacy OrgRun {run_id} adoption was incomplete")
        return topology

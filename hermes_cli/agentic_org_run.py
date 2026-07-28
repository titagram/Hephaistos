"""Atomic local materialization and adoption for simplified OrgRun DAGs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import itertools
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable

from hermes_cli import kanban_db as kb
from hermes_cli.implementation_plan import (
    IMPLEMENTATION_PLAN_SCHEMA,
    ImplementationAmendment,
    ImplementationPlan,
    ImplementationTask,
    PlanValidation,
    canonical_plan_json,
    parse_implementation_plan,
    validate_implementation_plan,
    verify_plan_validation,
)
from hermes_cli.org_run_store import (
    get_org_run,
    insert_org_node,
    insert_org_run,
    insert_plan_version,
    list_org_nodes,
    refresh_org_run_state,
    set_org_nodes_state,
    update_org_node_contract,
    update_org_run_plan,
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


def _execution_body(plan: ImplementationPlan, task: ImplementationTask) -> str:
    return (
        f"{plan.objective}\n\n"
        f"Acceptance criteria:\n- "
        + "\n- ".join(task.acceptance_criteria)
        + "\n\nVerification:\n- "
        + "\n- ".join(task.verification)
    )


def _task_review_body() -> str:
    return (
        "Independently verify this implementation task, its declared "
        "scope, acceptance criteria, and focused test evidence."
    )


def _integration_body() -> str:
    return (
        "Integrate accepted task results in dependency order and verify "
        "the complete local objective."
    )


def _global_review_body() -> str:
    return (
        "Independently verify the integrated objective, acceptance "
        "criteria, and regression evidence."
    )


def _expected_live_task_fields(
    plan: ImplementationPlan,
    spec: _NodeSpec,
) -> tuple[str, str | None, list[str] | None]:
    """Return the exact title/body/skills rendered for one current-plan node."""
    tasks = {task.id: task for task in plan.tasks}
    logical_task_id = _logical_task_id(
        plan.run_id,
        spec.node_id,
        spec.node_kind,
    )
    if spec.node_kind == "anchor":
        return (
            f"OrgRun: {plan.run_id}",
            "Local Agentic-Kanban OrgRun anchor.\n\n"
            f"Objective: {plan.objective}\nBase commit: {plan.base_commit}",
            None,
        )
    if spec.node_kind == "execution" and logical_task_id is not None:
        task = tasks[logical_task_id]
        return task.title, _execution_body(plan, task), None
    if spec.node_kind == "task_review" and logical_task_id is not None:
        return (
            f"Review: {tasks[logical_task_id].title}",
            _task_review_body(),
            list(_REVIEW_SKILLS),
        )
    if spec.node_kind == "integration":
        return (
            f"Integrate OrgRun {plan.run_id}",
            _integration_body(),
            None,
        )
    if spec.node_kind == "global_review":
        return (
            f"Review integrated OrgRun {plan.run_id}",
            _global_review_body(),
            list(_REVIEW_SKILLS),
        )
    if spec.node_kind == "finalization":
        return (
            f"Finalize OrgRun {plan.run_id}",
            "Summarize verified outcomes, residual risks, and local evidence.",
            None,
        )
    raise ValueError(
        f"OrgRun {plan.run_id} has unsupported managed node: {spec.node_id}"
    )


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


def _expected_plan_node_roles(
    plan: ImplementationPlan,
) -> tuple[tuple[str, str, str], ...]:
    return (
        (f"org-run:{plan.run_id}:anchor", "anchor", "orchestrator"),
        *(
            (
                _task_node_key(plan.run_id, task.id),
                "execution",
                task.role,
            )
            for task in plan.tasks
        ),
        *(
            (
                f"{_task_node_key(plan.run_id, task.id)}:review",
                "task_review",
                "reviewer",
            )
            for task in plan.tasks
            if _review_required(task)
        ),
        (
            f"org-run:{plan.run_id}:integration",
            "integration",
            "orchestrator",
        ),
        *(
            [
                (
                    f"org-run:{plan.run_id}:review",
                    "global_review",
                    "reviewer",
                )
            ]
            if _global_review_required(plan)
            else []
        ),
        (
            f"org-run:{plan.run_id}:finalize",
            "finalization",
            "orchestrator",
        ),
    )


def _ordered_plan_dependencies(
    plan: ImplementationPlan,
) -> dict[str, tuple[str, ...]]:
    tasks = {task.id: task for task in plan.tasks}
    dependencies = {
        task.id: set(task.depends_on)
        for task in plan.tasks
    }
    for first_id, second_id in itertools.combinations(sorted(tasks), 2):
        if set(tasks[first_id].write_scope) & set(tasks[second_id].write_scope):
            dependencies[second_id].add(first_id)
    return {
        task_id: tuple(sorted(parent_ids))
        for task_id, parent_ids in dependencies.items()
    }


def _expected_plan_node_specs(plan: ImplementationPlan) -> dict[str, _NodeSpec]:
    anchor_node_id = f"org-run:{plan.run_id}:anchor"
    specs = [
        _NodeSpec(
            node_id=anchor_node_id,
            task_id="",
            node_kind="anchor",
            logical_role="orchestrator",
            task_contract={
                "objective": plan.objective,
                "acceptance_criteria": list(plan.acceptance_criteria),
            },
            dependency_node_ids=(),
        )
    ]
    terminal_node_ids = {
        task.id: (
            f"{_task_node_key(plan.run_id, task.id)}:review"
            if _review_required(task)
            else _task_node_key(plan.run_id, task.id)
        )
        for task in plan.tasks
    }
    dependencies = _ordered_plan_dependencies(plan)
    for task in plan.tasks:
        execution_node_id = _task_node_key(plan.run_id, task.id)
        specs.append(
            _NodeSpec(
                node_id=execution_node_id,
                task_id="",
                node_kind="execution",
                logical_role=task.role,
                task_contract=_task_contract(task),
                dependency_node_ids=(
                    anchor_node_id,
                    *(
                        terminal_node_ids[parent_id]
                        for parent_id in dependencies[task.id]
                    ),
                ),
            )
        )
        if _review_required(task):
            specs.append(
                _NodeSpec(
                    node_id=f"{execution_node_id}:review",
                    task_id="",
                    node_kind="task_review",
                    logical_role="reviewer",
                    task_contract={
                        "task_id": task.id,
                        "acceptance_criteria": list(task.acceptance_criteria),
                        "verification": list(task.verification),
                    },
                    dependency_node_ids=(execution_node_id,),
                )
            )
    integration_node_id = f"org-run:{plan.run_id}:integration"
    specs.append(
        _NodeSpec(
            node_id=integration_node_id,
            task_id="",
            node_kind="integration",
            logical_role="orchestrator",
            task_contract={
                "objective": plan.objective,
                "acceptance_criteria": list(plan.acceptance_criteria),
            },
            dependency_node_ids=tuple(
                terminal_node_ids[task_id]
                for task_id in sorted(terminal_node_ids)
            ),
        )
    )
    final_parent_node_id = integration_node_id
    if _global_review_required(plan):
        review_node_id = f"org-run:{plan.run_id}:review"
        specs.append(
            _NodeSpec(
                node_id=review_node_id,
                task_id="",
                node_kind="global_review",
                logical_role="reviewer",
                task_contract={
                    "objective": plan.objective,
                    "acceptance_criteria": list(plan.acceptance_criteria),
                },
                dependency_node_ids=(integration_node_id,),
            )
        )
        final_parent_node_id = review_node_id
    finalization_node_id = f"org-run:{plan.run_id}:finalize"
    specs.append(
        _NodeSpec(
            node_id=finalization_node_id,
            task_id="",
            node_kind="finalization",
            logical_role="orchestrator",
            task_contract={
                "objective": plan.objective,
                "acceptance_criteria": list(plan.acceptance_criteria),
            },
            dependency_node_ids=(final_parent_node_id,),
        )
    )
    return {spec.node_id: spec for spec in specs}


def _effective_spec_key(spec: _NodeSpec) -> tuple[Any, ...]:
    return (
        spec.node_kind,
        spec.logical_role,
        _canonical_json(spec.task_contract),
        tuple(sorted(spec.dependency_node_ids)),
    )


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
    """Materialize a simplified OrgRun DAG in one owned SQLite transaction."""
    if conn.in_transaction:
        raise ValueError(
            "materialize_org_run cannot run inside an existing transaction"
        )
    verify_plan_validation(plan, validation)
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
                body=_execution_body(plan, task),
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
                body=_task_review_body(),
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
            body=_integration_body(),
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
                body=_global_review_body(),
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
            state="materialized",
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


def _verified_plan_version(
    conn: sqlite3.Connection,
    run_id: str,
    plan_version: int,
) -> tuple[dict[str, Any], str]:
    row = conn.execute(
        "SELECT plan_hash, plan_json FROM kanban_org_plan_versions "
        "WHERE run_id = ? AND plan_version = ?",
        (run_id, int(plan_version)),
    ).fetchone()
    if row is None:
        raise ValueError(f"OrgRun {run_id} has no plan version {plan_version}")
    try:
        payload = json.loads(row["plan_json"])
        if not isinstance(payload, dict):
            raise ValueError("stored plan must be an object")
        if payload.get("schema") == IMPLEMENTATION_PLAN_SCHEMA:
            canonical_json = canonical_plan_json(
                parse_implementation_plan(payload)
            )
        elif payload.get("schema") == "hades.legacy-org-run-adoption.v1":
            canonical_json = _canonical_json(payload)
        else:
            raise ValueError("unsupported stored plan schema")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"OrgRun {run_id} has invalid plan version {plan_version}"
        ) from exc
    expected_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if row["plan_json"] != canonical_json or row["plan_hash"] != expected_hash:
        raise ValueError(f"OrgRun {run_id} has invalid plan version {plan_version}")
    return payload, expected_hash


def _load_plan_version(
    conn: sqlite3.Connection,
    run_id: str,
    plan_version: int,
) -> ImplementationPlan:
    payload, _plan_hash = _verified_plan_version(conn, run_id, plan_version)
    try:
        plan = parse_implementation_plan(payload)
    except ValueError as exc:
        raise ValueError(
            f"OrgRun {run_id} has invalid plan version {plan_version}"
        ) from exc
    if plan.run_id != run_id:
        raise ValueError(f"OrgRun {run_id} has invalid stored plan identity")
    return plan


def _apply_amendment_to_plan(
    plan: ImplementationPlan,
    amendment: ImplementationAmendment,
) -> ImplementationPlan:
    if amendment.run_id != plan.run_id:
        raise ValueError(
            f"amendment run_id {amendment.run_id} does not match {plan.run_id}"
        )
    current_ids = {task.id for task in plan.tasks}
    replacements = {
        replacement.replaces: replacement.task
        for replacement in amendment.replace_tasks
    }
    cancelled = set(amendment.cancel_task_ids)
    targets = set(replacements) | cancelled
    missing = sorted(targets - current_ids)
    if missing:
        raise ValueError(f"unknown amendment target: {', '.join(missing)}")

    replacement_ids = {
        old_id: task.id for old_id, task in replacements.items()
    }

    def rewrite_dependencies(task: ImplementationTask) -> ImplementationTask:
        dependencies: list[str] = []
        for dependency in task.depends_on:
            if dependency in cancelled:
                continue
            rewritten = replacement_ids.get(dependency, dependency)
            if rewritten not in dependencies:
                dependencies.append(rewritten)
        return replace(task, depends_on=tuple(dependencies))

    tasks: list[ImplementationTask] = []
    for task in plan.tasks:
        if task.id in cancelled:
            continue
        replacement = replacements.get(task.id)
        tasks.append(rewrite_dependencies(replacement or task))
    tasks.extend(rewrite_dependencies(task) for task in amendment.add_tasks)
    if not tasks:
        raise ValueError("implementation amendment cannot remove every task")
    return replace(plan, tasks=tuple(tasks))


def _amendment_operation_hash(amendment: ImplementationAmendment) -> str:
    payload = asdict(amendment)
    payload.pop("reason")
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _amendment_event_matches(
    conn: sqlite3.Connection,
    *,
    anchor_task_id: str,
    plan_version: int,
    amendment: ImplementationAmendment,
) -> bool:
    rows = conn.execute(
        "SELECT payload FROM task_events "
        "WHERE task_id = ? AND kind = 'org_run_amended' "
        "ORDER BY id DESC",
        (anchor_task_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if payload.get("plan_version") != plan_version:
            continue
        return (
            payload.get("base_plan_version") == amendment.base_plan_version
            and payload.get("reason") == amendment.reason
            and payload.get("operation_hash")
            == _amendment_operation_hash(amendment)
        )
    return False


def _amendment_replay_topology(
    conn: sqlite3.Connection,
    run,
    amendment: ImplementationAmendment,
) -> OrgRunTopology | None:
    if run.plan_version != amendment.base_plan_version + 1:
        return None
    try:
        base_plan = _load_plan_version(
            conn,
            amendment.run_id,
            amendment.base_plan_version,
        )
        candidate = _apply_amendment_to_plan(base_plan, amendment)
    except ValueError:
        return None
    candidate_hash = hashlib.sha256(
        canonical_plan_json(candidate).encode("utf-8")
    ).hexdigest()
    if (
        candidate_hash != run.plan_hash
        or not _amendment_event_matches(
            conn,
            anchor_task_id=run.anchor_task_id,
            plan_version=run.plan_version,
            amendment=amendment,
        )
    ):
        return None
    return load_org_run_topology(conn, amendment.run_id)


def _new_amendment_node_keys(
    plan: ImplementationPlan,
    task_ids: set[str],
    *,
    add_global_review: bool,
) -> tuple[str, ...]:
    keys: list[str] = []
    by_id = {task.id: task for task in plan.tasks}
    for task_id in sorted(task_ids):
        task = by_id[task_id]
        keys.append(_task_node_key(plan.run_id, task_id))
        if _review_required(task):
            keys.append(f"{_task_node_key(plan.run_id, task_id)}:review")
    if add_global_review:
        keys.append(f"org-run:{plan.run_id}:review")
    return tuple(keys)


def _reject_amendment_key_collisions(
    conn: sqlite3.Connection,
    run_id: str,
    node_keys: tuple[str, ...],
) -> None:
    if not node_keys:
        return
    placeholders = ",".join("?" for _ in node_keys)
    historical = conn.execute(
        f"SELECT node_id FROM kanban_org_nodes "
        f"WHERE run_id = ? AND node_id IN ({placeholders}) LIMIT 1",
        (run_id, *node_keys),
    ).fetchone()
    if historical is not None:
        raise ValueError(
            f"OrgRun node id was already used: {historical['node_id']}"
        )
    collision = conn.execute(
        f"SELECT idempotency_key FROM tasks "
        f"WHERE idempotency_key IN ({placeholders}) "
        f"AND status != 'archived' LIMIT 1",
        node_keys,
    ).fetchone()
    if collision is not None:
        raise ValueError(
            "pre-existing OrgRun task key requires explicit adoption: "
            f"{collision['idempotency_key']}"
        )


def _archive_amendment_targets(
    conn: sqlite3.Connection,
    run_id: str,
    target_ids: set[str],
) -> tuple[str, ...]:
    node_ids: list[str] = []
    for task_id in sorted(target_ids):
        prefix = _task_node_key(run_id, task_id)
        rows = conn.execute(
            "SELECT node_id, task_id FROM kanban_org_nodes "
            "WHERE run_id = ? AND state = 'active' "
            "AND node_id IN (?, ?)",
            (run_id, prefix, f"{prefix}:review"),
        ).fetchall()
        for row in rows:
            if not kb.archive_task(conn, str(row["task_id"])):
                raise ValueError(
                    f"cannot amend task {task_id}: card is already archived"
                )
            node_ids.append(str(row["node_id"]))
    set_org_nodes_state(conn, run_id, tuple(node_ids), "cancelled")
    return tuple(node_ids)


def _assert_amendment_targets_unstarted(
    conn: sqlite3.Connection,
    run_id: str,
    target_ids: set[str],
) -> None:
    for task_id in sorted(target_ids):
        prefix = _task_node_key(run_id, task_id)
        rows = conn.execute(
            "SELECT n.node_kind, t.status "
            "FROM kanban_org_nodes AS n "
            "JOIN tasks AS t ON t.id = n.task_id "
            "WHERE n.run_id = ? AND n.state = 'active' "
            "AND n.node_id IN (?, ?)",
            (run_id, prefix, f"{prefix}:review"),
        ).fetchall()
        if not any(row["node_kind"] == "execution" for row in rows):
            raise ValueError(f"unknown amendment target: {task_id}")
        started = sorted(
            str(row["node_kind"])
            for row in rows
            if row["status"] in {"done", "running"}
        )
        if started:
            raise ValueError(
                f"cannot amend started task {task_id}: {', '.join(started)}"
            )


def _assert_downstream_phases_unstarted(
    conn: sqlite3.Connection,
    topology: OrgRunTopology,
) -> None:
    downstream = {
        "integration": topology.integration_id,
        "global_review": topology.review_id,
        "finalization": topology.finalization_id,
    }
    started: list[str] = []
    for node_kind, task_id in downstream.items():
        if task_id is None:
            continue
        task = kb.get_task(conn, task_id)
        if task is None:
            raise ValueError(
                f"OrgRun {topology.run_id} has incomplete stored topology"
            )
        if task.status in {"running", "done"}:
            started.append(f"{node_kind}={task.status}")
    if started:
        raise ValueError(
            "cannot amend OrgRun after downstream phase started: "
            + ", ".join(started)
        )


def _reconcile_parent_links(
    conn: sqlite3.Connection,
    child_id: str,
    *,
    expected_parent_ids: set[str],
    managed_parent_ids: set[str],
) -> None:
    current = set(kb.parent_ids(conn, child_id))
    for parent_id in sorted((current & managed_parent_ids) - expected_parent_ids):
        kb.unlink_tasks(conn, parent_id, child_id)
    for parent_id in sorted(expected_parent_ids - current):
        kb.link_tasks(conn, parent_id, child_id)


def apply_org_run_amendment(
    conn: sqlite3.Connection,
    amendment: ImplementationAmendment,
    *,
    board: str | None,
    repository: Path,
    profile_exists: Callable[[str], bool],
    role_route_exists: Callable[[str], bool],
) -> OrgRunTopology:
    """Atomically apply one validated amendment to a local OrgRun."""
    if conn.in_transaction:
        raise ValueError(
            "apply_org_run_amendment cannot run inside an existing transaction"
        )
    with kb.write_txn(conn), kb._allow_managed_plan_mutations(conn):
        run = get_org_run(conn, amendment.run_id)
        if run is None:
            raise KeyError(f"unknown OrgRun: {amendment.run_id}")
        if run.origin != "local":
            raise ValueError("only local OrgRuns can be amended")
        board_slug = board if board is not None else kb.get_current_board()
        if board_slug != run.board_slug:
            raise ValueError(
                f"OrgRun {amendment.run_id} belongs to board {run.board_slug}"
            )
        if run.plan_version != amendment.base_plan_version:
            replay = _amendment_replay_topology(conn, run, amendment)
            if replay is not None:
                return replay
            raise ValueError(
                "stale base_plan_version: "
                f"expected {run.plan_version}, got {amendment.base_plan_version}"
            )

        current_plan = _load_plan_version(
            conn,
            amendment.run_id,
            run.plan_version,
        )
        current_topology = load_org_run_topology(conn, amendment.run_id)
        if current_topology is None:
            raise ValueError(
                f"OrgRun {amendment.run_id} has incomplete stored topology"
            )
        _assert_downstream_phases_unstarted(conn, current_topology)
        targets = {
            *(replacement.replaces for replacement in amendment.replace_tasks),
            *amendment.cancel_task_ids,
        }
        _assert_amendment_targets_unstarted(
            conn,
            amendment.run_id,
            targets,
        )
        amended_plan = _apply_amendment_to_plan(current_plan, amendment)
        validation = validate_implementation_plan(
            amended_plan,
            repository=repository,
            profile_exists=profile_exists,
            role_route_exists=role_route_exists,
        )
        new_version = run.plan_version + 1

        new_task_ids = {
            *(task.id for task in amendment.add_tasks),
            *(replacement.task.id for replacement in amendment.replace_tasks),
        }
        current_global_review = current_topology.review_id is not None
        amended_global_review = _global_review_required(amended_plan)
        add_global_review = amended_global_review and not current_global_review
        node_keys = _new_amendment_node_keys(
            amended_plan,
            new_task_ids,
            add_global_review=add_global_review,
        )
        _reject_amendment_key_collisions(
            conn,
            amendment.run_id,
            node_keys,
        )

        active_nodes = [
            node for node in list_org_nodes(conn, amendment.run_id)
            if node.state == "active"
        ]
        managed_task_ids = {node.task_id for node in active_nodes}
        _archive_amendment_targets(conn, amendment.run_id, targets)

        execution_ids = {
            task_id: topology.execution_id
            for task_id, topology in current_topology.tasks.items()
            if task_id not in targets
        }
        review_ids = {
            task_id: topology.review_id
            for task_id, topology in current_topology.tasks.items()
            if task_id not in targets and topology.review_id is not None
        }
        runnable_kwargs = _runnable_workspace_kwargs(board)
        triage = all(
            (
                task := kb.get_task(conn, task_topology.execution_id)
            ) is not None
            and task.status == "triage"
            for task_topology in current_topology.tasks.values()
        )
        node_specs: list[_NodeSpec] = []

        for task in amended_plan.tasks:
            if task.id not in new_task_ids:
                continue
            node_id = _task_node_key(amendment.run_id, task.id)
            execution_ids[task.id] = kb.create_task(
                conn,
                title=task.title,
                body=_execution_body(amended_plan, task),
                assignee=_profile(validation, task.role),
                created_by=_CREATED_BY,
                parents=[current_topology.anchor_id],
                triage=triage,
                idempotency_key=node_id,
                board=board,
                **runnable_kwargs,
            )

        for task in amended_plan.tasks:
            if task.id not in new_task_ids or not _review_required(task):
                continue
            node_id = f"{_task_node_key(amendment.run_id, task.id)}:review"
            review_ids[task.id] = kb.create_task(
                conn,
                title=f"Review: {task.title}",
                body=_task_review_body(),
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
            for task in amended_plan.tasks
        }
        terminal_node_ids = {
            task.id: (
                f"{_task_node_key(amendment.run_id, task.id)}:review"
                if task.id in review_ids
                else _task_node_key(amendment.run_id, task.id)
            )
            for task in amended_plan.tasks
        }
        managed_task_ids.update(execution_ids.values())
        managed_task_ids.update(review_ids.values())

        for task in amended_plan.tasks:
            dependency_ids = validation.ordered_dependencies.get(task.id, ())
            expected_parents = {
                terminal_task_ids[parent_id] for parent_id in dependency_ids
            }
            _reconcile_parent_links(
                conn,
                execution_ids[task.id],
                expected_parent_ids=expected_parents,
                managed_parent_ids=managed_task_ids - {current_topology.anchor_id},
            )
            if task.id not in new_task_ids:
                continue
            dependency_node_ids = tuple(
                terminal_node_ids[parent_id] for parent_id in dependency_ids
            )
            node_specs.append(
                _NodeSpec(
                    node_id=_task_node_key(amendment.run_id, task.id),
                    task_id=execution_ids[task.id],
                    node_kind="execution",
                    logical_role=task.role,
                    task_contract=_task_contract(task),
                    dependency_node_ids=(
                        f"org-run:{amendment.run_id}:anchor",
                        *dependency_node_ids,
                    ),
                )
            )
            if task.id in review_ids:
                node_specs.append(
                    _NodeSpec(
                        node_id=(
                            f"{_task_node_key(amendment.run_id, task.id)}:review"
                        ),
                        task_id=review_ids[task.id],
                        node_kind="task_review",
                        logical_role="reviewer",
                        task_contract={
                            "task_id": task.id,
                            "acceptance_criteria": list(task.acceptance_criteria),
                            "verification": list(task.verification),
                        },
                        dependency_node_ids=(
                            _task_node_key(amendment.run_id, task.id),
                        ),
                    )
                )

        expected_integration_parents = set(terminal_task_ids.values())
        _reconcile_parent_links(
            conn,
            current_topology.integration_id,
            expected_parent_ids=expected_integration_parents,
            managed_parent_ids=managed_task_ids,
        )

        global_review_id = current_topology.review_id
        final_parent_id = current_topology.integration_id
        if add_global_review:
            review_node_id = f"org-run:{amendment.run_id}:review"
            global_review_id = kb.create_task(
                conn,
                title=f"Review integrated OrgRun {amendment.run_id}",
                body=_global_review_body(),
                assignee=_profile(validation, "reviewer"),
                created_by=_CREATED_BY,
                parents=[current_topology.integration_id],
                skills=_REVIEW_SKILLS,
                idempotency_key=review_node_id,
                board=board,
                **runnable_kwargs,
            )
            node_specs.append(
                _NodeSpec(
                    node_id=review_node_id,
                    task_id=global_review_id,
                    node_kind="global_review",
                    logical_role="reviewer",
                    task_contract={
                        "objective": amended_plan.objective,
                        "acceptance_criteria": list(
                            amended_plan.acceptance_criteria
                        ),
                    },
                    dependency_node_ids=(
                        f"org-run:{amendment.run_id}:integration",
                    ),
                )
            )
            managed_task_ids.add(global_review_id)
        elif current_global_review and not amended_global_review:
            assert global_review_id is not None
            review_status = kb.get_task(conn, global_review_id).status
            if review_status in {"done", "running"}:
                raise ValueError(
                    "cannot remove a started global review during amendment"
                )
            if not kb.archive_task(conn, global_review_id):
                raise ValueError("cannot archive the obsolete global review")
            set_org_nodes_state(
                conn,
                amendment.run_id,
                (f"org-run:{amendment.run_id}:review",),
                "cancelled",
            )
            global_review_id = None

        if global_review_id is not None:
            final_parent_id = global_review_id
        _reconcile_parent_links(
            conn,
            current_topology.finalization_id,
            expected_parent_ids={final_parent_id},
            managed_parent_ids=managed_task_ids,
        )

        for spec in node_specs:
            insert_org_node(
                conn,
                run_id=amendment.run_id,
                node_id=spec.node_id,
                task_id=spec.task_id,
                node_kind=spec.node_kind,
                plan_version=new_version,
                contract_hash=_contract_hash(
                    node_kind=spec.node_kind,
                    logical_role=spec.logical_role,
                    task_contract=spec.task_contract,
                    dependency_node_ids=spec.dependency_node_ids,
                    plan_version=new_version,
                    base_commit=amended_plan.base_commit,
                ),
                logical_role=spec.logical_role,
            )

        current_specs = _expected_plan_node_specs(current_plan)
        amended_specs = _expected_plan_node_specs(amended_plan)
        new_node_ids = {spec.node_id for spec in node_specs}
        for node in active_nodes:
            if node.node_id in new_node_ids:
                continue
            old_spec = current_specs.get(node.node_id)
            new_spec = amended_specs.get(node.node_id)
            if (
                old_spec is None
                or new_spec is None
                or _effective_spec_key(old_spec) == _effective_spec_key(new_spec)
            ):
                continue
            update_org_node_contract(
                conn,
                run_id=amendment.run_id,
                node_id=node.node_id,
                plan_version=new_version,
                contract_hash=_contract_hash(
                    node_kind=new_spec.node_kind,
                    logical_role=new_spec.logical_role,
                    task_contract=new_spec.task_contract,
                    dependency_node_ids=new_spec.dependency_node_ids,
                    plan_version=new_version,
                    base_commit=amended_plan.base_commit,
                ),
            )

        insert_plan_version(
            conn,
            run_id=amendment.run_id,
            plan_version=new_version,
            plan_hash=validation.plan_hash,
            plan_json=canonical_plan_json(amended_plan),
            reason=amendment.reason,
        )
        update_org_run_plan(
            conn,
            amendment.run_id,
            plan_version=new_version,
            plan_hash=validation.plan_hash,
        )
        kb._append_event(
            conn,
            current_topology.anchor_id,
            "org_run_amended",
            {
                "plan_version": new_version,
                "base_plan_version": amendment.base_plan_version,
                "reason": amendment.reason,
                "operation_hash": _amendment_operation_hash(amendment),
            },
        )
        topology = load_org_run_topology(conn, amendment.run_id)
        if topology is None:
            raise ValueError(
                f"OrgRun {amendment.run_id} has incomplete stored topology"
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


def _decoded_skills(value: Any) -> list[str] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed]


def _raise_managed_plan_drift(
    run_id: str,
    node_id: str,
    field: str,
) -> None:
    raise ValueError(
        f"OrgRun {run_id} managed plan drift at {node_id}: {field}"
    )


def _validate_current_plan_live_nodes(
    conn: sqlite3.Connection,
    run_id: str,
    plan: ImplementationPlan,
    nodes: list,
) -> None:
    """Verify live card contracts and parents against the current plan."""
    specs = _expected_plan_node_specs(plan)
    active = {
        node.node_id: node
        for node in nodes
        if node.state == "active"
    }
    for node_id, node in sorted(active.items()):
        spec = specs.get(node_id)
        if spec is None:
            _raise_managed_plan_drift(run_id, node_id, "unexpected node")
        row = conn.execute(
            "SELECT title, body, assignee, skills FROM tasks WHERE id = ?",
            (node.task_id,),
        ).fetchone()
        if row is None:
            _raise_managed_plan_drift(run_id, node_id, "missing card")
        if row["assignee"] != spec.logical_role:
            _raise_managed_plan_drift(run_id, node_id, "logical role")
        title, body, skills = _expected_live_task_fields(plan, spec)
        if row["title"] != title:
            _raise_managed_plan_drift(run_id, node_id, "title")
        if row["body"] != body:
            _raise_managed_plan_drift(run_id, node_id, "body")
        if _decoded_skills(row["skills"]) != skills:
            _raise_managed_plan_drift(run_id, node_id, "skills")
        try:
            expected_parent_ids = sorted(
                active[parent_node_id].task_id
                for parent_node_id in spec.dependency_node_ids
            )
        except KeyError as exc:
            _raise_managed_plan_drift(
                run_id,
                node_id,
                f"missing dependency node {exc.args[0]}",
            )
        if kb.parent_ids(conn, node.task_id) != expected_parent_ids:
            _raise_managed_plan_drift(run_id, node_id, "parent links")


def _validate_stored_node_provenance(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    plan_version: int,
    plan_hash: str,
    nodes: list,
) -> None:
    try:
        plan_payload, verified_plan_hash = _verified_plan_version(
            conn,
            run_id,
            plan_version,
        )
    except ValueError as exc:
        raise ValueError(
            f"OrgRun {run_id} has incomplete stored topology"
        ) from exc
    if verified_plan_hash != plan_hash:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")

    expected_records: set[tuple[str, str, str, str]]
    current_plan: ImplementationPlan | None = None
    if plan_payload.get("schema") == IMPLEMENTATION_PLAN_SCHEMA:
        try:
            plan = parse_implementation_plan(plan_payload)
        except ValueError as exc:
            raise ValueError(
                f"OrgRun {run_id} has incomplete stored topology"
            ) from exc
        if plan.run_id != run_id:
            raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
        current_plan = plan
        expected_specs = _expected_plan_node_roles(plan)
        expected_node_ids = {node_id for node_id, _, _ in expected_specs}
        placeholders = ",".join("?" for _ in expected_node_ids)
        task_rows = conn.execute(
            f"SELECT id, idempotency_key FROM tasks "
            f"WHERE idempotency_key IN ({placeholders})",
            tuple(sorted(expected_node_ids)),
        ).fetchall()
        task_ids = {
            str(row["idempotency_key"]): str(row["id"])
            for row in task_rows
        }
        if set(task_ids) != expected_node_ids:
            raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
        expected_records = {
            (node_id, task_ids[node_id], node_kind, logical_role)
            for node_id, node_kind, logical_role in expected_specs
        }
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
    else:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")

    if any(node.state not in {"active", "cancelled"} for node in nodes):
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
    if current_plan is not None:
        for node in nodes:
            try:
                historical_payload, _historical_hash = _verified_plan_version(
                    conn,
                    run_id,
                    node.plan_version,
                )
                historical_plan = parse_implementation_plan(historical_payload)
            except ValueError:
                raise ValueError(
                    f"OrgRun {run_id} has incomplete stored topology"
                ) from None
            if historical_plan.run_id != run_id:
                raise ValueError(
                    f"OrgRun {run_id} has incomplete stored topology"
                )
            spec = _expected_plan_node_specs(historical_plan).get(node.node_id)
            if (
                spec is None
                or spec.node_kind != node.node_kind
                or spec.logical_role != node.logical_role
                or node.contract_hash
                != _contract_hash(
                    node_kind=spec.node_kind,
                    logical_role=spec.logical_role,
                    task_contract=spec.task_contract,
                    dependency_node_ids=spec.dependency_node_ids,
                    plan_version=node.plan_version,
                    base_commit=historical_plan.base_commit,
                )
            ):
                raise ValueError(
                    f"OrgRun {run_id} has incomplete stored topology"
                )
            task_row = conn.execute(
                "SELECT idempotency_key FROM tasks WHERE id = ?",
                (node.task_id,),
            ).fetchone()
            if (
                task_row is None
                or task_row["idempotency_key"] != node.node_id
            ):
                raise ValueError(
                    f"OrgRun {run_id} has incomplete stored topology"
                )
    actual_records = {
        (node.node_id, node.task_id, node.node_kind, node.logical_role)
        for node in nodes
        if node.state == "active"
    }
    if actual_records != expected_records:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
    if current_plan is not None:
        _validate_current_plan_live_nodes(
            conn,
            run_id,
            current_plan,
            nodes,
        )


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
        if node.state != "active":
            continue
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


def validate_live_org_run_task(
    conn: sqlite3.Connection,
    task_id: str,
) -> str | None:
    """Validate a managed card's whole current topology and return its role."""
    row = conn.execute(
        "SELECT run_id, logical_role FROM kanban_org_nodes "
        "WHERE task_id = ? AND state = 'active'",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    topology = load_org_run_topology(conn, str(row["run_id"]))
    if topology is None:
        raise ValueError(f"OrgRun {row['run_id']} has incomplete stored topology")
    return str(row["logical_role"])


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
            state="materialized",
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
        refresh_org_run_state(conn, run_id)
        return topology

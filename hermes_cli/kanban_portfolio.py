"""Materialize execution portfolios on the existing Kanban board."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import sqlite3

from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import ExecutionPortfolio, PortfolioValidation
from hermes_cli.kanban_swarm import latest_blackboard, post_blackboard_update


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


def _protocol(org_run_id: str, remote_task_id: str, write_scope: tuple[str, ...]) -> str:
    scope = ", ".join(write_scope) or "(read-only)"
    return (
        "\n\n## OrgRun protocol\n"
        f"- OrgRun: `{org_run_id}`.\n"
        f"- Remote task: `{remote_task_id}`.\n"
        f"- Declared write scope: `{scope}`.\n"
        "- Stay inside declared write scope.\n"
        "- Complete with structured evidence or block with a typed reason.\n"
        "- Do not publish memory or contact the backend directly.\n"
    )


def _topology_from_blackboard(
    conn: sqlite3.Connection, anchor_id: str
) -> OrgRunCreated | None:
    value = latest_blackboard(conn, anchor_id).get("topology")
    if not isinstance(value, dict):
        return None
    try:
        remote_tasks = {
            remote_id: RemoteTaskTopology(**raw)
            for remote_id, raw in value["remote_tasks"].items()
        }
        return OrgRunCreated(
            anchor_id=str(value["anchor_id"]),
            remote_tasks=remote_tasks,
            integration_id=str(value["integration_id"]),
            review_id=str(value["review_id"]),
            synthesis_id=str(value["synthesis_id"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _create_or_complete_anchor(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str,
    idempotency_key: str,
    created_by: str,
    parents: list[str] | None = None,
) -> str:
    task_id = kb.create_task(
        conn,
        title=title,
        body=body,
        assignee=created_by,
        created_by=created_by,
        parents=parents or [],
        idempotency_key=idempotency_key,
    )
    task = kb.get_task(conn, task_id)
    if task is not None and task.status != "done":
        kb.complete_task(
            conn,
            task_id,
            summary="OrgRun topology anchor created.",
            metadata={"kind": "hades_org_anchor_v1"},
        )
    return task_id


def create_org_run(
    conn: sqlite3.Connection,
    plan: ExecutionPortfolio,
    validation: PortfolioValidation,
    *,
    board: str | None = None,
    activate: bool = True,
) -> OrgRunCreated:
    """Create or recover an idempotent OrgRun graph."""
    if plan.org_run_id == "":
        raise ValueError("org_run_id is required")
    created_by = "org-orchestrator"
    anchor_id = kb.create_task(
        conn,
        title=f"OrgRun: {plan.org_run_id}",
        body=(
            "Durable Hades OrgRun anchor and bounded blackboard.\n\n"
            f"Project: {plan.project_id}\nRepository: {plan.repository_id}\n"
            f"Base commit: {plan.base_commit}"
        ),
        assignee=created_by,
        created_by=created_by,
        idempotency_key=f"org-run:{plan.org_run_id}:anchor",
        board=board,
    )
    existing = _topology_from_blackboard(conn, anchor_id)
    if existing is not None:
        return existing
    anchor = kb.get_task(conn, anchor_id)
    if anchor is not None and anchor.status != "done":
        kb.complete_task(
            conn,
            anchor_id,
            summary="OrgRun portfolio accepted for local materialization.",
            metadata={
                "kind": "hades_org_run_v1",
                "org_run_id": plan.org_run_id,
                "base_commit": plan.base_commit,
            },
        )

    remote_anchors: dict[str, str] = {}
    for task in plan.tasks:
        remote_anchor = _create_or_complete_anchor(
            conn,
            title=f"Remote anchor: {task.remote_task_id}",
            body=task.body,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:anchor",
            created_by=created_by,
            parents=[anchor_id],
        )
        remote_anchors[task.remote_task_id] = remote_anchor

    execution_ids: dict[str, str] = {}
    review_ids: dict[str, str] = {}
    integration_ready_ids: dict[str, str] = {}
    for task in plan.tasks:
        execution_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Execute: {task.title}",
            body=task.body + _protocol(plan.org_run_id, task.remote_task_id, task.write_scope),
            assignee=task.assignee,
            created_by=created_by,
            parents=[remote_anchors[task.remote_task_id]],
            priority=task.priority,
            triage=not activate,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:execute",
            board=board,
        )
        review_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Review: {task.title}",
            body=(
                "Review the implementation evidence, changed files, scope and focused tests.\n"
                + _protocol(plan.org_run_id, task.remote_task_id, task.write_scope)
            ),
            assignee="default",
            created_by=created_by,
            parents=[execution_ids[task.remote_task_id]],
            priority=task.priority,
            skills=["requesting-code-review"],
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:review",
            board=board,
        )
        integration_ready_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Ready for integration: {task.title}",
            body="Validate that this task has supplied complete integration evidence.",
            assignee="default",
            created_by=created_by,
            parents=[review_ids[task.remote_task_id]],
            priority=task.priority,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:ready",
            board=board,
        )

    for task_id, parents in validation.ordered_dependencies.items():
        for parent_id in parents:
            kb.link_tasks(
                conn,
                integration_ready_ids[parent_id],
                execution_ids[task_id],
            )

    integration_id = kb.create_task(
        conn,
        title=f"Integrate OrgRun {plan.org_run_id}",
        body="Apply accepted patches in dependency order and run focused plus global tests.",
        assignee="default",
        created_by=created_by,
        parents=list(integration_ready_ids.values()),
        idempotency_key=f"org-run:{plan.org_run_id}:integration",
        board=board,
    )
    review_id = kb.create_task(
        conn,
        title=f"Review integrated OrgRun {plan.org_run_id}",
        body="Independently verify the integrated worktree, acceptance criteria and regression suite.",
        assignee="default",
        created_by=created_by,
        parents=[integration_id],
        skills=["requesting-code-review"],
        idempotency_key=f"org-run:{plan.org_run_id}:org-review",
        board=board,
    )

    completion_ids: dict[str, str] = {}
    for task in plan.tasks:
        completion_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Publish result: {task.remote_task_id}",
            body="Publish bounded completion evidence only after global integration review passes.",
            assignee="default",
            created_by=created_by,
            parents=[review_id],
            priority=task.priority,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:complete",
            board=board,
        )
    synthesis_id = kb.create_task(
        conn,
        title=f"Synthesize OrgRun {plan.org_run_id}",
        body="Summarize verified outcomes, residual risks and backend-facing bounded evidence.",
        assignee="default",
        created_by=created_by,
        parents=list(completion_ids.values()),
        idempotency_key=f"org-run:{plan.org_run_id}:synthesis",
        board=board,
    )

    created = OrgRunCreated(
        anchor_id=anchor_id,
        remote_tasks={
            task.remote_task_id: RemoteTaskTopology(
                anchor_id=remote_anchors[task.remote_task_id],
                execution_id=execution_ids[task.remote_task_id],
                review_id=review_ids[task.remote_task_id],
                integration_ready_id=integration_ready_ids[task.remote_task_id],
                completion_id=completion_ids[task.remote_task_id],
            )
            for task in plan.tasks
        },
        integration_id=integration_id,
        review_id=review_id,
        synthesis_id=synthesis_id,
    )
    post_blackboard_update(
        conn,
        anchor_id,
        author=created_by,
        key="portfolio",
        value={
            "schema": plan.schema,
            "org_run_id": plan.org_run_id,
            "project_id": plan.project_id,
            "repository_id": plan.repository_id,
            "base_commit": plan.base_commit,
        },
    )
    post_blackboard_update(
        conn,
        anchor_id,
        author=created_by,
        key="topology",
        value={
            "anchor_id": created.anchor_id,
            "remote_tasks": {
                key: asdict(value) for key, value in created.remote_tasks.items()
            },
            "integration_id": created.integration_id,
            "review_id": created.review_id,
            "synthesis_id": created.synthesis_id,
        },
    )
    post_blackboard_update(
        conn,
        anchor_id,
        author=created_by,
        key="conflicts",
        value=list(validation.conflicts),
    )
    return created

"""Validation primitives for durable Hades execution portfolios."""

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


@dataclass(frozen=True)
class PortfolioValidation:
    ordered_dependencies: dict[str, tuple[str, ...]]
    conflicts: tuple[tuple[str, str, str], ...]


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
        depends_on = raw.get("depends_on", [])
        write_scope = raw.get("write_scope", [])
        if not isinstance(depends_on, list) or not isinstance(write_scope, list):
            raise ValueError(f"tasks[{index}].depends_on and write_scope must be lists")
        try:
            priority = int(raw.get("priority", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"tasks[{index}].priority must be an integer") from exc
        tasks.append(
            PortfolioTask(
                remote_task_id=_text(
                    raw.get("remote_task_id"), f"tasks[{index}].remote_task_id"
                ),
                work_item_id=_text(
                    raw.get("work_item_id"), f"tasks[{index}].work_item_id"
                ),
                title=_text(raw.get("title"), f"tasks[{index}].title"),
                body=_text(raw.get("body"), f"tasks[{index}].body"),
                assignee=_text(raw.get("assignee"), f"tasks[{index}].assignee"),
                priority=priority,
                risk=risk,
                depends_on=tuple(_text(value, "depends_on item") for value in depends_on),
                write_scope=tuple(_scope_path(value) for value in write_scope),
            )
        )
    return ExecutionPortfolio(
        schema=schema,
        org_run_id=_text(payload.get("org_run_id"), "org_run_id"),
        project_id=_text(payload.get("project_id"), "project_id"),
        repository_id=_text(payload.get("repository_id"), "repository_id"),
        workspace_binding_id=_text(
            payload.get("workspace_binding_id"), "workspace_binding_id"
        ),
        base_commit=_text(payload.get("base_commit"), "base_commit"),
        tasks=tuple(tasks),
    )


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
        for second in ordered[index + 1 :]:
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
        ordered_dependencies={
            key: tuple(sorted(value)) for key, value in sorted(dependencies.items())
        },
        conflicts=tuple(conflicts),
    )

"""Provider-free contract for locally materialized OrgRun implementation plans."""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from tools.delegation_routing import ALLOWED_ROLES


IMPLEMENTATION_PLAN_SCHEMA = "hades.implementation-plan.v1"
RISK_LEVELS = frozenset({"low", "medium", "high"})
_RUNTIME_ROLES = ("orchestrator", "leaf", "reviewer")
_FORBIDDEN_KEY_PARTS = ("provider", "model", "credential")


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


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _string_list(value: Any, field: str, *, non_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (non_empty and not value):
        qualifier = "a non-empty list" if non_empty else "a list"
        raise ValueError(f"{field} must be {qualifier}")
    return tuple(_text(item, f"{field} item") for item in value)


def _scope_path(value: Any) -> str:
    text = _text(value, "write_scope item").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"write_scope must be repository-relative: {text}")
    return path.as_posix()


def _reject_provider_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError("implementation plans cannot contain provider/model/credential fields")
            _reject_provider_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_provider_keys(nested)


def parse_implementation_plan(payload: Mapping[str, Any]) -> ImplementationPlan:
    """Parse a pure local implementation plan without provider configuration."""
    if not isinstance(payload, Mapping):
        raise ValueError("implementation plan must be an object")
    _reject_provider_keys(payload)
    schema = _text(payload.get("schema"), "schema")
    if schema != IMPLEMENTATION_PLAN_SCHEMA:
        raise ValueError(f"unsupported implementation plan schema: {schema}")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("tasks must be a non-empty list")
    tasks: list[ImplementationTask] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, Mapping):
            raise ValueError(f"tasks[{index}] must be an object")
        role = _text(raw.get("role"), f"tasks[{index}].role")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"unsupported role: {role}")
        risk = _text(raw.get("risk"), f"tasks[{index}].risk")
        if risk not in RISK_LEVELS:
            raise ValueError(f"tasks[{index}].risk is invalid: {risk}")
        write_scope = _string_list(raw.get("write_scope", []), f"tasks[{index}].write_scope")
        tasks.append(ImplementationTask(
            id=_text(raw.get("id"), f"tasks[{index}].id"),
            title=_text(raw.get("title"), f"tasks[{index}].title"),
            role=role,
            risk=risk,
            write_scope=tuple(_scope_path(item) for item in write_scope),
            depends_on=_string_list(raw.get("depends_on", []), f"tasks[{index}].depends_on"),
            acceptance_criteria=_string_list(
                raw.get("acceptance_criteria"), f"tasks[{index}].acceptance_criteria", non_empty=True
            ),
            verification=_string_list(raw.get("verification"), f"tasks[{index}].verification", non_empty=True),
            independent_review=bool(raw.get("independent_review", False)),
        ))
    return ImplementationPlan(
        schema=schema,
        run_id=_text(payload.get("run_id"), "run_id"),
        objective=_text(payload.get("objective"), "objective"),
        base_commit=_text(payload.get("base_commit"), "base_commit"),
        acceptance_criteria=_string_list(payload.get("acceptance_criteria"), "acceptance_criteria", non_empty=True),
        tasks=tuple(tasks),
        independent_review=bool(payload.get("independent_review", False)),
        origin=_text(payload.get("origin", "local"), "origin"),
    )


def canonical_plan_json(plan: ImplementationPlan) -> str:
    """Return the stable JSON representation used for plan identity."""
    return json.dumps(asdict(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def validate_implementation_plan(
    plan: ImplementationPlan,
    *,
    repository: Path,
    profile_exists: Callable[[str], bool],
    role_route_exists: Callable[[str], bool],
) -> PlanValidation:
    """Validate a plan deterministically against local repository and routing state."""
    by_id: dict[str, ImplementationTask] = {}
    for task in plan.tasks:
        if task.id in by_id:
            raise ValueError(f"duplicate task id: {task.id}")
        by_id[task.id] = task

    dependencies = {task.id: set(task.depends_on) for task in plan.tasks}
    for task_id, parents in dependencies.items():
        for parent in parents:
            if parent not in by_id:
                raise ValueError(f"unknown dependency {parent} for {task_id}")
            if parent == task_id:
                raise ValueError(f"self dependency: {task_id}")

    conflicts: list[tuple[str, str, str]] = []
    for first_id, second_id in itertools.combinations(sorted(by_id), 2):
        for scope in sorted(set(by_id[first_id].write_scope) & set(by_id[second_id].write_scope)):
            dependencies[second_id].add(first_id)
            conflicts.append((first_id, second_id, scope))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(f"implementation plan dependency cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for parent in sorted(dependencies[task_id]):
            visit(parent)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(by_id):
        visit(task_id)

    for role in _RUNTIME_ROLES:
        if not profile_exists(role):
            raise ValueError(f"missing profile for role: {role}")
        if not role_route_exists(role):
            raise ValueError(f"missing delegation role route: {role}")

    try:
        subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{plan.base_commit}^{{commit}}"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("base_commit does not resolve to a commit") from exc

    return PlanValidation(
        plan_hash=hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest(),
        ordered_dependencies={key: tuple(sorted(value)) for key, value in sorted(dependencies.items())},
        conflicts=tuple(conflicts),
        resolved_profiles={role: role for role in _RUNTIME_ROLES},
        routed_roles=_RUNTIME_ROLES,
    )

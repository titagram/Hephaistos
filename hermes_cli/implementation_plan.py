"""Provider-free contract for locally materialized OrgRun implementation plans."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from tools.delegation_routing import ALLOWED_ROLES


IMPLEMENTATION_PLAN_SCHEMA = "hades.implementation-plan.v1"
IMPLEMENTATION_AMENDMENT_SCHEMA = "hades.implementation-amendment.v1"
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


def _parse_task(raw: Any, field: str) -> ImplementationTask:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field} must be an object")
    role = _text(raw.get("role"), f"{field}.role")
    if role not in ALLOWED_ROLES:
        raise ValueError(f"unsupported role: {role}")
    risk = _text(raw.get("risk"), f"{field}.risk")
    if risk not in RISK_LEVELS:
        raise ValueError(f"{field}.risk is invalid: {risk}")
    write_scope = _string_list(raw.get("write_scope", []), f"{field}.write_scope")
    return ImplementationTask(
        id=_text(raw.get("id"), f"{field}.id"),
        title=_text(raw.get("title"), f"{field}.title"),
        role=role,
        risk=risk,
        write_scope=tuple(_scope_path(item) for item in write_scope),
        depends_on=_string_list(raw.get("depends_on", []), f"{field}.depends_on"),
        acceptance_criteria=_string_list(
            raw.get("acceptance_criteria"),
            f"{field}.acceptance_criteria",
            non_empty=True,
        ),
        verification=_string_list(
            raw.get("verification"),
            f"{field}.verification",
            non_empty=True,
        ),
        independent_review=bool(raw.get("independent_review", False)),
    )


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
    tasks = [
        _parse_task(raw, f"tasks[{index}]")
        for index, raw in enumerate(raw_tasks)
    ]
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


def parse_implementation_amendment(
    payload: Mapping[str, Any],
) -> ImplementationAmendment:
    """Parse a provider-free amendment to one local implementation plan."""
    if not isinstance(payload, Mapping):
        raise ValueError("implementation amendment must be an object")
    _reject_provider_keys(payload)
    schema = _text(payload.get("schema"), "schema")
    if schema != IMPLEMENTATION_AMENDMENT_SCHEMA:
        raise ValueError(f"unsupported implementation amendment schema: {schema}")

    raw_version = payload.get("base_plan_version")
    if (
        isinstance(raw_version, bool)
        or not isinstance(raw_version, int)
        or raw_version < 1
    ):
        raise ValueError("base_plan_version must be a positive integer")

    raw_add = payload.get("add_tasks")
    raw_replace = payload.get("replace_tasks")
    raw_cancel = payload.get("cancel_task_ids")
    if not isinstance(raw_add, list):
        raise ValueError("add_tasks must be a list")
    if not isinstance(raw_replace, list):
        raise ValueError("replace_tasks must be a list")
    cancel_task_ids = _string_list(raw_cancel, "cancel_task_ids")

    add_tasks = tuple(
        _parse_task(raw, f"add_tasks[{index}]")
        for index, raw in enumerate(raw_add)
    )
    replacements: list[ReplacementTask] = []
    for index, raw in enumerate(raw_replace):
        if not isinstance(raw, Mapping):
            raise ValueError(f"replace_tasks[{index}] must be an object")
        replacements.append(
            ReplacementTask(
                replaces=_text(
                    raw.get("replaces"),
                    f"replace_tasks[{index}].replaces",
                ),
                task=_parse_task(
                    raw.get("task"),
                    f"replace_tasks[{index}].task",
                ),
            )
        )

    if not add_tasks and not replacements and not cancel_task_ids:
        raise ValueError("implementation amendment cannot be empty")

    operation_targets = [
        *(replacement.replaces for replacement in replacements),
        *cancel_task_ids,
    ]
    if len(operation_targets) != len(set(operation_targets)):
        raise ValueError("implementation amendment has a repeated target id")

    new_task_ids = [
        *(task.id for task in add_tasks),
        *(replacement.task.id for replacement in replacements),
    ]
    if len(new_task_ids) != len(set(new_task_ids)):
        raise ValueError("implementation amendment has a repeated new task id")
    if set(new_task_ids) & set(operation_targets):
        raise ValueError("implementation amendment reuses a target id")

    return ImplementationAmendment(
        schema=schema,
        run_id=_text(payload.get("run_id"), "run_id"),
        base_plan_version=raw_version,
        reason=_text(payload.get("reason"), "reason"),
        add_tasks=add_tasks,
        replace_tasks=tuple(replacements),
        cancel_task_ids=cancel_task_ids,
    )


def canonical_plan_json(plan: ImplementationPlan) -> str:
    """Return the stable JSON representation used for plan identity."""
    return json.dumps(asdict(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _deterministic_plan_projection(
    plan: ImplementationPlan,
) -> tuple[
    str,
    dict[str, tuple[str, ...]],
    tuple[tuple[str, str, str], ...],
]:
    """Derive the immutable hash and dependency projection from plan content."""
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
        for scope in sorted(
            set(by_id[first_id].write_scope)
            & set(by_id[second_id].write_scope)
        ):
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

    return (
        hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest(),
        {
            key: tuple(sorted(value))
            for key, value in sorted(dependencies.items())
        },
        tuple(conflicts),
    )


def verify_plan_validation(
    plan: ImplementationPlan,
    validation: PlanValidation,
) -> None:
    """Reject a supplied validation that is not the plan's exact projection."""
    plan_hash, dependencies, conflicts = _deterministic_plan_projection(plan)
    expected_profiles = {role: role for role in _RUNTIME_ROLES}
    if (
        validation.plan_hash != plan_hash
        or validation.ordered_dependencies != dependencies
        or validation.conflicts != conflicts
        or validation.resolved_profiles != expected_profiles
        or tuple(validation.routed_roles) != _RUNTIME_ROLES
    ):
        raise ValueError("supplied plan validation does not match plan content")


def validate_implementation_plan(
    plan: ImplementationPlan,
    *,
    repository: Path,
    profile_exists: Callable[[str], bool],
    role_route_exists: Callable[[str], bool],
) -> PlanValidation:
    """Validate a plan deterministically against local repository and routing state."""
    plan_hash, dependencies, conflicts = _deterministic_plan_projection(plan)

    for role in _RUNTIME_ROLES:
        if not profile_exists(role):
            raise ValueError(f"missing profile for role: {role}")
        if not role_route_exists(role):
            raise ValueError(f"missing delegation role route: {role}")

    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", plan.base_commit) is None:
        raise ValueError("base_commit must be a full canonical commit OID")
    try:
        resolved = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "rev-parse",
                "--verify",
                f"{plan.base_commit}^{{commit}}",
            ],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("base_commit does not resolve to a commit") from exc
    if resolved != plan.base_commit:
        raise ValueError("base_commit must be a full canonical commit OID")

    return PlanValidation(
        plan_hash=plan_hash,
        ordered_dependencies=dependencies,
        conflicts=conflicts,
        resolved_profiles={role: role for role in _RUNTIME_ROLES},
        routed_roles=_RUNTIME_ROLES,
    )

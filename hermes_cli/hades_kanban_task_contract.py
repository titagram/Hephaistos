"""Versioned contract helpers for Hades kanban task work payloads."""

from __future__ import annotations

from typing import Any

KANBAN_TASK_WORK_SCHEMA = "hades.kanban_task_work.v1"

REQUIRED_FIELDS = (
    "schema",
    "task_id",
    "project_id",
    "repository_id",
    "title",
    "description",
    "acceptance_criteria",
    "priority",
    "risk",
    "normalized_problem",
    "task_type",
    "clarification_status",
    "ready_for_agent_work",
    "required_context",
    "source_access_policy",
    "project_awareness_required",
    "memory_required",
    "created_from",
)
TASK_TYPES = {"implementation", "analysis", "bug"}
CLARIFICATION_STATUSES = {"ready", "needs_clarification"}
BUG_INTAKE_STATUSES = {"not_applicable", "created", "existing", "missing_workspace_binding"}


def validate_kanban_task_work_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return stable machine-readable contract errors for v1 task payloads."""

    errors: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return [_error("payload", "invalid_type", "payload must be an object")]

    schema = str(payload.get("schema") or "").strip()
    if schema != KANBAN_TASK_WORK_SCHEMA:
        errors.append(_error("schema", "invalid_schema", f"schema must be {KANBAN_TASK_WORK_SCHEMA}"))

    for field in REQUIRED_FIELDS:
        if field not in payload:
            errors.append(_error(field, "missing_required_field", f"{field} is required"))

    for field in ("task_id", "project_id", "repository_id", "title", "normalized_problem", "priority", "risk"):
        if field in payload and not _nonempty_string(payload.get(field)):
            errors.append(_error(field, "empty_required_field", f"{field} must be a non-empty string"))

    description = payload.get("description")
    if "description" in payload and not isinstance(description, str):
        errors.append(_error("description", "invalid_type", "description must be a string"))

    acceptance = payload.get("acceptance_criteria")
    if "acceptance_criteria" in payload and not _string_list(acceptance):
        errors.append(_error("acceptance_criteria", "invalid_type", "acceptance_criteria must be a non-empty string list"))

    required_context = payload.get("required_context")
    if "required_context" in payload and not _string_list(required_context):
        errors.append(_error("required_context", "invalid_type", "required_context must be a non-empty string list"))

    task_type = str(payload.get("task_type") or "").strip()
    if "task_type" in payload and task_type not in TASK_TYPES:
        errors.append(_error("task_type", "invalid_value", f"task_type must be one of {sorted(TASK_TYPES)}"))

    clarification_status = str(payload.get("clarification_status") or "").strip()
    if "clarification_status" in payload and clarification_status not in CLARIFICATION_STATUSES:
        errors.append(
            _error(
                "clarification_status",
                "invalid_value",
                f"clarification_status must be one of {sorted(CLARIFICATION_STATUSES)}",
            )
        )

    if payload.get("ready_for_agent_work") is not True:
        errors.append(_error("ready_for_agent_work", "not_ready", "ready_for_agent_work must be true"))
    if payload.get("project_awareness_required") is not True:
        errors.append(
            _error("project_awareness_required", "invalid_value", "project_awareness_required must be true")
        )
    if payload.get("memory_required") is not True:
        errors.append(_error("memory_required", "invalid_value", "memory_required must be true"))

    source_policy = payload.get("source_access_policy")
    if "source_access_policy" in payload and not isinstance(source_policy, dict):
        errors.append(_error("source_access_policy", "invalid_type", "source_access_policy must be an object"))

    created_from = payload.get("created_from")
    if "created_from" in payload and not isinstance(created_from, dict):
        errors.append(_error("created_from", "invalid_type", "created_from must be an object"))
    elif isinstance(created_from, dict):
        if created_from.get("type") != "kanban_task":
            errors.append(_error("created_from.type", "invalid_value", "created_from.type must be kanban_task"))
        if not _nonempty_string(created_from.get("source")):
            errors.append(_error("created_from.source", "empty_required_field", "created_from.source is required"))

    if task_type == "bug":
        bug_intake = payload.get("bug_intake")
        if not isinstance(bug_intake, dict):
            errors.append(_error("bug_intake", "missing_required_field", "bug_intake is required for bug tasks"))
        else:
            status = str(bug_intake.get("status") or "").strip()
            if status not in BUG_INTAKE_STATUSES:
                errors.append(
                    _error(
                        "bug_intake.status",
                        "invalid_value",
                        f"bug_intake.status must be one of {sorted(BUG_INTAKE_STATUSES)}",
                    )
                )
            evidence_refs = payload.get("evidence_refs")
            if status in {"created", "existing"} and not _ref_list(evidence_refs):
                errors.append(
                    _error(
                        "evidence_refs",
                        "missing_required_field",
                        "created/existing bug intake requires evidence_refs",
                    )
                )

    return errors


def kanban_task_contract_status(payload: dict[str, Any]) -> dict[str, Any]:
    errors = validate_kanban_task_work_payload(payload)
    return {
        "schema": KANBAN_TASK_WORK_SCHEMA,
        "valid": not errors,
        "errors": errors,
    }


def kanban_task_prompt(payload: dict[str, Any]) -> str:
    """Build bounded worker input for a valid-ish kanban task payload."""

    if str(payload.get("schema") or "").strip() != KANBAN_TASK_WORK_SCHEMA:
        return ""

    lines = [
        "Hades backend kanban task",
        f"Task ID: {payload.get('task_id') or 'unknown'}",
        f"Title: {payload.get('title') or '(untitled)'}",
        f"Type: {payload.get('task_type') or 'unknown'}",
        f"Priority: {payload.get('priority') or 'normal'}",
        "",
        "Normalized problem:",
        str(payload.get("normalized_problem") or payload.get("description") or "").strip(),
    ]
    description = str(payload.get("description") or "").strip()
    if description:
        lines.extend(["", "Description:", description])
    acceptance = payload.get("acceptance_criteria")
    if isinstance(acceptance, list) and acceptance:
        lines.extend(["", "Acceptance criteria:"])
        lines.extend(f"- {item}" for item in acceptance if isinstance(item, str) and item.strip())
    evidence_refs = payload.get("evidence_refs")
    if isinstance(evidence_refs, list) and evidence_refs:
        lines.extend(["", "Evidence refs:"])
        for ref in evidence_refs[:10]:
            if isinstance(ref, dict):
                lines.append(f"- {ref.get('kind') or ref.get('schema') or 'evidence'}: {ref.get('id') or ref.get('ref')}")
            elif isinstance(ref, str) and ref.strip():
                lines.append(f"- {ref.strip()}")
    required_context = payload.get("required_context")
    if isinstance(required_context, list) and required_context:
        lines.extend(["", "Required context:", ", ".join(str(item) for item in required_context[:10])])
    lines.extend(
        [
            "",
            "Before making precise source-free claims, use shared Hades memory and project awareness evidence.",
        ]
    )
    return "\n".join(line for line in lines if line is not None).strip()


def _error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)


def _ref_list(value: Any) -> bool:
    return isinstance(value, list) and any(bool(item) for item in value)

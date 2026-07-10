"""Structured work contracts required for orchestrator delegations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OrchestratorTaskContract:
    objective: str
    deliverable: str
    in_scope: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    workspace: str
    write_scope: tuple[str, ...]
    input_evidence: tuple[str, ...]
    dependencies: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    required_verification: tuple[str, ...]
    return_schema: tuple[str, ...]


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _required_list(
    raw: Mapping[str, Any], key: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    value = raw.get(key)
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or (not value and not allow_empty)
    ):
        suffix = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{key} must be {suffix} of non-empty strings")
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain only non-empty strings")
        normalized.append(item.strip())
    return tuple(normalized)


def parse_orchestrator_contract(
    raw: Mapping[str, Any],
) -> OrchestratorTaskContract:
    """Validate and freeze a model-supplied orchestrator task contract."""
    if not isinstance(raw, Mapping):
        raise ValueError("task_contract is required for orchestrator role")
    return OrchestratorTaskContract(
        objective=_required_text(raw, "objective"),
        deliverable=_required_text(raw, "deliverable"),
        in_scope=_required_list(raw, "in_scope"),
        out_of_scope=_required_list(raw, "out_of_scope"),
        workspace=_required_text(raw, "workspace"),
        write_scope=_required_list(raw, "write_scope"),
        input_evidence=_required_list(raw, "input_evidence"),
        dependencies=_required_list(raw, "dependencies", allow_empty=True),
        acceptance_criteria=_required_list(raw, "acceptance_criteria"),
        required_verification=_required_list(raw, "required_verification"),
        return_schema=_required_list(raw, "return_schema"),
    )


def contract_prompt_block(contract: OrchestratorTaskContract) -> str:
    """Render a validated contract as an explicit child-prompt section."""
    fields = (
        ("Objective", (contract.objective,)),
        ("Deliverable", (contract.deliverable,)),
        ("In Scope", contract.in_scope),
        ("Out Of Scope", contract.out_of_scope),
        ("Workspace", (contract.workspace,)),
        ("Write Scope", contract.write_scope),
        ("Input Evidence", contract.input_evidence),
        ("Dependencies", contract.dependencies),
        ("Acceptance Criteria", contract.acceptance_criteria),
        ("Required Verification", contract.required_verification),
        ("Return Schema", contract.return_schema),
    )
    lines = ["## Structured Task Contract"]
    for heading, values in fields:
        lines.append(f"\n### {heading}")
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- (none)")
    return "\n".join(lines)

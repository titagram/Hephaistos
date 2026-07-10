import dataclasses

import pytest

from tools.delegation_contract import (
    contract_prompt_block,
    parse_orchestrator_contract,
)


VALID = {
    "objective": "Implement routing",
    "deliverable": "Tested CLI command",
    "in_scope": ["hermes_cli/delegation_onboarding.py"],
    "out_of_scope": ["backend deployment"],
    "workspace": ".",
    "write_scope": ["hermes_cli/**", "tests/**"],
    "input_evidence": ["spec:delegation-onboarding"],
    "dependencies": [],
    "acceptance_criteria": ["focused tests pass"],
    "required_verification": [
        "pytest tests/hermes_cli/test_delegation_onboarding.py -q"
    ],
    "return_schema": ["child_plan", "evidence", "risks", "escalations"],
}


def test_contract_requires_every_nonempty_semantic_field():
    required_nonempty = set(VALID) - {"dependencies"}
    for key in required_nonempty:
        broken = {**VALID, key: [] if isinstance(VALID[key], list) else ""}
        with pytest.raises(ValueError, match=key):
            parse_orchestrator_contract(broken)


def test_contract_allows_explicit_empty_dependencies():
    assert parse_orchestrator_contract(VALID).dependencies == ()


def test_contract_is_immutable_and_normalizes_lists_to_tuples():
    contract = parse_orchestrator_contract(VALID)

    assert contract.in_scope == ("hermes_cli/delegation_onboarding.py",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        contract.objective = "changed"


def test_contract_rejects_non_string_list_members():
    with pytest.raises(ValueError, match="write_scope"):
        parse_orchestrator_contract({**VALID, "write_scope": ["tests/**", 7]})


def test_contract_prompt_block_contains_each_contract_section():
    block = contract_prompt_block(parse_orchestrator_contract(VALID))

    for key in VALID:
        assert key.replace("_", " ").title() in block
    assert "hermes_cli/delegation_onboarding.py" in block
    assert "(none)" in block

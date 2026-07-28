from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.implementation_plan import (
    IMPLEMENTATION_PLAN_SCHEMA,
    canonical_plan_json,
    parse_implementation_plan,
    validate_implementation_plan,
)


REPOSITORY = Path(__file__).resolve().parents[2]


def valid_payload() -> dict:
    return {
        "schema": "hades.implementation-plan.v1",
        "run_id": "local-run-001",
        "objective": "Ship an offline OrgRun",
        "base_commit": "a" * 40,
        "acceptance_criteria": ["All focused tests pass"],
        "independent_review": False,
        "tasks": [{
            "id": "runtime",
            "title": "Disconnect runtime sync",
            "role": "leaf",
            "risk": "high",
            "write_scope": ["hermes_cli/kanban.py"],
            "depends_on": [],
            "acceptance_criteria": ["No backend client is constructed"],
            "verification": ["pytest tests/hermes_cli/test_kanban_cli.py"],
            "independent_review": True,
        }],
    }


def parsed_plan(**changes):
    payload = valid_payload()
    payload.update(changes)
    return parse_implementation_plan(payload)


def existing_commit() -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"], text=True
    ).strip()


def validate(plan):
    return validate_implementation_plan(
        plan,
        repository=REPOSITORY,
        profile_exists=lambda role: True,
        role_route_exists=lambda role: True,
    )


def test_parses_immutable_local_plan_contract():
    plan = parse_implementation_plan(valid_payload())

    assert plan.schema == IMPLEMENTATION_PLAN_SCHEMA
    assert plan.tasks[0].write_scope == ("hermes_cli/kanban.py",)
    with pytest.raises(AttributeError):
        plan.run_id = "changed"


@pytest.mark.parametrize("field", ["provider", "model", "credential"])
def test_rejects_provider_model_and_credential_fields_recursively(field):
    payload = valid_payload()
    payload["tasks"][0]["nested"] = {field: "forbidden"}

    with pytest.raises(ValueError, match="provider/model/credential"):
        parse_implementation_plan(payload)


def test_rejects_duplicate_ids():
    payload = valid_payload()
    payload["tasks"].append(dict(payload["tasks"][0]))

    with pytest.raises(ValueError, match="duplicate task id: runtime"):
        validate(parsed_plan(tasks=payload["tasks"]))


@pytest.mark.parametrize(
    ("depends_on", "message"),
    [(["missing"], "unknown dependency missing for runtime"), (["runtime"], "self dependency: runtime")],
)
def test_rejects_unknown_and_self_dependencies(depends_on, message):
    payload = valid_payload()
    payload["tasks"][0]["depends_on"] = depends_on

    with pytest.raises(ValueError, match=message):
        validate(parsed_plan(tasks=payload["tasks"]))


def test_rejects_cyclic_dependencies():
    payload = valid_payload()
    payload["tasks"].append({
        **payload["tasks"][0],
        "id": "review",
        "depends_on": ["runtime"],
        "write_scope": ["hermes_cli/review.py"],
    })
    payload["tasks"][0]["depends_on"] = ["review"]

    with pytest.raises(ValueError, match="implementation plan dependency cycle"):
        validate(parsed_plan(tasks=payload["tasks"]))


@pytest.mark.parametrize("scope", ["../outside.py", "/tmp/outside.py"])
def test_rejects_non_relative_write_scopes(scope):
    payload = valid_payload()
    payload["tasks"][0]["write_scope"] = [scope]

    with pytest.raises(ValueError, match="write_scope must be repository-relative"):
        parse_implementation_plan(payload)


@pytest.mark.parametrize("field", ["acceptance_criteria", "verification"])
def test_rejects_empty_task_requirements(field):
    payload = valid_payload()
    payload["tasks"][0][field] = []

    with pytest.raises(ValueError, match=f"tasks\\[0\\]\\.{field} must be a non-empty list"):
        parse_implementation_plan(payload)


def test_rejects_unsupported_role():
    payload = valid_payload()
    payload["tasks"][0]["role"] = "builder"

    with pytest.raises(ValueError, match="unsupported role: builder"):
        parse_implementation_plan(payload)


@pytest.mark.parametrize(
    ("profile_exists", "role_route_exists", "message"),
    [
        (lambda role: role != "reviewer", lambda role: True, "missing profile for role: reviewer"),
        (lambda role: True, lambda role: role != "reviewer", "missing delegation role route: reviewer"),
    ],
)
def test_requires_profiles_and_routes_for_all_runtime_roles(profile_exists, role_route_exists, message):
    plan = replace(parsed_plan(), base_commit=existing_commit())

    with pytest.raises(ValueError, match=message):
        validate_implementation_plan(
            plan,
            repository=REPOSITORY,
            profile_exists=profile_exists,
            role_route_exists=role_route_exists,
        )


def test_rejects_missing_base_commit():
    plan = parsed_plan()

    with pytest.raises(ValueError, match="base_commit does not resolve to a commit"):
        validate(plan)


def test_hash_and_serialization_are_deterministic():
    plan = replace(parsed_plan(), base_commit=existing_commit())
    first = validate_implementation_plan(
        plan, repository=REPOSITORY, profile_exists=lambda role: True, role_route_exists=lambda role: True
    )
    second = validate_implementation_plan(
        plan, repository=REPOSITORY, profile_exists=lambda role: True, role_route_exists=lambda role: True
    )

    assert first.plan_hash == second.plan_hash
    assert canonical_plan_json(plan) == canonical_plan_json(plan)
    assert '"origin":"local"' in canonical_plan_json(plan)


def test_exact_path_overlaps_are_serialized_by_sorted_task_id():
    payload = valid_payload()
    payload["base_commit"] = existing_commit()
    payload["tasks"].extend([
        {**payload["tasks"][0], "id": "zeta", "write_scope": ["hermes_cli/shared.py"]},
        {**payload["tasks"][0], "id": "alpha", "write_scope": ["hermes_cli/shared.py"]},
    ])
    payload["tasks"][0]["write_scope"] = ["hermes_cli/nested"]

    result = validate_implementation_plan(
        parse_implementation_plan(payload),
        repository=REPOSITORY,
        profile_exists=lambda role: True,
        role_route_exists=lambda role: True,
    )

    assert result.ordered_dependencies["alpha"] == ()
    assert result.ordered_dependencies["zeta"] == ("alpha",)
    assert result.conflicts == (("alpha", "zeta", "hermes_cli/shared.py"),)

import pytest

from hermes_cli.hierarchical_execution import (
    EXECUTION_PORTFOLIO_SCHEMA,
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from dataclasses import replace


def valid_payload():
    return {
        "schema": EXECUTION_PORTFOLIO_SCHEMA,
        "org_run_id": "org_demo_001",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "base_commit": "a" * 40,
        "tasks": [{
            "remote_task_id": "HD-101",
            "work_item_id": "awi_101",
            "title": "Change contract",
            "body": "Implement the bounded change.",
            "assignee": "default",
            "priority": 10,
            "risk": "high",
            "depends_on": [],
            "write_scope": ["hermes_cli/contracts.py"],
        }],
    }


def test_parse_execution_portfolio():
    plan = parse_execution_portfolio(valid_payload())
    assert plan.org_run_id == "org_demo_001"
    assert plan.tasks[0].remote_task_id == "HD-101"
    assert plan.tasks[0].write_scope == ("hermes_cli/contracts.py",)


def test_rejects_unknown_schema():
    payload = valid_payload()
    payload["schema"] = "invented.v9"
    with pytest.raises(ValueError, match="unsupported portfolio schema"):
        parse_execution_portfolio(payload)


def test_unknown_dependency_is_rejected():
    plan = parse_execution_portfolio(valid_payload())
    broken = replace(plan, tasks=(replace(plan.tasks[0], depends_on=("HD-999",)),))
    with pytest.raises(ValueError, match="unknown dependency HD-999"):
        validate_execution_portfolio(broken)


def test_write_overlap_is_serialized_by_priority_then_id():
    payload = valid_payload()
    payload["tasks"].append({
        "remote_task_id": "HD-102",
        "work_item_id": "awi_102",
        "title": "Second writer",
        "body": "Change the same file.",
        "assignee": "default",
        "priority": 5,
        "risk": "medium",
        "depends_on": [],
        "write_scope": ["hermes_cli/contracts.py"],
    })
    result = validate_execution_portfolio(parse_execution_portfolio(payload))
    assert result.ordered_dependencies["HD-101"] == ()
    assert result.ordered_dependencies["HD-102"] == ("HD-101",)
    assert result.conflicts == (("HD-101", "HD-102", "hermes_cli/contracts.py"),)


def test_duplicate_remote_id_is_rejected():
    payload = valid_payload()
    payload["tasks"].append(dict(payload["tasks"][0], work_item_id="awi_102"))
    with pytest.raises(ValueError, match="duplicate remote_task_id"):
        validate_execution_portfolio(parse_execution_portfolio(payload))


def test_dependency_cycle_is_rejected():
    payload = valid_payload()
    payload["tasks"].append({
        "remote_task_id": "HD-102",
        "work_item_id": "awi_102",
        "title": "Cycle",
        "body": "Cycle",
        "assignee": "default",
        "priority": 1,
        "risk": "low",
        "depends_on": ["HD-101"],
        "write_scope": [],
    })
    payload["tasks"][0]["depends_on"] = ["HD-102"]
    with pytest.raises(ValueError, match="portfolio dependency cycle"):
        validate_execution_portfolio(parse_execution_portfolio(payload))

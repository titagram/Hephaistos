from __future__ import annotations

import json
from types import SimpleNamespace


def test_hades_coordination_profiles_are_curated_and_local_only():
    from hermes_cli.hades_coordination import hades_coordination_profiles

    profiles = hades_coordination_profiles()
    ids = {profile["id"] for profile in profiles}

    assert {"planner", "implementer", "reviewer", "sync-curator", "memory-steward"}.issubset(ids)
    for profile in profiles:
        routing = profile["model_routing"]
        assert profile["backend_visible"] is False
        assert routing["provider_source"] == "config.yaml"
        assert "local_model_profile" in routing
        assert "provider" not in routing
        assert "model" not in routing


def test_hades_coordination_profiles_are_copy_safe():
    from hermes_cli.hades_coordination import hades_coordination_profiles

    profiles = hades_coordination_profiles()
    profiles[0]["toolsets"].append("mutated")

    fresh = hades_coordination_profiles()

    assert "mutated" not in fresh[0]["toolsets"]


def test_hades_backend_profiles_json(capsys):
    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="profiles", json=True))

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["local_only"] is True
    assert payload["backend_visible"] is False
    assert payload["config_source"] == "config.yaml"
    assert payload["skill"] == "autonomous-ai-agents/hades-coordination"
    assert payload["profiles"][0]["model_routing"]["provider_source"] == "config.yaml"

from hermes_cli import kanban_db as kb
from hermes_cli.hades_coordination import post_coordination_event, snapshot_org_run
from hermes_cli.hierarchical_execution import parse_execution_portfolio, validate_execution_portfolio
from hermes_cli.kanban_portfolio import create_org_run
from hermes_cli.kanban_swarm import latest_blackboard

def _org_plan():
    return parse_execution_portfolio({"schema": "hades.execution-portfolio.v1", "org_run_id": "org_coord_1", "project_id": "p", "repository_id": "r", "workspace_binding_id": "w", "base_commit": "a" * 40, "tasks": [{"remote_task_id": "HD-1", "work_item_id": "awi-1", "title": "Task", "body": "Body", "assignee": "default", "priority": 1, "risk": "low", "depends_on": [], "write_scope": ["src/a.py"]}]})

def test_snapshot_reports_execution_and_only_execution_is_dispatchable(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan(); created = create_org_run(conn, plan, validate_execution_portfolio(plan)); snapshot = snapshot_org_run(conn, plan.org_run_id, created)
        assert snapshot.phase == "execution"
        assert snapshot.complete is False
        assert snapshot.dispatchable == (created.remote_tasks["HD-1"].execution_id,)
    finally: conn.close()
def test_typed_coordination_event_is_bounded_and_structured(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan(); created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        post_coordination_event(conn, anchor_id=created.anchor_id, event_type="review_request", summary="Review the bounded evidence.", related_task_ids=[created.remote_tasks["HD-1"].review_id], required_action="verify tests", evidence_refs=["run:1"])
        blackboard = latest_blackboard(conn, created.anchor_id)
        assert blackboard["coordination:review_request"]["type"] == "review_request"
        assert blackboard["coordination:review_request"]["required_action"] == "verify tests"
    finally: conn.close()

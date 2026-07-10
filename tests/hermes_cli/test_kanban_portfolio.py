from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import create_org_run


def payload():
    return {
        "schema": "hades.execution-portfolio.v1",
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


def test_create_org_run_separates_anchor_execution_review_and_completion(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        remote = created.remote_tasks["HD-101"]
        assert kb.get_task(conn, created.anchor_id).status == "done"
        assert kb.get_task(conn, remote.anchor_id).status == "done"
        assert kb.get_task(conn, remote.execution_id).status == "ready"
        assert kb.get_task(conn, remote.review_id).status == "todo"
        assert kb.get_task(conn, remote.integration_ready_id).status == "todo"
        assert kb.get_task(conn, remote.completion_id).status == "todo"
        assert kb.parent_ids(conn, remote.execution_id) == [remote.anchor_id]
        assert kb.parent_ids(conn, remote.review_id) == [remote.execution_id]
        assert kb.parent_ids(conn, remote.integration_ready_id) == [remote.review_id]
        assert kb.parent_ids(conn, created.integration_id) == [remote.integration_ready_id]
        assert kb.parent_ids(conn, remote.completion_id) == [created.review_id]
    finally:
        conn.close()


def test_create_org_run_is_idempotent(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        validation = validate_execution_portfolio(plan)
        first = create_org_run(conn, plan, validation)
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        second = create_org_run(conn, plan, validation)
        assert second == first
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == count
    finally:
        conn.close()


def test_inactive_org_run_stays_in_triage(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(
            conn,
            plan,
            validate_execution_portfolio(plan),
            activate=False,
        )
        assert kb.get_task(conn, created.remote_tasks["HD-101"].execution_id).status == "triage"
    finally:
        conn.close()


def test_remote_dependency_links_integration_ready_to_execution(tmp_path):
    raw = payload()
    raw["tasks"].append({
        "remote_task_id": "HD-102",
        "work_item_id": "awi_102",
        "title": "Dependent change",
        "body": "Wait for the contract.",
        "assignee": "default",
        "priority": 1,
        "risk": "medium",
        "depends_on": ["HD-101"],
        "write_scope": ["hermes_cli/consumer.py"],
    })
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(raw)
        created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        parent_ready = created.remote_tasks["HD-101"].integration_ready_id
        dependent_execution = created.remote_tasks["HD-102"].execution_id
        assert parent_ready in kb.parent_ids(conn, dependent_execution)
        assert parent_ready != created.remote_tasks["HD-101"].completion_id
    finally:
        conn.close()

from __future__ import annotations

from hermes_cli import kanban_db as kb
from hermes_cli.hades_coordination import publish_org_run_proposal
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import (
    create_org_run,
    import_remote_mandate,
    reconcile_remote_mandate,
)


def _plan():
    return parse_execution_portfolio({
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": "org-projection-1",
        "project_id": "project-uuid",
        "repository_id": "repo",
        "workspace_binding_id": "binding-1",
        "base_commit": "a" * 40,
        "tasks": [
            {"remote_task_id": "r1", "work_item_id": "w1", "title": "A", "body": "A", "assignee": "default", "priority": 2, "risk": "low", "depends_on": [], "write_scope": ["src/a.py"]},
            {"remote_task_id": "r2", "work_item_id": "w2", "title": "B", "body": "B", "assignee": "default", "priority": 1, "risk": "low", "depends_on": ["r1"], "write_scope": ["src/b.py"]},
            {"remote_task_id": "r3", "work_item_id": "w3", "title": "C", "body": "C", "assignee": "default", "priority": 1, "risk": "low", "depends_on": [], "write_scope": ["src/c.py"]},
        ],
    })


def test_remote_version_change_blocks_only_derived_subtree(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan()
        validation = validate_execution_portfolio(plan)
        org = create_org_run(conn, plan, validation)
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")

        result = reconcile_remote_mandate(
            conn, topology=org, dependencies=validation.ordered_dependencies,
            remote_id="r1", version="2",
        )

        assert result.status == "stale"
        assert result.previous_version == "1"
        assert set(result.affected_remote_ids) == {"r1", "r2"}
        assert all(kb.get_task(conn, node).status == "blocked" for node in result.affected_nodes)
        assert kb.get_task(conn, org.remote_tasks["r3"].execution_id).status != "blocked"
        assert result.evidence_valid is False
    finally:
        conn.close()


def test_same_remote_version_is_idempotent_and_does_not_block(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); validation = validate_execution_portfolio(plan)
        org = create_org_run(conn, plan, validation)
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")
        result = reconcile_remote_mandate(conn, topology=org, dependencies=validation.ordered_dependencies, remote_id="r1", version="1")
        assert result.status == "current"
        assert result.affected_nodes == ()
        assert result.evidence_valid is True
    finally:
        conn.close()


def test_publish_is_append_only_project_scoped_and_idempotent(tmp_path):
    class Client:
        def __init__(self): self.messages = []
        def create_inbox_message(self, **payload): self.messages.append(payload); return {"ok": True}
        def update_project_manager_card(self, *args, **kwargs): raise AssertionError("remote card mutation forbidden")

    client = Client()
    first = publish_org_run_proposal(
        client=client, project_id="project-uuid", sender_agent_id="agent-a",
        target_agent_id="agent-pm", remote_task_id="r1", remote_task_version="2",
        proposal_type="decision_proposal", summary="Mandate changed; reconcile local scope.",
        evidence_refs=["packet:ev-1"], idempotency_key="projection:r1:2",
        now=1_000,
    )
    second = publish_org_run_proposal(
        client=client, project_id="project-uuid", sender_agent_id="agent-a",
        target_agent_id="agent-pm", remote_task_id="r1", remote_task_version="2",
        proposal_type="decision_proposal", summary="Mandate changed; reconcile local scope.",
        evidence_refs=["packet:ev-1"], idempotency_key="projection:r1:2",
        now=1_000,
    )
    assert first == second
    assert len(client.messages) == 1
    envelope = client.messages[0]
    assert envelope["project_id"] == "project-uuid"
    assert envelope["effect"] == "information_read"
    assert envelope["message_type"] == "local_decision"
    assert envelope["payload"]["proposal_type"] == "decision_proposal"
    assert envelope["payload"]["evidence_refs"] == ["packet:ev-1"]


def test_projection_sync_off_does_no_remote_work(tmp_path):
    from hermes_cli.hades_kanban_sync import sync_remote_mandates
    class Client:
        def list_agent_work_items(self, **kwargs): raise AssertionError("network forbidden")
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        result = sync_remote_mandates(conn, Client(), project_id="project-uuid", mode="off")
        assert result.mode == "off"
        assert result.cursor is None
    finally:
        conn.close()


def test_projection_cursor_and_offline_status_are_durable(tmp_path):
    from hermes_cli.hades_kanban_sync import sync_remote_mandates
    from hermes_cli.kanban_swarm import latest_blackboard
    class Client:
        def __init__(self): self.cursors = []
        def list_agent_work_items(self, **kwargs):
            self.cursors.append(kwargs.get("cursor"))
            if len(self.cursors) == 1:
                return {"items": [], "next_cursor": "cursor-1"}
            raise OSError("offline")
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        anchor = kb.create_task(conn, title="anchor", assignee="default")
        client = Client()
        first = sync_remote_mandates(conn, client, project_id="project-uuid", mode="pull_only", projection_anchor_id=anchor)
        second = sync_remote_mandates(conn, client, project_id="project-uuid", mode="pull_only", projection_anchor_id=anchor)
        assert first.cursor == "cursor-1"
        assert second.status == "offline" and second.cursor == "cursor-1"
        assert client.cursors == [None, "cursor-1"]
        assert latest_blackboard(conn, anchor)["remote_projection_sync"]["status"] == "offline"
    finally:
        conn.close()

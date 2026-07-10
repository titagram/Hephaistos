from hermes_cli import kanban_db as kb
from hermes_cli.hades_kanban_sync import (
    claim_remote_for_local_task,
    heartbeat_remote_for_local_task,
    publish_remote_result,
    sync_remote_kanban,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def list_agent_work_items(self, **kwargs):
        self.calls.append(kwargs)
        return {"items": [
            {"id": "awi-1", "payload": {"title": "Remote task", "body": "Do it", "priority": "3"}},
            {"id": "awi-2", "payload": {"title": "Second"}},
            {"payload": {"title": "Missing id"}},
        ]}

    def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
        self.claimed = (work_item_id, local_workspace_id)
        return {"lease_token": "lease-1"}

    def heartbeat_agent_work_item(self, work_item_id, *, lease_token):
        self.heartbeat = (work_item_id, lease_token)
        return {}

    def complete_agent_work_item(self, work_item_id, *, lease_token, chat_message=None, memory_entry=None):
        self.completed = (work_item_id, lease_token, chat_message)
        return {}

    def fail_agent_work_item(self, work_item_id, *, lease_token, message):
        self.failed = (work_item_id, lease_token, message)
        return {}


def test_sync_is_off_without_network(_hermetic_environment):
    client = FakeClient()
    kb.init_db()
    with kb.connect() as conn:
        result = sync_remote_kanban(conn, client, project_id="p", mode="off")
    assert result.pulled == 0
    assert client.calls == []


def test_pull_only_imports_triage_cards_idempotently(_hermetic_environment):
    client = FakeClient()
    kb.init_db()
    with kb.connect() as conn:
        first = sync_remote_kanban(conn, client, project_id="p", mode="pull_only")
        second = sync_remote_kanban(conn, client, project_id="p", mode="mirror")
        rows = conn.execute("SELECT title, status, priority FROM tasks ORDER BY title").fetchall()
    assert first.created == 2
    assert second.created == 0
    assert second.existing == 2
    assert [tuple(row) for row in rows] == [("Remote task", "triage", 3), ("Second", "triage", 0)]


def test_remote_lease_claim_heartbeat_and_result_are_idempotent(_hermetic_environment):
    kb.init_db()
    client = FakeClient()
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title="Remote task",
            assignee="default",
            idempotency_key="remote-kanban:p:awi-1",
            triage=True,
        )
        task = kb.get_task(conn, task_id)
        allowed, reason = claim_remote_for_local_task(
            conn, client, task, local_workspace_id="lw-1"
        )
        assert allowed and "acquired" in reason
        assert heartbeat_remote_for_local_task(conn, client, task_id)
        assert publish_remote_result(conn, client, task_id, success=True, message="done")
        assert not publish_remote_result(conn, client, task_id, success=True, message="again")
    assert client.claimed == ("awi-1", "lw-1")
    assert client.heartbeat == ("awi-1", "lease-1")
    assert client.completed == ("awi-1", "lease-1", "done")

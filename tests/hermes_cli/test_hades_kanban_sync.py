from hermes_cli import kanban_db as kb
from hermes_cli.hades_kanban_sync import sync_remote_kanban


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

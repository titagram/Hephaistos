from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli.hades_kanban_sync import (
    claim_remote_for_local_task,
    heartbeat_remote_for_local_task,
    migrate_legacy_remote_links,
    publish_remote_result,
    run_kanban_sync,
    sync_remote_kanban,
)
from hermes_cli.kanban_backend import KanbanBackendContext


def _linked_context(project_id="p"):
    return KanbanBackendContext(
        "linked", Path.cwd(), project_id=project_id,
        workspace_binding_id=f"binding-{project_id}",
        local_workspace_id=f"local-{project_id}", agent_id=f"agent-{project_id}",
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
        result = sync_remote_kanban(conn, client, context=_linked_context(), mode="off")
    assert result.pulled == 0
    assert client.calls == []


def test_pull_only_imports_triage_cards_idempotently(_hermetic_environment):
    client = FakeClient()
    kb.init_db()
    with kb.connect() as conn:
        first = sync_remote_kanban(conn, client, context=_linked_context(), mode="pull_only")
        second = sync_remote_kanban(conn, client, context=_linked_context(), mode="mirror")
        rows = conn.execute("SELECT title, status, priority FROM tasks ORDER BY title").fetchall()
    assert first.created == 2
    assert second.created == 0
    assert second.existing == 2
    assert [tuple(row) for row in rows] == [("Remote task", "triage", 3), ("Second", "triage", 0)]


def test_pull_rejects_cross_project_page_without_partial_writes(_hermetic_environment):
    """A malformed remote page must not create a partial local projection."""
    kb.init_db()
    context = KanbanBackendContext(
        "linked", Path.cwd(), project_id="p1", workspace_binding_id="b1",
        local_workspace_id="lw1", agent_id="a1",
    )

    class PageClient:
        def list_agent_work_items(self, **kwargs):
            return {"items": [
                {"id": "w1", "project_id": "p1", "payload": {"title": "ok"}},
                {"id": "w2", "project_id": "p2", "payload": {"title": "wrong"}},
            ]}

    with kb.connect() as conn:
        result = sync_remote_kanban(conn, PageClient(), context=context, mode="pull_only")
        assert result.status == "rejected_page"
        assert kb.list_tasks(conn) == []
        assert kb.list_remote_links(conn) == []


def test_legacy_remote_card_migrates_without_network(_hermetic_environment):
    """Legacy card identity is converted locally and its latest lease is preserved."""
    kb.init_db()
    context = KanbanBackendContext(
        "linked", Path.cwd(), project_id="p1", workspace_binding_id="b1",
        local_workspace_id="lw1", agent_id="a1",
    )
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn, title="legacy", idempotency_key="remote-kanban:p1:w1",
        )
        kb.add_comment(
            conn, task_id, "hades-backend-sync",
            'HADES_REMOTE_LEASE {"work_item_id":"w1","lease_token":"lease-1"}',
        )

        assert migrate_legacy_remote_links(conn, context) == 1
        link = kb.get_remote_link(conn, task_id)
        assert link is not None
        assert link.project_id == "p1"
        assert link.workspace_binding_id == "b1"
        assert link.remote_work_item_id == "w1"
        assert link.lease_status == "acquired"
        assert link.lease_token == "lease-1"
        assert migrate_legacy_remote_links(conn, context) == 0


def test_legacy_migration_marks_conflicting_lease_history(_hermetic_environment):
    """A valid newer lease cannot hide a conflicting legacy remote identity."""
    kb.init_db()
    context = KanbanBackendContext(
        "linked", Path.cwd(), project_id="p1", workspace_binding_id="b1",
        local_workspace_id="lw1", agent_id="a1",
    )
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn, title="legacy", idempotency_key="remote-kanban:p1:w1",
        )
        kb.add_comment(
            conn, task_id, "hades-backend-sync",
            'HADES_REMOTE_LEASE {"work_item_id":"other","lease_token":"old"}',
        )
        kb.add_comment(
            conn, task_id, "hades-backend-sync",
            'HADES_REMOTE_LEASE {"work_item_id":"w1","lease_token":"lease-1"}',
        )

        assert migrate_legacy_remote_links(conn, context) == 1
        link = kb.get_remote_link(conn, task_id)
        assert link is not None
        assert link.lease_token == "lease-1"
        assert link.last_error == "legacy remote lease history is ambiguous"


def test_high_level_sync_reports_local_only_without_constructing_a_client(_hermetic_environment):
    """An unlinked board stays fully local and therefore never reaches a backend factory."""
    constructed = False

    def forbidden_client_factory(*args):
        nonlocal constructed
        constructed = True
        raise AssertionError("local-only sync must not construct a backend client")

    report = run_kanban_sync(cwd=Path.cwd(), client_factory=forbidden_client_factory)

    assert report.state == "local_only"
    assert report.workspace_binding_id is None
    assert not constructed


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

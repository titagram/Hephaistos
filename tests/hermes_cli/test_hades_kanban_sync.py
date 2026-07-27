from pathlib import Path
import threading
from types import SimpleNamespace

from hermes_cli import kanban_db as kb
from hermes_cli.hades_kanban_sync import (
    claim_remote_for_local_task,
    drain_remote_outbox,
    heartbeat_remote_for_local_task,
    make_remote_admission,
    migrate_legacy_remote_links,
    publish_remote_result,
    run_kanban_sync,
    sync_remote_kanban,
)
from hermes_cli.kanban_backend import KanbanBackendContext, KanbanSyncReport


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
            {"id": "awi-1", "workspace_binding_id": "binding-p", "payload": {"title": "Remote task", "body": "Do it", "priority": "3"}},
            {"id": "awi-2", "workspace_binding_id": "binding-p", "payload": {"title": "Second"}},
            {"workspace_binding_id": "binding-p", "payload": {"title": "Missing id"}},
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


class FlakyCompletionClient:
    def __init__(self, failures: int):
        self.failures = failures
        self.complete_calls = 0

    def complete_agent_work_item(self, *args, **kwargs):
        self.complete_calls += 1
        if self.complete_calls <= self.failures:
            raise ConnectionError("offline")
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
                {"id": "w1", "project_id": "p1", "workspace_binding_id": "b1", "payload": {"title": "ok"}},
                {"id": "w2", "project_id": "p2", "workspace_binding_id": "b1", "payload": {"title": "wrong"}},
            ]}

    with kb.connect() as conn:
        result = sync_remote_kanban(conn, PageClient(), context=context, mode="pull_only")
        assert result.status == "rejected_page"
        assert kb.list_tasks(conn) == []
        assert kb.list_remote_links(conn) == []


def test_pull_rejects_cross_binding_page_without_partial_writes(_hermetic_environment):
    """A same-project card from another workspace binding is not local work."""
    kb.init_db()
    context = KanbanBackendContext(
        "linked", Path.cwd(), project_id="p1", workspace_binding_id="b1",
        local_workspace_id="lw1", agent_id="a1",
    )

    class PageClient:
        calls = []

        def list_agent_work_items(self, **kwargs):
            self.calls.append(kwargs)
            return {"items": [
                {"id": "w1", "project_id": "p1", "workspace_binding_id": "b1", "payload": {"title": "ok"}},
                {"id": "w2", "project_id": "p1", "workspace_binding_id": "b1", "payload": {"title": "wrong", "workspace_binding_id": "b2"}},
            ]}

    client = PageClient()
    with kb.connect() as conn:
        result = sync_remote_kanban(conn, client, context=context, mode="pull_only")
        assert result.status == "rejected_page"
        assert kb.list_tasks(conn) == []
        assert kb.list_remote_links(conn) == []
    assert client.calls == [{
        "project_id": "p1", "workspace_binding_id": "b1", "agent_key": "local_agent",
        "status": "queued", "limit": 100,
    }]


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


def test_legacy_migration_uses_latest_lease_when_comments_share_a_second(
    _hermetic_environment, monkeypatch,
):
    """Comment IDs deterministically break timestamp ties during migration."""
    kb.init_db()
    context = KanbanBackendContext(
        "linked", Path.cwd(), project_id="p1", workspace_binding_id="b1",
        local_workspace_id="lw1", agent_id="a1",
    )
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn, title="legacy", idempotency_key="remote-kanban:p1:w1",
        )
        monkeypatch.setattr(kb.time, "time", lambda: 1_700_000_000)
        kb.add_comment(
            conn, task_id, "hades-backend-sync",
            'HADES_REMOTE_LEASE {"work_item_id":"w1","lease_token":"lease-old"}',
        )
        kb.add_comment(
            conn, task_id, "hades-backend-sync",
            'HADES_REMOTE_LEASE {"work_item_id":"w1","lease_token":"lease-new"}',
        )

        assert migrate_legacy_remote_links(conn, context) == 1
        link = kb.get_remote_link(conn, task_id)
        assert link is not None
        assert link.lease_token == "lease-new"


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


def test_high_level_sync_drains_pending_terminal_results(_hermetic_environment, monkeypatch):
    """A successful sync trigger also retries the durable completion outbox."""
    from hermes_cli import hades_kanban_sync as remote_sync
    from hermes_cli import kanban_backend as backend

    context = _linked_context()
    client = object()
    drained = []
    monkeypatch.setattr(backend, "resolve_kanban_backend_context", lambda **_: context)
    monkeypatch.setattr(backend, "make_kanban_client", lambda *_, **__: client)
    monkeypatch.setattr(remote_sync, "migrate_legacy_remote_links", lambda *_: None)
    monkeypatch.setattr(
        remote_sync,
        "sync_remote_kanban",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="ok", pulled=2, created=1, existing=1, failed=0, error=None,
        ),
    )
    monkeypatch.setattr(
        remote_sync,
        "drain_remote_outbox",
        lambda conn, *, context, client_factory, **_: drained.append((context, client_factory())) or (1, 0),
    )

    report = backend.run_kanban_sync(cwd=Path.cwd())

    assert report.state == "synced"
    assert report.delivered == 1
    assert drained == [(context, client)]


def test_maybe_sync_is_bounded_per_workspace_binding(_hermetic_environment, monkeypatch):
    """Dispatcher ticks do not repeatedly hammer the same linked backend."""
    from hermes_cli import kanban_backend as backend

    context = _linked_context()
    reports = []
    monkeypatch.setattr(backend, "resolve_kanban_backend_context", lambda **_: context)
    monkeypatch.setattr(
        backend,
        "run_kanban_sync",
        lambda **kwargs: reports.append(kwargs) or KanbanSyncReport(
            state="synced", workspace_binding_id=context.workspace_binding_id,
        ),
    )

    first = backend.maybe_run_kanban_sync(now=1_000, min_interval_seconds=30)
    second = backend.maybe_run_kanban_sync(now=1_001, min_interval_seconds=30)

    assert first.state == "synced"
    assert second.state == "sync_deferred"
    assert reports == [{"board": None, "cwd": None, "now": 1_000}]


def test_maybe_sync_preserves_offline_state_during_backoff(_hermetic_environment, monkeypatch):
    """A deferred retry after outage must not look healthy to dispatch admission."""
    from hermes_cli import kanban_backend as backend

    context = _linked_context()
    reports = []
    monkeypatch.setattr(backend, "resolve_kanban_backend_context", lambda **_: context)
    monkeypatch.setattr(
        backend,
        "run_kanban_sync",
        lambda **kwargs: reports.append(kwargs) or KanbanSyncReport(
            state="backend_offline", workspace_binding_id=context.workspace_binding_id,
        ),
    )

    first = backend.maybe_run_kanban_sync(now=2_000, min_interval_seconds=30)
    second = backend.maybe_run_kanban_sync(now=2_001, min_interval_seconds=30)

    assert first.state == "backend_offline"
    assert second.state == "backend_offline"
    assert len(reports) == 1


def test_dispatch_context_only_resolves_after_synced_or_healthy_deferred_report(
    _hermetic_environment, monkeypatch,
):
    """Offline and validation reports never reopen the backend client path."""
    from hermes_cli import kanban_backend as backend

    context = _linked_context()
    resolutions = []
    monkeypatch.setattr(
        backend,
        "resolve_kanban_backend_context",
        lambda **kwargs: resolutions.append(kwargs) or context,
    )

    offline = backend.dispatch_context_for_sync_report(
        KanbanSyncReport(state="backend_offline"),
    )
    deferred = backend.dispatch_context_for_sync_report(
        KanbanSyncReport(state="sync_deferred"), board="ariadne",
    )

    assert offline.mode == "local_only"
    assert deferred == context
    assert resolutions == [{"board": "ariadne", "cwd": None}]


def test_maybe_sync_marks_inflight_before_running_network_sync(_hermetic_environment, monkeypatch):
    """Concurrent dispatcher ticks share one binding-scoped network attempt."""
    from hermes_cli import kanban_backend as backend

    context = _linked_context()
    started = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    calls = []
    outcomes = []
    monkeypatch.setattr(backend, "resolve_kanban_backend_context", lambda **_: context)

    def _run(**_kwargs):
        calls.append("sync")
        started.set()
        release.wait(timeout=2)
        return KanbanSyncReport(
            state="synced", workspace_binding_id=context.workspace_binding_id,
        )

    monkeypatch.setattr(backend, "run_kanban_sync", _run)

    first_thread = threading.Thread(
        target=lambda: outcomes.append(backend.maybe_run_kanban_sync(now=3_000)),
    )
    first_thread.start()
    assert started.wait(timeout=1)

    def _second():
        outcomes.append(backend.maybe_run_kanban_sync(now=3_000))
        second_done.set()

    second_thread = threading.Thread(target=_second)
    second_thread.start()
    second_done.wait(timeout=0.25)
    release.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert calls == ["sync"]
    assert {report.state for report in outcomes} == {"synced", "sync_inflight"}


def test_maybe_sync_replaces_failed_inflight_marker_and_keeps_bindings_independent(
    _hermetic_environment, monkeypatch,
):
    """Unexpected failures clear the marker; other bindings never share it."""
    from hermes_cli import kanban_backend as backend

    contexts = {
        "a": _linked_context("a"),
        "b": _linked_context("b"),
    }
    calls = []
    monkeypatch.setattr(
        backend,
        "resolve_kanban_backend_context",
        lambda *, cwd=None, **_: contexts[str(cwd)],
    )

    def _run(*, cwd=None, **_kwargs):
        calls.append(str(cwd))
        if str(cwd) == "a" and calls.count("a") == 1:
            raise RuntimeError("temporary backend failure")
        return KanbanSyncReport(
            state="synced",
            workspace_binding_id=contexts[str(cwd)].workspace_binding_id,
        )

    monkeypatch.setattr(backend, "run_kanban_sync", _run)

    failed = backend.maybe_run_kanban_sync(cwd="a", now=4_000)
    other_binding = backend.maybe_run_kanban_sync(cwd="b", now=4_000)
    retried = backend.maybe_run_kanban_sync(cwd="a", now=4_031)

    assert failed.state == "backend_offline"
    assert other_binding.state == "synced"
    assert retried.state == "synced"
    assert calls == ["a", "b", "a"]


def test_local_card_admission_never_constructs_backend_client(_hermetic_environment):
    """Removing the local-card branch must not make local dispatch depend on Hades."""
    calls = []
    kb.init_db()
    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="local")
        admission = make_remote_admission(
            conn,
            context=KanbanBackendContext("local_only", Path.cwd()),
            client_factory=lambda: calls.append("client"),
        )
        assert admission(kb.get_task(conn, task_id)).action == "allow"
    assert calls == []


def test_remote_card_defers_when_backend_is_offline(_hermetic_environment):
    """Removing the remote fail-closed branch would dispatch without a lease."""
    kb.init_db()
    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="remote")
        kb.upsert_remote_link(
            conn, task_id=task_id, project_id="p1",
            workspace_binding_id="b1", remote_work_item_id="w1",
        )
        admission = make_remote_admission(
            conn,
            context=KanbanBackendContext(
                "linked", Path.cwd(), project_id="p1",
                workspace_binding_id="b1", local_workspace_id="lw1", agent_id="a1",
            ),
            client_factory=lambda: (_ for _ in ()).throw(ConnectionError("offline")),
        )
        decision = admission(kb.get_task(conn, task_id))
    assert decision.action == "defer"
    assert decision.reason == "remote_backend_unavailable"


def test_terminal_result_is_queued_then_delivered_once(_hermetic_environment):
    """Removing durable enqueue or sent state loses/duplicates a terminal result."""
    kb.init_db()
    client = FlakyCompletionClient(failures=1)
    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="remote")
        kb.upsert_remote_link(
            conn, task_id=task_id, project_id="p1",
            workspace_binding_id="b1", remote_work_item_id="w1",
        )
        kb.set_remote_lease(
            conn, task_id, lease_token="lease-1", lease_status="acquired",
        )
        assert kb.complete_task(conn, task_id, summary="done locally")
        context = KanbanBackendContext(
            "linked", Path.cwd(), project_id="p1",
            workspace_binding_id="b1", local_workspace_id="lw1", agent_id="a1",
        )
        assert not publish_remote_result(
            conn, context=context, task_id=task_id,
            success=True, message="done", client_factory=lambda: client,
        )
        assert kb.get_task(conn, task_id).status == "done"
        pending = kb.list_due_remote_results(conn, now=2_000_000_000)
        assert len(pending) == 1
        delivered, failed = drain_remote_outbox(
            conn, context=context,
            client_factory=lambda: client, now=2_000_000_000,
        )
        assert (delivered, failed) == (1, 0)
        assert client.complete_calls == 2
        assert kb.list_due_remote_results(conn, now=2_000_000_000) == []


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
        kb.upsert_remote_link(
            conn, task_id=task_id, project_id="p", workspace_binding_id="b",
            remote_work_item_id="awi-1",
        )
        task = kb.get_task(conn, task_id)
        allowed, reason = claim_remote_for_local_task(
            conn, client, task, local_workspace_id="lw-1"
        )
        assert allowed and "acquired" in reason
        assert heartbeat_remote_for_local_task(conn, client, task_id)
        context = KanbanBackendContext(
            "linked", Path.cwd(), project_id="p", workspace_binding_id="b",
            local_workspace_id="lw-1", agent_id="a1",
        )
        assert publish_remote_result(
            conn, context=context, task_id=task_id, success=True, message="done",
            client_factory=lambda: client,
        )
        assert not publish_remote_result(
            conn, context=context, task_id=task_id, success=True, message="again",
            client_factory=lambda: client,
        )
    assert client.claimed == ("awi-1", "lw-1")
    assert client.heartbeat == ("awi-1", "lease-1")
    assert client.completed == ("awi-1", "lease-1", "done")

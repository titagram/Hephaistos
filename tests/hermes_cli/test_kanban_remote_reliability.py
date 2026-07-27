from __future__ import annotations

import argparse
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from hermes_cli import hades_kanban_sync as remote_sync
from hermes_cli import kanban as kanban_cli
from hermes_cli import kanban_backend
from hermes_cli import kanban_db as kb
from hermes_cli.hades_backend_client import HadesBackendError
from hermes_cli.kanban_backend import KanbanBackendContext, KanbanSyncReport


def _child_try_sync_lock(db_path: str, queue) -> None:
    with kb.connect(Path(db_path)) as conn:
        acquired = kb.try_acquire_kanban_sync_lock(
            conn,
            workspace_binding_id="binding-1",
            owner_token="child",
            now=100,
        )
    queue.put(acquired)


def _linked_context(*, online: bool = True) -> KanbanBackendContext:
    return KanbanBackendContext(
        "linked",
        Path.cwd(),
        project_id="project-1",
        workspace_binding_id="binding-1",
        local_workspace_id="local-1",
        agent_id="agent-1",
        backend_available=online,
    )


def _remote_task(conn, *, title: str = "remote") -> str:
    task_id = kb.create_task(conn, title=title, assignee="default")
    kb.upsert_remote_link(
        conn,
        task_id=task_id,
        project_id="project-1",
        workspace_binding_id="binding-1",
        remote_work_item_id=f"work-{task_id}",
    )
    kb.set_remote_lease(
        conn,
        task_id,
        lease_token=f"lease-{task_id}",
        lease_status="acquired",
    )
    return task_id


def test_core_terminal_transitions_enqueue_binding_scoped_results_before_delivery(
    monkeypatch,
):
    """Removing either core enqueue would lose ordinary CLI/dashboard/worker exits."""
    kb.init_db()
    monkeypatch.setattr(kb, "_fire_remote_terminal_delivery_hook", lambda *a, **k: None)
    with kb.connect() as conn:
        completed = _remote_task(conn, title="completed")
        blocked = _remote_task(conn, title="blocked")

        assert kb.complete_task(conn, completed, summary="done")
        assert kb.block_task(conn, blocked, reason="cannot continue", kind="capability")

        rows = conn.execute(
            "SELECT task_id, operation, idempotency_key, status "
            "FROM kanban_sync_outbox ORDER BY task_id"
        ).fetchall()

    assert {
        (r["task_id"], r["operation"], r["status"]) for r in rows
    } == {
        (blocked, "fail", "pending"),
        (completed, "complete", "pending"),
    }
    assert all(":binding-1:" in r["idempotency_key"] for r in rows)


def test_cli_completion_survives_restart_before_outbox_drain(monkeypatch):
    """A process exit after local completion must not erase the remote result."""
    kb.init_db()
    monkeypatch.setattr(kb, "_fire_remote_terminal_delivery_hook", lambda *a, **k: None)
    with kb.connect() as conn:
        task_id = _remote_task(conn)

    args = argparse.Namespace(
        task_ids=[task_id],
        summary="done from CLI",
        result=None,
        metadata=None,
    )
    assert kanban_cli._cmd_complete(args) == 0

    calls: list[str] = []

    class Client:
        def complete_agent_work_item(self, work_item_id, **_kwargs):
            calls.append(work_item_id)

    with kb.connect() as reopened:
        delivered, failed = remote_sync.drain_remote_outbox(
            reopened,
            context=_linked_context(),
            client_factory=lambda _agent=None: Client(),
            now=2_000_000_000,
        )
    assert (delivered, failed) == (1, 0)
    assert calls == [f"work-{task_id}"]


def test_internal_give_up_enqueues_remote_failure(monkeypatch):
    """Circuit-breaker terminal failures use the same durable terminal policy."""
    kb.init_db()
    monkeypatch.setattr(kb, "_fire_remote_terminal_delivery_hook", lambda *a, **k: None)
    with kb.connect() as conn:
        task_id = _remote_task(conn)
        assert kb.claim_task(conn, task_id) is not None
        assert kb._record_spawn_failure(
            conn, task_id, "spawn failed", failure_limit=1,
        )
        row = conn.execute(
            "SELECT operation, status FROM kanban_sync_outbox WHERE task_id=?",
            (task_id,),
        ).fetchone()
    assert tuple(row) == ("fail", "pending")

    calls: list[str] = []

    class Client:
        def fail_agent_work_item(self, work_item_id, **_kwargs):
            calls.append(work_item_id)

    with kb.connect() as reopened:
        assert remote_sync.drain_remote_outbox(
            reopened,
            context=_linked_context(),
            client_factory=lambda _context: Client(),
            now=2_000_000_000,
        ) == (1, 0)
    assert calls == [f"work-{task_id}"]


def test_dashboard_block_survives_restart_before_outbox_drain(monkeypatch):
    """Dashboard terminal writes use the same durable core hook as workers/CLI."""
    from plugins.kanban.dashboard import plugin_api

    kb.init_db()
    monkeypatch.setattr(kb, "_fire_remote_terminal_delivery_hook", lambda *a, **k: None)
    with kb.connect() as conn:
        task_id = _remote_task(conn)

    response = plugin_api.update_task(
        task_id,
        plugin_api.UpdateTaskBody(
            status="blocked",
            block_reason="dashboard failure",
        ),
        board=None,
    )
    assert response["task"]["status"] == "blocked"

    calls: list[str] = []

    class Client:
        def fail_agent_work_item(self, work_item_id, **_kwargs):
            calls.append(work_item_id)

    with kb.connect() as reopened:
        assert remote_sync.drain_remote_outbox(
            reopened,
            context=_linked_context(),
            client_factory=lambda _context: Client(),
            now=2_000_000_000,
        ) == (1, 0)
    assert calls == [f"work-{task_id}"]


def test_remote_heartbeat_is_wired_to_local_worker_heartbeat(monkeypatch):
    """Long-running linked tasks renew the remote lease whenever local liveness does."""
    kb.init_db()
    calls: list[str] = []
    monkeypatch.setattr(
        remote_sync,
        "heartbeat_remote_for_local_task_context",
        lambda conn, task_id, **_kwargs: calls.append(task_id) or "renewed",
    )
    with kb.connect() as conn:
        task_id = _remote_task(conn)
        assert kb.claim_task(conn, task_id) is not None
        assert kb.heartbeat_worker(conn, task_id)
        assert kb.heartbeat_worker(conn, task_id)
    assert calls == [task_id, task_id]


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (ConnectionError("token=super-secret transport down"), "acquired"),
        (
            HadesBackendError(
                "token=super-secret lease expired",
                status_code=410,
                code="lease_expired",
            ),
            "expired",
        ),
    ],
)
def test_remote_heartbeat_classifies_transport_vs_expiry(monkeypatch, exc, expected_status):
    kb.init_db()

    class Client:
        def heartbeat_agent_work_item(self, *_args, **_kwargs):
            raise exc

    monkeypatch.setattr(
        kanban_backend,
        "resolve_kanban_backend_context",
        lambda **_kwargs: _linked_context(),
    )
    monkeypatch.setattr(
        kanban_backend,
        "make_kanban_client",
        lambda *_args, **_kwargs: Client(),
    )
    with kb.connect() as conn:
        task_id = _remote_task(conn)
        outcome = remote_sync.heartbeat_remote_for_local_task_context(
            conn, task_id, board=None,
        )
        link = kb.get_remote_link(conn, task_id)
    assert outcome == ("transport_unavailable" if expected_status == "acquired" else "expired")
    assert link.lease_status == expected_status
    assert "super-secret" not in (link.last_error or "")


@pytest.mark.parametrize(
    ("failure", "action", "reason"),
    [
        (ConnectionError("offline"), "defer", "remote_backend_unavailable"),
        (
            HadesBackendError("forbidden token=secret-value", status_code=403),
            "supersede",
            "remote_authorization_rejected",
        ),
        (None, "supersede", "remote_claim_malformed"),
    ],
)
def test_admission_has_structured_transport_and_permanent_outcomes(
    failure, action, reason,
):
    kb.init_db()

    class Client:
        def claim_agent_work_item(self, *_args, **_kwargs):
            if failure is not None:
                raise failure
            return {}

    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="remote", assignee="default")
        kb.upsert_remote_link(
            conn,
            task_id=task_id,
            project_id="project-1",
            workspace_binding_id="binding-1",
            remote_work_item_id="work-1",
        )
        decision = remote_sync.make_remote_admission(
            conn,
            context=_linked_context(),
            client_factory=lambda _agent=None: Client(),
        )(kb.get_task(conn, task_id))
    assert decision.action == action
    assert decision.reason == reason
    assert "secret-value" not in decision.reason


def test_offline_exact_context_reuses_persisted_lease_without_client(monkeypatch):
    """A known outage must not erase identity or reject an already-owned lease."""
    context = _linked_context()
    monkeypatch.setattr(
        kanban_backend,
        "resolve_kanban_backend_context",
        lambda **_kwargs: context,
    )
    offline = kanban_backend.dispatch_context_for_sync_report(
        KanbanSyncReport(
            state="backend_offline",
            workspace_binding_id="binding-1",
        )
    )
    assert offline.mode == "linked"
    assert offline.workspace_binding_id == "binding-1"
    assert offline.backend_available is False

    kb.init_db()
    with kb.connect() as conn:
        task_id = _remote_task(conn)
        decision = remote_sync.make_remote_admission(
            conn,
            context=offline,
            client_factory=lambda _agent=None: pytest.fail("must not create client"),
        )(kb.get_task(conn, task_id))
    assert decision.action == "allow"


def test_unconfigured_backend_defers_unleased_remote_card_without_false_mismatch():
    """Stored remote identity is not a conflicting active binding when none exists."""
    kb.init_db()
    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="remote", assignee="default")
        kb.upsert_remote_link(
            conn,
            task_id=task_id,
            project_id="project-1",
            workspace_binding_id="binding-1",
            remote_work_item_id="work-1",
        )
        decision = remote_sync.make_remote_admission(
            conn,
            context=KanbanBackendContext("local_only", Path.cwd()),
        )(kb.get_task(conn, task_id))
    assert decision == kb.DispatchAdmission(
        "defer", "remote_backend_unavailable",
    )


def test_status_flag_is_read_only_and_never_runs_network_sync(monkeypatch, capsys):
    """`sync --status` reports durable local state without any mutation trigger."""
    kb.init_db()
    monkeypatch.setattr(
        kanban_backend,
        "run_kanban_sync",
        lambda **_kwargs: pytest.fail("status must not perform sync"),
    )
    monkeypatch.setattr(
        kanban_backend,
        "read_kanban_sync_status",
        lambda **_kwargs: KanbanSyncReport(state="backend_offline", outbox_pending=2),
    )
    args = argparse.Namespace(board=None, status=True, json=True)
    assert kanban_cli._cmd_sync(args) == 0
    assert '"outbox_pending": 2' in capsys.readouterr().out


def test_atomic_remote_materialization_prevents_unlinked_orphan(monkeypatch):
    """Two database connections racing the same remote identity produce one linked task."""
    kb.init_db()
    barrier = threading.Barrier(2)

    class Client:
        def list_agent_work_items(self, **_kwargs):
            barrier.wait(timeout=2)
            return {
                "items": [{
                    "id": "work-race",
                    "project_id": "project-1",
                    "workspace_binding_id": "binding-1",
                    "payload": {"title": "one"},
                }]
            }

    errors: list[BaseException] = []

    def worker():
        try:
            with kb.connect() as conn:
                remote_sync.sync_remote_kanban(
                    conn, Client(), context=_linked_context(), mode="pull_only",
                )
        except BaseException as exc:  # pragma: no branch - test captures both threads.
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not errors
    with kb.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE idempotency_key=?",
            ("remote-kanban:project-1:work-race",),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_remote_links WHERE remote_work_item_id=?",
            ("work-race",),
        ).fetchone()[0] == 1


def test_concurrent_outbox_drainers_claim_one_remote_mutation():
    """CAS ownership prevents duplicate mutation across independent connections."""
    kb.init_db()
    with kb.connect() as conn:
        task_id = _remote_task(conn)
        kb.enqueue_remote_result(
            conn,
            task_id=task_id,
            operation="complete",
            payload={"message": "done"},
            idempotency_key=f"complete:project-1:binding-1:work-{task_id}",
            now=100,
        )

    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class Client:
        def complete_agent_work_item(self, work_item_id, **_kwargs):
            calls.append(work_item_id)
            entered.set()
            release.wait(timeout=2)

    outcomes: list[tuple[int, int]] = []

    def drain():
        with kb.connect() as conn:
            outcomes.append(remote_sync.drain_remote_outbox(
                conn,
                context=_linked_context(),
                client_factory=lambda _agent=None: Client(),
                now=100,
            ))

    first = threading.Thread(target=drain)
    first.start()
    assert entered.wait(timeout=2)
    second = threading.Thread(target=drain)
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert calls == [f"work-{task_id}"]
    assert sorted(outcomes) == [(0, 0), (1, 0)]


def test_durable_backoff_survives_process_local_state_reset(monkeypatch):
    """A restarted dispatcher observes persisted binding backoff before syncing."""
    kb.init_db()
    context = _linked_context()
    calls: list[int] = []
    monkeypatch.setattr(
        kanban_backend,
        "resolve_kanban_backend_context",
        lambda **_kwargs: context,
    )
    monkeypatch.setattr(
        kanban_backend,
        "run_kanban_sync",
        lambda **kwargs: calls.append(kwargs["now"]) or KanbanSyncReport(
            state="backend_offline",
            workspace_binding_id="binding-1",
            error="offline",
        ),
    )

    first = kanban_backend.maybe_run_kanban_sync(now=1_000, min_interval_seconds=30)
    second = kanban_backend.maybe_run_kanban_sync(now=1_001, min_interval_seconds=30)

    assert first.state == "backend_offline"
    assert second.state == "backend_offline"
    assert calls == [1_000]


def test_sync_lock_is_owned_across_processes():
    """SQLite lock ownership survives beyond one interpreter/thread registry."""
    db_path = kb.init_db()
    with kb.connect(db_path) as conn:
        assert kb.try_acquire_kanban_sync_lock(
            conn,
            workspace_binding_id="binding-1",
            owner_token="parent",
            now=100,
        )

    spawn = multiprocessing.get_context("spawn")
    queue = spawn.Queue()
    child = spawn.Process(target=_child_try_sync_lock, args=(str(db_path), queue))
    child.start()
    child.join(timeout=10)
    assert child.exitcode == 0
    assert queue.get(timeout=2) is False

    with kb.connect(db_path) as conn:
        kb.release_kanban_sync_lock(
            conn,
            workspace_binding_id="binding-1",
            owner_token="parent",
        )
    queue_after = spawn.Queue()
    next_child = spawn.Process(
        target=_child_try_sync_lock,
        args=(str(db_path), queue_after),
    )
    next_child.start()
    next_child.join(timeout=10)
    assert next_child.exitcode == 0
    assert queue_after.get(timeout=2) is True

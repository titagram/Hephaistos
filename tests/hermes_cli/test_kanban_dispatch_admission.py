import pytest

from hermes_cli import kanban_db as kb


def test_dispatch_without_admission_preserves_legacy_spawn(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    spawned: list[str] = []
    try:
        task_id = kb.create_task(conn, title="legacy", assignee="default")

        def spawn(task, workspace):
            spawned.append(task.id)
            return 43210

        result = kb.dispatch_once(conn, spawn_fn=spawn)
        assert spawned == [task_id]
        assert result.spawned[0][0] == task_id
        assert kb.get_task(conn, task_id).status == "running"
    finally:
        conn.close()


def test_dispatch_admission_rejects_unknown_action():
    with pytest.raises(ValueError, match="unknown dispatch admission action"):
        kb.DispatchAdmission(action="invented", reason="bad")


def test_release_active_claim_closes_run_without_failure(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(conn, title="release", assignee="default")
        assert kb.claim_task(conn, task_id) is not None
        assert kb.release_active_claim(
            conn,
            task_id,
            target_status="ready",
            reason="remote admission deferred",
        )
        task = kb.get_task(conn, task_id)
        assert task.status == "ready"
        assert task.claim_lock is None
        assert task.current_run_id is None
        assert task.consecutive_failures == 0
        run = kb.list_runs(conn, task_id)[-1]
        assert run.status == "released"
        assert run.outcome == "released"
        events = kb.list_events(conn, task_id)
        assert events[-1].kind == "admission_released"
    finally:
        conn.close()


def test_defer_releases_claim_and_does_not_spawn(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    spawned: list[str] = []
    try:
        task_id = kb.create_task(conn, title="defer", assignee="default")
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, workspace: spawned.append(task.id),
            admission_fn=lambda task: kb.DispatchAdmission("defer", "backend offline"),
        )
        assert spawned == []
        assert result.admission_deferred == [(task_id, "backend offline")]
        assert kb.get_task(conn, task_id).status == "ready"
        assert kb.get_task(conn, task_id).consecutive_failures == 0
    finally:
        conn.close()


def test_supersede_blocks_without_counting_failure(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(conn, title="lost", assignee="default")
        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, workspace: 99,
            admission_fn=lambda task: kb.DispatchAdmission("supersede", "claimed elsewhere"),
        )
        assert result.admission_superseded == [(task_id, "claimed elsewhere")]
        task = kb.get_task(conn, task_id)
        assert task.status == "blocked"
        assert task.consecutive_failures == 0
    finally:
        conn.close()


def test_admission_exception_defers_safely(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        task_id = kb.create_task(conn, title="error", assignee="default")

        def broken(task):
            raise RuntimeError("network unavailable")

        result = kb.dispatch_once(conn, admission_fn=broken, spawn_fn=lambda *_: 1)
        assert result.admission_deferred == [
            (task_id, "admission callback error: network unavailable")
        ]
        assert kb.get_task(conn, task_id).status == "ready"
    finally:
        conn.close()

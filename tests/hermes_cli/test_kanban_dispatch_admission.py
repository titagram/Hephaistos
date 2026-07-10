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

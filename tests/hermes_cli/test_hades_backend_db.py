from __future__ import annotations


def test_agent_and_workspace_binding_round_trip(tmp_path):
    from hermes_cli import hades_backend_db as db

    with db.connect_closing(tmp_path / "hades_backend.db") as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_ABC",
            capabilities={"memory": True},
        )
        agent = db.get_agent(conn, "agent_1")

        binding = db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="fp_1",
            display_path="~/repo",
            repo_root="/Users/me/repo",
            git_remote_display="github.com/acme/repo",
            git_remote_hash="remote_hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )

        loaded = db.get_binding_for_fingerprint(conn, "fp_1")

    assert agent is not None
    assert agent.agent_id == "agent_1"
    assert agent.capabilities == {"memory": True}
    assert binding.backend_workspace_binding_id == "wb_1"
    assert loaded is not None
    assert loaded.status == "linked"
    assert loaded.display_path == "~/repo"


def test_cross_project_workspace_collision_is_rejected(tmp_path):
    from hermes_cli import hades_backend_db as db

    with db.connect_closing(tmp_path / "hades_backend.db") as conn:
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="same",
            display_path="~/repo",
            repo_root="/repo",
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

        try:
            db.upsert_workspace_binding(
                conn,
                project_id="proj_2",
                agent_id="agent_1",
                local_project_id="p_local",
                workspace_fingerprint="same",
                display_path="~/repo",
                repo_root="/repo",
                git_remote_display="",
                git_remote_hash="",
                head_commit="",
                backend_workspace_binding_id="wb_2",
            )
        except db.WorkspaceBindingConflict as exc:
            assert exc.existing_project_id == "proj_1"
            assert exc.new_project_id == "proj_2"
        else:  # pragma: no cover - guard
            raise AssertionError("expected conflict")


def test_jobs_memory_proposals_and_inbox_are_idempotent(tmp_path):
    from hermes_cli import hades_backend_db as db

    with db.connect_closing(tmp_path / "hades_backend.db") as conn:
        first = db.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="received",
        )
        second = db.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="received",
        )
        proposal = db.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="development_note",
            summary="Remember the API shape",
            provenance={"source": "test"},
        )
        db.mark_memory_proposal_status(conn, proposal.id, "refused", "policy_denied")
        db.save_inbox_event(
            conn,
            event_id="evt_1",
            project_id="proj_1",
            event_type="message",
            payload={"text": "hello"},
        )
        db.save_inbox_event(
            conn,
            event_id="evt_1",
            project_id="proj_1",
            event_type="message",
            payload={"text": "hello"},
        )

        jobs = db.list_jobs(conn)
        updated = db.update_job_status(conn, "job_1", "completed", result={"summary": "done"})
        proposals = db.list_memory_proposals(conn)
        events = db.list_inbox_events(conn, project_id="proj_1")

    assert first.job_id == second.job_id
    assert len(jobs) == 1
    assert updated is not None
    assert updated.status == "completed"
    assert updated.result == {"summary": "done"}
    assert proposals[0].status == "refused"
    assert proposals[0].reason == "policy_denied"
    assert len(events) == 1


def test_plugin_work_items_are_tracked_separately_from_backend_jobs(tmp_path):
    from hermes_cli import hades_backend_db as db

    with db.connect_closing(tmp_path / "hades_backend.db") as conn:
        first = db.upsert_plugin_work_item(
            conn,
            work_item_id="awi_1",
            project_id="proj_1",
            repository_id="repo_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            kind="devboard.agent_chat_turn.v1",
            status="queued",
            payload={"prompt": "hello"},
        )
        db.update_plugin_work_item_status(
            conn,
            "awi_1",
            "claimed",
            lease_token="lease_1",
        )
        second = db.upsert_plugin_work_item(
            conn,
            work_item_id="awi_1",
            project_id="proj_1",
            repository_id="repo_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            kind="devboard.agent_chat_turn.v1",
            status="completed",
            payload={"prompt": "hello"},
            result={"final_response": "done"},
        )
        counts = db.count_plugin_work_items_by_status(conn)
        loaded = db.get_plugin_work_item(conn, "awi_1")
        listed = db.list_plugin_work_items(conn)

    assert first.work_item_id == second.work_item_id
    assert counts == {"completed": 1}
    assert [item.work_item_id for item in listed] == ["awi_1"]
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.lease_token == "lease_1"
    assert loaded.result == {"final_response": "done"}


def test_cleanup_terminal_plugin_work_items_keeps_active_and_fresh_items(tmp_path):
    from hermes_cli import hades_backend_db as db

    now = 2_000_000
    old = now - 31 * 86400
    fresh = now - 2 * 86400
    with db.connect_closing(tmp_path / "hades_backend.db") as conn:
        db.upsert_plugin_work_item(
            conn,
            work_item_id="awi_completed_old",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="completed",
            payload={"prompt": "old completed"},
            result={"final_response": "done"},
        )
        db.upsert_plugin_work_item(
            conn,
            work_item_id="awi_failed_fresh",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="failed",
            payload={"prompt": "fresh failure"},
            result={"message": "failed"},
        )
        db.upsert_plugin_work_item(
            conn,
            work_item_id="awi_queued_old",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="queued",
            payload={"prompt": "active old"},
        )
        conn.execute(
            "UPDATE plugin_work_items SET updated_at = ? WHERE work_item_id = ?",
            (old, "awi_completed_old"),
        )
        conn.execute(
            "UPDATE plugin_work_items SET updated_at = ? WHERE work_item_id = ?",
            (fresh, "awi_failed_fresh"),
        )
        conn.execute(
            "UPDATE plugin_work_items SET updated_at = ? WHERE work_item_id = ?",
            (old, "awi_queued_old"),
        )
        conn.commit()

        dry_run = db.cleanup_terminal_plugin_work_items(conn, retention_days=30, now=now, dry_run=True)
        before_delete = db.list_plugin_work_items(conn)
        deleted = db.cleanup_terminal_plugin_work_items(conn, retention_days=30, now=now)
        remaining = db.list_plugin_work_items(conn)

    assert dry_run["would_remove"] == 1
    assert dry_run["removed"] == 0
    assert dry_run["status_completed"] == 1
    assert {item.work_item_id for item in before_delete} == {
        "awi_completed_old",
        "awi_failed_fresh",
        "awi_queued_old",
    }
    assert deleted["removed"] == 1
    assert {item.work_item_id for item in remaining} == {"awi_failed_fresh", "awi_queued_old"}


def test_legacy_coordination_rows_migrate_without_loss(tmp_path):
    import json
    import sqlite3

    from hermes_cli import hades_backend_db as db

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE agent_coordination_events (
           sequence INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT,
           parent_id TEXT, recipients TEXT, event_type TEXT, summary TEXT,
           evidence_refs TEXT, artifact TEXT, created_at INTEGER, expires_at INTEGER)"""
    )
    conn.execute(
        """INSERT INTO agent_coordination_events
           (sender_id, parent_id, recipients, event_type, summary, evidence_refs,
            artifact, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("a", "parent", json.dumps(["b"]), "question", "legacy", "[]", None, 1, 9999999999),
    )
    conn.commit()
    conn.close()

    with db.connect_closing(path) as migrated:
        event = migrated.execute("SELECT event_id FROM agent_coordination_events").fetchone()
        recipient = migrated.execute(
            "SELECT recipient_id FROM agent_coordination_event_recipients"
        ).fetchone()
    assert event[0] == "legacy:1"
    assert recipient[0] == "b"

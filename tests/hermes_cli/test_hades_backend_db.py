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

    assert first.work_item_id == second.work_item_id
    assert counts == {"completed": 1}
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.lease_token == "lease_1"
    assert loaded.result == {"final_response": "done"}

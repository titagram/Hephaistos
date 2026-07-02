from __future__ import annotations


def test_sync_runner_logs_redacted_backend_errors(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    class FakeClient:
        def memory_snapshot(self, **payload):
            raise RuntimeError("token=super-secret-token failed")

        def list_inbox(self, **payload):
            return {"events": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    with caplog.at_level(logging.WARNING, logger="hermes_cli.hades_backend"):
        result = run_backend_sync(client_factory=lambda: FakeClient(), quiet=True)

    records = [
        record
        for record in caplog.records
        if getattr(record, "hades_event", None) == "sync.error"
    ]

    assert result.exit_code == 1
    assert records
    assert records[0].hades_workspace_binding_id == "wb_1"
    assert "super-secret-token" not in records[0].hades_error
    assert "super-secret-token" not in records[0].getMessage()


def test_sync_runner_expires_waiting_jobs_after_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_expired",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={"deadline_at": 10},
            status="waiting_confirmation",
        )

    class FakeClient:
        def __init__(self):
            self.statuses = []

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

    fake = FakeClient()
    result = run_backend_sync(client_factory=lambda: fake, now=20)

    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_expired")

    assert result.exit_code == 0
    assert result.summary["expired"] == 1
    assert job is not None
    assert job.status == "expired"
    assert fake.statuses == [("job_expired", "expired", fake.statuses[0][2])]


def test_cleanup_orphaned_memory_cache_removes_unlinked_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_orphan",
        )
        hdb.mark_binding_unlinked(conn, "wf_1")
        hdb.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_orphan",
            version="v1",
            items=[{"summary": "stale"}],
        )

        report = hdb.cleanup_orphaned_memory_cache(conn, include_all=True)
        cache = hdb.get_memory_cache(conn, "wb_orphan")

    assert report["removed"] == 1
    assert report["candidates"] == 1
    assert cache is None


def test_cleanup_terminal_backend_jobs_keeps_active_and_fresh_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    now = 2_000_000
    old = now - 31 * 86400
    fresh = now - 2 * 86400
    with hdb.connect_closing() as conn:
        hdb.upsert_job(
            conn,
            job_id="job_done_old",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="completed",
        )
        hdb.upsert_job(
            conn,
            job_id="job_failed_fresh",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="failed",
        )
        hdb.upsert_job(
            conn,
            job_id="job_waiting_old",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (old, "job_done_old"))
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (fresh, "job_failed_fresh"))
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (old, "job_waiting_old"))
        conn.commit()

        dry_run = hdb.cleanup_terminal_backend_jobs(conn, retention_days=30, now=now, dry_run=True)
        assert hdb.get_job(conn, "job_done_old") is not None

        report = hdb.cleanup_terminal_backend_jobs(conn, retention_days=30, now=now)

        assert dry_run["would_remove"] == 1
        assert dry_run["removed"] == 0
        assert report["removed"] == 1
        assert report["status_completed"] == 1
        assert hdb.get_job(conn, "job_done_old") is None
        assert hdb.get_job(conn, "job_failed_fresh") is not None
        assert hdb.get_job(conn, "job_waiting_old") is not None


def test_cleanup_reviewed_memory_proposals_keeps_unacknowledged_refusals(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    now = 2_000_000
    old = now - 91 * 86400
    with hdb.connect_closing() as conn:
        accepted = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="accepted",
            provenance={},
        )
        refused = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="refused",
            provenance={},
        )
        hdb.mark_memory_proposal_status(conn, accepted.id, "accepted")
        hdb.mark_memory_proposal_status(conn, refused.id, "refused", "backend policy")
        conn.execute("UPDATE memory_proposals SET updated_at = ? WHERE id IN (?, ?)", (old, accepted.id, refused.id))
        conn.commit()

        report = hdb.cleanup_reviewed_memory_proposals(conn, retention_days=90, now=now)
        remaining = {proposal.id: proposal.status for proposal in hdb.list_memory_proposals(conn)}

    assert report["removed"] == 1
    assert report["status_accepted"] == 1
    assert accepted.id not in remaining
    assert remaining[refused.id] == "refused"


def test_cleanup_inbox_events_removes_stale_events(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    now = 2_000_000
    old = now - 31 * 86400
    fresh = now - 2 * 86400
    with hdb.connect_closing() as conn:
        hdb.save_inbox_event(conn, event_id="evt_old", project_id="proj_1", event_type="notice", payload={"x": 1})
        hdb.save_inbox_event(conn, event_id="evt_fresh", project_id="proj_1", event_type="notice", payload={"x": 2})
        conn.execute("UPDATE inbox_events SET received_at = ? WHERE event_id = ?", (old, "evt_old"))
        conn.execute("UPDATE inbox_events SET received_at = ? WHERE event_id = ?", (fresh, "evt_fresh"))
        conn.commit()

        report = hdb.cleanup_inbox_events(conn, retention_days=30, now=now)
        remaining = [event.event_id for event in hdb.list_inbox_events(conn)]

    assert report["removed"] == 1
    assert report["unread"] == 1
    assert remaining == ["evt_fresh"]


def test_sync_runner_uploads_artifacts_and_polls_persephone_inbox(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "artifacts": True, "persephone": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    class FakeClient:
        def __init__(self):
            self.artifacts = []
            self.results = []

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {
                "jobs": [
                    {
                        "job_id": "job_tree",
                        "capability": "sync_git_tree",
                        "payload": {"max_files": 10, "max_bytes": 20_000},
                    }
                ]
            }

        def update_job_status(self, job_id, **payload):
            return {}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {}

        def upload_artifact(self, **payload):
            self.artifacts.append(payload)
            return {"artifact": {"id": "artifact_1"}}

        def list_inbox(self, **payload):
            return {
                "events": [
                    {
                        "id": "evt_1",
                        "event_type": "proposal.reviewed",
                        "payload": {"message": "Memory proposal refused."},
                    }
                ]
            }

    fake = FakeClient()
    result = run_backend_sync(client_factory=lambda: fake)

    with hdb.connect_closing() as conn:
        events = hdb.list_inbox_events(conn, project_id="proj_1")

    assert result.exit_code == 0
    assert result.summary["completed"] == 1
    assert result.summary["artifacts_uploaded"] == 1
    assert result.summary["inbox_events"] == 1
    assert fake.artifacts[0]["schema"] == "hades.git_tree.v1"
    assert fake.artifacts[0]["job_id"] == "job_tree"
    assert fake.artifacts[0]["workspace_binding_id"] == "wb_1"
    assert fake.results[0][0] == "job_tree"
    assert events[0].event_id == "evt_1"
    assert events[0].event_type == "proposal.reviewed"


def test_git_tree_artifact_omits_sensitive_ignored_binary_and_large_files(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (workspace / ".env").write_text("HADES_TOKEN=super-secret-token\n", encoding="utf-8")
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (workspace / "large.txt").write_text("x" * 64, encoding="utf-8")
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n")

    result = execute_job(
        {
            "job_id": "job_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 20, "max_bytes": 10_000, "max_file_bytes": 16},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    paths = {item["path"] for item in artifact["files"]}
    omitted = {item["path"]: item["reason"] for item in artifact["omitted"]}

    assert result["status"] == "completed"
    assert "README.md" in paths
    assert ".env" not in paths
    assert "ignored.txt" not in paths
    assert "large.txt" not in paths
    assert "logo.png" not in paths
    assert omitted[".env"] == "sensitive_name"
    assert omitted["ignored.txt"] == "gitignored"
    assert omitted["large.txt"] == "file_too_large"
    assert omitted["logo.png"] == "binary_or_archive"
    assert artifact["raw_source_included"] is False
    assert artifact["retention_class"] == "source_metadata"
    assert artifact["redactions"] == len(artifact["omitted"])
    assert "super-secret-token" not in str(artifact)


def test_background_sync_runs_once_and_records_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    calls = []

    def fake_sync_runner(**kwargs):
        calls.append(kwargs)
        return sync.SyncResult({"pulled": 0, "completed": 0}, 0)

    first = sync.maybe_run_backend_sync(now=1000, run_inline=True, sync_runner=fake_sync_runner)
    second = sync.maybe_run_backend_sync(now=1001, run_inline=True, sync_runner=fake_sync_runner)

    with hdb.connect_closing() as conn:
        state = hdb.get_sync_state(conn, sync.BACKGROUND_SYNC_STATE_KEY)

    assert first.status == "ran"
    assert first.reason == "ok"
    assert second.status == "skipped"
    assert second.reason == "backoff"
    assert calls == [{"quiet": True}]
    assert state is not None
    assert state["status"] == "ok"
    assert state["failure_count"] == 0
    assert state["next_attempt_at"] == 1300


def test_background_sync_records_failure_backoff_and_degraded_status(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    def failing_sync_runner(**kwargs):
        return sync.SyncResult({"error": 1}, 1)

    decision = sync.maybe_run_backend_sync(
        now=2000,
        run_inline=True,
        failure_base_delay_seconds=30,
        sync_runner=failing_sync_runner,
    )
    skipped = sync.maybe_run_backend_sync(now=2020, run_inline=True, sync_runner=failing_sync_runner)
    payload = load_backend_status_payload()

    assert decision.status == "ran"
    assert decision.reason == "failed"
    assert skipped.status == "skipped"
    assert skipped.reason == "backoff"
    assert payload["degraded"] is True
    assert payload["sync"]["background"]["status"] == "failed"
    assert payload["sync"]["background"]["failure_count"] == 1
    assert payload["sync"]["background"]["next_attempt_at"] == 2030
    assert payload["actions"] == [
        "Background backend sync is backing off; run `hades backend sync` to retry now."
    ]


def test_manual_sync_success_clears_background_backoff_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import BACKGROUND_SYNC_STATE_KEY, run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.record_sync_state(
            conn,
            BACKGROUND_SYNC_STATE_KEY,
            {"status": "failed", "failure_count": 3, "next_attempt_at": 9999},
        )

    class FakeClient:
        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    result = run_backend_sync(client_factory=lambda: FakeClient())

    with hdb.connect_closing() as conn:
        state = hdb.get_sync_state(conn, BACKGROUND_SYNC_STATE_KEY)

    assert result.exit_code == 0
    assert state is None


def test_background_sync_skips_when_already_running(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    monkeypatch.setattr(sync, "_BACKGROUND_SYNC_RUNNING", True)

    decision = sync.maybe_run_backend_sync(now=3000, run_inline=True)

    assert decision.status == "skipped"
    assert decision.reason == "already_running"

from __future__ import annotations


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

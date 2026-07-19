from __future__ import annotations


def test_hades_backend_mvp_smoke_no_network(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HADES_BACKEND_AGENT_TOKEN_TEST", "derived-token")

    from hermes_cli import hades_backend_db as db
    import hermes_cli.doctor as doctor
    from hermes_cli.hades_backend_status import load_backend_status_payload
    from hermes_cli.hades_backend_sync import run_backend_sync
    import tui_gateway.server as tui_server

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("launch smoke\n", encoding="utf-8")

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True, "artifacts": True, "persephone": True},
        )
        db.upsert_workspace_binding(
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
        proposal = db.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="Remember smoke state",
            provenance={"source": "smoke"},
        )

    class FakeBackendClient:
        def __init__(self):
            self.statuses = []
            self.proposals = []
            self.artifacts = []
            self.results = []
            self.reports = []

        def memory_snapshot(self, **payload):
            return {"version": "v_remote", "items": [{"summary": "remote memory"}]}

        def create_memory_proposal(self, **payload):
            self.proposals.append(payload)
            return {"proposal": {"status": "accepted"}}

        def list_inbox(self, **payload):
            return {
                "events": [
                    {
                        "id": "evt_1",
                        "project_id": payload["project_id"],
                        "event_type": "proposal.reviewed",
                        "payload": {"message": "accepted"},
                    }
                ]
            }

        def pull_jobs(self, **payload):
            return {
                "jobs": [
                    {
                        "job_id": "job_tree",
                        "capability": "sync_git_tree",
                        "payload": {"max_files": 20, "max_bytes": 20_000, "max_file_bytes": 20_000},
                    }
                ]
            }

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

        def upload_artifact(self, **payload):
            self.artifacts.append(payload)
            return {"artifact": {"id": "artifact_1"}}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {}

        def submit_doctor_report(self, **payload):
            self.reports.append(payload)
            return {"report": {"id": "report_1"}}

    fake = FakeBackendClient()
    result = run_backend_sync(client_factory=lambda: fake)
    monkeypatch.setattr(doctor, "_hades_backend_client_from_config", lambda: fake)
    doctor._submit_hades_doctor_report([])

    with db.connect_closing() as conn:
        job = db.get_job(conn, "job_tree")
        proposals = db.list_memory_proposals(conn, ids=[proposal.id])
        cache = db.get_memory_cache(conn, "wb_1")
        inbox = db.list_inbox_events(conn, project_id="proj_1")
        last_summary = db.get_sync_state(conn, "last_sync_summary")

    tui_response = tui_server._methods["backend.status"](1, {})
    assert "error" not in tui_response, tui_response.get("error")
    status = load_backend_status_payload()

    assert result.exit_code == 0
    assert result.summary["duration_ms"] >= 0
    required_summary = {
        "pulled": 1,
        "completed": 1,
        "waiting": 0,
        "failed": 0,
        "skipped": 0,
        "expired": 0,
        "memory_snapshots": 1,
        "proposals_synced": 1,
        "proposal_errors": 0,
        "artifacts_uploaded": 1,
        "artifacts_skipped": 0,
        "artifact_errors": 0,
        "source_slices_uploaded": 0,
        "source_slice_errors": 0,
        "inbox_events": 1,
    }
    assert required_summary.items() <= result.summary.items()
    assert all(value >= 0 for value in result.summary.values() if isinstance(value, int))
    assert result.summary["completed"] + result.summary["failed"] <= result.summary["pulled"]
    assert job is not None
    assert job.status == "completed"
    assert proposals[0].status == "accepted"
    assert cache is not None
    assert cache.version == "v_remote"
    assert inbox[0].event_id == "evt_1"
    assert last_summary == result.summary
    assert fake.proposals[0]["local_proposal_id"] == proposal.id
    assert fake.artifacts[0]["schema"] == "hades.git_tree.v1"
    assert fake.results[0][0] == "job_tree"
    assert fake.reports[0]["payload"]["job_counts"] == {"completed": 1}
    assert status["configured"] is True
    assert status["degraded"] is False
    assert status["job_counts"] == {"completed": 1}
    assert status["proposal_counts"] == {"accepted": 1}
    assert status["inbox_counts"] == {"total": 1, "unread": 1}
    assert status["sync"]["last_summary"] == result.summary
    assert tui_response["result"] == status

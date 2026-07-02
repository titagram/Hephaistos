from __future__ import annotations

from fastapi.testclient import TestClient


def test_hades_backend_status_web_route_reports_canonical_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import web_server

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="sync_git_tree",
            payload={"paths": ["."]},
            status="waiting_confirmation",
        )
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="remember",
            summary="Rejected proposal",
            provenance={"source": "test"},
        )
        hdb.mark_memory_proposal_status(conn, proposal.id, "refused", "duplicate")
        hdb.save_inbox_event(
            conn,
            event_id="evt_1",
            project_id="proj_1",
            event_type="message",
            payload={"message": "hello"},
        )
        hdb.record_sync_state(conn, "last_sync_summary", {"completed": 1, "waiting": 1})

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        response = client.get("/api/hades/backend/status")
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["degraded"] is True
    assert body["agent"]["project_id"] == "proj_1"
    assert body["bindings"][0]["workspace_binding_id"] == "wb_1"
    assert body["job_counts"]["waiting_confirmation"] == 1
    assert body["proposal_counts"]["refused"] == 1
    assert body["inbox_counts"] == {"total": 1, "unread": 1}
    assert body["sync"]["last_summary"] == {"completed": 1, "waiting": 1}
    assert isinstance(body["sync"]["last_summary_updated_at"], int)
    assert any("backend job" in action for action in body["actions"])
    assert any("memory proposal" in action for action in body["actions"])


def test_hades_backend_web_routes_review_jobs_and_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_actions
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime
    from hermes_cli import web_server

    class FakeBackendClient:
        def __init__(self):
            self.status_updates = []
            self.results = []

        def update_job_status(self, job_id, **payload):
            self.status_updates.append((job_id, payload))
            return {"ok": True}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {"ok": True}

    fake_client = FakeBackendClient()
    monkeypatch.setattr(hades_backend_runtime, "client_from_config", lambda: fake_client)
    monkeypatch.setattr(
        hades_backend_actions,
        "execute_job",
        lambda job, workspace_root: {"status": "completed", "summary": "tree synced"},
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(repo),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_refuse",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="sync_git_tree",
            payload={"paths": ["."]},
            status="waiting_confirmation",
        )
        hdb.upsert_job(
            conn,
            job_id="job_approve",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="sync_git_tree",
            payload={"paths": ["."]},
            status="waiting_confirmation",
        )
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="remember",
            summary="Rejected proposal",
            provenance={"source": "test"},
        )
        hdb.mark_memory_proposal_status(conn, proposal.id, "refused", "duplicate")

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        jobs_response = client.get("/api/hades/backend/jobs")
        proposals_response = client.get("/api/hades/backend/proposals")
        ack_response = client.post(f"/api/hades/backend/proposals/{proposal.id}/ack")
        refuse_response = client.post(
            "/api/hades/backend/jobs/job_refuse/refuse",
            json={"reason": "too broad"},
        )
        approve_response = client.post("/api/hades/backend/jobs/job_approve/approve")
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert jobs_response.status_code == 200
    assert {job["job_id"] for job in jobs_response.json()["jobs"]} == {"job_refuse", "job_approve"}
    assert proposals_response.status_code == 200
    assert proposals_response.json()["proposals"][0]["proposal_id"] == proposal.id

    assert ack_response.status_code == 200
    assert ack_response.json()["proposal"]["status"] == "acknowledged"
    assert refuse_response.status_code == 200
    assert refuse_response.json()["job"]["status"] == "cancelled"
    assert approve_response.status_code == 200
    assert approve_response.json()["job"]["status"] == "completed"
    assert any(payload["status"] == "cancelled" for _, payload in fake_client.status_updates)
    assert any(payload["status"] == "started" for _, payload in fake_client.status_updates)
    assert fake_client.results[0][0] == "job_approve"

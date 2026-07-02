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

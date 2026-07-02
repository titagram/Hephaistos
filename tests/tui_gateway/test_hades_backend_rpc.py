from __future__ import annotations

import tui_gateway.server as server


def _call(method, params=None):
    handler = server._methods[method]
    resp = handler(1, params or {})
    assert "error" not in resp, resp.get("error")
    return resp["result"]


def test_backend_status_reports_unconfigured_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = _call("backend.status")

    assert result["configured"] is False
    assert result["agent"] is None
    assert result["bindings"] == []
    assert result["degraded"] is False
    assert result["actions"] == []
    assert result["job_counts"] == {}
    assert result["proposal_counts"] == {}
    assert result["inbox_counts"] == {"total": 0, "unread": 0}
    assert result["sync"]["background"] is None
    assert result["sync"]["background_updated_at"] is None
    assert result["sync"]["last_error"] is None
    assert result["sync"]["last_error_updated_at"] is None
    assert result["sync"]["last_summary"] is None
    assert result["sync"]["last_summary_updated_at"] is None


def test_backend_status_reports_agent_and_bindings(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint="fp_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        db.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )
        proposal = db.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="Remember backend contract",
            provenance={},
        )
        db.mark_memory_proposal_status(conn, proposal.id, "conflicted", "superseded")
        db.save_inbox_event(
            conn,
            event_id="evt_1",
            project_id="proj_1",
            event_type="message",
            payload={"text": "hello"},
        )
        db.record_sync_state(conn, "last_sync_error", {"message": "backend unavailable"})

    result = _call("backend.status")

    assert result["configured"] is True
    assert result["agent"]["agent_id"] == "agent_1"
    assert result["bindings"][0]["workspace_binding_id"] == "wb_1"
    assert result["job_counts"] == {"waiting_confirmation": 1}
    assert result["proposal_counts"] == {"conflicted": 1}
    assert result["inbox_counts"] == {"total": 1, "unread": 1}
    assert result["sync"]["last_error"]["message"] == "backend unavailable"
    assert isinstance(result["sync"]["last_error_updated_at"], int)
    assert result["degraded"] is True
    assert result["actions"] == [
        "Review 1 backend job(s) waiting for confirmation.",
        "Review 1 refused/conflicted memory proposal(s).",
        "Inspect last backend sync error and rerun `hades backend sync`.",
    ]

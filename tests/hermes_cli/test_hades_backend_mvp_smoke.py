from __future__ import annotations


def test_hades_backend_local_status_smoke_never_constructs_client(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as db
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli.hades_backend_status import load_backend_status_payload
    import tui_gateway.server as tui_server

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="TOKEN_TEST",
            capabilities={"sync_git_tree": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        db.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "artifacts_uploaded": 1,
                "artifacts_skipped": 0,
                "artifact_errors": 0,
                "source_slice_candidates": 3,
            },
        )

    client_constructions = []

    def fail_if_client_is_constructed(**kwargs):
        client_constructions.append(kwargs)
        raise AssertionError("local status smoke must not contact Backend")

    monkeypatch.setattr(runtime, "client_from_config", fail_if_client_is_constructed)
    local_status = load_backend_status_payload(cwd=workspace)
    rpc_response = tui_server._methods["backend.status"](1, {})

    assert "error" not in rpc_response, rpc_response.get("error")
    assert client_constructions == []
    assert local_status["configured"] is True
    assert local_status["bindings"][0]["workspace_binding_id"] == "wb_1"
    assert local_status["sync"]["last_summary"]["artifacts_uploaded"] == 1
    assert rpc_response["result"]["sync"]["last_summary"] == local_status["sync"]["last_summary"]

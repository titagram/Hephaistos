from __future__ import annotations


def test_hades_backend_memory_provider_piggybacks_sync_once_per_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    import plugins.memory.hades_backend as provider_mod

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    calls = []
    monkeypatch.setattr(provider_mod, "run_backend_sync", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(provider_mod.time, "time", lambda: 1000)

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    provider.sync_turn("user", "assistant", session_id="session_1")
    provider.sync_turn("user again", "assistant again", session_id="session_1")

    assert len(calls) == 1
    assert calls[0]["quiet"] is True

from __future__ import annotations

from types import SimpleNamespace


def test_current_agent_prefers_agent_bound_to_current_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    current_workspace = tmp_path / "current"
    other_workspace = tmp_path / "other"
    current_workspace.mkdir()
    other_workspace.mkdir()

    from hermes_cli import hades_backend_db as db
    from hermes_cli import hades_backend_runtime as runtime

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_current_workspace",
            project_id="project_current_workspace",
            base_url="https://backend.example",
            label="current-workspace",
            token_env_key="TOKEN_CURRENT_WORKSPACE",
            capabilities={},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="project_current_workspace",
            agent_id="agent_current_workspace",
            local_project_id="local_current_workspace",
            workspace_fingerprint="fingerprint_current_workspace",
            display_path=str(current_workspace),
            repo_root=str(current_workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_current_workspace",
        )
        db.save_agent(
            conn,
            agent_id="agent_newer_default",
            project_id="project_newer_default",
            base_url="https://backend.example",
            label="newer-default",
            token_env_key="TOKEN_NEWER_DEFAULT",
            capabilities={},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="project_newer_default",
            agent_id="agent_newer_default",
            local_project_id="local_newer_default",
            workspace_fingerprint="fingerprint_newer_default",
            display_path=str(other_workspace),
            repo_root=str(other_workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_newer_default",
        )

    monkeypatch.chdir(current_workspace)

    assert runtime.current_agent().agent_id == "agent_current_workspace"


def test_plugin_work_items_token_does_not_fallback_to_agent_token(monkeypatch):
    from hermes_cli import hades_backend_runtime as runtime

    agent = SimpleNamespace(token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST")
    secrets = {"HADES_BACKEND_AGENT_TOKEN_TEST": "agent-token"}

    monkeypatch.setattr(runtime, "backend_config", lambda: {})
    monkeypatch.setattr(runtime, "get_secret", lambda key, default="": secrets.get(key, default))

    assert runtime.agent_token(agent) == "agent-token"
    assert runtime.plugin_work_items_token(agent) == ""


def test_plugin_work_items_token_uses_configured_plugin_secret(monkeypatch):
    from hermes_cli import hades_backend_runtime as runtime

    agent = SimpleNamespace(token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST")
    secrets = {
        "HADES_BACKEND_AGENT_TOKEN_TEST": "agent-token",
        "HADES_BACKEND_PLUGIN_TOKEN_TEST": "plugin-token",
    }

    monkeypatch.setattr(
        runtime,
        "backend_config",
        lambda: {"plugin_token_env_key": "HADES_BACKEND_PLUGIN_TOKEN_TEST"},
    )
    monkeypatch.setattr(runtime, "get_secret", lambda key, default="": secrets.get(key, default))

    assert runtime.plugin_work_items_token(agent) == "plugin-token"


def test_plugin_work_items_client_uses_configured_device_id(monkeypatch):
    from hermes_cli import hades_backend_runtime as runtime

    captured = {}
    agent = SimpleNamespace(base_url="https://backend.example", token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST")

    class FakeClient:
        def __init__(self, base_url, token, **kwargs):
            captured["base_url"] = base_url
            captured["token"] = token
            captured["kwargs"] = kwargs

    monkeypatch.setattr(runtime, "current_agent", lambda: agent)
    monkeypatch.setattr(
        runtime,
        "backend_config",
        lambda: {
                "plugin_token_env_key": "HADES_BACKEND_PLUGIN_TOKEN_TEST",
                "plugin_device_id": "dev_1",
                "plugin_device_secret_env_key": "HADES_BACKEND_PLUGIN_DEVICE_SECRET_TEST",
            },
        )
    monkeypatch.setattr(
        runtime,
        "get_secret",
        lambda key, default="": {
            "HADES_BACKEND_PLUGIN_TOKEN_TEST": "plugin-token",
            "HADES_BACKEND_PLUGIN_DEVICE_SECRET_TEST": "device-secret",
        }.get(key, default),
    )
    monkeypatch.setattr(runtime, "HadesPluginWorkItemsClient", FakeClient)

    runtime.plugin_work_items_client_from_config()

    assert captured == {
        "base_url": "https://backend.example",
        "token": "plugin-token",
        "kwargs": {"device_id": "dev_1", "device_secret": "device-secret"},
    }

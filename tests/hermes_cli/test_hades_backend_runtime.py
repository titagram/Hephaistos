from __future__ import annotations

from types import SimpleNamespace


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

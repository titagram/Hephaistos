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
        },
    )
    monkeypatch.setattr(runtime, "get_secret", lambda key, default="": {"HADES_BACKEND_PLUGIN_TOKEN_TEST": "plugin-token"}.get(key, default))
    monkeypatch.setattr(runtime, "HadesPluginWorkItemsClient", FakeClient)

    runtime.plugin_work_items_client_from_config()

    assert captured == {
        "base_url": "https://backend.example",
        "token": "plugin-token",
        "kwargs": {"device_id": "dev_1"},
    }

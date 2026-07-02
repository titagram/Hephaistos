from __future__ import annotations

import json

import httpx


def test_plugin_work_items_client_uses_plugin_routes_and_bearer_auth():
    from hermes_cli.hades_plugin_work_items_client import HadesPluginWorkItemsClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer plugin-token"
        if request.method == "GET":
            assert request.url.path == "/api/plugin/v1/agent-work-items"
            assert request.url.params["project_id"] == "proj_1"
            assert request.url.params["agent_key"] == "local_agent"
            return httpx.Response(200, json={"items": [{"id": "awi_1"}]})
        payload = json.loads(request.content.decode("utf-8"))
        if request.url.path.endswith("/claim"):
            assert payload == {"local_workspace_id": "lw_1"}
            return httpx.Response(200, json={"lease_token": "lease_1", "item": {"id": "awi_1"}})
        if request.url.path.endswith("/heartbeat"):
            assert payload == {"lease_token": "lease_1"}
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    client = HadesPluginWorkItemsClient(
        "https://backend.example",
        "plugin-token",
        transport=httpx.MockTransport(handler),
    )

    assert client.list_agent_work_items(project_id="proj_1", agent_key="local_agent")["items"][0]["id"] == "awi_1"
    assert client.claim_agent_work_item("awi_1", local_workspace_id="lw_1")["lease_token"] == "lease_1"
    assert client.heartbeat_agent_work_item("awi_1", lease_token="lease_1") == {"ok": True}
    assert [request.url.path for request in seen] == [
        "/api/plugin/v1/agent-work-items",
        "/api/plugin/v1/agent-work-items/awi_1/claim",
        "/api/plugin/v1/agent-work-items/awi_1/heartbeat",
    ]


def test_plugin_complete_sends_chat_message_and_optional_memory_entry():
    from hermes_cli.hades_plugin_work_items_client import HadesPluginWorkItemsClient

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/plugin/v1/agent-work-items/awi_1/complete"
        bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True})

    client = HadesPluginWorkItemsClient(
        "https://backend.example",
        "plugin-token",
        transport=httpx.MockTransport(handler),
    )

    assert client.complete_agent_work_item(
        "awi_1",
        lease_token="lease_1",
        chat_message="Done from local Hades.",
        memory_entry={"kind": "agent_note", "summary": "done", "payload": {"changed": []}},
    ) == {"ok": True}

    assert bodies == [
        {
            "lease_token": "lease_1",
            "chat_message": "Done from local Hades.",
            "memory_entry": {"kind": "agent_note", "summary": "done", "payload": {"changed": []}},
        }
    ]


def test_plugin_fail_uses_message_payload_and_redacts_errors():
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_plugin_work_items_client import HadesPluginWorkItemsClient

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert request.url.path == "/api/plugin/v1/agent-work-items/awi_1/fail"
        assert payload == {"lease_token": "lease_1", "message": "nope"}
        return httpx.Response(500, json={"message": "token=super-secret-token failed"})

    client = HadesPluginWorkItemsClient(
        "https://backend.example",
        "plugin-token",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.fail_agent_work_item("awi_1", lease_token="lease_1", message="nope")
    except HadesBackendError as exc:
        text = str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected HadesBackendError")

    assert "500" in text
    assert "super-secret-token" not in text

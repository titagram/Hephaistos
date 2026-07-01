from __future__ import annotations

import json

import httpx


def test_client_uses_hades_v1_routes_and_bearer_auth():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/api/hades/v1/agents/register"
        assert request.headers["authorization"] == "Bearer bootstrap-token"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["project_id"] == "proj_1"
        assert payload["agent_id"] == "agent_1"
        return httpx.Response(
            200,
            json={
                "agent_id": "agent_1",
                "agent_token": "derived-token",
                "capabilities": {"memory": True, "jobs": True},
            },
        )

    client = HadesBackendClient(
        "https://backend.example",
        "bootstrap-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.register_agent(
        project_id="proj_1",
        agent_id="agent_1",
        label="dev-machine",
        platform="darwin",
        version="0.17.0",
        capabilities=["read_files"],
    )

    assert response["agent_token"] == "derived-token"
    assert seen


def test_token_env_key_is_stable_and_redaction_hides_tokens():
    from hermes_cli.hades_backend_client import redact_secret, token_env_key

    first = token_env_key("https://backend.example", "proj_1", "agent_1")
    second = token_env_key("https://backend.example/", "proj_1", "agent_1")

    assert first == second
    assert first.startswith("HADES_BACKEND_AGENT_TOKEN_")
    assert first.isupper()
    assert "sk-live-secret" not in redact_secret("token=sk-live-secret")
    assert "derived-token" not in redact_secret("Bearer derived-token")


def test_client_raises_backend_error_with_redacted_body():
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad token sk-live-secret"})

    client = HadesBackendClient(
        "https://backend.example",
        "sk-live-secret",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.health()
    except HadesBackendError as exc:
        text = str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected HadesBackendError")

    assert "401" in text
    assert "sk-live-secret" not in text


def test_get_payloads_use_query_params_for_laravel_routes():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/hades/v1/agent/jobs"
        assert request.content == b""
        assert request.url.params["project_id"] == "proj_1"
        assert request.url.params["workspace_binding_id"] == "wb_1"
        assert request.url.query.decode("utf-8").count("capabilities%5B%5D=") == 2
        return httpx.Response(200, json={"jobs": []})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.pull_jobs(
        project_id="proj_1",
        workspace_binding_id="wb_1",
        capabilities=["read_files", "sync_git_tree"],
    )

    assert response == {"jobs": []}
    assert seen


def test_client_posts_doctor_reports_and_persephone_messages():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append((request.method, request.url.path, payload))
        return httpx.Response(201, json={"ok": True})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    assert client.submit_doctor_report(project_id="proj_1", status="warning", payload={"checks": []}) == {"ok": True}
    assert client.create_inbox_message(project_id="proj_1", event_type="proposal.reviewed", payload={"message": "done"}) == {"ok": True}
    assert seen == [
        (
            "POST",
            "/api/hades/v1/doctor/reports",
            {"project_id": "proj_1", "status": "warning", "payload": {"checks": []}},
        ),
        (
            "POST",
            "/api/hades/v1/persephone/messages",
            {"project_id": "proj_1", "event_type": "proposal.reviewed", "payload": {"message": "done"}},
        ),
    ]

from __future__ import annotations

import json
import random

import httpx
import pytest

from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError
from hermes_cli.hades_persephone_messages import (
    AGENT_MESSAGE_SCHEMA,
    EffectClass,
    MessageType,
    parse_envelope,
)


def _envelope(*, message_id: str = "msg_1"):
    return parse_envelope(
        {
            "schema": AGENT_MESSAGE_SCHEMA,
            "message_id": message_id,
            "correlation_id": "corr_1",
            "causation_id": None,
            "project_id": "project_1",
            "sender_agent_id": "agent_sender",
            "target_agent_id": "agent_target",
            "target_workspace_binding_id": None,
            "message_type": MessageType.INFORMATION_REQUEST.value,
            "effect": EffectClass.INFORMATION_READ.value,
            "capability": "project_memory_search",
            "remote_task_id": None,
            "remote_task_version": None,
            "expires_at": 9_999_999_999,
            "payload": {"query": "where is the contract?"},
        },
        now=100,
    )


@pytest.fixture
def store(tmp_path):
    from hermes_cli import hades_backend_db as db

    with db.connect_closing(tmp_path / "hades.db") as conn:
        yield conn


def test_client_sse_parser_resumes_with_exact_target_query_and_limit():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = (
            'id: 43\nevent: message\ndata: {"id":"43","payload":{"n":1}}\n\n'
            'id: 44\ndata: {"id":"44","payload":{"n":2}}\n\n'
            'id: 45\ndata: {"id":"45"}\n\n'
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream; charset=utf-8"}, text=body
        )

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )
    events = list(
        client.iter_persephone_events(
            project_id="project_1",
            target_agent_id="agent_target",
            cursor="42",
            limit=2,
        )
    )

    assert [event["id"] for event in events] == ["43", "44"]
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/hades/v1/persephone/events"
    assert dict(seen[0].url.params) == {
        "project_id": "project_1",
        "target_agent_id": "agent_target",
        "cursor": "42",
        "limit": "2",
    }


def test_client_sse_stops_on_explicit_stop_event_without_yielding_it():
    body = (
        'id: 43\ndata: {"id":"43"}\n\n'
        'event: stop\ndata: {"reason":"bounded"}\n\n'
        'id: 44\ndata: {"id":"44"}\n\n'
    )
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=body
            )
        ),
    )

    assert list(
        client.iter_persephone_events(
            project_id="project_1", target_agent_id="agent_target", limit=10
        )
    ) == [{"id": "43"}]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(200, headers={"content-type": "application/json"}, json={}),
            "content type",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: not-json\n\n",
            ),
            "malformed",
        ),
    ],
)
def test_client_sse_rejects_wrong_content_type_and_malformed_data(response, error):
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(lambda request: response),
    )

    with pytest.raises(HadesBackendError, match=error):
        list(
            client.iter_persephone_events(
                project_id="project_1", target_agent_id="agent_target", limit=2
            )
        )


def test_stream_wrapper_falls_back_to_polling_with_the_same_cursor():
    from hermes_cli.hades_persephone_transport import iter_persephone_events

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        return httpx.Response(200, json={"events": [{"id": "43"}], "cursor": "43"})

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )
    result = list(
        iter_persephone_events(
            client,
            project_id="project_1",
            target_agent_id="agent_target",
            target_workspace_binding_id="binding_1",
            cursor="42",
            limit=2,
        )
    )

    assert result == [{"id": "43"}]
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "events",
        "inbox",
    ]
    for request in requests:
        assert dict(request.url.params) == {
            "project_id": "project_1",
            "target_agent_id": "agent_target",
            "target_workspace_binding_id": "binding_1",
            "cursor": "42",
            "limit": "2",
        }


@pytest.mark.parametrize("status", [400, 401, 403, 409, 410, 422])
def test_terminal_stream_http_errors_never_fall_back_to_polling(status):
    from hermes_cli.hades_persephone_transport import iter_persephone_events

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(status, text="raw-rejected-body-must-not-escape")

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(HadesBackendError) as caught:
        list(
            iter_persephone_events(
                client,
                project_id="project_1",
                target_agent_id="agent_target",
                cursor="42",
                limit=2,
            )
        )

    assert caught.value.status_code == status
    assert "raw-rejected-body-must-not-escape" not in str(caught.value)
    assert requests == ["/api/hades/v1/persephone/events"]


@pytest.mark.parametrize("status", [404, 405, 406, 408, 415, 425, 429, 500, 501, 503])
def test_unavailable_or_transient_stream_http_errors_fall_back_once(status):
    from hermes_cli.hades_persephone_transport import iter_persephone_events

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/events"):
            return httpx.Response(status, text="raw-stream-body")
        return httpx.Response(200, json={"events": [{"id": "43"}]})

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )
    assert list(
        iter_persephone_events(
            client,
            project_id="project_1",
            target_agent_id="agent_target",
            cursor="42",
            limit=2,
        )
    ) == [{"id": "43"}]
    assert requests == [
        "/api/hades/v1/persephone/events",
        "/api/hades/v1/persephone/inbox",
    ]


def test_malformed_stream_after_an_event_falls_back_without_partial_duplicates():
    from hermes_cli.hades_persephone_transport import iter_persephone_events

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text='id: 43\ndata: {"id":"43"}\n\ndata: malformed\n\n',
            )
        return httpx.Response(
            200, json={"events": [{"id": "43"}, {"id": "44"}], "cursor": "44"}
        )

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )

    assert list(
        iter_persephone_events(
            client,
            project_id="project_1",
            target_agent_id="agent_target",
            cursor="42",
            limit=2,
        )
    ) == [{"id": "43"}, {"id": "44"}]
    assert [request.url.params["cursor"] for request in requests] == ["42", "42"]


def test_poll_rejects_malformed_backend_shape_without_echoing_payload():
    from hermes_cli.hades_persephone_transport import poll_persephone_events

    secret_payload = "do-not-echo-this-payload"
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"events": secret_payload})
        ),
    )

    with pytest.raises(HadesBackendError) as caught:
        poll_persephone_events(
            client,
            project_id="project_1",
            target_agent_id="agent_target",
            cursor="42",
            limit=2,
        )
    assert secret_payload not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            500,
            json={"error": {"code": "temporary_failure", "message": "unguessable-body-secret"}},
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            text="unguessable-invalid-json-secret",
        ),
    ],
)
def test_poll_sanitizes_backend_error_and_invalid_json_bodies(response):
    from hermes_cli.hades_persephone_transport import poll_persephone_events

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(lambda request: response),
    )
    with pytest.raises(HadesBackendError) as caught:
        poll_persephone_events(
            client,
            project_id="project_1",
            target_agent_id="agent_target",
            cursor="42",
            limit=2,
        )

    assert "unguessable" not in str(caught.value)
    if response.status_code == 500:
        assert caught.value.status_code == 500
        assert caught.value.code == "temporary_failure"


@pytest.mark.parametrize("limit", [0, 101, True])
def test_poll_enforces_openapi_limit_before_network(limit):
    from hermes_cli.hades_persephone_transport import poll_persephone_events

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"events": []})

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ValueError, match="limit"):
        poll_persephone_events(
            client,
            project_id="project_1",
            target_agent_id="agent_target",
            limit=limit,
        )
    assert calls == 0


def test_transient_send_failure_schedules_exponential_jittered_retry(store):
    from hermes_cli.hades_persephone_store import enqueue_outbox, get_message
    from hermes_cli.hades_persephone_transport import RetryPolicy, send_due_messages

    class FakeClient:
        def create_inbox_message(self, **payload):
            raise HadesBackendError("timeout and raw payload must not be stored")

    enqueue_outbox(store, _envelope(), now=100)
    result = send_due_messages(
        store,
        FakeClient(),
        now=100,
        retry=RetryPolicy(base=4, maximum=60, jitter=0.25, max_attempts=4),
        rng=random.Random(7),
    )
    row = get_message(store, "msg_1", queue="outbox")

    assert result == {"sent": 0, "retry": 1, "dead_letter": 0}
    assert row is not None
    assert row.state == "retry"
    assert 103 <= row.next_attempt_at <= 105
    assert row.last_error == "transport_error"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 406, 410, 415, 422])
def test_terminal_schema_or_auth_4xx_goes_directly_to_dead_letter(store, status):
    from hermes_cli.hades_persephone_store import enqueue_outbox, get_message
    from hermes_cli.hades_persephone_transport import send_due_messages

    class FakeClient:
        def create_inbox_message(self, **payload):
            raise HadesBackendError(
                "unsafe body", status_code=status, code="validation_failed"
            )

    enqueue_outbox(store, _envelope(), now=100)
    result = send_due_messages(store, FakeClient(), now=100)
    row = get_message(store, "msg_1", queue="outbox")

    assert result == {"sent": 0, "retry": 0, "dead_letter": 1}
    assert row is not None
    assert row.state == "dead_letter"
    assert row.last_error == f"http_{status}:validation_failed"


def test_transient_4xx_retries_but_max_attempts_dead_letters(store):
    from hermes_cli.hades_persephone_store import (
        enqueue_outbox,
        get_message,
        transition_message,
    )
    from hermes_cli.hades_persephone_transport import RetryPolicy, send_due_messages

    class FakeClient:
        def create_inbox_message(self, **payload):
            raise HadesBackendError("busy", status_code=429)

    enqueue_outbox(store, _envelope(), now=100)
    # Seed two completed attempts; the next claim is attempt 3 and reaches the cap.
    store.execute(
        "UPDATE persephone_outbox SET state = 'sending', attempts = 2 WHERE message_id = 'msg_1'"
    )
    store.commit()
    transition_message(
        store, "msg_1", "retry", queue="outbox", now=100, next_attempt_at=100
    )
    result = send_due_messages(
        store, FakeClient(), now=100, retry=RetryPolicy(max_attempts=3)
    )
    row = get_message(store, "msg_1", queue="outbox")

    assert result == {"sent": 0, "retry": 0, "dead_letter": 1}
    assert row is not None
    assert row.attempts == 3
    assert row.state == "dead_letter"
    assert row.last_error == "http_429:max_attempts"


def test_successful_delivery_sends_exact_envelope_and_marks_sent(store):
    from hermes_cli.hades_persephone_store import enqueue_outbox, get_message
    from hermes_cli.hades_persephone_transport import send_due_messages

    sent: list[dict] = []

    class FakeClient:
        def create_inbox_message(self, **payload):
            sent.append(payload)
            return {"ok": True}

    envelope = _envelope()
    enqueue_outbox(store, envelope, now=100)
    result = send_due_messages(store, FakeClient(), now=100)
    row = get_message(store, "msg_1", queue="outbox")

    assert result == {"sent": 1, "retry": 0, "dead_letter": 0}
    assert sent == [envelope.to_dict()]
    assert row is not None and row.state == "sent"


def test_send_due_messages_filters_by_project_and_sender(store):
    from hermes_cli.hades_persephone_store import enqueue_outbox, get_message
    from hermes_cli.hades_persephone_transport import send_due_messages

    sent: list[str] = []

    class FakeClient:
        def create_inbox_message(self, **payload):
            sent.append(payload["message_id"])
            return {"ok": True}

    enqueue_outbox(store, _envelope(message_id="sender_a"), now=100)
    other = parse_envelope(
        {**_envelope(message_id="sender_b").to_dict(), "sender_agent_id": "agent_other"},
        now=100,
    )
    enqueue_outbox(store, other, now=100)

    result = send_due_messages(
        store,
        FakeClient(),
        now=100,
        project_id="project_1",
        sender_agent_id="agent_other",
    )

    assert result == {"sent": 1, "retry": 0, "dead_letter": 0}
    assert sent == ["sender_b"]
    assert get_message(store, "sender_a", queue="outbox").state == "outbox_pending"


@pytest.mark.parametrize(
    "body",
    [
        "data: " + ("x" * 1_000_000) + "\n\n",
        ("data: " + ("x" * 40_000) + "\n") * 3,
        "id: " + ("x" * 10_000) + '\ndata: {"id":"43"}\n\n',
        "event: " + ("x" * 10_000) + '\ndata: {"id":"43"}\n\n',
    ],
)
def test_oversized_or_never_terminated_sse_blocks_are_rejected_safely(body):
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=body
            )
        ),
    )
    with pytest.raises(HadesBackendError, match="size limit") as caught:
        list(
            client.iter_persephone_events(
                project_id="project_1", target_agent_id="agent_target", limit=2
            )
        )
    assert "x" * 100 not in str(caught.value)


def test_unterminated_stream_is_cut_off_before_all_chunks_are_consumed():
    consumed = 0

    class EndlessLine(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal consumed
            for _ in range(100):
                consumed += 1
                yield b"x" * 40_000

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=EndlessLine(),
            )
        ),
    )
    with pytest.raises(HadesBackendError, match="size limit"):
        list(
            client.iter_persephone_events(
                project_id="project_1", target_agent_id="agent_target", limit=2
            )
        )

    assert consumed == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base", float("nan")),
        ("base", float("inf")),
        ("maximum", float("-inf")),
        ("jitter", float("nan")),
        ("jitter", float("inf")),
    ],
)
def test_retry_policy_rejects_non_finite_float_configuration(field, value):
    from hermes_cli.hades_persephone_transport import RetryPolicy

    kwargs = {field: value}
    with pytest.raises(ValueError, match="finite"):
        RetryPolicy(**kwargs)


def test_retry_policy_keeps_extreme_finite_values_bounded_and_deterministic():
    from hermes_cli.hades_persephone_transport import RetryPolicy

    policy = RetryPolicy(base=1e300, maximum=1e300, jitter=1.0, max_attempts=2)
    first = policy.delay(1, rng=random.Random(11))
    second = policy.delay(1, rng=random.Random(11))

    assert first == second
    assert 1 <= first <= int(policy.maximum)


@pytest.mark.parametrize(
    "value",
    [True, False, "3", 2.5, float("nan"), float("inf"), float("-inf"), 0, -1],
)
def test_retry_policy_requires_a_positive_integral_max_attempts(value):
    from hermes_cli.hades_persephone_transport import RetryPolicy

    with pytest.raises(ValueError, match="max_attempts must be a positive integer"):
        RetryPolicy(max_attempts=value)


def test_retry_exhaustion_dead_letters_exactly_at_configured_maximum(store):
    from hermes_cli.hades_persephone_store import enqueue_outbox, get_message
    from hermes_cli.hades_persephone_transport import RetryPolicy, send_due_messages

    calls = 0

    class FakeClient:
        def create_inbox_message(self, **payload):
            nonlocal calls
            calls += 1
            raise HadesBackendError("temporary", status_code=503)

    policy = RetryPolicy(base=1, maximum=1, jitter=0, max_attempts=2)
    enqueue_outbox(store, _envelope(), now=100)

    first = send_due_messages(store, FakeClient(), now=100, retry=policy)
    after_first = get_message(store, "msg_1", queue="outbox")
    second = send_due_messages(store, FakeClient(), now=101, retry=policy)
    after_second = get_message(store, "msg_1", queue="outbox")

    assert first == {"sent": 0, "retry": 1, "dead_letter": 0}
    assert after_first is not None
    assert after_first.state == "retry"
    assert after_first.attempts == 1
    assert second == {"sent": 0, "retry": 0, "dead_letter": 1}
    assert after_second is not None
    assert after_second.state == "dead_letter"
    assert after_second.attempts == 2
    assert calls == 2

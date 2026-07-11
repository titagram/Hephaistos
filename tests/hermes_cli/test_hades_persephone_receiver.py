from __future__ import annotations

from contextlib import contextmanager
import time

import pytest

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_persephone_messages import AGENT_MESSAGE_SCHEMA


NOW = 2_000_000_000


def _binding(
    *, project: str = "project_a", agent: str = "agent_a", binding: str = "wb_a"
) -> db.WorkspaceBinding:
    return db.WorkspaceBinding(
        workspace_fingerprint=f"fingerprint_{binding}",
        project_id=project,
        agent_id=agent,
        local_project_id=f"local_{project}",
        backend_workspace_binding_id=binding,
        display_path=f"~/{binding}",
        repo_root=f"/tmp/{binding}",
        git_remote_display="",
        git_remote_hash="",
        head_commit="",
        status="linked",
    )


def _envelope(
    *,
    message_id: str = "msg_1",
    project: str = "project_a",
    agent: str = "agent_a",
    binding: str | None = "wb_a",
    message_type: str = "information_request",
    effect: str = "information_read",
    capability: str = "source_search",
    expires_at: int = NOW + 100,
) -> dict:
    return {
        "schema": AGENT_MESSAGE_SCHEMA,
        "message_id": message_id,
        "correlation_id": f"corr_{message_id}",
        "causation_id": None,
        "project_id": project,
        "sender_agent_id": "sender",
        "target_agent_id": agent,
        "target_workspace_binding_id": binding,
        "message_type": message_type,
        "effect": effect,
        "capability": capability,
        "remote_task_id": None,
        "remote_task_version": None,
        "expires_at": expires_at,
        "payload": {"query": "Where is AuthService defined?"},
    }


def _event(**kwargs) -> dict:
    envelope = _envelope(**kwargs)
    return {"id": f"cursor_{envelope['message_id']}", "payload": envelope}


@pytest.fixture
def receiver(tmp_path):
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver

    path = tmp_path / "receiver.db"

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    return PersephoneReceiver(connection_factory=connections, now=lambda: NOW)


def test_receiver_routes_project_b_while_started_from_project_a(receiver):
    receiver.refresh_bindings(
        [_binding(), _binding(project="project_b", agent="agent_b", binding="wb_b")]
    )

    result = receiver.ingest_event(
        _event(project="project_b", agent="agent_b", binding="wb_b")
    )

    assert result == "accepted"


def test_sse_cursor_can_wrap_a_top_level_envelope(receiver):
    receiver.refresh_bindings([_binding()])
    event = _envelope()
    event["id"] = "opaque_sse_cursor"

    assert receiver.ingest_event(event) == "accepted"


def test_missing_binding_never_falls_back(receiver):
    receiver.refresh_bindings([_binding()])

    result = receiver.ingest_event(_event(binding="missing"))

    assert result == "target_binding_unavailable"


def test_binding_must_belong_to_exact_project_and_agent(receiver):
    receiver.refresh_bindings([_binding(project="other", agent="agent_a", binding="wb_a")])

    assert receiver.ingest_event(_event(binding="wb_a")) == "receiver_route_unavailable"


@pytest.mark.parametrize(
    ("effect", "capability"),
    [
        ("mutating", "run_tests"),
        ("information_read", "run_tests"),
        ("information_read", "unknown_future_tool"),
    ],
)
def test_mutating_ambiguous_or_unsupported_request_waits_for_human(
    receiver, effect, capability
):
    receiver.refresh_bindings([_binding()])

    result = receiver.ingest_event(_event(effect=effect, capability=capability))

    assert result == "waiting_human_approval"


def test_only_information_requests_are_auto_accepted(receiver):
    receiver.refresh_bindings([_binding()])

    result = receiver.ingest_event(
        _event(message_type="cancel_request", capability="source_search")
    )

    assert result == "waiting_human_approval"


def test_receiver_dispatches_only_auto_accepted_information_requests(tmp_path):
    from contextlib import contextmanager

    from hermes_cli.hades_persephone_receiver import PersephoneReceiver

    path = tmp_path / "dispatch.db"

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    calls = []

    def execute(conn, message_id, *, binding, now, response_message_id):
        calls.append((message_id, binding.backend_workspace_binding_id, now, response_message_id))

    receiver = PersephoneReceiver(
        connection_factory=connections,
        information_executor=execute,
        response_id_factory=lambda: "response_1",
        now=lambda: NOW,
    )
    receiver.refresh_bindings([_binding()])

    assert receiver.ingest_event(_event()) == "retry_pending"
    assert calls == [("msg_1", "wb_a", NOW, "response_1")]

    assert receiver.ingest_event(_event(message_id="mutating", capability="run_tests")) == "waiting_human_approval"
    assert len(calls) == 1


def test_receiver_worker_atomically_enqueues_information_response(tmp_path):
    from contextlib import contextmanager

    from hermes_cli.hades_information_worker import execute_stored_information_request
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import get_message

    path = tmp_path / "integrated.db"
    (tmp_path / "module.py").write_text("needle = True\n", encoding="utf-8")

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    binding = _binding()
    object.__setattr__(binding, "repo_root", str(tmp_path))
    receiver = PersephoneReceiver(
        connection_factory=connections,
        information_executor=execute_stored_information_request,
        response_id_factory=lambda: "response_integrated",
        now=lambda: NOW,
    )
    receiver.refresh_bindings([binding])

    assert receiver.ingest_event(_event()) == "accepted"
    with connections() as conn:
        request = get_message(conn, "msg_1")
        response = get_message(conn, "response_integrated", queue="outbox")
    assert request is not None and request.state == "responded"
    assert response is not None and response.envelope.message_type.value == "information_response"


def test_failed_worker_redelivery_retries_once_with_deterministic_response(tmp_path):
    from contextlib import contextmanager

    from hermes_cli.hades_information_worker import execute_stored_information_request
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import (
        get_message,
        record_information_failure,
    )

    path = tmp_path / "retry.db"
    (tmp_path / "module.py").write_text("needle = True\n", encoding="utf-8")

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    calls = 0

    def flaky(conn, message_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            record_information_failure(conn, message_id, now=kwargs["now"])
            return None
        return execute_stored_information_request(conn, message_id, **kwargs)

    binding = _binding()
    object.__setattr__(binding, "repo_root", str(tmp_path))
    receiver = PersephoneReceiver(
        connection_factory=connections,
        information_executor=flaky,
        now=lambda: NOW,
    )
    receiver.refresh_bindings([binding])
    event = _event()

    assert receiver.ingest_event(event) == "retry_pending"
    assert receiver.ingest_event(event) == "accepted"

    with connections() as conn:
        request = get_message(conn, "msg_1")
        outbox_count = conn.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0]
        response_id = request.response_message_id
    assert calls == 2
    assert request is not None and request.state == "responded"
    assert outbox_count == 1
    assert response_id == receiver._response_message_id(request.envelope)


def test_terminal_duplicate_repairs_cursor_without_reexecuting(tmp_path):
    from contextlib import contextmanager

    from hermes_cli.hades_information_worker import execute_stored_information_request
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import get_cursor

    path = tmp_path / "cursor-repair.db"
    (tmp_path / "module.py").write_text("needle = True\n", encoding="utf-8")

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    calls = 0

    def execute(*args, **kwargs):
        nonlocal calls
        calls += 1
        return execute_stored_information_request(*args, **kwargs)

    binding = _binding()
    object.__setattr__(binding, "repo_root", str(tmp_path))
    receiver = PersephoneReceiver(
        connection_factory=connections,
        information_executor=execute,
        now=lambda: NOW,
    )
    receiver.refresh_bindings([binding])
    event = _event()
    assert receiver.ingest_event(event) == "accepted"
    with connections() as conn:
        conn.execute("DELETE FROM persephone_cursors")
        conn.commit()

    assert receiver.ingest_event(event) == "accepted"
    with connections() as conn:
        cursor = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
    assert calls == 1
    assert cursor == "cursor_msg_1"


def test_executor_refresh_recovers_abandoned_processing_before_redelivery(tmp_path):
    from contextlib import contextmanager

    from hermes_cli.hades_information_worker import execute_stored_information_request
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import get_message

    path = tmp_path / "startup-recovery.db"
    (tmp_path / "module.py").write_text("needle = True\n", encoding="utf-8")

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    binding = _binding()
    object.__setattr__(binding, "repo_root", str(tmp_path))
    crashed = PersephoneReceiver(
        connection_factory=connections,
        information_executor=lambda *args, **kwargs: None,
        now=lambda: NOW,
    )
    crashed.refresh_bindings([binding])
    assert crashed.ingest_event(_event()) == "retry_pending"

    restarted = PersephoneReceiver(
        connection_factory=connections,
        information_executor=execute_stored_information_request,
        now=lambda: NOW + 31,
    )
    restarted.refresh_bindings([binding])
    assert restarted.ingest_event(_event()) == "accepted"
    with connections() as conn:
        stored = get_message(conn, "msg_1")
        outbox_count = conn.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0]
    assert stored is not None and stored.state == "responded"
    assert outbox_count == 1


def test_expired_event_is_durable_then_marked_expired(receiver):
    from hermes_cli.hades_persephone_store import get_message

    receiver.refresh_bindings([_binding()])
    event = _event(expires_at=NOW)

    assert receiver.ingest_event(event) == "expired"
    with receiver.connection_factory() as conn:
        stored = get_message(conn, "msg_1")
    assert stored is not None
    assert stored.state == "expired"


def test_duplicate_ingestion_is_idempotent_and_keeps_cursor(receiver):
    from hermes_cli.hades_persephone_store import get_cursor, get_message

    receiver.refresh_bindings([_binding()])
    event = _event()

    assert receiver.ingest_event(event) == "accepted"
    assert receiver.ingest_event(event) == "accepted"
    with receiver.connection_factory() as conn:
        stored = get_message(conn, "msg_1")
        cursor = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
        count = conn.execute("SELECT COUNT(*) FROM persephone_inbox").fetchone()[0]
    assert stored is not None and stored.state == "received"
    assert cursor is None
    assert count == 1


def test_old_duplicate_cannot_rewind_a_newer_opaque_cursor(receiver):
    from hermes_cli.hades_persephone_store import get_cursor

    receiver.refresh_bindings([_binding()])
    assert receiver.ingest_event(_event(message_id="old")) == "accepted"
    assert receiver.ingest_event(_event(message_id="new")) == "accepted"
    assert receiver.ingest_event(_event(message_id="old")) == "accepted"

    with receiver.connection_factory() as conn:
        cursor = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
    assert cursor is None


def test_capability_absence_disables_agent_dispatch_but_not_generic_inbox(receiver):
    receiver.refresh_bindings([_binding()], queue_capability=False)

    assert receiver.ingest_event(_event()) == "agent_queue_unsupported"


def test_refresh_ignores_unlinked_bindings(receiver):
    receiver.refresh_bindings([_binding(), _binding(binding="wb_old")])
    old = _binding(binding="wb_old")
    object.__setattr__(old, "status", "unlinked")
    receiver.refresh_bindings([_binding(), old])

    assert receiver.ingest_event(_event(binding="wb_old")) == "target_binding_unavailable"


def test_conflicting_duplicate_backend_binding_id_is_not_routed(receiver):
    receiver.refresh_bindings(
        [
            _binding(project="project_a", agent="agent_a", binding="wb_shared"),
            _binding(project="project_b", agent="agent_b", binding="wb_shared"),
        ]
    )

    assert (
        receiver.ingest_event(_event(binding="wb_shared"))
        == "target_binding_unavailable"
    )


def test_bounded_round_robin_does_not_starve_later_projects(tmp_path):
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver

    path = tmp_path / "fair.db"

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    calls: list[str] = []

    class Client:
        def __init__(self, project):
            self.project = project

        def capabilities(self):
            return {"persephone_agent_queue_v1": True}

    clients = {f"project_{n}": Client(f"project_{n}") for n in range(3)}
    receiver = PersephoneReceiver(
        connection_factory=connections,
        client_factory=lambda agent: clients[agent.project_id],
        event_reader=lambda client, **kwargs: calls.append(kwargs["project_id"]) or [],
        max_projects_per_cycle=1,
        batch_size=2,
        now=lambda: NOW,
    )
    bindings = [
        _binding(project=f"project_{n}", agent=f"agent_{n}", binding=f"wb_{n}")
        for n in range(3)
    ]
    agents = {
        f"agent_{n}": db.BackendAgent(
            agent_id=f"agent_{n}",
            project_id=f"project_{n}",
            base_url="https://example.invalid",
            label="test",
            token_env_key="TOKEN",
            capabilities={},
        )
        for n in range(3)
    }
    receiver.refresh_bindings(bindings, agents=agents)

    receiver.run_once()
    receiver.run_once()
    receiver.run_once()

    assert calls == ["project_0", "project_1", "project_2"]


def test_receiver_health_enters_bounded_backoff_and_recovers(tmp_path):
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver

    path = tmp_path / "health.db"
    clock = [NOW]
    calls = [0]

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    class Client:
        def capabilities(self):
            return {"persephone_agent_queue_v1": True}

    def events(client, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("temporary")
        return []

    receiver = PersephoneReceiver(
        connection_factory=connections,
        client_factory=lambda agent: Client(),
        event_reader=events,
        now=lambda: clock[0],
    )
    binding = _binding()
    agent = db.BackendAgent(
        agent_id="agent_a",
        project_id="project_a",
        base_url="https://example.invalid",
        label="test",
        token_env_key="TOKEN",
        capabilities={},
    )
    receiver.refresh_bindings([binding], agents={agent.agent_id: agent})

    receiver.run_once()
    health = receiver.health_snapshot()
    assert health["state"] == "backoff"
    assert health["failure_count"] == 1
    assert health["next_retry_at"] == NOW + 2

    receiver.run_once()
    assert calls[0] == 1
    clock[0] += 2
    receiver.run_once()
    assert receiver.health_snapshot()["state"] == "connected"


def test_worker_a_rejects_worker_b_envelope_without_contaminating_b_cursor(tmp_path):
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import get_cursor, get_message

    path = tmp_path / "subscription.db"

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    agents = {
        f"agent_{name}": db.BackendAgent(
            agent_id=f"agent_{name}",
            project_id=f"project_{name}",
            base_url="https://example.invalid",
            label=name,
            token_env_key=f"TOKEN_{name}",
            capabilities={},
        )
        for name in ("a", "b")
    }

    class Client:
        def capabilities(self):
            return {"persephone_agent_queue_v1": True}

        def close(self):
            pass

    receiver = PersephoneReceiver(
        connection_factory=connections,
        client_factory=lambda agent: Client(),
        event_reader=lambda client, **kwargs: (
            [_event(message_id="from_a_claiming_b", project="project_b", agent="agent_b", binding="wb_b")]
            if kwargs["project_id"] == "project_a"
            else []
        ),
        max_projects_per_cycle=1,
        now=lambda: NOW,
    )
    receiver.refresh_bindings(
        [
            _binding(project="project_a", agent="agent_a", binding="wb_a"),
            _binding(project="project_b", agent="agent_b", binding="wb_b"),
        ],
        agents=agents,
    )

    receiver.run_once()

    with connections() as conn:
        stored = get_message(conn, "from_a_claiming_b")
        cursor_a = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
        cursor_b = get_cursor(conn, project_id="project_b", target_agent_id="agent_b")
    assert stored is not None and stored.state == "received"
    assert cursor_a == "cursor_from_a_claiming_b"
    assert cursor_b is None

    # The envelope remains globally valid and is processed when B receives it.
    assert receiver.ingest_event(
        _event(
            message_id="from_a_claiming_b",
            project="project_b",
            agent="agent_b",
            binding="wb_b",
        ),
        expected_project_id="project_b",
        expected_target_agent_id="agent_b",
    ) == "accepted"
    with connections() as conn:
        from hermes_cli.hades_persephone_store import record_cursor

        record_cursor(
            conn,
            project_id="project_b",
            target_agent_id="agent_b",
            cursor="newer_b_cursor",
            now=NOW + 1,
        )
    assert receiver.ingest_event(
        _event(
            message_id="from_a_claiming_b",
            project="project_b",
            agent="agent_b",
            binding="wb_b",
        ),
        expected_project_id="project_b",
        expected_target_agent_id="agent_b",
    ) == "accepted"
    with connections() as conn:
        stored = get_message(conn, "from_a_claiming_b")
        cursor_b = get_cursor(conn, project_id="project_b", target_agent_id="agent_b")
        audit = conn.execute(
            "SELECT disposition FROM persephone_subscription_deliveries "
            "WHERE subscription_project_id = ? AND subscription_agent_id = ? "
            "AND message_id = ?",
            ("project_a", "agent_a", "from_a_claiming_b"),
        ).fetchone()
    assert stored is not None and stored.state == "received"
    assert cursor_b == "newer_b_cursor"
    assert audit is not None and audit["disposition"] == "subscription_route_mismatch"


def test_binding_scoped_subscription_rejects_other_binding(receiver):
    receiver.refresh_bindings([_binding(), _binding(binding="wb_other")])

    result = receiver.ingest_event(
        _event(binding="wb_other"),
        expected_project_id="project_a",
        expected_target_agent_id="agent_a",
        expected_workspace_binding_id="wb_a",
    )

    assert result == "subscription_route_mismatch"


def test_manual_poll_a_rejects_b_envelope_and_advances_only_a_cursor(receiver):
    from hermes_cli.hades_backend_sync import _sync_inbox
    from hermes_cli.hades_persephone_store import get_cursor, get_message

    receiver.refresh_bindings(
        [
            _binding(project="project_a", agent="agent_a", binding="wb_a"),
            _binding(project="project_b", agent="agent_b", binding="wb_b"),
        ]
    )
    response = {
        "events": [
            _event(
                message_id="manual_b",
                project="project_b",
                agent="agent_b",
                binding="wb_b",
            )
        ]
    }

    assert _sync_inbox(
        response,
        "project_a",
        receiver=receiver,
        target_agent_id="agent_a",
    ) == 1
    with receiver.connection_factory() as conn:
        stored = get_message(conn, "manual_b")
        cursor_a = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
        cursor_b = get_cursor(conn, project_id="project_b", target_agent_id="agent_b")
    assert stored is not None and stored.state == "received"
    assert cursor_a == "cursor_manual_b"
    assert cursor_b is None


def test_cross_subscription_duplicate_is_still_rejected_without_rewriting_b(receiver):
    from hermes_cli.hades_persephone_store import get_cursor, get_message

    receiver.refresh_bindings(
        [
            _binding(project="project_a", agent="agent_a", binding="wb_a"),
            _binding(project="project_b", agent="agent_b", binding="wb_b"),
        ]
    )
    event = _event(
        message_id="already_b",
        project="project_b",
        agent="agent_b",
        binding="wb_b",
    )
    assert receiver.ingest_event(event) == "accepted"

    assert receiver.ingest_event(
        event,
        expected_project_id="project_a",
        expected_target_agent_id="agent_a",
    ) == "subscription_route_mismatch"

    with receiver.connection_factory() as conn:
        stored = get_message(conn, "already_b")
        cursor_a = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
        cursor_b = get_cursor(conn, project_id="project_b", target_agent_id="agent_b")
    assert stored is not None and stored.state == "received"
    assert cursor_a == "cursor_already_b"
    assert cursor_b is None


def test_wrong_subscription_audit_survives_restart_and_replay(tmp_path):
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import get_cursor, get_message

    path = tmp_path / "restart.db"

    @contextmanager
    def connections():
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    bindings = [
        _binding(project="project_a", agent="agent_a", binding="wb_a"),
        _binding(project="project_b", agent="agent_b", binding="wb_b"),
    ]
    event = _event(
        message_id="restart_mismatch",
        project="project_b",
        agent="agent_b",
        binding="wb_b",
    )
    first = PersephoneReceiver(connection_factory=connections, now=lambda: NOW)
    first.refresh_bindings(bindings)
    assert first.ingest_event(
        event,
        expected_project_id="project_a",
        expected_target_agent_id="agent_a",
    ) == "subscription_route_mismatch"

    # A newer A cursor must not be rewound when the old mismatched delivery is
    # replayed after process restart.
    with connections() as conn:
        from hermes_cli.hades_persephone_store import record_cursor

        record_cursor(
            conn,
            project_id="project_a",
            target_agent_id="agent_a",
            cursor="newer_a_cursor",
            now=NOW + 1,
        )
    second = PersephoneReceiver(connection_factory=connections, now=lambda: NOW + 2)
    second.refresh_bindings(bindings)
    assert second.ingest_event(
        event,
        expected_project_id="project_a",
        expected_target_agent_id="agent_a",
    ) == "subscription_route_mismatch"
    assert second.ingest_event(
        event,
        expected_project_id="project_b",
        expected_target_agent_id="agent_b",
    ) == "accepted"

    with connections() as conn:
        stored = get_message(conn, "restart_mismatch")
        cursor_a = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
        cursor_b = get_cursor(conn, project_id="project_b", target_agent_id="agent_b")
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM persephone_subscription_deliveries "
            "WHERE message_id = ?",
            ("restart_mismatch",),
        ).fetchone()[0]
    assert stored is not None and stored.state == "received"
    assert cursor_a == "newer_a_cursor"
    assert cursor_b is None
    assert audit_count == 1


class _CloseCountingClient:
    def __init__(self):
        self.close_count = 0

    def capabilities(self):
        return {"persephone_agent_queue_v1": True}

    def close(self):
        self.close_count += 1


def _agent(*, token: str = "TOKEN", base_url: str = "https://one.invalid"):
    return db.BackendAgent(
        agent_id="agent_a",
        project_id="project_a",
        base_url=base_url,
        label="test",
        token_env_key=token,
        capabilities={},
    )


def test_refresh_reuses_only_identical_worker_descriptor(receiver):
    first = _CloseCountingClient()
    receiver.client_factory = lambda agent: first
    receiver.refresh_bindings([_binding()], agents={"agent_a": _agent()})
    receiver.run_once()

    receiver.refresh_bindings([_binding()], agents={"agent_a": _agent()})

    assert receiver._workers[("project_a", "agent_a")].client is first
    assert first.close_count == 0


@pytest.mark.parametrize(
    "replacement",
    [
        _agent(token="TOKEN_CHANGED"),
        _agent(base_url="https://two.invalid"),
    ],
)
def test_refresh_closes_client_once_when_agent_descriptor_changes(receiver, replacement):
    first = _CloseCountingClient()
    second = _CloseCountingClient()
    receiver.client_factory = lambda agent: first if agent.token_env_key == "TOKEN" else second
    receiver.refresh_bindings([_binding()], agents={"agent_a": _agent()})
    receiver.run_once()

    receiver.refresh_bindings([_binding()], agents={"agent_a": replacement})
    receiver.refresh_bindings([_binding()], agents={"agent_a": replacement})

    assert first.close_count == 1
    assert receiver._workers[("project_a", "agent_a")].client is None


def test_refresh_closes_removed_worker_client_exactly_once(receiver):
    client = _CloseCountingClient()
    receiver.client_factory = lambda agent: client
    receiver.refresh_bindings([_binding()], agents={"agent_a": _agent()})
    receiver.run_once()

    receiver.refresh_bindings([], agents={})
    receiver.refresh_bindings([], agents={})
    receiver.stop(timeout=1)

    assert client.close_count == 1


def test_refresh_closes_client_when_binding_descriptor_changes(receiver):
    client = _CloseCountingClient()
    receiver.client_factory = lambda agent: client
    original = _binding()
    changed = _binding()
    object.__setattr__(changed, "repo_root", "/different/workspace")
    receiver.refresh_bindings([original], agents={"agent_a": _agent()})
    receiver.run_once()

    receiver.refresh_bindings([changed], agents={"agent_a": _agent()})

    assert client.close_count == 1
    assert receiver._workers[("project_a", "agent_a")].client is None


def test_stop_closes_client_to_unblock_reader_and_eventually_joins(tmp_path):
    from threading import Event

    from hermes_cli.hades_persephone_receiver import PersephoneReceiver

    entered = Event()
    released = Event()

    class BlockingClient(_CloseCountingClient):
        def close(self):
            super().close()
            released.set()

    client = BlockingClient()

    def blocking_reader(active_client, **kwargs):
        entered.set()
        assert released.wait(timeout=2)
        return []

    receiver = PersephoneReceiver(
        client_factory=lambda agent: client,
        event_reader=blocking_reader,
        poll_interval=30,
        now=lambda: NOW,
    )
    receiver.refresh_bindings([_binding()], agents={"agent_a": _agent()})
    receiver.start()
    assert entered.wait(timeout=1)

    receiver.stop(timeout=1)

    assert receiver.thread is None
    assert client.close_count == 1


def test_many_refresh_rotations_do_not_retain_closed_clients(receiver):
    import gc
    import weakref

    close_calls: list[int] = []
    refs: list[weakref.ReferenceType] = []

    class RotatingClient(_CloseCountingClient):
        def close(self):
            super().close()
            close_calls.append(self.close_count)

    def factory(agent):
        client = RotatingClient()
        refs.append(weakref.ref(client))
        return client

    receiver.client_factory = factory
    receiver.event_reader = lambda client, **kwargs: []
    for index in range(30):
        receiver.refresh_bindings(
            [_binding()], agents={"agent_a": _agent(token=f"TOKEN_{index}")}
        )
        receiver.run_once()
    receiver.refresh_bindings([], agents={})

    gc.collect()
    assert len(close_calls) == 30
    assert all(reference() is None for reference in refs)
    assert not hasattr(receiver, "_closed_clients")


def test_stop_refresh_race_detaches_and_closes_client_exactly_once():
    from threading import Barrier, Event, Thread

    from hermes_cli.hades_persephone_receiver import PersephoneReceiver

    entered = Event()
    released = Event()
    barrier = Barrier(3)

    class BlockingClient(_CloseCountingClient):
        def close(self):
            super().close()
            released.set()

    client = BlockingClient()

    def reader(active_client, **kwargs):
        entered.set()
        assert released.wait(timeout=2)
        return []

    receiver = PersephoneReceiver(
        client_factory=lambda agent: client,
        event_reader=reader,
        poll_interval=30,
        now=lambda: NOW,
    )
    receiver.refresh_bindings([_binding()], agents={"agent_a": _agent()})
    receiver.start()
    assert entered.wait(timeout=1)

    def refresh():
        barrier.wait()
        receiver.refresh_bindings([], agents={})

    def stop():
        barrier.wait()
        receiver.stop(timeout=1)

    refresh_thread = Thread(target=refresh)
    stop_thread = Thread(target=stop)
    refresh_thread.start()
    stop_thread.start()
    barrier.wait()
    refresh_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not refresh_thread.is_alive()
    assert not stop_thread.is_alive()
    assert receiver.thread is None
    assert client.close_count == 1


def test_start_stop_are_owned_and_idempotent(receiver):
    receiver.refresh_bindings([_binding()])
    receiver.poll_interval = 0.001

    receiver.start()
    first = receiver.thread
    receiver.start()
    receiver.stop(timeout=1)
    receiver.stop(timeout=1)

    assert first is not None
    assert receiver.thread is None


def test_classify_request_is_deny_by_default():
    from hermes_cli.hades_persephone_messages import parse_envelope
    from hermes_cli.hades_persephone_receiver import classify_request

    allowed = parse_envelope(_envelope(), now=NOW)
    denied = parse_envelope(_envelope(capability="database_query"), now=NOW)

    assert classify_request(allowed) == "accepted"
    assert classify_request(denied) == "waiting_human_approval"

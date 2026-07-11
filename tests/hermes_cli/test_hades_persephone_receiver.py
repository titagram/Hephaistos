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
    assert stored is not None and stored.state == "processing"
    assert cursor == "cursor_msg_1"
    assert count == 1


def test_old_duplicate_cannot_rewind_a_newer_opaque_cursor(receiver):
    from hermes_cli.hades_persephone_store import get_cursor

    receiver.refresh_bindings([_binding()])
    assert receiver.ingest_event(_event(message_id="old")) == "accepted"
    assert receiver.ingest_event(_event(message_id="new")) == "accepted"
    assert receiver.ingest_event(_event(message_id="old")) == "accepted"

    with receiver.connection_factory() as conn:
        cursor = get_cursor(conn, project_id="project_a", target_agent_id="agent_a")
    assert cursor == "cursor_new"


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

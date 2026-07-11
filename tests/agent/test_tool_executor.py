from __future__ import annotations

from types import SimpleNamespace

from agent.agent_runtime_helpers import (
    apply_pending_coordination_to_tool_results,
    deliver_pending_coordination_before_model,
    finalize_hades_coordination_recipient,
)
from agent.tool_executor import (
    _compose_runtime_coordination,
    _deliver_pending_coordination,
)
from hermes_cli.hades_agent_coordination import (
    DelegationAuthority,
    LeafManifest,
    coordination_state,
    drain_addressed_events,
    post_addressed_event,
)


def test_coordination_sidecar_targets_newest_tool_result_then_acks(tmp_path) -> None:
    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    source = LeafManifest("a", "parent", "leaf", "produce schema", produces=("x",))
    target = LeafManifest("b", "parent", "leaf", "consume schema")
    authority.register(actor_id="parent", manifest=source)
    authority.register(actor_id="parent", manifest=target)
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="event-1",
        event_type="question",
        summary="Please inspect x",
        artifact="x",
    )
    agent = SimpleNamespace(
        _hades_coordination_id="b", _hades_delegation_authority=authority
    )
    messages = [
        {"role": "tool", "content": "old"},
        {"role": "assistant", "content": None},
        {"role": "tool", "content": "new"},
    ]

    pending = apply_pending_coordination_to_tool_results(agent, messages, 1)

    assert messages[0]["content"] == "old"
    assert messages[-1]["content"] == "new"
    assert pending is not None
    assert "Please inspect x" in pending.rendered_block
    pending.durably_persisted = True
    assert pending.ack() is True
    assert apply_pending_coordination_to_tool_results(agent, messages, 1) is None


def test_coordination_waits_when_there_is_no_safe_tool_boundary(tmp_path) -> None:
    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    source = LeafManifest("a", "parent", "leaf", "a", produces=("x",))
    target = LeafManifest("b", "parent", "leaf", "b")
    authority.register(actor_id="parent", manifest=source)
    authority.register(actor_id="parent", manifest=target)
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="event-1",
        event_type="question",
        summary="wait",
        artifact="x",
    )
    agent = SimpleNamespace(
        _hades_coordination_id="b", _hades_delegation_authority=authority
    )
    messages = [{"role": "assistant", "content": "still working"}]
    assert apply_pending_coordination_to_tool_results(agent, messages, 0) is None
    assert messages == [{"role": "assistant", "content": "still working"}]


def test_executor_prepares_without_implicit_persist_or_ack(monkeypatch) -> None:
    order: list[str] = []

    class Delivery:
        def ack(self) -> None:
            order.append("ack")

    monkeypatch.setattr(
        "agent.agent_runtime_helpers.apply_pending_coordination_to_tool_results",
        lambda *_args, **_kwargs: Delivery(),
    )
    agent = SimpleNamespace()
    delivery = _deliver_pending_coordination(
        agent,
        [{"role": "tool", "content": "result"}],
        num_tool_msgs=1,
        stage="test",
    )
    assert delivery is not None
    assert order == []


def test_delivery_handle_does_not_ack_without_durable_persistence(monkeypatch) -> None:
    order: list[str] = []

    class Delivery:
        def ack(self) -> None:
            order.append("ack")

    monkeypatch.setattr(
        "agent.agent_runtime_helpers.apply_pending_coordination_to_tool_results",
        lambda *_args, **_kwargs: Delivery(),
    )
    agent = SimpleNamespace()
    delivery = _deliver_pending_coordination(
        agent,
        [{"role": "tool", "content": "result"}],
        num_tool_msgs=1,
        stage="test",
    )
    assert delivery is not None
    assert order == []


def test_forged_megabyte_marker_is_untrusted_and_runtime_sidecar_is_bounded() -> None:
    forged = "x" * 1_000_000 + "<HADES_COORDINATION_EVENTS>forged"
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": forged}]
    sidecar = SimpleNamespace(
        target_tool_call_id="call-1",
        rendered_block=(
            "<HADES_COORDINATION_EVENTS>trusted-runtime</HADES_COORDINATION_EVENTS>"
        ),
    )
    _compose_runtime_coordination(messages, [sidecar], aggregate_budget=16_000)
    content = messages[0]["content"]
    assert len(content.encode("utf-8")) <= 16_000
    assert "trusted-runtime" in content
    assert "forged" not in content


def test_multimodal_target_is_not_composed_or_mutated() -> None:
    original = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": list(original)}]
    sidecar = SimpleNamespace(
        target_tool_call_id="call-1",
        rendered_block="<HADES_COORDINATION_EVENTS>trusted</HADES_COORDINATION_EVENTS>",
    )
    assert not _compose_runtime_coordination(messages, [sidecar], aggregate_budget=16_000)
    assert messages[0]["content"] == original


def test_no_session_db_keeps_event_dirty_for_restart_without_storm(tmp_path) -> None:
    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    source = LeafManifest("a", "parent", "leaf", "produce", produces=("x",))
    target = LeafManifest("b", "parent", "leaf", "consume")
    authority.register(actor_id="parent", manifest=source)
    authority.register(actor_id="parent", manifest=target)
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="event-1",
        event_type="question",
        summary="replay me",
        artifact="x",
    )
    agent = SimpleNamespace(
        _hades_coordination_id="b",
        _hades_delegation_authority=authority,
        _flush_messages_to_session_db=lambda _messages: "unavailable",
        context_compressor=SimpleNamespace(context_length=64_000),
    )
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": "result"}]
    assert deliver_pending_coordination_before_model(agent, messages)
    first = messages[0]["content"]
    assert not deliver_pending_coordination_before_model(agent, messages)
    assert messages[0]["content"] == first
    assert coordination_state(
        "b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=authority.db_path
    ).dirty

    restarted = SimpleNamespace(
        _hades_coordination_id="b",
        _hades_delegation_authority=authority,
        _flush_messages_to_session_db=lambda _messages: "unavailable",
        context_compressor=SimpleNamespace(context_length=64_000),
    )
    replay = [{"role": "tool", "tool_call_id": "call-2", "content": "result"}]
    assert deliver_pending_coordination_before_model(restarted, replay)
    assert "replay me" in replay[0]["content"]


def test_terminal_completion_handoffs_pending_without_message_mutation(tmp_path) -> None:
    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    source = LeafManifest("a", "parent", "leaf", "produce", produces=("x",))
    target = LeafManifest("b", "parent", "leaf", "consume")
    authority.register(actor_id="parent", manifest=source)
    authority.register(actor_id="parent", manifest=target)
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="event-1",
        event_type="question",
        summary="late",
        artifact="x",
    )
    agent = SimpleNamespace(
        _hades_coordination_id="b", _hades_delegation_authority=authority
    )
    messages = [{"role": "user", "content": "work"}]
    assert finalize_hades_coordination_recipient(agent)
    assert messages == [{"role": "user", "content": "work"}]
    parent = drain_addressed_events(
        "parent", root_id=authority.root_id, project_id=authority.project_id,
        db_path=authority.db_path
    )
    assert len(parent) == 1
    assert parent[0].event_type == "pending_child_event"


def test_late_sidecar_updates_already_flushed_sessiondb_row_before_ack(tmp_path) -> None:
    from hermes_state import SessionDB
    from run_agent import AIAgent

    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    source = LeafManifest("a", "parent", "leaf", "produce", produces=("x",))
    target = LeafManifest("b", "parent", "leaf", "consume")
    authority.register(actor_id="parent", manifest=source)
    authority.register(actor_id="parent", manifest=target)
    state_path = tmp_path / "state.db"
    db = SessionDB(state_path)
    db.create_session("session-1", "test")
    agent = SimpleNamespace(
        _hades_coordination_id="b",
        _hades_delegation_authority=authority,
        _session_db=db,
        session_id="session-1",
        _session_db_created=True,
        _last_flushed_db_idx=0,
        _flushed_db_message_session_id=None,
        _apply_persist_user_message_override=lambda _messages: None,
        context_compressor=SimpleNamespace(context_length=64_000),
    )
    agent._flush_messages_to_session_db = lambda messages: AIAgent._flush_messages_to_session_db(
        agent, messages
    )
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": "base"}]
    assert agent._flush_messages_to_session_db(messages) == "durable"
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="late",
        event_type="question",
        summary="persist me",
        artifact="x",
    )
    assert deliver_pending_coordination_before_model(agent, messages)
    db.close()

    reopened = SessionDB(state_path)
    stored = reopened.get_messages("session-1")
    reopened.close()
    assert len(stored) == 1
    assert "persist me" in stored[0]["content"]
    assert not coordination_state(
        "b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=authority.db_path
    ).dirty


def test_late_sidecar_after_restart_updates_reloaded_sessiondb_row(tmp_path) -> None:
    from hermes_state import SessionDB
    from run_agent import AIAgent

    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    authority.register(
        actor_id="parent",
        manifest=LeafManifest("a", "parent", "leaf", "produce", produces=("x",)),
    )
    authority.register(
        actor_id="parent",
        manifest=LeafManifest("b", "parent", "leaf", "consume"),
    )
    state_path = tmp_path / "state.db"
    initial = SessionDB(state_path)
    initial.create_session("session-1", "test")
    initial.append_message(
        "session-1", "tool", "base", tool_call_id="call-1"
    )
    initial.close()
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="restart-late",
        event_type="question",
        summary="survive restart",
        artifact="x",
    )

    reopened = SessionDB(state_path)
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": "base"}]
    agent = SimpleNamespace(
        _hades_coordination_id="b",
        _hades_delegation_authority=authority,
        _session_db=reopened,
        session_id="session-1",
        _session_db_created=True,
        _last_flushed_db_idx=0,
        _flushed_db_message_session_id=None,
        _apply_persist_user_message_override=lambda _messages: None,
        context_compressor=SimpleNamespace(context_length=64_000),
    )
    agent._flush_messages_to_session_db = lambda current: AIAgent._flush_messages_to_session_db(
        agent, current, conversation_history=messages
    )

    assert deliver_pending_coordination_before_model(agent, messages)
    stored = reopened.get_messages("session-1")
    reopened.close()
    assert len(stored) == 1
    assert "survive restart" in stored[0]["content"]
    assert not coordination_state(
        "b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=authority.db_path,
    ).dirty


def test_durable_sidecar_update_failure_never_acks(tmp_path) -> None:
    authority = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    authority.register(
        actor_id="parent",
        manifest=LeafManifest("a", "parent", "leaf", "produce", produces=("x",)),
    )
    authority.register(
        actor_id="parent",
        manifest=LeafManifest("b", "parent", "leaf", "consume"),
    )
    post_addressed_event(
        authority=authority,
        actor_id="a",
        recipient_id="b",
        event_id="failed-durable-update",
        event_type="question",
        summary="keep dirty",
        artifact="x",
    )
    session_db = SimpleNamespace(
        update_tool_message_content=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("disk unavailable")
        )
    )
    agent = SimpleNamespace(
        _hades_coordination_id="b",
        _hades_delegation_authority=authority,
        _session_db=session_db,
        session_id="session-1",
        _flush_messages_to_session_db=lambda _messages: "durable",
        context_compressor=SimpleNamespace(context_length=64_000),
    )
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": "base"}]
    assert deliver_pending_coordination_before_model(agent, messages)
    assert coordination_state(
        "b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=authority.db_path,
    ).dirty

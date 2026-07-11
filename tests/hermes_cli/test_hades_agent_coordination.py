from __future__ import annotations

import pytest

from hermes_cli.hades_agent_coordination import (
    AuthorityError,
    DelegationAuthority,
    LeafManifest,
    ack_coordination_events,
    coordination_state,
    drain_addressed_events,
    is_relevant_request,
    post_addressed_event,
    render_coordination_block,
)


def manifest(agent_id: str, **kwargs) -> LeafManifest:
    return LeafManifest(
        agent_id=agent_id,
        parent_id=kwargs.pop("parent_id", "parent"),
        role=kwargs.pop("role", "leaf"),
        objective=kwargs.pop("objective", f"work on {agent_id}"),
        **kwargs,
    )


def authority(tmp_path) -> DelegationAuthority:
    registry = DelegationAuthority("parent", db_path=tmp_path / "coord.db")
    registry.register(
        actor_id="parent",
        manifest=manifest("a", produces=("schema.json",), interfaces=("users",)),
    )
    registry.register(
        actor_id="parent",
        manifest=manifest("b", dependencies=("a",), interfaces=("users",)),
    )
    return registry


def test_relevance_requires_explicit_relationship() -> None:
    assert not is_relevant_request(
        manifest("a", write_scope=("a/**",)),
        manifest("b", write_scope=("b/**",)),
    )
    assert is_relevant_request(
        manifest("a", write_scope=("src/**",)),
        manifest("b", write_scope=("src/api.py",)),
    )
    assert is_relevant_request(
        manifest("a", produces=("schema.json",)), manifest("b"), "schema.json"
    )


def test_only_direct_parent_updates_contract(tmp_path) -> None:
    registry = authority(tmp_path)
    updated = registry.update_contract(
        actor="parent", target="b", patch={"objective": "bounded change"}
    )
    assert updated.objective == "bounded change"
    with pytest.raises(AuthorityError):
        registry.update_contract(actor="a", target="b", patch={"objective": "bad"})


def test_irrelevant_sibling_is_parent_routed(tmp_path) -> None:
    registry = authority(tmp_path)
    registry.register(
        actor_id="parent",
        manifest=manifest("c", write_scope=("isolated/**",)),
    )
    event = post_addressed_event(
        authority=registry,
        actor_id="c",
        recipient_id="b",
        event_id="route-parent",
        event_type="question",
        summary="unrelated",
    )
    assert event.recipients == ("parent",)


def test_ack_is_replay_safe_and_generation_aware(tmp_path) -> None:
    registry = authority(tmp_path)
    event = post_addressed_event(
        authority=registry,
        actor_id="a",
        recipient_id="b",
        event_id="event-1",
        event_type="question",
        summary="consume schema",
        artifact="schema.json",
    )
    state = coordination_state("b", db_path=registry.db_path)
    assert drain_addressed_events("b", db_path=registry.db_path) == [event]
    ack_coordination_events(
        "b",
        through_sequence=event.sequence,
        through_generation=state.generation,
        db_path=registry.db_path,
    )
    assert drain_addressed_events("b", db_path=registry.db_path) == []
    assert not coordination_state("b", db_path=registry.db_path).dirty


def test_render_has_hard_utf8_budget(tmp_path) -> None:
    registry = authority(tmp_path)
    events = []
    for index in range(20):
        events.append(
            post_addressed_event(
                authority=registry,
                actor_id="a",
                recipient_id="b",
                event_id=f"event-{index}",
                event_type="question",
                summary="ø" * 2_000,
                artifact="schema.json",
            )
        )
    assert len(render_coordination_block(events).encode("utf-8")) <= 8_192

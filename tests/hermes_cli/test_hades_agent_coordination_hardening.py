from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from hermes_cli.hades_agent_coordination import (
    AuthorityError,
    DelegationAuthority,
    LeafManifest,
    coordination_state,
    drain_addressed_events,
    mark_coordination_recipient_completed,
    post_addressed_event,
    prepare_pending_coordination,
)
from types import SimpleNamespace
from hermes_cli.kanban_swarm import post_addressed_blackboard_update


def registry(tmp_path):
    path = tmp_path / "coordination.db"
    authority = DelegationAuthority(root_id="root", project_id="project-a", db_path=path)
    authority.register(
        actor_id="root",
        manifest=LeafManifest(
            "orchestrator",
            "root",
            "orchestrator",
            "coordinate API",
            status="running",
            task_version=2,
            contract_version=3,
            interfaces=("users-api",),
            produces=("schema.json",),
        ),
    )
    authority.register(
        actor_id="orchestrator",
        manifest=LeafManifest(
            "leaf-a",
            "orchestrator",
            "leaf",
            "produce schema",
            dependencies=(),
            interfaces=("users-api",),
            produces=("schema.json",),
        ),
    )
    authority.register(
        actor_id="orchestrator",
        manifest=LeafManifest(
            "leaf-b",
            "orchestrator",
            "leaf",
            "consume schema",
            dependencies=("leaf-a",),
            interfaces=("users-api",),
        ),
    )
    return authority, path


def test_same_ids_are_isolated_across_roots_and_projects(tmp_path) -> None:
    path = tmp_path / "coordination.db"
    left = DelegationAuthority("root-a", project_id="project-a", db_path=path)
    right = DelegationAuthority("root-b", project_id="project-b", db_path=path)
    for authority in (left, right):
        authority.register(
            actor_id=authority.root_id,
            manifest=LeafManifest(
                "shared-id",
                authority.root_id,
                "leaf",
                authority.project_id,
                root_id=authority.root_id,
                project_id=authority.project_id,
            ),
        )
    assert left.get("shared-id").objective == "project-a"
    assert right.get("shared-id").objective == "project-b"
    with pytest.raises(AuthorityError):
        left.register(
            actor_id="root-a",
            manifest=LeafManifest(
                "hijack", "root-a", "leaf", "bad",
                root_id="root-b", project_id="project-b",
            ),
        )


def test_event_id_is_namespaced_and_full_request_is_immutable(tmp_path) -> None:
    authority, path = registry(tmp_path)
    kwargs = dict(
        authority=authority,
        actor_id="leaf-a",
        recipient_id="leaf-b",
        event_id="same-id",
        event_type="question",
        summary="canonical",
        db_path=path,
        ttl_seconds=30,
        now=100,
    )
    first = post_addressed_event(**kwargs)
    assert post_addressed_event(**kwargs).sequence == first.sequence
    for changed in (
        {"event_type": "answer"},
        {"summary": "different"},
        {"artifact": "other"},
        {"evidence_refs": ("other",)},
        {"blocker": "leaf-b"},
        {"recipient_id": "orchestrator"},
        {"ttl_seconds": 31},
    ):
        with pytest.raises(ValueError, match="different event"):
            post_addressed_event(**(kwargs | changed))


def test_idempotent_retry_keeps_original_route_after_recipient_completes(tmp_path) -> None:
    authority, path = registry(tmp_path)
    kwargs = dict(
        authority=authority,
        actor_id="leaf-a",
        recipient_id="leaf-b",
        event_id="stable-route",
        event_type="question",
        summary="canonical",
        db_path=path,
    )
    first = post_addressed_event(**kwargs)
    mark_coordination_recipient_completed(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path,
    )
    retry = post_addressed_event(**kwargs)
    assert retry.sequence == first.sequence
    assert retry.recipients == ("leaf-b",)


def test_manifest_versions_use_compare_and_swap(tmp_path) -> None:
    authority, _ = registry(tmp_path)
    current = authority.get("leaf-b")
    updated = authority.update_contract(
        actor="orchestrator",
        target="leaf-b",
        expected_task_version=current.task_version,
        expected_contract_version=current.contract_version,
        patch={"objective": "v2", "contract_version": current.contract_version + 1},
    )
    assert updated.contract_version == 2
    assert authority.update_contract(
        actor="orchestrator",
        target="leaf-b",
        expected_task_version=1,
        expected_contract_version=2,
        patch={"objective": "v2", "contract_version": 2},
    ) == updated
    with pytest.raises(ValueError, match="version"):
        authority.update_contract(
            actor="orchestrator",
            target="leaf-b",
            expected_task_version=1,
            expected_contract_version=1,
            patch={"objective": "stale", "contract_version": 3},
        )
    with pytest.raises(ValueError, match="version"):
        authority.update_contract(
            actor="orchestrator",
            target="leaf-b",
            expected_task_version=0,
            expected_contract_version=2,
            patch={"task_version": 2, "contract_version": 3},
        )
    with pytest.raises(ValueError, match="same version"):
        authority.update_contract(
            actor="orchestrator",
            target="leaf-b",
            expected_task_version=1,
            expected_contract_version=2,
            patch={"objective": "conflict", "contract_version": 2},
        )


def test_authority_registry_owns_routing_and_root_is_read_only(tmp_path) -> None:
    authority, path = registry(tmp_path)
    with pytest.raises(AuthorityError):
        authority.register(
            actor_id="root",
            manifest=LeafManifest("grandchild", "orchestrator", "leaf", "bad"),
        )
    with pytest.raises(AuthorityError):
        authority.register(
            actor_id="orchestrator",
            manifest=LeafManifest(
                "orchestrator", "orchestrator", "leaf", "self-hijack"
            ),
        )
    with pytest.raises(ValueError, match="compare-and-swap"):
        authority.register(
            actor_id="orchestrator",
            manifest=LeafManifest(
                "leaf-b", "orchestrator", "leaf", "reparent contract",
                task_version=2, contract_version=2,
            ),
        )
    assert authority.inspect("root", "leaf-b").status == "running"
    with pytest.raises(AuthorityError):
        authority.update_contract(
            actor="root", target="leaf-b", expected_task_version=1,
            expected_contract_version=1,
            patch={"objective": "commandeered", "contract_version": 2}
        )
    event = post_addressed_event(
        authority=authority,
        actor_id="root",
        recipient_id="leaf-b",
        event_id="root-query-1",
        event_type="question",
        summary="Report status only",
        db_path=path,
    )
    assert event.recipients == ("leaf-b",)


def test_event_id_is_concurrent_restart_safe_and_generation_coalesces(tmp_path) -> None:
    authority, path = registry(tmp_path)

    def retry(_index):
        return post_addressed_event(
            authority=authority,
            actor_id="leaf-a",
            recipient_id="leaf-b",
            event_id="same-event",
            event_type="question",
            summary="Consume schema",
            artifact="schema.json",
            db_path=path,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(retry, range(32)))
    assert len({event.sequence for event in events}) == 1
    assert coordination_state(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path
    ).generation == 1
    restarted = DelegationAuthority(root_id="root", project_id="project-a", db_path=path)
    assert len(drain_addressed_events(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path
    )) == 1
    assert restarted.get("leaf-b").contract_version == 1


def test_hard_evidence_limits_and_recipient_query_limit(tmp_path) -> None:
    authority, path = registry(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        post_addressed_event(
            authority=authority,
            actor_id="leaf-a",
            recipient_id="leaf-b",
            event_id="too-many-refs",
            event_type="question",
            summary="bounded",
            evidence_refs=tuple(f"ref-{i}" for i in range(17)),
            db_path=path,
        )
    for index in range(30):
        post_addressed_event(
            authority=authority,
            actor_id="leaf-a",
            recipient_id="leaf-b",
            event_id=f"event-{index}",
            event_type="question",
            summary=f"event {index}",
            db_path=path,
        )
    assert len(drain_addressed_events(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path, limit=7
    )) == 7


def test_completed_recipient_notifies_parent_once_per_generation(tmp_path) -> None:
    authority, path = registry(tmp_path)
    mark_coordination_recipient_completed(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path
    )
    for _ in range(3):
        post_addressed_event(
            authority=authority,
            actor_id="leaf-a",
            recipient_id="leaf-b",
            event_id="late-event",
            event_type="question",
            summary="late question",
            db_path=path,
        )
    parent_events = drain_addressed_events(
        "orchestrator", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path
    )
    assert len(parent_events) == 1
    assert parent_events[0].event_type == "pending_child_event"


def test_coordination_write_never_commits_callers_kanban_transaction(tmp_path) -> None:
    authority, path = registry(tmp_path)
    shared = sqlite3.connect(tmp_path / "kanban.db")
    shared.execute("CREATE TABLE scratch(value TEXT)")
    shared.commit()
    shared.execute("BEGIN")
    shared.execute("INSERT INTO scratch VALUES ('uncommitted')")
    def post(index):
        return post_addressed_blackboard_update(
            shared,
            authority=authority,
            actor_id="leaf-a",
            recipient_id="leaf-b",
            event_id=f"transaction-proof-{index}",
            event_type="question",
            summary="does not own caller transaction",
            db_path=path,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(list(pool.map(post, range(8)))) == 8
    shared.rollback()
    assert shared.execute("SELECT COUNT(*) FROM scratch").fetchone()[0] == 0


def test_post_between_drain_and_ack_keeps_new_generation_dirty(tmp_path) -> None:
    authority, path = registry(tmp_path)
    post_addressed_event(
        authority=authority,
        actor_id="leaf-a",
        recipient_id="leaf-b",
        event_id="before-drain",
        event_type="question",
        summary="first",
        db_path=path,
    )
    agent = SimpleNamespace(
        _hades_coordination_id="leaf-b", _hades_delegation_authority=authority
    )
    messages = [{"role": "tool", "tool_call_id": "call-1", "content": "ok"}]
    delivery = prepare_pending_coordination(agent, messages, 1)
    assert delivery is not None
    post_addressed_event(
        authority=authority,
        actor_id="leaf-a",
        recipient_id="leaf-b",
        event_id="during-persist",
        event_type="question",
        summary="second",
        db_path=path,
    )
    delivery.durably_persisted = True
    assert delivery.ack()
    state = coordination_state(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path
    )
    assert state.dirty
    assert state.generation == 2
    assert [event.event_id for event in drain_addressed_events(
        "leaf-b", root_id=authority.root_id, project_id=authority.project_id,
        db_path=path
    )] == [
        "during-persist"
    ]

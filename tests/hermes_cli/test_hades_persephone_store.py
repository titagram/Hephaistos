from __future__ import annotations

import json
import sqlite3

import pytest

from hermes_cli.hades_persephone_messages import (
    AGENT_MESSAGE_SCHEMA,
    EffectClass,
    MessageType,
    parse_envelope,
)


def _envelope(
    *,
    message_id: str = "msg_1",
    project_id: str = "project_1",
    target_agent_id: str = "agent_target",
    message_type: str = MessageType.INFORMATION_REQUEST.value,
    effect: str = EffectClass.INFORMATION_READ.value,
):
    return parse_envelope(
        {
            "schema": AGENT_MESSAGE_SCHEMA,
            "message_id": message_id,
            "correlation_id": "correlation_1",
            "causation_id": None,
            "project_id": project_id,
            "sender_agent_id": "agent_sender",
            "target_agent_id": target_agent_id,
            "target_workspace_binding_id": None,
            "message_type": message_type,
            "effect": effect,
            "capability": "project_memory_search",
            "remote_task_id": None,
            "remote_task_version": None,
            "expires_at": 9_999_999_999,
            "payload": {"query": "where is the contract?"},
        },
        now=100,
    )


@pytest.fixture
def tmp_db(tmp_path):
    from hermes_cli import hades_backend_db as db

    with db.connect_closing(tmp_path / "hades_backend.db") as conn:
        yield conn


def test_persist_before_send_and_deduplicate(tmp_db):
    from hermes_cli.hades_persephone_store import claim_due_outbox, enqueue_outbox

    envelope = _envelope()
    first = enqueue_outbox(tmp_db, envelope, now=100)
    second = enqueue_outbox(tmp_db, envelope, now=200)

    assert first.message_id == second.message_id
    claimed = claim_due_outbox(tmp_db, now=100, limit=10)
    assert [item.message_id for item in claimed] == ["msg_1"]
    assert claimed[0].state == "sending"
    assert claimed[0].attempts == 1
    assert claim_due_outbox(tmp_db, now=100, limit=10) == []


def test_duplicate_message_id_with_different_envelope_is_rejected(tmp_db):
    from hermes_cli.hades_persephone_store import MessageConflict, enqueue_outbox

    enqueue_outbox(tmp_db, _envelope(), now=100)
    with pytest.raises(MessageConflict):
        enqueue_outbox(tmp_db, _envelope(project_id="project_2"), now=100)

    row = tmp_db.execute(
        "SELECT project_id FROM persephone_outbox WHERE message_id = 'msg_1'"
    ).fetchone()
    assert row["project_id"] == "project_1"


def test_ack_requires_processed_response(tmp_db):
    from hermes_cli.hades_persephone_store import (
        InvalidTransition,
        record_inbox,
        transition_message,
    )

    record_inbox(tmp_db, _envelope(), now=100)
    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, "msg_1", "acknowledged", now=101)


def test_response_can_be_acknowledged_only_after_processing(tmp_db):
    from hermes_cli.hades_persephone_store import record_inbox, transition_message

    response = _envelope(message_type=MessageType.INFORMATION_RESPONSE.value)
    record_inbox(tmp_db, response, now=100)
    transition_message(tmp_db, "msg_1", "processing", now=101)
    transition_message(tmp_db, "msg_1", "processed", now=102)
    acknowledged = transition_message(tmp_db, "msg_1", "acknowledged", now=103)
    duplicate = transition_message(tmp_db, "msg_1", "acknowledged", now=200)

    assert acknowledged.state == "acknowledged"
    assert acknowledged.updated_at == 103
    assert duplicate.updated_at == 103


def test_outbox_retry_and_startup_recovery_are_guarded(tmp_db):
    from hermes_cli.hades_persephone_store import (
        claim_due_outbox,
        enqueue_outbox,
        recover_abandoned_outbox,
        transition_message,
    )

    enqueue_outbox(tmp_db, _envelope(), now=100)
    claim_due_outbox(tmp_db, now=100, limit=1)
    assert recover_abandoned_outbox(tmp_db, now=120, abandoned_before=99) == 0
    assert recover_abandoned_outbox(tmp_db, now=120, abandoned_before=101) == 1
    assert claim_due_outbox(tmp_db, now=119, limit=1) == []
    [claimed] = claim_due_outbox(tmp_db, now=120, limit=1)
    assert claimed.attempts == 2
    sent = transition_message(tmp_db, "msg_1", "sent", queue="outbox", now=121)
    assert sent.state == "sent"


def test_human_approval_is_project_scoped_and_idempotent(tmp_db):
    from hermes_cli.hades_persephone_store import (
        InvalidTransition,
        approve_request,
        pending_human_requests,
        record_inbox,
        transition_message,
    )

    request = _envelope(effect=EffectClass.MUTATING.value)
    record_inbox(tmp_db, request, now=100)
    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, request.message_id, "processing", now=100)
    transition_message(tmp_db, request.message_id, "waiting_human_approval", now=101)
    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, request.message_id, "approved", now=101)

    assert pending_human_requests(tmp_db, project_id="other") == []
    assert [item.message_id for item in pending_human_requests(tmp_db, project_id="project_1")] == [
        "msg_1"
    ]
    approved = approve_request(
        tmp_db,
        "msg_1",
        approved=True,
        decided_by="human:gabriele",
        reason="reviewed locally",
        project_id="project_1",
        now=102,
    )
    assert approved.state == "approved"
    assert approved.human_decision == "approved"
    assert approved.human_decided_by == "human:gabriele"
    assert pending_human_requests(tmp_db, project_id="project_1") == []

    same = approve_request(
        tmp_db,
        "msg_1",
        approved=True,
        decided_by="human:gabriele",
        reason="reviewed locally",
        project_id="project_1",
        now=200,
    )
    assert same.updated_at == 102
    with pytest.raises(InvalidTransition):
        approve_request(
            tmp_db,
            "msg_1",
            approved=False,
            decided_by="human:gabriele",
            project_id="project_1",
            now=201,
        )


def test_approval_requires_named_local_decider(tmp_db):
    from hermes_cli.hades_persephone_store import (
        approve_request,
        record_inbox,
        transition_message,
    )

    record_inbox(tmp_db, _envelope(effect=EffectClass.MUTATING.value), now=100)
    transition_message(tmp_db, "msg_1", "waiting_human_approval", now=101)
    with pytest.raises(ValueError, match="decided_by"):
        approve_request(tmp_db, "msg_1", approved=True, decided_by="  ", now=102)


def test_cursor_is_independent_per_project_and_agent(tmp_db):
    from hermes_cli.hades_persephone_store import get_cursor, record_cursor

    record_cursor(tmp_db, project_id="project_1", target_agent_id="agent_1", cursor="42", now=100)
    record_cursor(tmp_db, project_id="project_1", target_agent_id="agent_2", cursor="9", now=101)
    record_cursor(tmp_db, project_id="project_2", target_agent_id="agent_1", cursor="7", now=102)

    assert get_cursor(tmp_db, project_id="project_1", target_agent_id="agent_1") == "42"
    assert get_cursor(tmp_db, project_id="project_1", target_agent_id="agent_2") == "9"
    assert get_cursor(tmp_db, project_id="project_2", target_agent_id="agent_1") == "7"


def test_existing_database_migrates_without_replacing_legacy_inbox(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "existing.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE inbox_events (event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "event_type TEXT NOT NULL, payload TEXT NOT NULL, received_at INTEGER NOT NULL, read_at INTEGER)"
    )
    legacy.execute(
        "INSERT INTO inbox_events VALUES (?, ?, ?, ?, ?, NULL)",
        ("legacy_1", "project_1", "notice", json.dumps({"ok": True}), 10),
    )
    legacy.commit()
    legacy.close()

    with db.connect_closing(path) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        event = conn.execute("SELECT * FROM inbox_events WHERE event_id = 'legacy_1'").fetchone()
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }

    assert {"persephone_outbox", "persephone_inbox", "persephone_cursors"} <= tables
    assert event is not None
    assert {
        "idx_persephone_outbox_target_state",
        "idx_persephone_outbox_next_attempt",
        "idx_persephone_inbox_target_state",
    } <= indexes

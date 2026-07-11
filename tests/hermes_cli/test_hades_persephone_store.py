from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

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
    capability: str = "project_memory_search",
    correlation_id: str = "correlation_1",
    causation_id: str | None = None,
    sender_agent_id: str = "agent_sender",
):
    return parse_envelope(
        {
            "schema": AGENT_MESSAGE_SCHEMA,
            "message_id": message_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "project_id": project_id,
            "sender_agent_id": sender_agent_id,
            "target_agent_id": target_agent_id,
            "target_workspace_binding_id": None,
            "message_type": message_type,
            "effect": effect,
            "capability": capability,
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


def test_claim_due_outbox_can_be_scoped_to_one_project_and_sender(tmp_db):
    from hermes_cli.hades_persephone_store import claim_due_outbox, enqueue_outbox

    enqueue_outbox(tmp_db, _envelope(message_id="p1", project_id="project_1"), now=100)
    enqueue_outbox(tmp_db, _envelope(message_id="p2", project_id="project_2"), now=100)

    claimed = claim_due_outbox(
        tmp_db,
        now=100,
        limit=10,
        project_id="project_2",
        sender_agent_id="agent_sender",
    )

    assert [item.message_id for item in claimed] == ["p2"]
    untouched = tmp_db.execute(
        "SELECT state FROM persephone_outbox WHERE message_id = 'p1'"
    ).fetchone()
    assert untouched["state"] == "outbox_pending"


def test_claim_due_outbox_scopes_same_project_to_sender(tmp_db):
    from hermes_cli.hades_persephone_store import claim_due_outbox, enqueue_outbox

    enqueue_outbox(
        tmp_db,
        _envelope(message_id="sender_a", sender_agent_id="agent_a"),
        now=100,
    )
    enqueue_outbox(
        tmp_db,
        _envelope(message_id="sender_b", sender_agent_id="agent_b"),
        now=100,
    )

    claimed = claim_due_outbox(
        tmp_db,
        now=100,
        limit=10,
        project_id="project_1",
        sender_agent_id="agent_b",
    )

    assert [item.message_id for item in claimed] == ["sender_b"]
    assert claimed[0].sender_agent_id == "agent_b"
    assert tmp_db.execute(
        "SELECT state FROM persephone_outbox WHERE message_id = 'sender_a'"
    ).fetchone()["state"] == "outbox_pending"


def test_claim_due_outbox_rejects_partial_sender_scope(tmp_db):
    from hermes_cli.hades_persephone_store import claim_due_outbox

    with pytest.raises(ValueError, match="project_id and sender_agent_id"):
        claim_due_outbox(tmp_db, project_id="project_1")
    with pytest.raises(ValueError, match="project_id and sender_agent_id"):
        claim_due_outbox(tmp_db, sender_agent_id="agent_a")


def test_duplicate_message_id_with_different_envelope_is_rejected(tmp_db):
    from hermes_cli.hades_persephone_store import MessageConflict, enqueue_outbox

    enqueue_outbox(tmp_db, _envelope(), now=100)
    with pytest.raises(MessageConflict):
        enqueue_outbox(tmp_db, _envelope(project_id="project_2"), now=100)

    row = tmp_db.execute(
        "SELECT project_id FROM persephone_outbox WHERE message_id = 'msg_1'"
    ).fetchone()
    assert row["project_id"] == "project_1"


def test_global_identity_rejects_cross_queue_reuse_even_for_identical_envelope(tmp_db):
    from hermes_cli.hades_persephone_store import MessageConflict, enqueue_outbox, record_inbox

    envelope = _envelope()
    enqueue_outbox(tmp_db, envelope, now=100)
    with pytest.raises(MessageConflict, match="direction"):
        record_inbox(tmp_db, envelope, now=101)

    identity = tmp_db.execute(
        "SELECT direction, project_id FROM persephone_message_identities WHERE message_id = ?",
        (envelope.message_id,),
    ).fetchone()
    assert dict(identity) == {"direction": "outbox", "project_id": "project_1"}


def test_global_identity_rejects_cross_queue_different_project(tmp_db):
    from hermes_cli.hades_persephone_store import MessageConflict, enqueue_outbox, record_inbox

    record_inbox(tmp_db, _envelope(), now=100)
    with pytest.raises(MessageConflict):
        enqueue_outbox(tmp_db, _envelope(project_id="project_2"), now=101)
    assert tmp_db.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0] == 0


def test_global_identity_cross_queue_claim_is_atomic_across_connections(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_persephone_store import (
        MessageConflict,
        enqueue_outbox,
        record_inbox,
    )

    path = tmp_path / "concurrent.db"
    with db.connect_closing(path):
        pass
    barrier = threading.Barrier(2)

    def claim(direction):
        with db.connect_closing(path) as conn:
            barrier.wait(timeout=5)
            try:
                if direction == "inbox":
                    record_inbox(conn, _envelope(), now=100)
                else:
                    enqueue_outbox(conn, _envelope(), now=100)
                return direction
            except MessageConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("inbox", "outbox")))

    assert sorted(results) in (["conflict", "inbox"], ["conflict", "outbox"])
    with db.connect_closing(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM persephone_message_identities").fetchone()[0] == 1
        queue_count = conn.execute("SELECT COUNT(*) FROM persephone_inbox").fetchone()[0]
        queue_count += conn.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0]
        assert queue_count == 1


def test_ack_requires_processed_response(tmp_db):
    from hermes_cli.hades_persephone_store import (
        InvalidTransition,
        record_inbox,
        transition_message,
    )

    record_inbox(tmp_db, _envelope(), now=100)
    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, "msg_1", "acknowledged", now=101)


def test_request_response_is_persisted_and_linked_atomically(tmp_db):
    from hermes_cli.hades_persephone_store import (
        get_message,
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response = _envelope(
        message_id="msg_response",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
    )
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)

    stored_response = persist_response_for_request(tmp_db, request.message_id, response, now=103)
    linked_request = get_message(tmp_db, request.message_id)

    assert stored_response.state == "outbox_pending"
    assert linked_request is not None
    assert linked_request.state == "responded"
    assert linked_request.response_message_id == response.message_id
    acknowledged = transition_message(tmp_db, request.message_id, "acknowledged", now=104)
    assert acknowledged.state == "acknowledged"


def test_processing_request_and_response_are_committed_in_one_transaction(tmp_db):
    from hermes_cli.hades_persephone_store import (
        get_message,
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response = _envelope(
        message_id="atomic_response",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
    )
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)

    persist_response_for_request(tmp_db, request.message_id, response, now=102)

    stored = get_message(tmp_db, request.message_id)
    assert stored is not None and stored.state == "responded"
    assert stored.response_message_id == "atomic_response"


def test_information_failure_is_bounded_and_recoverable(tmp_db):
    from hermes_cli.hades_persephone_store import (
        record_inbox,
        record_information_failure,
        transition_message,
    )

    request = _envelope()
    record_inbox(tmp_db, request, now=100)
    for attempt in range(1, 4):
        transition_message(tmp_db, request.message_id, "processing", now=attempt * 10)
        stored = record_information_failure(
            tmp_db, request.message_id, now=attempt * 10 + 1, max_attempts=3
        )
        assert stored.attempts == attempt
        assert stored.last_error == "information_handler_failed"
        assert stored.state == ("received" if attempt < 3 else "rejected")


def test_claim_information_request_is_not_idempotent_for_same_state(tmp_db):
    from hermes_cli.hades_persephone_store import (
        claim_information_request,
        get_message,
        record_inbox,
    )

    record_inbox(tmp_db, _envelope(), now=100)

    assert claim_information_request(tmp_db, "msg_1", now=101) is True
    assert claim_information_request(tmp_db, "msg_1", now=102) is False
    assert get_message(tmp_db, "msg_1").state == "processing"


def test_claim_information_request_has_one_winner_across_connections(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_persephone_store import (
        claim_information_request,
        record_inbox,
    )

    path = tmp_path / "information-claim.db"
    with db.connect_closing(path) as conn:
        record_inbox(conn, _envelope(), now=100)
    barrier = threading.Barrier(2)

    def claim(_: int) -> bool:
        with db.connect_closing(path) as conn:
            barrier.wait(timeout=5)
            return claim_information_request(conn, "msg_1", now=101)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))

    assert sorted(results) == [False, True]


def test_claim_information_request_accepts_only_received_or_retry(tmp_db):
    from hermes_cli.hades_persephone_store import claim_information_request

    with pytest.raises(ValueError, match="received.*retry"):
        claim_information_request(tmp_db, "msg_1", expected_states=("processing",))


def test_atomic_response_rolls_back_outbox_and_identity_when_link_fails(tmp_db):
    from hermes_cli.hades_persephone_store import (
        get_message,
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response = _envelope(
        message_id="rollback_response",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
    )
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    tmp_db.execute(
        "CREATE TRIGGER fail_response_link BEFORE UPDATE OF state ON persephone_inbox "
        "WHEN NEW.state = 'responded' BEGIN SELECT RAISE(ABORT, 'simulated crash'); END"
    )

    with pytest.raises(Exception, match="simulated crash"):
        persist_response_for_request(tmp_db, request.message_id, response, now=102)

    assert get_message(tmp_db, request.message_id).state == "processing"
    assert get_message(tmp_db, response.message_id, queue="outbox") is None
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM persephone_message_identities WHERE message_id = ?",
        (response.message_id,),
    ).fetchone()[0] == 0


def test_recover_abandoned_information_requests_is_scoped_and_bounded(tmp_db):
    from hermes_cli.hades_persephone_store import (
        get_message,
        record_inbox,
        recover_abandoned_information_requests,
        transition_message,
    )

    request = _envelope()
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)

    assert recover_abandoned_information_requests(
        tmp_db, now=200, abandoned_before=150, max_attempts=3
    ) == 1
    stored = get_message(tmp_db, request.message_id)
    assert stored is not None and stored.state == "received"
    assert stored.attempts == 1
    assert stored.last_error == "information_worker_restarted"
    assert recover_abandoned_information_requests(
        tmp_db, now=201, abandoned_before=150, max_attempts=3
    ) == 0


def test_recover_abandoned_legacy_processed_information_request(tmp_db):
    from hermes_cli.hades_persephone_store import (
        get_message,
        record_inbox,
        recover_abandoned_information_requests,
        transition_message,
    )

    request = _envelope()
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)

    assert recover_abandoned_information_requests(
        tmp_db, now=200, abandoned_before=150
    ) == 1
    stored = get_message(tmp_db, request.message_id)
    assert stored is not None and stored.state == "received"


def test_abandoned_recovery_processes_one_deterministic_bounded_batch(tmp_db):
    from hermes_cli.hades_persephone_store import (
        recover_abandoned_information_requests,
        record_inbox,
        transition_message,
    )

    for index in range(5):
        request = _envelope(message_id=f"batch_{index}")
        record_inbox(tmp_db, request, now=100 + index)
        transition_message(tmp_db, request.message_id, "processing", now=110 + index)

    assert recover_abandoned_information_requests(
        tmp_db, now=200, abandoned_before=150, limit=2
    ) == 2
    rows = tmp_db.execute(
        "SELECT message_id, state FROM persephone_inbox ORDER BY updated_at, message_id"
    ).fetchall()
    recovered = [row["message_id"] for row in rows if row["state"] == "received"]
    assert recovered == ["batch_0", "batch_1"]


def test_recovery_filters_ineligible_rows_before_limit(tmp_db):
    from hermes_cli.hades_persephone_store import (
        recover_abandoned_information_requests,
        record_inbox,
        transition_message,
    )

    for index in range(8):
        mutating = _envelope(
            message_id=f"mutating_{index}",
            effect=EffectClass.MUTATING.value,
            capability="run_tests",
        )
        record_inbox(tmp_db, mutating, now=10 + index)
        tmp_db.execute(
            "UPDATE persephone_inbox SET state = 'processing', updated_at = ? "
            "WHERE message_id = ?",
            (20 + index, mutating.message_id),
        )
        tmp_db.commit()
    for index in range(2):
        request = _envelope(message_id=f"eligible_{index}")
        record_inbox(tmp_db, request, now=100 + index)
        transition_message(tmp_db, request.message_id, "processing", now=110 + index)
    tmp_db.commit()

    assert recover_abandoned_information_requests(
        tmp_db, now=200, abandoned_before=150, limit=2
    ) == 2
    states = dict(
        tmp_db.execute("SELECT message_id, state FROM persephone_inbox").fetchall()
    )
    assert states["eligible_0"] == states["eligible_1"] == "received"
    assert all(states[f"mutating_{index}"] == "processing" for index in range(8))

    plan = tmp_db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM persephone_inbox "
        "WHERE state IN ('processing', 'processed') AND updated_at <= ? "
        "AND json_extract(envelope, '$.message_type') = 'information_request' "
        "ORDER BY updated_at, message_id LIMIT ?",
        (150, 2),
    ).fetchall()
    assert "idx_persephone_inbox_recovery" in " ".join(str(row["detail"]) for row in plan)


def test_inbox_denormalizes_validated_recovery_authority(tmp_db):
    from hermes_cli.hades_persephone_store import record_inbox

    request = _envelope(message_id="denormalized")
    record_inbox(tmp_db, request, now=100)
    row = tmp_db.execute(
        "SELECT message_type, effect, capability FROM persephone_inbox "
        "WHERE message_id = 'denormalized'"
    ).fetchone()
    assert tuple(row) == (
        MessageType.INFORMATION_REQUEST.value,
        EffectClass.INFORMATION_READ.value,
        "project_memory_search",
    )


def test_recovery_covering_plan_has_no_temp_sort(tmp_db):
    plan = tmp_db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM persephone_inbox "
        "WHERE state IN ('processing', 'processed') AND message_type = ? "
        "AND effect = ? AND capability IN (?, ?, ?, ?, ?, ?) "
        "AND updated_at <= ? "
        "ORDER BY state, message_type, effect, capability, updated_at, message_id LIMIT ?",
        (
            MessageType.INFORMATION_REQUEST.value,
            EffectClass.INFORMATION_READ.value,
            "artifact_metadata",
            "git_metadata",
            "project_memory_search",
            "source_search",
            "source_slice",
            "symbol_lookup",
            150,
            2,
        ),
    ).fetchall()
    detail = " ".join(str(row["detail"]) for row in plan)
    assert "idx_persephone_inbox_recovery_covering" in detail
    assert "TEMP B-TREE" not in detail.upper()


def test_legacy_inbox_authority_is_validated_and_backfilled(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "legacy.db"
    request = _envelope(message_id="legacy_authority")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE persephone_inbox ("
        "message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
        "received_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
        "human_decision TEXT, human_decided_by TEXT, human_reason TEXT, "
        "human_decided_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO persephone_inbox VALUES (?, ?, ?, ?, 'received', 100, 100, "
        "NULL, NULL, NULL, NULL)",
        (
            request.message_id,
            request.project_id,
            request.target_agent_id,
            json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":")),
        ),
    )
    conn.commit()
    conn.close()

    with db.connect_closing(path) as migrated:
        row = migrated.execute(
            "SELECT message_type, effect, capability FROM persephone_inbox"
        ).fetchone()
    assert tuple(row) == (
        MessageType.INFORMATION_REQUEST.value,
        EffectClass.INFORMATION_READ.value,
        "project_memory_search",
    )


def _create_mismatched_legacy_queue(
    path, *, queue: str, request, row_message_id: str, target_agent_id: str
):
    conn = sqlite3.connect(path)
    if queue == "inbox":
        conn.execute(
            "CREATE TABLE persephone_inbox ("
            "message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
            "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
            "received_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
            "human_decision TEXT, human_decided_by TEXT, human_reason TEXT, "
            "human_decided_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO persephone_inbox VALUES (?, ?, ?, ?, 'received', 100, 100, "
            "NULL, NULL, NULL, NULL)",
            (
                row_message_id,
                request.project_id,
                target_agent_id,
                json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )
    else:
        conn.execute(
            "CREATE TABLE persephone_outbox ("
            "message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
            "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL, "
            "last_error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO persephone_outbox VALUES (?, ?, ?, ?, 'outbox_pending', 0, "
            "100, NULL, 100, 100)",
            (
                row_message_id,
                request.project_id,
                target_agent_id,
                json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("queue", ["inbox", "outbox"])
def test_migration_mismatch_rolls_back_registry_columns_and_covering_index(
    tmp_path, queue
):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / f"legacy-{queue}.db"
    request = _envelope(message_id=f"envelope_{queue}")
    _create_mismatched_legacy_queue(
        path,
        queue=queue,
        request=request,
        row_message_id=(f"row_{queue}" if queue == "inbox" else request.message_id),
        target_agent_id=(request.target_agent_id if queue == "inbox" else "wrong_target"),
    )

    with pytest.raises(db.PersephoneIdentityMigrationConflict):
        db.connect(path)

    raw = sqlite3.connect(path)
    identity_count = raw.execute(
        "SELECT COUNT(*) FROM persephone_message_identities"
    ).fetchone()[0]
    covering_index = raw.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_persephone_inbox_recovery_covering'"
    ).fetchone()[0]
    if queue == "inbox":
        columns = {row[1] for row in raw.execute("PRAGMA table_info(persephone_inbox)")}
        assert "message_type" not in columns
    raw.close()
    assert identity_count == 0
    assert covering_index == 0


def test_existing_denormalized_authority_mismatch_is_rejected(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_persephone_store import record_inbox

    path = tmp_path / "denormalized-mismatch.db"
    with db.connect_closing(path) as conn:
        record_inbox(conn, _envelope(message_id="denorm_mismatch"), now=100)
        conn.execute(
            "UPDATE persephone_inbox SET capability = 'source_search' "
            "WHERE message_id = 'denorm_mismatch'"
        )
        conn.commit()
    db._INITIALIZED_PATHS.discard(str(path.resolve()))

    with pytest.raises(db.PersephoneIdentityMigrationConflict, match="denormalized"):
        db.connect(path)


def test_information_failure_api_refuses_non_information_processing_work(tmp_db):
    from hermes_cli.hades_persephone_store import (
        InvalidTransition,
        record_inbox,
        record_information_failure,
    )

    mutating = _envelope(effect=EffectClass.MUTATING.value, capability="run_tests")
    record_inbox(tmp_db, mutating, now=100)
    tmp_db.execute(
        "UPDATE persephone_inbox SET state = 'processing' WHERE message_id = ?",
        (mutating.message_id,),
    )
    tmp_db.commit()

    with pytest.raises(InvalidTransition, match="information"):
        record_information_failure(tmp_db, mutating.message_id, now=101)


def test_ack_refuses_a_tampered_or_missing_durable_response(tmp_db):
    from hermes_cli.hades_persephone_store import (
        InvalidTransition,
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response = _envelope(
        message_id="msg_response",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        causation_id=request.message_id,
    )
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)
    persist_response_for_request(tmp_db, request.message_id, response, now=103)
    tmp_db.execute("DELETE FROM persephone_outbox WHERE message_id = ?", (response.message_id,))
    tmp_db.commit()

    with pytest.raises(InvalidTransition, match="durable"):
        transition_message(tmp_db, request.message_id, "acknowledged", now=104)


def test_generic_transition_cannot_claim_a_request_was_responded(tmp_db):
    from hermes_cli.hades_persephone_store import (
        InvalidTransition,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)

    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, request.message_id, "responded", now=103)
    with pytest.raises(InvalidTransition):
        transition_message(tmp_db, request.message_id, "acknowledged", now=103)


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"project_id": "project_2"}, "project"),
        ({"correlation_id": "wrong"}, "correlation"),
        ({"causation_id": "wrong"}, "causation"),
        ({"sender_agent_id": "forged"}, "sender"),
        ({"target_agent_id": "forged"}, "target"),
    ],
)
def test_response_link_rejects_forged_or_mismatched_envelopes(tmp_db, override, error):
    from hermes_cli.hades_persephone_store import (
        MessageConflict,
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response_fields = {
        "message_id": "msg_response",
        "project_id": request.project_id,
        "target_agent_id": request.sender_agent_id,
        "sender_agent_id": request.target_agent_id,
        "message_type": MessageType.INFORMATION_RESPONSE.value,
        "correlation_id": request.correlation_id,
        "causation_id": request.message_id,
    }
    response_fields.update(override)
    response = _envelope(**response_fields)
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)

    with pytest.raises(MessageConflict, match=error):
        persist_response_for_request(tmp_db, request.message_id, response, now=103)
    assert tmp_db.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0] == 0


def test_response_link_rolls_back_outbox_and_identity_if_request_update_crashes(tmp_db):
    from hermes_cli.hades_persephone_store import (
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response = _envelope(
        message_id="msg_response",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        causation_id=request.message_id,
    )
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)
    tmp_db.execute(
        "CREATE TRIGGER fail_response_link BEFORE UPDATE OF response_message_id ON persephone_inbox "
        "BEGIN SELECT RAISE(ABORT, 'simulated crash'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="simulated crash"):
        persist_response_for_request(tmp_db, request.message_id, response, now=103)
    assert tmp_db.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0] == 0
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM persephone_message_identities WHERE message_id = 'msg_response'"
        ).fetchone()[0]
        == 0
    )
    row = tmp_db.execute(
        "SELECT state, response_message_id FROM persephone_inbox WHERE message_id = 'msg_1'"
    ).fetchone()
    assert tuple(row) == ("processed", None)
    tmp_db.execute("DROP TRIGGER fail_response_link")
    retried = persist_response_for_request(tmp_db, request.message_id, response, now=104)
    assert retried.message_id == response.message_id


def test_response_link_is_idempotent_but_rejects_a_second_response(tmp_db):
    from hermes_cli.hades_persephone_store import (
        MessageConflict,
        persist_response_for_request,
        record_inbox,
        transition_message,
    )

    request = _envelope()
    response = _envelope(
        message_id="msg_response",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        causation_id=request.message_id,
    )
    other_response = _envelope(
        message_id="msg_other",
        target_agent_id=request.sender_agent_id,
        sender_agent_id=request.target_agent_id,
        message_type=MessageType.INFORMATION_RESPONSE.value,
        causation_id=request.message_id,
    )
    record_inbox(tmp_db, request, now=100)
    transition_message(tmp_db, request.message_id, "processing", now=101)
    transition_message(tmp_db, request.message_id, "processed", now=102)
    first = persist_response_for_request(tmp_db, request.message_id, response, now=103)
    second = persist_response_for_request(tmp_db, request.message_id, response, now=200)

    assert second.updated_at == first.updated_at
    with pytest.raises(MessageConflict, match="different response"):
        persist_response_for_request(tmp_db, request.message_id, other_response, now=201)
    assert tmp_db.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0] == 1


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

    assert {
        "persephone_outbox",
        "persephone_inbox",
        "persephone_cursors",
        "persephone_message_identities",
    } <= tables
    assert event is not None
    assert {
        "idx_persephone_outbox_target_state",
        "idx_persephone_outbox_next_attempt",
        "idx_persephone_inbox_target_state",
    } <= indexes


def test_existing_o2_rows_backfill_global_identity_and_response_link_column(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "pre_registry.db"
    envelope = _envelope()
    encoded = json.dumps(
        envelope.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE persephone_inbox (message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
        "received_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, human_decision TEXT, "
        "human_decided_by TEXT, human_reason TEXT, human_decided_at INTEGER)"
    )
    legacy.execute(
        "INSERT INTO persephone_inbox VALUES (?, ?, ?, ?, 'received', 100, 100, NULL, NULL, NULL, NULL)",
        (envelope.message_id, envelope.project_id, envelope.target_agent_id, encoded),
    )
    legacy.commit()
    legacy.close()

    with db.connect_closing(path) as conn:
        identity = conn.execute(
            "SELECT project_id, direction, envelope FROM persephone_message_identities "
            "WHERE message_id = ?",
            (envelope.message_id,),
        ).fetchone()
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(persephone_inbox)").fetchall()
        }

    assert dict(identity) == {
        "project_id": envelope.project_id,
        "direction": "inbox",
        "envelope": encoded,
    }
    assert "response_message_id" in columns


def test_existing_outbox_backfills_canonical_sender_and_sender_due_index(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "pre-sender-outbox.db"
    envelope = _envelope(sender_agent_id="legacy_sender")
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE persephone_outbox (message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
        "attempts INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL, last_error TEXT, "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO persephone_outbox VALUES (?, ?, ?, ?, 'outbox_pending', 0, 100, NULL, 100, 100)",
        (
            envelope.message_id,
            envelope.project_id,
            envelope.target_agent_id,
            json.dumps(envelope.to_dict(), indent=2),
        ),
    )
    legacy.commit()
    legacy.close()

    with db.connect_closing(path) as conn:
        row = conn.execute(
            "SELECT sender_agent_id, envelope FROM persephone_outbox WHERE message_id = ?",
            (envelope.message_id,),
        ).fetchone()
        indexes = {
            item["name"]
            for item in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }

    assert row["sender_agent_id"] == "legacy_sender"
    assert row["envelope"] == json.dumps(
        envelope.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert "idx_persephone_outbox_sender_due" in indexes


def test_outbox_sender_migration_conflict_rolls_back_backfill_and_index(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "sender-conflict.db"
    envelope = _envelope(sender_agent_id="canonical_sender")
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE persephone_outbox (message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "sender_agent_id TEXT, target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, "
        "state TEXT NOT NULL, attempts INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL, "
        "last_error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO persephone_outbox VALUES (?, ?, ?, ?, ?, 'outbox_pending', 0, 100, NULL, 100, 100)",
        (
            envelope.message_id,
            envelope.project_id,
            "wrong_sender",
            envelope.target_agent_id,
            json.dumps(envelope.to_dict(), sort_keys=True, separators=(",", ":")),
        ),
    )
    legacy.commit()
    legacy.close()

    with pytest.raises(db.PersephoneIdentityMigrationConflict, match="sender_agent_id"):
        db.connect(path)

    raw = sqlite3.connect(path)
    sender = raw.execute(
        "SELECT sender_agent_id FROM persephone_outbox WHERE message_id = ?",
        (envelope.message_id,),
    ).fetchone()[0]
    index_count = raw.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_persephone_outbox_sender_due'"
    ).fetchone()[0]
    identity_count = raw.execute(
        "SELECT COUNT(*) FROM persephone_message_identities"
    ).fetchone()[0]
    raw.close()

    assert sender == "wrong_sender"
    assert index_count == 0
    assert identity_count == 0


def test_existing_cross_queue_identity_conflict_aborts_migration_explicitly(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "conflicting.db"
    inbox = _envelope()
    outbox = _envelope(project_id="project_2")
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE persephone_inbox (message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
        "received_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, human_decision TEXT, "
        "human_decided_by TEXT, human_reason TEXT, human_decided_at INTEGER);"
        "CREATE TABLE persephone_outbox (message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "target_agent_id TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, "
        "attempts INTEGER NOT NULL, next_attempt_at INTEGER NOT NULL, last_error TEXT, "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);"
    )
    legacy.execute(
        "INSERT INTO persephone_inbox VALUES (?, ?, ?, ?, 'received', 100, 100, NULL, NULL, NULL, NULL)",
        (
            inbox.message_id,
            inbox.project_id,
            inbox.target_agent_id,
            json.dumps(inbox.to_dict(), sort_keys=True, separators=(",", ":")),
        ),
    )
    legacy.execute(
        "INSERT INTO persephone_outbox VALUES (?, ?, ?, ?, 'outbox_pending', 0, 100, NULL, 100, 100)",
        (
            outbox.message_id,
            outbox.project_id,
            outbox.target_agent_id,
            json.dumps(outbox.to_dict(), sort_keys=True, separators=(",", ":")),
        ),
    )
    legacy.commit()
    legacy.close()

    with pytest.raises(db.PersephoneIdentityMigrationConflict, match="msg_1"):
        db.connect(path)

"""Durable local state for project-scoped Persephone agent messages.

This module deliberately contains no network transport.  An outbound message
is committed before a sender can claim it, and every inbound transition is
guarded so process restarts or duplicate backend delivery cannot skip policy
or human-approval boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import time
from typing import Literal

from hermes_cli.hades_persephone_messages import (
    AgentMessageEnvelope,
    EffectClass,
    MessageType,
    parse_envelope,
)
from hermes_cli.sqlite_util import write_txn


QueueName = Literal["inbox", "outbox"]


class InvalidTransition(ValueError):
    """Raised when a queue record cannot enter the requested state."""


class MessageConflict(ValueError):
    """Raised when a duplicate message ID carries different immutable data."""


@dataclass(frozen=True)
class StoredMessage:
    message_id: str
    project_id: str
    target_agent_id: str
    envelope: AgentMessageEnvelope
    state: str
    attempts: int
    next_attempt_at: int | None
    last_error: str | None
    created_at: int
    updated_at: int
    human_decision: str | None = None
    human_decided_by: str | None = None
    human_reason: str | None = None
    human_decided_at: int | None = None
    response_message_id: str | None = None


_OUTBOX_TRANSITIONS = {
    "outbox_pending": frozenset({"sending", "dead_letter"}),
    "retry": frozenset({"sending", "dead_letter"}),
    "sending": frozenset({"sent", "retry", "dead_letter"}),
    "sent": frozenset(),
    "dead_letter": frozenset(),
}
_INBOX_TRANSITIONS = {
    "received": frozenset({"processing", "waiting_human_approval", "rejected", "expired"}),
    # Human decisions use approve_request(), which records the actor and
    # decision atomically.  A generic state transition must not bypass that
    # audit boundary.
    "waiting_human_approval": frozenset({"expired"}),
    "approved": frozenset({"processing", "expired"}),
    "processing": frozenset({"processed", "rejected", "expired"}),
    # A request reaches responded only through persist_response_for_request(),
    # which links the durable outbox record in the same transaction.
    "processed": frozenset({"acknowledged"}),
    "responded": frozenset({"acknowledged"}),
    "acknowledged": frozenset(),
    "rejected": frozenset(),
    "expired": frozenset(),
}
_RESPONSE_TYPES = frozenset(
    {
        MessageType.INFORMATION_RESPONSE,
        MessageType.STATUS_RESPONSE,
        MessageType.LOCAL_DECISION,
    }
)
_EXPECTED_RESPONSE_TYPE = {
    MessageType.INFORMATION_REQUEST: MessageType.INFORMATION_RESPONSE,
    MessageType.STATUS_QUERY: MessageType.STATUS_RESPONSE,
    MessageType.CANCEL_REQUEST: MessageType.LOCAL_DECISION,
}
_INFORMATION_CAPABILITIES = frozenset(
    {"source_slice", "source_search", "symbol_lookup", "git_metadata", "artifact_metadata", "project_memory_search"}
)


def _now(value: int | None) -> int:
    return int(time.time()) if value is None else int(value)


def _serialized(envelope: AgentMessageEnvelope) -> str:
    return json.dumps(envelope.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parsed(raw: str) -> AgentMessageEnvelope:
    # Expiry is a receiver policy checked again after persistence.  Loading a
    # historical row must remain possible for audit, recovery, and cleanup.
    return parse_envelope(json.loads(raw), now=0)


def _outbox_from_row(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        message_id=row["message_id"],
        project_id=row["project_id"],
        target_agent_id=row["target_agent_id"],
        envelope=_parsed(row["envelope"]),
        state=row["state"],
        attempts=int(row["attempts"]),
        next_attempt_at=int(row["next_attempt_at"]),
        last_error=row["last_error"],
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _inbox_from_row(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        message_id=row["message_id"],
        project_id=row["project_id"],
        target_agent_id=row["target_agent_id"],
        envelope=_parsed(row["envelope"]),
        state=row["state"],
        attempts=int(row["attempts"]),
        next_attempt_at=int(row["next_attempt_at"]),
        last_error=row["last_error"],
        created_at=int(row["received_at"]),
        updated_at=int(row["updated_at"]),
        human_decision=row["human_decision"],
        human_decided_by=row["human_decided_by"],
        human_reason=row["human_reason"],
        human_decided_at=(
            int(row["human_decided_at"]) if row["human_decided_at"] is not None else None
        ),
        response_message_id=row["response_message_id"],
    )


def _claim_identity_in_txn(
    conn: sqlite3.Connection,
    envelope: AgentMessageEnvelope,
    *,
    direction: QueueName,
    claimed_at: int,
) -> str:
    encoded = _serialized(envelope)
    existing = conn.execute(
        "SELECT project_id, direction, envelope FROM persephone_message_identities "
        "WHERE message_id = ?",
        (envelope.message_id,),
    ).fetchone()
    if existing is not None:
        if existing["direction"] != direction:
            raise MessageConflict(
                f"message_id {envelope.message_id!r} is already claimed for direction "
                f"{existing['direction']!r}"
            )
        if existing["project_id"] != envelope.project_id or existing["envelope"] != encoded:
            raise MessageConflict(
                f"message_id {envelope.message_id!r} is already claimed with different data"
            )
        return encoded
    conn.execute(
        "INSERT INTO persephone_message_identities "
        "(message_id, project_id, direction, envelope, claimed_at) VALUES (?, ?, ?, ?, ?)",
        (envelope.message_id, envelope.project_id, direction, encoded, claimed_at),
    )
    return encoded


def _insert_outbox_in_txn(
    conn: sqlite3.Connection,
    envelope: AgentMessageEnvelope,
    *,
    timestamp: int,
    due_at: int,
) -> None:
    encoded = _claim_identity_in_txn(
        conn, envelope, direction="outbox", claimed_at=timestamp
    )
    existing = conn.execute(
        "SELECT envelope FROM persephone_outbox WHERE message_id = ?", (envelope.message_id,)
    ).fetchone()
    if existing is not None and existing["envelope"] != encoded:
        raise MessageConflict(f"outbox message_id {envelope.message_id!r} already has other data")
    conn.execute(
        "INSERT OR IGNORE INTO persephone_outbox "
        "(message_id, project_id, target_agent_id, envelope, state, attempts, "
        " next_attempt_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'outbox_pending', 0, ?, ?, ?)",
        (
            envelope.message_id,
            envelope.project_id,
            envelope.target_agent_id,
            encoded,
            due_at,
            timestamp,
            timestamp,
        ),
    )


def get_message(
    conn: sqlite3.Connection,
    message_id: str,
    *,
    queue: QueueName = "inbox",
) -> StoredMessage | None:
    if queue == "outbox":
        row = conn.execute(
            "SELECT * FROM persephone_outbox WHERE message_id = ?", (message_id,)
        ).fetchone()
        return _outbox_from_row(row) if row is not None else None
    if queue == "inbox":
        row = conn.execute(
            "SELECT * FROM persephone_inbox WHERE message_id = ?", (message_id,)
        ).fetchone()
        return _inbox_from_row(row) if row is not None else None
    raise ValueError(f"unsupported queue: {queue}")


def enqueue_outbox(
    conn: sqlite3.Connection,
    envelope: AgentMessageEnvelope,
    *,
    now: int | None = None,
    next_attempt_at: int | None = None,
) -> StoredMessage:
    timestamp = _now(now)
    due_at = timestamp if next_attempt_at is None else int(next_attempt_at)
    with write_txn(conn):
        _insert_outbox_in_txn(conn, envelope, timestamp=timestamp, due_at=due_at)
    result = get_message(conn, envelope.message_id, queue="outbox")
    assert result is not None
    return result


def claim_due_outbox(
    conn: sqlite3.Connection,
    *,
    now: int | None = None,
    limit: int = 50,
) -> list[StoredMessage]:
    timestamp = _now(now)
    bounded_limit = max(0, int(limit))
    if bounded_limit == 0:
        return []
    with write_txn(conn):
        rows = conn.execute(
            "SELECT message_id FROM persephone_outbox "
            "WHERE state IN ('outbox_pending', 'retry') AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at ASC, created_at ASC, message_id ASC LIMIT ?",
            (timestamp, bounded_limit),
        ).fetchall()
        message_ids = [str(row["message_id"]) for row in rows]
        for message_id in message_ids:
            conn.execute(
                "UPDATE persephone_outbox SET state = 'sending', attempts = attempts + 1, "
                "updated_at = ? WHERE message_id = ? "
                "AND state IN ('outbox_pending', 'retry')",
                (timestamp, message_id),
            )
        claimed_rows = [
            conn.execute(
                "SELECT * FROM persephone_outbox WHERE message_id = ? AND state = 'sending'",
                (message_id,),
            ).fetchone()
            for message_id in message_ids
        ]
    return [_outbox_from_row(row) for row in claimed_rows if row is not None]


def recover_abandoned_outbox(
    conn: sqlite3.Connection,
    *,
    now: int | None = None,
    abandoned_before: int | None = None,
) -> int:
    timestamp = _now(now)
    cutoff = timestamp if abandoned_before is None else int(abandoned_before)
    with write_txn(conn):
        cursor = conn.execute(
            "UPDATE persephone_outbox SET state = 'retry', next_attempt_at = ?, "
            "last_error = COALESCE(last_error, 'sender_restarted'), updated_at = ? "
            "WHERE state = 'sending' AND updated_at <= ?",
            (timestamp, timestamp, cutoff),
        )
    return max(0, int(cursor.rowcount))


def record_inbox(
    conn: sqlite3.Connection,
    envelope: AgentMessageEnvelope,
    *,
    now: int | None = None,
) -> StoredMessage:
    timestamp = _now(now)
    encoded = _serialized(envelope)
    with write_txn(conn):
        _claim_identity_in_txn(conn, envelope, direction="inbox", claimed_at=timestamp)
        existing = conn.execute(
            "SELECT envelope FROM persephone_inbox WHERE message_id = ?", (envelope.message_id,)
        ).fetchone()
        if existing is not None and existing["envelope"] != encoded:
            raise MessageConflict(f"inbox message_id {envelope.message_id!r} already has other data")
        conn.execute(
            "INSERT OR IGNORE INTO persephone_inbox "
            "(message_id, project_id, target_agent_id, envelope, state, received_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'received', ?, ?)",
            (
                envelope.message_id,
                envelope.project_id,
                envelope.target_agent_id,
                encoded,
                timestamp,
                timestamp,
            ),
        )
    result = get_message(conn, envelope.message_id)
    assert result is not None
    return result


def _validate_response_link(
    request: AgentMessageEnvelope,
    response: AgentMessageEnvelope,
) -> None:
    checks = (
        (response.project_id == request.project_id, "response project does not match request"),
        (
            response.correlation_id == request.correlation_id,
            "response correlation does not match request",
        ),
        (response.causation_id == request.message_id, "response causation does not match request"),
        (response.sender_agent_id == request.target_agent_id, "response sender is not request target"),
        (response.target_agent_id == request.sender_agent_id, "response target is not request sender"),
        (
            response.message_type == _EXPECTED_RESPONSE_TYPE.get(request.message_type),
            "response type does not match request type",
        ),
        (response.capability == request.capability, "response capability does not match request"),
        (response.remote_task_id == request.remote_task_id, "response remote task does not match request"),
        (
            response.remote_task_version == request.remote_task_version,
            "response remote task version does not match request",
        ),
        (response.effect == EffectClass.INFORMATION_READ, "response effect must be information_read"),
    )
    for valid, message in checks:
        if not valid:
            raise MessageConflict(message)


def _linked_response_in_txn(
    conn: sqlite3.Connection,
    request: StoredMessage,
) -> StoredMessage | None:
    response_id = request.response_message_id
    if not response_id:
        return None
    row = conn.execute(
        "SELECT * FROM persephone_outbox WHERE message_id = ?", (response_id,)
    ).fetchone()
    if row is None:
        return None
    response = _outbox_from_row(row)
    identity = conn.execute(
        "SELECT project_id, direction, envelope FROM persephone_message_identities "
        "WHERE message_id = ?",
        (response_id,),
    ).fetchone()
    if (
        identity is None
        or identity["project_id"] != response.project_id
        or identity["direction"] != "outbox"
        or identity["envelope"] != _serialized(response.envelope)
    ):
        return None
    try:
        _validate_response_link(request.envelope, response.envelope)
    except MessageConflict:
        return None
    return response


def persist_response_for_request(
    conn: sqlite3.Connection,
    request_message_id: str,
    response: AgentMessageEnvelope,
    *,
    now: int | None = None,
    next_attempt_at: int | None = None,
) -> StoredMessage:
    """Atomically persist a correlated response and link its processed request."""
    timestamp = _now(now)
    due_at = timestamp if next_attempt_at is None else int(next_attempt_at)
    with write_txn(conn):
        request_row = conn.execute(
            "SELECT * FROM persephone_inbox WHERE message_id = ?", (request_message_id,)
        ).fetchone()
        if request_row is None:
            raise KeyError(request_message_id)
        request = _inbox_from_row(request_row)
        _validate_response_link(request.envelope, response)

        if request.state in {"responded", "acknowledged"}:
            if request.response_message_id != response.message_id:
                raise MessageConflict("request is already linked to a different response")
            stored = _linked_response_in_txn(conn, request)
            if stored is None or _serialized(stored.envelope) != _serialized(response):
                raise MessageConflict("request response link is not durably valid")
            return stored
        if request.state not in {"processing", "processed"}:
            raise InvalidTransition(
                f"request {request_message_id!r} must be processing before persisting a response"
            )

        _insert_outbox_in_txn(conn, response, timestamp=timestamp, due_at=due_at)
        cursor = conn.execute(
            "UPDATE persephone_inbox SET state = 'responded', response_message_id = ?, "
            "updated_at = ? WHERE message_id = ? AND state = ? "
            "AND response_message_id IS NULL",
            (response.message_id, timestamp, request_message_id, request.state),
        )
        if cursor.rowcount != 1:
            raise InvalidTransition(
                f"concurrent response changed request {request_message_id!r}"
            )
        row = conn.execute(
            "SELECT * FROM persephone_outbox WHERE message_id = ?", (response.message_id,)
        ).fetchone()
        assert row is not None
        return _outbox_from_row(row)


def record_information_failure(
    conn: sqlite3.Connection,
    request_message_id: str,
    *,
    now: int | None = None,
    max_attempts: int = 3,
    retry_delay: int = 0,
) -> StoredMessage:
    """Return a failed information read to a bounded, claimable state."""
    timestamp = _now(now)
    bounded_max = max(1, int(max_attempts))
    with write_txn(conn):
        row = conn.execute(
            "SELECT * FROM persephone_inbox WHERE message_id = ?", (request_message_id,)
        ).fetchone()
        if row is None:
            raise KeyError(request_message_id)
        current = _inbox_from_row(row)
        if current.state != "processing":
            raise InvalidTransition("only a processing information request may fail")
        envelope = current.envelope
        if not (
            envelope.message_type == MessageType.INFORMATION_REQUEST
            and envelope.effect == EffectClass.INFORMATION_READ
            and envelope.capability in _INFORMATION_CAPABILITIES
        ):
            raise InvalidTransition("failure recovery is limited to information requests")
        attempts = current.attempts + 1
        state = "rejected" if attempts >= bounded_max else "received"
        cursor = conn.execute(
            "UPDATE persephone_inbox SET state = ?, attempts = ?, next_attempt_at = ?, "
            "last_error = 'information_handler_failed', updated_at = ? "
            "WHERE message_id = ? AND state = 'processing'",
            (state, attempts, timestamp + max(0, int(retry_delay)), timestamp, request_message_id),
        )
        if cursor.rowcount != 1:
            raise InvalidTransition("concurrent information failure transition")
    result = get_message(conn, request_message_id)
    assert result is not None
    return result


def recover_abandoned_information_requests(
    conn: sqlite3.Connection,
    *,
    now: int | None = None,
    abandoned_before: int | None = None,
    max_attempts: int = 3,
) -> int:
    """Recover only stale, auto-eligible information workers after restart."""
    timestamp = _now(now)
    cutoff = timestamp if abandoned_before is None else int(abandoned_before)
    bounded_max = max(1, int(max_attempts))
    recovered = 0
    with write_txn(conn):
        rows = conn.execute(
            "SELECT * FROM persephone_inbox WHERE state = 'processing' AND updated_at <= ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            current = _inbox_from_row(row)
            envelope = current.envelope
            if not (
                envelope.message_type == MessageType.INFORMATION_REQUEST
                and envelope.effect == EffectClass.INFORMATION_READ
                and envelope.capability in _INFORMATION_CAPABILITIES
            ):
                continue
            attempts = current.attempts + 1
            state = "rejected" if attempts >= bounded_max else "received"
            cursor = conn.execute(
                "UPDATE persephone_inbox SET state = ?, attempts = ?, next_attempt_at = ?, "
                "last_error = 'information_worker_restarted', updated_at = ? "
                "WHERE message_id = ? AND state = 'processing' AND updated_at <= ?",
                (state, attempts, timestamp, timestamp, current.message_id, cutoff),
            )
            recovered += max(0, int(cursor.rowcount))
    return recovered


def transition_message(
    conn: sqlite3.Connection,
    message_id: str,
    new_state: str,
    *,
    queue: QueueName = "inbox",
    now: int | None = None,
    next_attempt_at: int | None = None,
    last_error: str | None = None,
) -> StoredMessage:
    timestamp = _now(now)
    with write_txn(conn):
        if queue == "outbox":
            row = conn.execute(
                "SELECT * FROM persephone_outbox WHERE message_id = ?", (message_id,)
            ).fetchone()
            current = _outbox_from_row(row) if row is not None else None
        elif queue == "inbox":
            row = conn.execute(
                "SELECT * FROM persephone_inbox WHERE message_id = ?", (message_id,)
            ).fetchone()
            current = _inbox_from_row(row) if row is not None else None
        else:
            raise ValueError(f"unsupported queue: {queue}")
        if current is None:
            raise KeyError(message_id)
        if (
            queue == "inbox"
            and current.envelope.message_type in _EXPECTED_RESPONSE_TYPE
            and new_state in {"responded", "acknowledged"}
            and current.state in {"responded", "acknowledged"}
            and _linked_response_in_txn(conn, current) is None
        ):
            raise InvalidTransition("request has no durable validated response link")
        if new_state == current.state:
            return current
        transitions = _OUTBOX_TRANSITIONS if queue == "outbox" else _INBOX_TRANSITIONS
        if new_state not in transitions.get(current.state, frozenset()):
            raise InvalidTransition(
                f"cannot transition {queue} {current.state!r} to {new_state!r}"
            )
        if (
            queue == "inbox"
            and current.state == "processed"
            and new_state == "acknowledged"
            and current.envelope.message_type not in _RESPONSE_TYPES
        ):
            raise InvalidTransition("a request must persist its response before acknowledgement")
        if (
            queue == "inbox"
            and current.envelope.message_type in _EXPECTED_RESPONSE_TYPE
            and new_state == "responded"
        ):
            raise InvalidTransition("requests become responded only when a response is persisted")
        if (
            queue == "inbox"
            and current.state == "responded"
            and new_state == "acknowledged"
            and _linked_response_in_txn(conn, current) is None
        ):
            raise InvalidTransition("request has no durable validated response link")
        if (
            queue == "inbox"
            and current.state == "received"
            and new_state == "processing"
            and current.envelope.effect == EffectClass.MUTATING
        ):
            raise InvalidTransition("mutating requests require a recorded human approval")

        if queue == "outbox":
            due_at = current.next_attempt_at if next_attempt_at is None else int(next_attempt_at)
            cursor = conn.execute(
                "UPDATE persephone_outbox SET state = ?, next_attempt_at = ?, last_error = ?, "
                "updated_at = ? WHERE message_id = ? AND state = ?",
                (new_state, due_at, last_error, timestamp, message_id, current.state),
            )
        else:  # inbox
            cursor = conn.execute(
                "UPDATE persephone_inbox SET state = ?, updated_at = ? "
                "WHERE message_id = ? AND state = ?",
                (new_state, timestamp, message_id, current.state),
            )
        if cursor.rowcount != 1:
            raise InvalidTransition(f"concurrent transition changed {queue} message {message_id!r}")
    result = get_message(conn, message_id, queue=queue)
    assert result is not None
    return result


def pending_human_requests(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    target_agent_id: str | None = None,
) -> list[StoredMessage]:
    clauses = ["state = 'waiting_human_approval'"]
    values: list[str] = []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if target_agent_id is not None:
        clauses.append("target_agent_id = ?")
        values.append(target_agent_id)
    rows = conn.execute(
        f"SELECT * FROM persephone_inbox WHERE {' AND '.join(clauses)} "
        "ORDER BY received_at ASC, message_id ASC",
        values,
    ).fetchall()
    return [_inbox_from_row(row) for row in rows]


def approve_request(
    conn: sqlite3.Connection,
    message_id: str,
    *,
    approved: bool,
    decided_by: str,
    reason: str | None = None,
    project_id: str | None = None,
    now: int | None = None,
) -> StoredMessage:
    timestamp = _now(now)
    decided_by = str(decided_by).strip()
    if not decided_by:
        raise ValueError("decided_by must name the local human decision source")
    decision = "approved" if approved else "rejected"
    current = get_message(conn, message_id)
    if current is None or (project_id is not None and current.project_id != project_id):
        raise KeyError(message_id)
    if current.human_decision is not None:
        if (
            current.human_decision == decision
            and current.human_decided_by == decided_by
            and current.human_reason == reason
        ):
            return current
        raise InvalidTransition(f"message {message_id!r} already has a human decision")
    if current.state != "waiting_human_approval":
        raise InvalidTransition(f"message {message_id!r} is not waiting for human approval")
    with write_txn(conn):
        cursor = conn.execute(
            "UPDATE persephone_inbox SET state = ?, human_decision = ?, human_decided_by = ?, "
            "human_reason = ?, human_decided_at = ?, updated_at = ? "
            "WHERE message_id = ? AND state = 'waiting_human_approval' AND human_decision IS NULL",
            (decision, decision, decided_by, reason, timestamp, timestamp, message_id),
        )
        if cursor.rowcount != 1:
            raise InvalidTransition(f"concurrent decision changed message {message_id!r}")
    result = get_message(conn, message_id)
    assert result is not None
    return result


def record_cursor(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    target_agent_id: str,
    cursor: str,
    now: int | None = None,
) -> None:
    if not project_id.strip() or not target_agent_id.strip() or not str(cursor).strip():
        raise ValueError("project_id, target_agent_id, and cursor must be non-blank")
    timestamp = _now(now)
    with write_txn(conn):
        conn.execute(
            "INSERT INTO persephone_cursors (project_id, target_agent_id, cursor, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(project_id, target_agent_id) DO UPDATE SET "
            "cursor = excluded.cursor, updated_at = excluded.updated_at",
            (project_id, target_agent_id, str(cursor), timestamp),
        )


def get_cursor(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    target_agent_id: str,
) -> str | None:
    row = conn.execute(
        "SELECT cursor FROM persephone_cursors WHERE project_id = ? AND target_agent_id = ?",
        (project_id, target_agent_id),
    ).fetchone()
    return str(row["cursor"]) if row is not None else None


def record_subscription_mismatch(
    conn: sqlite3.Connection,
    envelope: AgentMessageEnvelope,
    *,
    subscription_project_id: str,
    subscription_agent_id: str,
    subscription_workspace_binding_id: str | None = None,
    cursor: str | None = None,
    now: int | None = None,
) -> bool:
    """Durably reject one misrouted delivery without rejecting its envelope.

    The same globally valid envelope may later arrive on its correct queue.
    Therefore this audit is keyed by subscription context rather than by the
    global message state.  Only the first durable delivery advances the opaque
    subscription cursor, preventing a replay from rewinding a newer cursor.
    """
    project = str(subscription_project_id or "").strip()
    agent = str(subscription_agent_id or "").strip()
    binding = str(subscription_workspace_binding_id or "").strip()
    resume = str(cursor or "").strip() or None
    if not project or not agent:
        raise ValueError("subscription project and agent must be non-blank")
    timestamp = _now(now)
    with write_txn(conn):
        inserted = conn.execute(
            "INSERT OR IGNORE INTO persephone_subscription_deliveries "
            "(subscription_project_id, subscription_agent_id, "
            " subscription_workspace_binding_id, message_id, cursor, disposition, "
            " envelope_project_id, envelope_target_agent_id, "
            " envelope_target_workspace_binding_id, received_at) "
            "VALUES (?, ?, ?, ?, ?, 'subscription_route_mismatch', ?, ?, ?, ?)",
            (
                project,
                agent,
                binding,
                envelope.message_id,
                resume,
                envelope.project_id,
                envelope.target_agent_id,
                envelope.target_workspace_binding_id,
                timestamp,
            ),
        ).rowcount == 1
        if inserted and resume is not None:
            conn.execute(
                "INSERT INTO persephone_cursors "
                "(project_id, target_agent_id, cursor, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(project_id, target_agent_id) DO UPDATE SET "
                "cursor = excluded.cursor, updated_at = excluded.updated_at",
                (project, agent, resume, timestamp),
            )
    return inserted


__all__ = [
    "InvalidTransition",
    "MessageConflict",
    "StoredMessage",
    "approve_request",
    "claim_due_outbox",
    "enqueue_outbox",
    "get_cursor",
    "get_message",
    "pending_human_requests",
    "persist_response_for_request",
    "record_cursor",
    "record_inbox",
    "record_information_failure",
    "recover_abandoned_information_requests",
    "record_subscription_mismatch",
    "recover_abandoned_outbox",
    "transition_message",
]

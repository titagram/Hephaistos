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

from hermes_cli.hades_persephone_messages import AgentMessageEnvelope, MessageType, parse_envelope
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
    "processed": frozenset({"responded", "acknowledged"}),
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
        attempts=0,
        next_attempt_at=None,
        last_error=None,
        created_at=int(row["received_at"]),
        updated_at=int(row["updated_at"]),
        human_decision=row["human_decision"],
        human_decided_by=row["human_decided_by"],
        human_reason=row["human_reason"],
        human_decided_at=(
            int(row["human_decided_at"]) if row["human_decided_at"] is not None else None
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
    encoded = _serialized(envelope)
    with write_txn(conn):
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
    current = get_message(conn, message_id, queue=queue)
    if current is None:
        raise KeyError(message_id)
    if new_state == current.state:
        return current
    transitions = _OUTBOX_TRANSITIONS if queue == "outbox" else _INBOX_TRANSITIONS
    if new_state not in transitions.get(current.state, frozenset()):
        raise InvalidTransition(f"cannot transition {queue} {current.state!r} to {new_state!r}")
    if (
        queue == "inbox"
        and current.state == "processed"
        and new_state == "acknowledged"
        and current.envelope.message_type not in _RESPONSE_TYPES
    ):
        raise InvalidTransition("a request must persist its response before acknowledgement")
    if (
        queue == "inbox"
        and current.state == "received"
        and new_state == "processing"
        and current.envelope.effect.value == "mutating"
    ):
        raise InvalidTransition("mutating requests require a recorded human approval")

    with write_txn(conn):
        if queue == "outbox":
            due_at = current.next_attempt_at if next_attempt_at is None else int(next_attempt_at)
            cursor = conn.execute(
                "UPDATE persephone_outbox SET state = ?, next_attempt_at = ?, last_error = ?, "
                "updated_at = ? WHERE message_id = ? AND state = ?",
                (new_state, due_at, last_error, timestamp, message_id, current.state),
            )
        else:
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
    "record_cursor",
    "record_inbox",
    "recover_abandoned_outbox",
    "transition_message",
]

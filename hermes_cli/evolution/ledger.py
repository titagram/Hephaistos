"""Private, append-only SQLite lifecycle ledger for local evolution."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Sequence

from hermes_constants import get_hermes_home
from hermes_state import apply_wal_with_fallback

from .contract import (
    bounded_reason,
    canonical_json_bytes,
    content_digest,
    require_digest,
)
from .state_machine import TransitionRequest, validate_transition


SCHEMA_VERSION = 1
_STATES = (
    "draft", "research_authorized", "blueprint_ready", "build_approved",
    "building", "quarantined", "canary_running", "promotion_ready", "active",
    "stable", "rejected", "research_expired", "build_failed", "canary_failed",
    "rolled_back", "retired",
)
_STATE_CHECK = ", ".join(repr(state) for state in _STATES)


class EvolutionLedgerError(RuntimeError):
    """A non-sensitive, fail-closed ledger initialization or write failure."""


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    attempt_id: str | None
    generation_id: str | None
    event_type: str
    prior_state: str | None
    next_state: str | None
    actor: str
    input_digests: tuple[str, ...]
    authorization_id: str | None
    reason_code: str
    reason_summary: str
    created_at: str


@dataclass(frozen=True)
class StoredEvent(LifecycleEvent):
    event_sequence: int
    previous_event_digest: str | None
    event_digest: str


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _check_owner_and_mode(path: Path, info: os.stat_result, *, directory: bool) -> None:
    if stat.S_ISLNK(info.st_mode):
        raise EvolutionLedgerError("unsafe_ledger_path")
    required_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not required_kind(info.st_mode):
        raise EvolutionLedgerError("unsafe_ledger_path")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise EvolutionLedgerError("unsafe_ledger_path")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise EvolutionLedgerError("unsafe_ledger_path")


def _secure_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EvolutionLedgerError("unsafe_ledger_path") from exc
    _check_owner_and_mode(path, info, directory=True)


def _secure_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EvolutionLedgerError("unsafe_ledger_path") from exc
    _check_owner_and_mode(path, info, directory=False)


def _prepare_path(path: Path) -> None:
    parent = path.parent
    if parent.exists() or parent.is_symlink():
        _secure_directory(parent)
    else:
        try:
            parent.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise EvolutionLedgerError("unsafe_ledger_path") from exc
        _secure_directory(parent)
    if path.exists() or path.is_symlink():
        _secure_file(path)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)
    except OSError as exc:
        raise EvolutionLedgerError("unsafe_ledger_path") from exc
    _secure_file(path)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_ref TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestions (
    suggestion_id TEXT PRIMARY KEY, attempt_id TEXT, canonical_digest TEXT CHECK(canonical_digest IS NULL OR length(canonical_digest) = 64), state TEXT CHECK(state IS NULL OR state IN ({_STATE_CHECK})), created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS suggestion_evidence (
    evidence_id TEXT PRIMARY KEY, suggestion_id TEXT NOT NULL, evidence_digest TEXT CHECK(length(evidence_digest) = 64), evidence_ref TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(suggestion_id) REFERENCES suggestions(suggestion_id)
);
CREATE TABLE IF NOT EXISTS blueprints (
    blueprint_id TEXT PRIMARY KEY, attempt_id TEXT, canonical_digest TEXT NOT NULL CHECK(length(canonical_digest) = 64), state TEXT CHECK(state IS NULL OR state IN ({_STATE_CHECK})), created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS authorization_requests (
    authorization_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, grant_kind TEXT NOT NULL CHECK(grant_kind IN ('research','build','promotion')), state TEXT NOT NULL CHECK(state IN ('requested','approved','denied','expired','consumed')), request_digest TEXT CHECK(request_digest IS NULL OR length(request_digest) = 64), created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS authorization_grants (
    authorization_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, grant_kind TEXT NOT NULL CHECK(grant_kind IN ('research','build','promotion')), scope_digest TEXT NOT NULL CHECK(length(scope_digest) = 64), expires_at TEXT, consumed_at TEXT, created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})), manifest_digest TEXT CHECK(manifest_digest IS NULL OR length(manifest_digest) = 64), created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS generations (
    generation_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, canonical_digest TEXT NOT NULL CHECK(length(canonical_digest) = 64), state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})), created_at TEXT NOT NULL,
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS generation_components (
    component_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, component_kind TEXT NOT NULL, canonical_digest TEXT NOT NULL CHECK(length(canonical_digest) = 64), relative_path TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
);
CREATE TABLE IF NOT EXISTS canary_runs (
    canary_run_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})), evidence_digest TEXT CHECK(evidence_digest IS NULL OR length(evidence_digest) = 64), created_at TEXT NOT NULL,
    FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
);
CREATE TABLE IF NOT EXISTS promotion_reports (
    promotion_report_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, report_digest TEXT NOT NULL CHECK(length(report_digest) = 64), state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})), created_at TEXT NOT NULL,
    FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
);
CREATE TABLE IF NOT EXISTS lifecycle_events (
    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT,
    generation_id TEXT,
    event_type TEXT NOT NULL,
    prior_state TEXT CHECK(prior_state IS NULL OR prior_state IN ({_STATE_CHECK})),
    next_state TEXT CHECK(next_state IS NULL OR next_state IN ({_STATE_CHECK})),
    actor TEXT NOT NULL,
    input_digests_json TEXT NOT NULL,
    authorization_id TEXT,
    reason_code TEXT NOT NULL,
    reason_summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    previous_event_digest TEXT CHECK(previous_event_digest IS NULL OR length(previous_event_digest) = 64),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id),
    FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
);
CREATE TRIGGER IF NOT EXISTS lifecycle_events_no_update BEFORE UPDATE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'immutable_lifecycle_event'); END;
CREATE TRIGGER IF NOT EXISTS lifecycle_events_no_delete BEFORE DELETE ON lifecycle_events BEGIN SELECT RAISE(ABORT, 'immutable_lifecycle_event'); END;
CREATE TRIGGER IF NOT EXISTS authorization_grants_no_update BEFORE UPDATE ON authorization_grants BEGIN SELECT RAISE(ABORT, 'immutable_authorization_grant'); END;
CREATE TRIGGER IF NOT EXISTS authorization_grants_no_delete BEFORE DELETE ON authorization_grants BEGIN SELECT RAISE(ABORT, 'immutable_authorization_grant'); END;
"""

_TABLES = frozenset(
    {
        "schema_version", "attempts", "suggestions", "suggestion_evidence",
        "blueprints", "authorization_requests", "authorization_grants",
        "candidates", "generations", "generation_components", "canary_runs",
        "promotion_reports", "lifecycle_events",
    }
)
_IMMUTABILITY_TRIGGERS = frozenset(
    {
        "lifecycle_events_no_update", "lifecycle_events_no_delete",
        "authorization_grants_no_update", "authorization_grants_no_delete",
    }
)


class EvolutionLedger:
    def __init__(self, path: Path | None = None):
        self.path = path or get_hermes_home() / "evolution" / "evolution.db"
        self.path = Path(self.path)
        _prepare_path(self.path)
        try:
            self.connection = sqlite3.connect(
                str(self.path), isolation_level=None, check_same_thread=False
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.journal_mode = apply_wal_with_fallback(
                self.connection, db_label="evolution.db"
            )
            self._initialize_schema()
        except EvolutionLedgerError:
            raise
        except sqlite3.DatabaseError as exc:
            raise EvolutionLedgerError("invalid_ledger_database") from exc

    def _initialize_schema(self) -> None:
        row = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        if row is not None:
            version = self.connection.execute("SELECT version FROM schema_version").fetchone()
            if version is None:
                raise EvolutionLedgerError("invalid_ledger_database")
            if version[0] != SCHEMA_VERSION:
                raise EvolutionLedgerError("unsupported_schema_version")
            tables = {
                item[0]
                for item in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not _TABLES <= tables:
                raise EvolutionLedgerError("invalid_ledger_database")
            triggers = {
                item[0]
                for item in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            if not _IMMUTABILITY_TRIGGERS <= triggers:
                raise EvolutionLedgerError("invalid_ledger_database")
        self.connection.executescript(_SCHEMA)
        row = self.connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self.connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        elif row[0] != SCHEMA_VERSION:
            raise EvolutionLedgerError("unsupported_schema_version")

    @property
    def schema_version(self) -> int:
        return int(self.connection.execute("SELECT version FROM schema_version").fetchone()[0])

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def create_attempt(self, source_kind: str, source_ref: str) -> str:
        if not isinstance(source_kind, str) or not source_kind or not isinstance(source_ref, str) or not source_ref:
            raise EvolutionLedgerError("invalid_attempt_source")
        attempt_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO attempts(attempt_id, source_kind, source_ref, state, created_at) VALUES (?, ?, ?, 'draft', ?)",
                (attempt_id, source_kind, source_ref, _now()),
            )
        return attempt_id

    def _payload(self, event: LifecycleEvent, previous: str | None) -> dict[str, object]:
        if not isinstance(event.input_digests, tuple):
            raise EvolutionLedgerError("invalid_event_digests")
        for digest in event.input_digests:
            require_digest(digest)
        for value in (event.event_id, event.event_type, event.actor, event.reason_code, event.reason_summary, event.created_at):
            if not isinstance(value, str) or not value or len(value) > 512:
                raise EvolutionLedgerError("invalid_event")
        if bounded_reason(event.reason_summary) != event.reason_summary:
            raise EvolutionLedgerError("invalid_event")
        return {
            "event_id": event.event_id, "attempt_id": event.attempt_id,
            "generation_id": event.generation_id, "event_type": event.event_type,
            "prior_state": event.prior_state, "next_state": event.next_state,
            "actor": event.actor, "input_digests": event.input_digests,
            "authorization_id": event.authorization_id, "reason_code": event.reason_code,
            "reason_summary": event.reason_summary, "created_at": event.created_at,
            "previous_event_digest": previous,
        }

    def append_event(self, event: LifecycleEvent) -> StoredEvent:
        with self.transaction() as connection:
            return self._append(connection, event)

    def _append(self, connection: sqlite3.Connection, event: LifecycleEvent) -> StoredEvent:
        previous_row = connection.execute(
            "SELECT event_digest FROM lifecycle_events ORDER BY event_sequence DESC LIMIT 1"
        ).fetchone()
        previous = None if previous_row is None else str(previous_row[0])
        payload = self._payload(event, previous)
        digest = content_digest(payload, domain="hermes-evolution-lifecycle-event-v1")
        cursor = connection.execute(
            "INSERT INTO lifecycle_events(event_id, attempt_id, generation_id, event_type, prior_state, next_state, actor, input_digests_json, authorization_id, reason_code, reason_summary, created_at, previous_event_digest, event_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.event_id, event.attempt_id, event.generation_id, event.event_type, event.prior_state, event.next_state, event.actor, canonical_json_bytes(list(event.input_digests)).decode(), event.authorization_id, event.reason_code, event.reason_summary, event.created_at, previous, digest),
        )
        return StoredEvent(**event.__dict__, event_sequence=int(cursor.lastrowid), previous_event_digest=previous, event_digest=digest)

    def transition(self, request: TransitionRequest) -> StoredEvent:
        validate_transition(request)
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE attempts SET state = ? WHERE attempt_id = ? AND state = ?",
                (request.next_state, request.attempt_id, request.prior_state),
            ).rowcount
            if updated != 1:
                raise EvolutionLedgerError("attempt_state_conflict")
            return self._append(connection, LifecycleEvent(
                event_id=str(uuid.uuid4()), attempt_id=request.attempt_id, generation_id=None,
                event_type="state_transition", prior_state=request.prior_state,
                next_state=request.next_state, actor=request.actor,
                input_digests=request.input_digests, authorization_id=request.authorization_id,
                reason_code="transition", reason_summary=request.reason, created_at=_now(),
            ))

    def history(self, *, limit: int = 100, after: int | None = None) -> list[StoredEvent]:
        if not isinstance(limit, int) or limit < 1 or limit > 1000:
            raise EvolutionLedgerError("invalid_history_limit")
        rows = self.connection.execute(
            "SELECT * FROM lifecycle_events WHERE event_sequence > COALESCE(?, 0) ORDER BY event_sequence LIMIT ?",
            (after, limit),
        ).fetchall()
        return [self._stored(row) for row in rows]

    def _stored(self, row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            event_id=row["event_id"], attempt_id=row["attempt_id"], generation_id=row["generation_id"],
            event_type=row["event_type"], prior_state=row["prior_state"], next_state=row["next_state"],
            actor=row["actor"], input_digests=tuple(json.loads(row["input_digests_json"])),
            authorization_id=row["authorization_id"], reason_code=row["reason_code"],
            reason_summary=row["reason_summary"], created_at=row["created_at"],
            event_sequence=row["event_sequence"], previous_event_digest=row["previous_event_digest"], event_digest=row["event_digest"],
        )

    def verify_chain(self, events: Sequence[StoredEvent] | None = None) -> list[str]:
        records = list(events) if events is not None else self.history(limit=1000)
        errors: list[str] = []
        previous: str | None = None
        for record in records:
            payload = self._payload(record, previous)
            expected = content_digest(payload, domain="hermes-evolution-lifecycle-event-v1")
            if record.previous_event_digest != previous or record.event_digest != expected:
                errors.append(str(record.event_sequence))
            previous = record.event_digest
        return errors

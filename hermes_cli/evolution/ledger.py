"""Private, append-only SQLite lifecycle ledger for local evolution."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from hermes_constants import get_hermes_home
from hermes_state import apply_wal_with_fallback

from .contract import canonical_json_bytes, content_digest, require_digest
from .state_machine import TransitionRequest, validate_transition


SCHEMA_VERSION = 2
_MAX_DIGESTS = 64
_VERIFY_BATCH_SIZE = 256
_PATH_SCHEME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z",
    re.ASCII,
)
_STATES = (
    "draft",
    "research_authorized",
    "blueprint_ready",
    "build_approved",
    "building",
    "quarantined",
    "canary_running",
    "promotion_ready",
    "active",
    "stable",
    "rejected",
    "research_expired",
    "build_failed",
    "canary_failed",
    "rolled_back",
    "retired",
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


@dataclass
class _PathGuard:
    """Retained path identity used for best-effort swap detection."""

    directory_fd: int | None
    file_fd: int
    directory_info: os.stat_result
    created: bool

    def close(self) -> None:
        os.close(self.file_fd)
        if self.directory_fd is not None:
            os.close(self.directory_fd)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_timestamp(value: object) -> str:
    if (
        not isinstance(value, str)
        or _TIMESTAMP_PATTERN.fullmatch(value) is None
    ):
        raise EvolutionLedgerError("invalid_event_timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, OverflowError):
        raise EvolutionLedgerError("invalid_event_timestamp") from None
    return value


def _id_check(column: str, limit: int = 256) -> str:
    return f"CHECK(length({column}) BETWEEN 1 AND {limit})"


_SCHEMA_V1_STATEMENTS = (
    """
    CREATE TABLE schema_version (
        singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1),
        version INTEGER NOT NULL CHECK(version = 1)
    ) WITHOUT ROWID
    """,
    f"""
    CREATE TABLE attempts (
        attempt_id TEXT NOT NULL PRIMARY KEY {_id_check("attempt_id")},
        source_kind TEXT NOT NULL {_id_check("source_kind", 64)},
        source_ref TEXT NOT NULL {_id_check("source_ref", 256)},
        state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})),
        created_at TEXT NOT NULL {_id_check("created_at", 64)}
    )
    """,
    f"""
    CREATE TABLE suggestions (
        suggestion_id TEXT NOT NULL PRIMARY KEY {_id_check("suggestion_id")},
        attempt_id TEXT {_id_check("attempt_id")},
        canonical_digest TEXT
            CHECK(canonical_digest IS NULL OR length(canonical_digest) = 64),
        state TEXT CHECK(state IS NULL OR state IN ({_STATE_CHECK})),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE suggestion_evidence (
        evidence_id TEXT NOT NULL PRIMARY KEY {_id_check("evidence_id")},
        suggestion_id TEXT NOT NULL {_id_check("suggestion_id")},
        evidence_digest TEXT NOT NULL CHECK(length(evidence_digest) = 64),
        evidence_ref TEXT NOT NULL {_id_check("evidence_ref", 256)},
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(suggestion_id) REFERENCES suggestions(suggestion_id)
    )
    """,
    f"""
    CREATE TABLE blueprints (
        blueprint_id TEXT NOT NULL PRIMARY KEY {_id_check("blueprint_id")},
        attempt_id TEXT {_id_check("attempt_id")},
        canonical_digest TEXT NOT NULL CHECK(length(canonical_digest) = 64),
        state TEXT CHECK(state IS NULL OR state IN ({_STATE_CHECK})),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE authorization_requests (
        authorization_id TEXT NOT NULL PRIMARY KEY {_id_check("authorization_id")},
        attempt_id TEXT NOT NULL {_id_check("attempt_id")},
        grant_kind TEXT NOT NULL
            CHECK(grant_kind IN ('research', 'build', 'promotion')),
        state TEXT NOT NULL
            CHECK(state IN ('requested', 'approved', 'denied', 'expired', 'consumed')),
        request_digest TEXT
            CHECK(request_digest IS NULL OR length(request_digest) = 64),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE authorization_grants (
        authorization_id TEXT NOT NULL PRIMARY KEY {_id_check("authorization_id")},
        attempt_id TEXT NOT NULL {_id_check("attempt_id")},
        grant_kind TEXT NOT NULL
            CHECK(grant_kind IN ('research', 'build', 'promotion')),
        scope_digest TEXT NOT NULL CHECK(length(scope_digest) = 64),
        expires_at TEXT CHECK(expires_at IS NULL OR length(expires_at) BETWEEN 1 AND 64),
        consumed_at TEXT CHECK(consumed_at IS NULL OR length(consumed_at) BETWEEN 1 AND 64),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE candidates (
        candidate_id TEXT NOT NULL PRIMARY KEY {_id_check("candidate_id")},
        attempt_id TEXT NOT NULL {_id_check("attempt_id")},
        state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})),
        manifest_digest TEXT
            CHECK(manifest_digest IS NULL OR length(manifest_digest) = 64),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE generations (
        generation_id TEXT NOT NULL PRIMARY KEY CHECK(length(generation_id) = 64),
        attempt_id TEXT NOT NULL {_id_check("attempt_id")},
        canonical_digest TEXT NOT NULL CHECK(length(canonical_digest) = 64),
        state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE generation_components (
        component_id TEXT NOT NULL PRIMARY KEY {_id_check("component_id")},
        generation_id TEXT NOT NULL CHECK(length(generation_id) = 64),
        component_kind TEXT NOT NULL {_id_check("component_kind", 128)},
        canonical_digest TEXT NOT NULL CHECK(length(canonical_digest) = 64),
        relative_path TEXT NOT NULL {_id_check("relative_path", 512)},
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
    )
    """,
    f"""
    CREATE TABLE canary_runs (
        canary_run_id TEXT NOT NULL PRIMARY KEY {_id_check("canary_run_id")},
        generation_id TEXT NOT NULL CHECK(length(generation_id) = 64),
        state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})),
        evidence_digest TEXT
            CHECK(evidence_digest IS NULL OR length(evidence_digest) = 64),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
    )
    """,
    f"""
    CREATE TABLE promotion_reports (
        promotion_report_id TEXT NOT NULL PRIMARY KEY {_id_check("promotion_report_id")},
        generation_id TEXT NOT NULL CHECK(length(generation_id) = 64),
        report_digest TEXT NOT NULL CHECK(length(report_digest) = 64),
        state TEXT NOT NULL CHECK(state IN ({_STATE_CHECK})),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
    )
    """,
    f"""
    CREATE TABLE lifecycle_events (
        event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE {_id_check("event_id")},
        attempt_id TEXT {_id_check("attempt_id")},
        generation_id TEXT
            CHECK(generation_id IS NULL OR length(generation_id) = 64),
        event_type TEXT NOT NULL {_id_check("event_type", 128)},
        prior_state TEXT CHECK(prior_state IS NULL OR prior_state IN ({_STATE_CHECK})),
        next_state TEXT CHECK(next_state IS NULL OR next_state IN ({_STATE_CHECK})),
        actor TEXT NOT NULL {_id_check("actor", 128)},
        input_digests_json TEXT NOT NULL
            CHECK(length(input_digests_json) BETWEEN 2 AND 4289),
        authorization_id TEXT {_id_check("authorization_id")},
        reason_code TEXT NOT NULL {_id_check("reason_code", 128)},
        reason_summary TEXT NOT NULL {_id_check("reason_summary", 512)},
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        previous_event_digest TEXT
            CHECK(previous_event_digest IS NULL OR length(previous_event_digest) = 64),
        event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id),
        FOREIGN KEY(generation_id) REFERENCES generations(generation_id)
    )
    """,
    """
    CREATE INDEX suggestions_attempt_idx ON suggestions(attempt_id)
    """,
    """
    CREATE INDEX suggestion_evidence_suggestion_idx
    ON suggestion_evidence(suggestion_id)
    """,
    """
    CREATE INDEX blueprints_attempt_idx ON blueprints(attempt_id)
    """,
    """
    CREATE INDEX authorization_requests_attempt_idx
    ON authorization_requests(attempt_id)
    """,
    """
    CREATE INDEX authorization_grants_attempt_idx
    ON authorization_grants(attempt_id)
    """,
    """
    CREATE INDEX candidates_attempt_idx ON candidates(attempt_id)
    """,
    """
    CREATE INDEX generations_attempt_idx ON generations(attempt_id)
    """,
    """
    CREATE INDEX generation_components_generation_idx
    ON generation_components(generation_id)
    """,
    """
    CREATE INDEX canary_runs_generation_idx ON canary_runs(generation_id)
    """,
    """
    CREATE INDEX promotion_reports_generation_idx
    ON promotion_reports(generation_id)
    """,
    """
    CREATE INDEX lifecycle_events_attempt_idx ON lifecycle_events(attempt_id)
    """,
    """
    CREATE INDEX lifecycle_events_generation_idx ON lifecycle_events(generation_id)
    """,
    """
    CREATE TRIGGER lifecycle_events_no_update
    BEFORE UPDATE ON lifecycle_events
    BEGIN
        SELECT RAISE(ABORT, 'immutable_lifecycle_event');
    END
    """,
    """
    CREATE TRIGGER lifecycle_events_no_delete
    BEFORE DELETE ON lifecycle_events
    BEGIN
        SELECT RAISE(ABORT, 'immutable_lifecycle_event');
    END
    """,
    """
    CREATE TRIGGER authorization_grants_no_update
    BEFORE UPDATE ON authorization_grants
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_grant');
    END
    """,
    """
    CREATE TRIGGER authorization_grants_no_delete
    BEFORE DELETE ON authorization_grants
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_grant');
    END
    """,
)

_TABLES_V1 = (
    "schema_version",
    "attempts",
    "suggestions",
    "suggestion_evidence",
    "blueprints",
    "authorization_requests",
    "authorization_grants",
    "candidates",
    "generations",
    "generation_components",
    "canary_runs",
    "promotion_reports",
    "lifecycle_events",
)

_V2_AUTH_SCHEMA_STATEMENTS = (
    f"""
    CREATE TABLE authorization_requests (
        request_id TEXT NOT NULL PRIMARY KEY {_id_check("request_id")},
        attempt_id TEXT NOT NULL {_id_check("attempt_id")},
        grant_kind TEXT NOT NULL
            CHECK(grant_kind IN ('research', 'build', 'promotion')),
        subject_digest TEXT NOT NULL CHECK(length(subject_digest) = 64),
        request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
        scope_json TEXT NOT NULL CHECK(length(scope_json) BETWEEN 2 AND 16384),
        ttl_seconds INTEGER NOT NULL
            CHECK(typeof(ttl_seconds) = 'integer' AND ttl_seconds BETWEEN 1 AND 86400),
        expires_at TEXT NOT NULL {_id_check("expires_at", 64)},
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE authorization_decisions (
        decision_id TEXT NOT NULL PRIMARY KEY {_id_check("decision_id")},
        request_id TEXT NOT NULL UNIQUE {_id_check("request_id")},
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'denied')),
        decided_by TEXT NOT NULL {_id_check("decided_by", 128)},
        confirmation_digest TEXT
            CHECK(confirmation_digest IS NULL OR length(confirmation_digest) = 64),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        CHECK(
            (decision = 'approved' AND confirmation_digest IS NOT NULL)
            OR (decision = 'denied' AND confirmation_digest IS NULL)
        ),
        FOREIGN KEY(request_id) REFERENCES authorization_requests(request_id)
    )
    """,
    f"""
    CREATE TABLE authorization_grants (
        grant_id TEXT NOT NULL PRIMARY KEY {_id_check("grant_id")},
        authorization_id TEXT NOT NULL UNIQUE {_id_check("authorization_id")}
            CHECK(authorization_id = grant_id),
        request_id TEXT NOT NULL UNIQUE {_id_check("request_id")},
        attempt_id TEXT NOT NULL {_id_check("attempt_id")},
        grant_kind TEXT NOT NULL
            CHECK(grant_kind IN ('research', 'build', 'promotion')),
        subject_digest TEXT NOT NULL CHECK(length(subject_digest) = 64),
        scope_json TEXT NOT NULL CHECK(length(scope_json) BETWEEN 2 AND 16384),
        expires_at TEXT NOT NULL {_id_check("expires_at", 64)},
        approved_by TEXT NOT NULL {_id_check("approved_by", 128)},
        confirmation_digest TEXT NOT NULL CHECK(length(confirmation_digest) = 64),
        consumed_at TEXT CHECK(consumed_at IS NULL),
        created_at TEXT NOT NULL {_id_check("created_at", 64)},
        FOREIGN KEY(request_id) REFERENCES authorization_requests(request_id),
        FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
    )
    """,
    f"""
    CREATE TABLE authorization_consumptions (
        consumption_id TEXT NOT NULL PRIMARY KEY {_id_check("consumption_id")},
        grant_id TEXT NOT NULL UNIQUE {_id_check("grant_id")},
        consumed_at TEXT NOT NULL {_id_check("consumed_at", 64)},
        FOREIGN KEY(grant_id) REFERENCES authorization_grants(grant_id)
    )
    """,
    """
    CREATE INDEX authorization_requests_attempt_idx
    ON authorization_requests(attempt_id)
    """,
    """
    CREATE INDEX authorization_grants_attempt_idx
    ON authorization_grants(attempt_id)
    """,
    """
    CREATE TRIGGER authorization_requests_no_update
    BEFORE UPDATE ON authorization_requests
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_request');
    END
    """,
    """
    CREATE TRIGGER authorization_requests_no_delete
    BEFORE DELETE ON authorization_requests
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_request');
    END
    """,
    """
    CREATE TRIGGER authorization_decisions_no_update
    BEFORE UPDATE ON authorization_decisions
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_decision');
    END
    """,
    """
    CREATE TRIGGER authorization_decisions_no_delete
    BEFORE DELETE ON authorization_decisions
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_decision');
    END
    """,
    """
    CREATE TRIGGER authorization_decisions_request_coherence
    BEFORE INSERT ON authorization_decisions
    WHEN NEW.decision = 'approved'
      AND NOT EXISTS (
          SELECT 1
          FROM authorization_requests AS request
          WHERE request.request_id = NEW.request_id
            AND request.request_digest = NEW.confirmation_digest
      )
    BEGIN
        SELECT RAISE(ABORT, 'authorization_decision_request_mismatch');
    END
    """,
    """
    CREATE TRIGGER authorization_grants_insert_coherence
    BEFORE INSERT ON authorization_grants
    WHEN NOT EXISTS (
        SELECT 1
        FROM authorization_requests AS request
        WHERE request.request_id = NEW.request_id
          AND request.attempt_id = NEW.attempt_id
          AND request.grant_kind = NEW.grant_kind
          AND request.subject_digest = NEW.subject_digest
          AND request.scope_json = NEW.scope_json
          AND request.expires_at = NEW.expires_at
          AND request.request_digest = NEW.confirmation_digest
    )
    OR NOT EXISTS (
        SELECT 1
        FROM authorization_decisions AS decision
        WHERE decision.request_id = NEW.request_id
          AND decision.decision = 'approved'
          AND decision.decided_by = NEW.approved_by
          AND decision.confirmation_digest = NEW.confirmation_digest
    )
    BEGIN
        SELECT RAISE(ABORT, 'authorization_grant_coherence');
    END
    """,
    """
    CREATE TRIGGER authorization_grants_no_update
    BEFORE UPDATE ON authorization_grants
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_grant');
    END
    """,
    """
    CREATE TRIGGER authorization_grants_no_delete
    BEFORE DELETE ON authorization_grants
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_grant');
    END
    """,
    """
    CREATE TRIGGER authorization_consumptions_no_update
    BEFORE UPDATE ON authorization_consumptions
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_consumption');
    END
    """,
    """
    CREATE TRIGGER authorization_consumptions_no_delete
    BEFORE DELETE ON authorization_consumptions
    BEGIN
        SELECT RAISE(ABORT, 'immutable_authorization_consumption');
    END
    """,
)

_V1_AUTH_OBJECT_PREFIXES = (
    "CREATE TABLE authorization_requests ",
    "CREATE TABLE authorization_grants ",
    "CREATE INDEX authorization_requests_attempt_idx ",
    "CREATE INDEX authorization_grants_attempt_idx ",
    "CREATE TRIGGER authorization_grants_no_update ",
    "CREATE TRIGGER authorization_grants_no_delete ",
)


def _statement_starts_with(statement: str, prefixes: tuple[str, ...]) -> bool:
    normalized = " ".join(statement.split())
    return any(normalized.startswith(prefix) for prefix in prefixes)


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE schema_version (
        singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1),
        version INTEGER NOT NULL CHECK(version = 2)
    ) WITHOUT ROWID
    """,
    *(
        statement
        for statement in _SCHEMA_V1_STATEMENTS
        if not _statement_starts_with(
            statement,
            ("CREATE TABLE schema_version ", *_V1_AUTH_OBJECT_PREFIXES),
        )
    ),
    *_V2_AUTH_SCHEMA_STATEMENTS,
)

_TABLES = (
    "schema_version",
    "attempts",
    "suggestions",
    "suggestion_evidence",
    "blueprints",
    "authorization_requests",
    "authorization_decisions",
    "authorization_grants",
    "authorization_consumptions",
    "candidates",
    "generations",
    "generation_components",
    "canary_runs",
    "promotion_reports",
    "lifecycle_events",
)


def _execute_schema_statement(
    connection: sqlite3.Connection, statement: str
) -> sqlite3.Cursor:
    """Execute one schema statement without sqlite3's implicit script commit."""

    return connection.execute(statement)


def _execute_migration_statement(
    connection: sqlite3.Connection, statement: str
) -> sqlite3.Cursor:
    """Execute one migration statement inside the caller's transaction."""

    return connection.execute(statement)


def _check_owner_and_mode(info: os.stat_result, *, directory: bool) -> None:
    required_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not required_kind(info.st_mode):
        raise EvolutionLedgerError("unsafe_ledger_path")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise EvolutionLedgerError("unsafe_ledger_path")
    required_mode = 0o700 if directory else 0o600
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != required_mode:
        raise EvolutionLedgerError("unsafe_ledger_path")


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _secure_lstat(path: Path, *, directory: bool) -> os.stat_result:
    try:
        info = path.lstat()
    except (OSError, TypeError, NotImplementedError) as exc:
        raise EvolutionLedgerError("unsafe_ledger_path") from exc
    _check_owner_and_mode(info, directory=directory)
    return info


def _open_protected_path(path: Path) -> _PathGuard:
    """Retain available handles while preserving a portable lstat fallback."""

    parent = path.parent
    if not path.name:
        raise EvolutionLedgerError("unsafe_ledger_path")
    if not parent.exists() and not parent.is_symlink():
        try:
            parent.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise EvolutionLedgerError("unsafe_ledger_path") from exc

    directory_info = _secure_lstat(parent, directory=True)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_fd: int | None = None
    try:
        directory_fd = os.open(parent, directory_flags)
    except (TypeError, NotImplementedError):
        directory_fd = None
    except OSError as exc:
        raise EvolutionLedgerError("unsafe_ledger_path") from exc
    if directory_fd is not None:
        try:
            opened_directory_info = os.fstat(directory_fd)
            _check_owner_and_mode(opened_directory_info, directory=True)
        except (OSError, TypeError, NotImplementedError) as exc:
            os.close(directory_fd)
            raise EvolutionLedgerError("unsafe_ledger_path") from exc
        if not _same_file(directory_info, opened_directory_info):
            os.close(directory_fd)
            raise EvolutionLedgerError("unsafe_ledger_path")
        directory_info = opened_directory_info

    def open_file(flags: int, mode: int = 0o777) -> int:
        nonlocal directory_fd
        if directory_fd is not None:
            try:
                return os.open(
                    path.name, flags, mode, dir_fd=directory_fd
                )
            except (TypeError, NotImplementedError):
                os.close(directory_fd)
                directory_fd = None
                current_directory = _secure_lstat(parent, directory=True)
                if not _same_file(directory_info, current_directory):
                    raise EvolutionLedgerError("unsafe_ledger_path")
        return os.open(path, flags, mode)

    try:
        file_flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            initial_file_info = path.lstat()
        except FileNotFoundError:
            initial_file_info = None
        except (OSError, TypeError, NotImplementedError) as exc:
            raise EvolutionLedgerError("unsafe_ledger_path") from exc
        if initial_file_info is None:
            create_flags = file_flags | os.O_CREAT | os.O_EXCL
            try:
                file_fd = open_file(create_flags, 0o600)
                created = True
            except (OSError, TypeError, NotImplementedError) as exc:
                raise EvolutionLedgerError("unsafe_ledger_path") from exc
        else:
            _check_owner_and_mode(initial_file_info, directory=False)
            try:
                file_fd = open_file(file_flags)
                created = False
            except (OSError, TypeError, NotImplementedError) as exc:
                raise EvolutionLedgerError("unsafe_ledger_path") from exc
        try:
            file_info = os.fstat(file_fd)
            _check_owner_and_mode(file_info, directory=False)
            linked_info = _secure_lstat(path, directory=False)
            if not _same_file(file_info, linked_info):
                raise EvolutionLedgerError("unsafe_ledger_path")
            current_directory = _secure_lstat(parent, directory=True)
            if not _same_file(directory_info, current_directory):
                raise EvolutionLedgerError("unsafe_ledger_path")
            return _PathGuard(
                directory_fd=directory_fd,
                file_fd=file_fd,
                directory_info=directory_info,
                created=created,
            )
        except BaseException:
            os.close(file_fd)
            raise
    except BaseException:
        if directory_fd is not None:
            os.close(directory_fd)
        raise


def _verify_retained_identity(
    path: Path,
    guard: _PathGuard,
    connection: sqlite3.Connection | None = None,
    connection_fds: set[int] | None = None,
) -> None:
    """Detect path swaps; FD correlation is defense in depth, not proof."""

    try:
        retained_directory = (
            os.fstat(guard.directory_fd)
            if guard.directory_fd is not None
            else guard.directory_info
        )
        retained_file = os.fstat(guard.file_fd)
        current_directory = _secure_lstat(path.parent, directory=True)
        current_file = _secure_lstat(path, directory=False)
    except (OSError, TypeError, NotImplementedError) as exc:
        raise EvolutionLedgerError("unsafe_ledger_path") from exc
    if not _same_file(retained_directory, current_directory) or not _same_file(
        retained_file, current_file
    ):
        raise EvolutionLedgerError("unsafe_ledger_path")
    if connection is not None:
        row = connection.execute("PRAGMA database_list").fetchone()
        if row is None or not row[2]:
            raise EvolutionLedgerError("unsafe_ledger_path")
        try:
            connected_file = _secure_lstat(
                Path(row[2]), directory=False
            )
        except (OSError, TypeError, NotImplementedError) as exc:
            raise EvolutionLedgerError("unsafe_ledger_path") from exc
        if not _same_file(retained_file, connected_file):
            raise EvolutionLedgerError("unsafe_ledger_path")
        if connection_fds is not None:
            for descriptor in connection_fds:
                try:
                    connected_info = os.fstat(descriptor)
                except (OSError, TypeError, NotImplementedError):
                    continue
                if _same_file(retained_file, connected_info):
                    break
            else:
                raise EvolutionLedgerError("unsafe_ledger_path")


def _database_uri(path: Path, *, read_only: bool) -> str:
    absolute = path.absolute()
    mode = "ro&immutable=1" if read_only else "rw"
    return f"{absolute.as_uri()}?mode={mode}"


def _open_file_descriptors() -> set[int] | None:
    if os.name != "posix":
        return None
    descriptor_root = next(
        (
            candidate
            for candidate in ("/proc/self/fd", "/dev/fd")
            if os.path.isdir(candidate)
        ),
        None,
    )
    if descriptor_root is None:
        return None
    try:
        candidates = {
            int(name)
            for name in os.listdir(descriptor_root)
            if name.isdigit()
        }
    except (OSError, TypeError, NotImplementedError):
        return None
    opened: set[int] = set()
    for descriptor in candidates:
        try:
            os.fstat(descriptor)
        except (OSError, TypeError, NotImplementedError):
            continue
        opened.add(descriptor)
    return opened


def _connect(
    path: Path, *, read_only: bool
) -> tuple[sqlite3.Connection, set[int] | None]:
    before = _open_file_descriptors()
    connection = sqlite3.connect(
        _database_uri(path, read_only=read_only),
        uri=True,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    after = _open_file_descriptors()
    opened = None if before is None or after is None else after - before
    return connection, opened


def _object_snapshot(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        )
    ]


def _table_metadata(
    connection: sqlite3.Connection,
    tables: tuple[str, ...] = _TABLES,
) -> dict[str, tuple[list[tuple[object, ...]], ...]]:
    metadata: dict[str, tuple[list[tuple[object, ...]], ...]] = {}
    for table in tables:
        quoted = table.replace('"', '""')
        metadata[table] = (
            [
                tuple(row)
                for row in connection.execute(f'PRAGMA table_info("{quoted}")')
            ],
            [
                tuple(row)
                for row in connection.execute(
                    f'PRAGMA foreign_key_list("{quoted}")'
                )
            ],
            [
                tuple(row)[1:]
                for row in connection.execute(f'PRAGMA index_list("{quoted}")')
            ],
        )
    return metadata


def _expected_schema(
    statements: tuple[str, ...] = _SCHEMA_STATEMENTS,
    tables: tuple[str, ...] = _TABLES,
) -> tuple[
    list[tuple[object, ...]],
    dict[str, tuple[list[tuple[object, ...]], ...]],
]:
    expected = sqlite3.connect(":memory:", isolation_level=None)
    try:
        expected.execute("PRAGMA foreign_keys=ON")
        for statement in statements:
            expected.execute(statement)
        return _object_snapshot(expected), _table_metadata(expected, tables)
    finally:
        expected.close()


def _declared_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_version'
        """
    ).fetchone()
    if table is None:
        raise EvolutionLedgerError("invalid_ledger_database")
    try:
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
    except sqlite3.DatabaseError as exc:
        raise EvolutionLedgerError("invalid_ledger_database") from exc
    if (
        len(rows) != 1
        or isinstance(rows[0][0], bool)
        or not isinstance(rows[0][0], int)
    ):
        raise EvolutionLedgerError("invalid_ledger_database")
    return int(rows[0][0])


def _validate_schema_version(
    connection: sqlite3.Connection,
    *,
    version: int,
    statements: tuple[str, ...],
    tables: tuple[str, ...],
) -> None:
    if _declared_version(connection) != version:
        raise EvolutionLedgerError("invalid_ledger_database")
    expected_objects, expected_metadata = _expected_schema(statements, tables)
    try:
        actual_objects = _object_snapshot(connection)
        actual_metadata = _table_metadata(connection, tables)
        version_rows = [
            tuple(row)
            for row in connection.execute(
                "SELECT singleton, version FROM schema_version"
            )
        ]
    except sqlite3.DatabaseError as exc:
        raise EvolutionLedgerError("invalid_ledger_database") from exc
    if (
        actual_objects != expected_objects
        or actual_metadata != expected_metadata
        or version_rows != [(1, version)]
    ):
        raise EvolutionLedgerError("invalid_ledger_database")


def _validate_schema(connection: sqlite3.Connection) -> None:
    version = _declared_version(connection)
    if version != SCHEMA_VERSION:
        raise EvolutionLedgerError("unsupported_schema_version")
    _validate_schema_version(
        connection,
        version=SCHEMA_VERSION,
        statements=_SCHEMA_STATEMENTS,
        tables=_TABLES,
    )


def _validate_preflight_schema(connection: sqlite3.Connection) -> int:
    version = _declared_version(connection)
    if version == 1:
        _validate_schema_version(
            connection,
            version=1,
            statements=_SCHEMA_V1_STATEMENTS,
            tables=_TABLES_V1,
        )
    elif version == SCHEMA_VERSION:
        _validate_schema(connection)
    elif version > SCHEMA_VERSION:
        raise EvolutionLedgerError("unsupported_schema_version")
    else:
        raise EvolutionLedgerError("invalid_ledger_database")
    return version


def _preflight_existing(
    path: Path, guard: _PathGuard
) -> int:
    """Validate a non-empty database without locks, DDL, WAL, or sidecars."""

    connection: sqlite3.Connection | None = None
    try:
        connection, connection_fds = _connect(path, read_only=True)
        _verify_retained_identity(
            path,
            guard,
            connection,
            connection_fds,
        )
        version = _validate_preflight_schema(connection)
        _verify_retained_identity(
            path,
            guard,
            connection,
            connection_fds,
        )
        return version
    except EvolutionLedgerError:
        raise
    except sqlite3.DatabaseError as exc:
        raise EvolutionLedgerError("invalid_ledger_database") from exc
    finally:
        if connection is not None:
            connection.close()


def _normalize_text(
    value: object,
    *,
    limit: int,
    code: str,
    collapse_whitespace: bool = False,
) -> str:
    if not isinstance(value, str):
        raise EvolutionLedgerError(code)
    normalized = unicodedata.normalize("NFC", value)
    normalized = (
        " ".join(normalized.split())
        if collapse_whitespace
        else normalized.strip()
    )
    if (
        not normalized
        or len(normalized) > limit
        or any(not character.isprintable() for character in normalized)
    ):
        raise EvolutionLedgerError(code)
    return normalized


def _optional_identity(value: object, *, digest: bool = False) -> str | None:
    if value is None:
        return None
    normalized = _normalize_text(value, limit=256, code="invalid_event")
    if digest:
        try:
            require_digest(normalized)
        except ValueError as exc:
            raise EvolutionLedgerError("invalid_event") from exc
    return normalized


class EvolutionLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(
            path or get_hermes_home() / "evolution" / "evolution.db"
        )
        self._lock = threading.RLock()
        self.connection: sqlite3.Connection
        self.journal_mode: str
        guard: _PathGuard | None = None
        connection: sqlite3.Connection | None = None
        existing_version: int | None = None
        try:
            guard = _open_protected_path(self.path)
            _verify_retained_identity(self.path, guard)
            empty_target = os.fstat(guard.file_fd).st_size == 0
            if not empty_target:
                existing_version = _preflight_existing(self.path, guard)

            connection, connection_fds = _connect(
                self.path, read_only=False
            )
            _verify_retained_identity(
                self.path,
                guard,
                connection,
                connection_fds,
            )
            connection.execute("PRAGMA foreign_keys=ON")
            if empty_target:
                self._initialize_empty(connection)
            else:
                if existing_version == 1:
                    self._migrate_v1_to_v2(connection)
                _validate_schema(connection)
            _verify_retained_identity(
                self.path,
                guard,
                connection,
                connection_fds,
            )
            self.journal_mode = apply_wal_with_fallback(
                connection, db_label="evolution.db"
            )
            self.connection = connection
        except EvolutionLedgerError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise EvolutionLedgerError("invalid_ledger_database") from exc
        finally:
            if guard is not None:
                guard.close()

    @staticmethod
    def _initialize_empty(connection: sqlite3.Connection) -> None:
        began = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            began = True
            existing = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                LIMIT 1
                """
            ).fetchone()
            if existing is None:
                for statement in _SCHEMA_STATEMENTS:
                    _execute_schema_statement(connection, statement)
                connection.execute(
                    """
                    INSERT INTO schema_version(singleton, version)
                    VALUES (1, ?)
                    """,
                    (SCHEMA_VERSION,),
                )
            _validate_schema(connection)
            connection.commit()
        except BaseException:
            if began and connection.in_transaction:
                try:
                    connection.rollback()
                except BaseException:
                    connection.close()
            raise

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        began = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            began = True
            locked_version = _declared_version(connection)
            if locked_version == SCHEMA_VERSION:
                _validate_schema(connection)
                connection.commit()
                return
            if locked_version != 1:
                if locked_version > SCHEMA_VERSION:
                    raise EvolutionLedgerError(
                        "unsupported_schema_version"
                    )
                raise EvolutionLedgerError("invalid_ledger_database")
            _validate_schema_version(
                connection,
                version=1,
                statements=_SCHEMA_V1_STATEMENTS,
                tables=_TABLES_V1,
            )
            authorization_rows = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM authorization_requests)
                    + (SELECT COUNT(*) FROM authorization_grants)
                """
            ).fetchone()[0]
            if authorization_rows != 0:
                raise EvolutionLedgerError(
                    "unmigratable_authorization_records"
                )
            for statement in (
                "DROP TRIGGER authorization_grants_no_update",
                "DROP TRIGGER authorization_grants_no_delete",
                "DROP INDEX authorization_requests_attempt_idx",
                "DROP INDEX authorization_grants_attempt_idx",
                "DROP TABLE authorization_grants",
                "DROP TABLE authorization_requests",
                "DROP TABLE schema_version",
                _SCHEMA_STATEMENTS[0],
                *_V2_AUTH_SCHEMA_STATEMENTS,
            ):
                _execute_migration_statement(connection, statement)
            connection.execute(
                """
                INSERT INTO schema_version(singleton, version)
                VALUES (1, ?)
                """,
                (SCHEMA_VERSION,),
            )
            _validate_schema(connection)
            connection.commit()
        except BaseException:
            if began and connection.in_transaction:
                try:
                    connection.rollback()
                except BaseException:
                    connection.close()
            raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return _declared_version(self.connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            began = False
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                began = True
                yield self.connection
                self.connection.commit()
            except BaseException:
                if began and self.connection.in_transaction:
                    try:
                        self.connection.rollback()
                    except BaseException:
                        self.connection.close()
                raise

    def create_attempt(self, source_kind: str, source_ref: str) -> str:
        kind = _normalize_text(
            source_kind, limit=64, code="invalid_attempt_source"
        )
        reference = _normalize_text(
            source_ref, limit=256, code="invalid_attempt_source"
        )
        if (
            "/" in reference
            or "\\" in reference
            or reference in {".", ".."}
            or _PATH_SCHEME_PATTERN.match(reference)
            or Path(reference).is_absolute()
        ):
            raise EvolutionLedgerError("invalid_attempt_source")
        attempt_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO attempts(
                    attempt_id, source_kind, source_ref, state, created_at
                ) VALUES (?, ?, ?, 'draft', ?)
                """,
                (attempt_id, kind, reference, _now()),
            )
        return attempt_id

    def _normalize_event(self, event: LifecycleEvent) -> LifecycleEvent:
        if not isinstance(event, LifecycleEvent):
            raise EvolutionLedgerError("invalid_event")
        if (
            not isinstance(event.input_digests, tuple)
            or len(event.input_digests) > _MAX_DIGESTS
        ):
            raise EvolutionLedgerError("invalid_event_digests")
        digests: list[str] = []
        try:
            for digest in event.input_digests:
                digests.append(require_digest(digest))
        except ValueError as exc:
            raise EvolutionLedgerError("invalid_event_digests") from exc
        prior = event.prior_state
        next_state = event.next_state
        if prior is not None and prior not in _STATES:
            raise EvolutionLedgerError("invalid_event")
        if next_state is not None and next_state not in _STATES:
            raise EvolutionLedgerError("invalid_event")
        return replace(
            event,
            event_id=_normalize_text(
                event.event_id, limit=256, code="invalid_event"
            ),
            attempt_id=_optional_identity(event.attempt_id),
            generation_id=_optional_identity(
                event.generation_id, digest=True
            ),
            event_type=_normalize_text(
                event.event_type, limit=128, code="invalid_event"
            ),
            actor=_normalize_text(
                event.actor, limit=128, code="invalid_event"
            ),
            input_digests=tuple(digests),
            authorization_id=_optional_identity(event.authorization_id),
            reason_code=_normalize_text(
                event.reason_code, limit=128, code="invalid_event"
            ),
            reason_summary=_normalize_text(
                event.reason_summary,
                limit=512,
                code="invalid_event",
                collapse_whitespace=True,
            ),
            created_at=_require_timestamp(event.created_at),
        )

    def _payload(
        self, event: LifecycleEvent, previous: str | None
    ) -> dict[str, object]:
        normalized = self._normalize_event(event)
        return {
            "event_id": normalized.event_id,
            "attempt_id": normalized.attempt_id,
            "generation_id": normalized.generation_id,
            "event_type": normalized.event_type,
            "prior_state": normalized.prior_state,
            "next_state": normalized.next_state,
            "actor": normalized.actor,
            "input_digests": normalized.input_digests,
            "authorization_id": normalized.authorization_id,
            "reason_code": normalized.reason_code,
            "reason_summary": normalized.reason_summary,
            "created_at": normalized.created_at,
            "previous_event_digest": previous,
        }

    def append_event(self, event: LifecycleEvent) -> StoredEvent:
        with self.transaction() as connection:
            return self._append(connection, event)

    def _append(
        self, connection: sqlite3.Connection, event: LifecycleEvent
    ) -> StoredEvent:
        normalized = self._normalize_event(event)
        previous_row = connection.execute(
            """
            SELECT event_digest
            FROM lifecycle_events
            ORDER BY event_sequence DESC
            LIMIT 1
            """
        ).fetchone()
        previous = None if previous_row is None else str(previous_row[0])
        payload = self._payload(normalized, previous)
        digest = content_digest(
            payload, domain="hermes-evolution-lifecycle-event-v1"
        )
        cursor = connection.execute(
            """
            INSERT INTO lifecycle_events(
                event_id, attempt_id, generation_id, event_type, prior_state,
                next_state, actor, input_digests_json, authorization_id,
                reason_code, reason_summary, created_at,
                previous_event_digest, event_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized.event_id,
                normalized.attempt_id,
                normalized.generation_id,
                normalized.event_type,
                normalized.prior_state,
                normalized.next_state,
                normalized.actor,
                canonical_json_bytes(list(normalized.input_digests)).decode(),
                normalized.authorization_id,
                normalized.reason_code,
                normalized.reason_summary,
                normalized.created_at,
                previous,
                digest,
            ),
        )
        return StoredEvent(
            **normalized.__dict__,
            event_sequence=int(cursor.lastrowid),
            previous_event_digest=previous,
            event_digest=digest,
        )

    def transition(self, request: TransitionRequest) -> StoredEvent:
        validate_transition(request)
        attempt_id = _normalize_text(
            request.attempt_id, limit=256, code="invalid_event"
        )
        with self.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE attempts
                SET state = ?
                WHERE attempt_id = ? AND state = ?
                """,
                (request.next_state, attempt_id, request.prior_state),
            ).rowcount
            if updated != 1:
                raise EvolutionLedgerError("attempt_state_conflict")
            return self._append(
                connection,
                LifecycleEvent(
                    event_id=str(uuid.uuid4()),
                    attempt_id=attempt_id,
                    generation_id=None,
                    event_type="state_transition",
                    prior_state=request.prior_state,
                    next_state=request.next_state,
                    actor=request.actor,
                    input_digests=request.input_digests,
                    authorization_id=request.authorization_id,
                    reason_code="transition",
                    reason_summary=request.reason,
                    created_at=_now(),
                ),
            )

    def history(
        self, *, limit: int = 100, after: int | None = None
    ) -> list[StoredEvent]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 1000
            or (
                after is not None
                and (
                    isinstance(after, bool)
                    or not isinstance(after, int)
                    or after < 0
                )
            )
        ):
            raise EvolutionLedgerError("invalid_history_limit")
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT *
                FROM lifecycle_events
                WHERE event_sequence > COALESCE(?, 0)
                ORDER BY event_sequence
                LIMIT ?
                """,
                (after, limit),
            ).fetchall()
        return [self._stored(row) for row in rows]

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            event_id=row["event_id"],
            attempt_id=row["attempt_id"],
            generation_id=row["generation_id"],
            event_type=row["event_type"],
            prior_state=row["prior_state"],
            next_state=row["next_state"],
            actor=row["actor"],
            input_digests=tuple(json.loads(row["input_digests_json"])),
            authorization_id=row["authorization_id"],
            reason_code=row["reason_code"],
            reason_summary=row["reason_summary"],
            created_at=row["created_at"],
            event_sequence=row["event_sequence"],
            previous_event_digest=row["previous_event_digest"],
            event_digest=row["event_digest"],
        )

    def _verify_records(
        self,
        records: Iterator[StoredEvent],
        *,
        previous: str | None,
        errors: list[str],
    ) -> str | None:
        for record in records:
            try:
                payload = self._payload(record, previous)
                expected = content_digest(
                    payload,
                    domain="hermes-evolution-lifecycle-event-v1",
                )
                if (
                    record.previous_event_digest != previous
                    or record.event_digest != expected
                ):
                    errors.append(str(record.event_sequence))
            except (EvolutionLedgerError, ValueError, TypeError, json.JSONDecodeError):
                errors.append(str(record.event_sequence))
            previous = (
                record.event_digest
                if isinstance(record.event_digest, str)
                else None
            )
        return previous

    def verify_chain(self) -> list[str]:
        errors: list[str] = []
        previous: str | None = None
        with self._lock:
            cursor = self.connection.execute(
                "SELECT * FROM lifecycle_events ORDER BY event_sequence"
            )
            while True:
                rows = cursor.fetchmany(_VERIFY_BATCH_SIZE)
                if not rows:
                    break
                for row in rows:
                    try:
                        record = self._stored(row)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        errors.append(str(row["event_sequence"]))
                        previous = (
                            row["event_digest"]
                            if isinstance(row["event_digest"], str)
                            else None
                        )
                        continue
                    previous = self._verify_records(
                        iter((record,)), previous=previous, errors=errors
                    )
        return errors

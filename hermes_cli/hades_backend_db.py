"""Profile-scoped SQLite state for Hades backend integration."""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from hermes_cli.sqlite_util import add_column_if_missing, write_txn
from hermes_constants import get_hermes_home

TERMINAL_BACKEND_JOB_STATUSES = (
    "cancelled",
    "completed",
    "expired",
    "failed",
    "skipped",
    "unlinked",
)
TERMINAL_PLUGIN_WORK_ITEM_STATUSES = (
    "cancelled",
    "completed",
    "completed_with_incomplete_memory",
    "expired",
    "failed",
    "skipped",
)
REVIEWED_MEMORY_PROPOSAL_STATUSES = ("accepted", "acknowledged")


def hades_backend_db_path() -> Path:
    return get_hermes_home() / "hades_backend.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS backend_agents (
    agent_id       TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    base_url       TEXT NOT NULL,
    label          TEXT NOT NULL,
    token_env_key  TEXT NOT NULL,
    capabilities   TEXT NOT NULL DEFAULT '{}',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    last_seen_at   INTEGER
);

CREATE TABLE IF NOT EXISTS workspace_bindings (
    workspace_fingerprint       TEXT PRIMARY KEY,
    project_id                  TEXT NOT NULL,
    agent_id                    TEXT NOT NULL,
    local_project_id            TEXT NOT NULL,
    backend_workspace_binding_id TEXT NOT NULL,
    display_path                TEXT NOT NULL,
    repo_root                   TEXT NOT NULL,
    git_remote_display          TEXT,
    git_remote_hash             TEXT,
    head_commit                 TEXT,
    status                      TEXT NOT NULL,
    created_at                  INTEGER NOT NULL,
    updated_at                  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_bindings_project
    ON workspace_bindings(project_id, local_project_id);

CREATE TABLE IF NOT EXISTS backend_route_auth_health (
    project_id            TEXT NOT NULL,
    agent_id              TEXT NOT NULL,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    first_failed_at       INTEGER,
    last_failed_at        INTEGER,
    reason_code           TEXT,
    updated_at            INTEGER NOT NULL,
    PRIMARY KEY (project_id, agent_id)
);

CREATE TABLE IF NOT EXISTS agent_coordination_manifests (
    root_id           TEXT NOT NULL,
    project_id        TEXT NOT NULL,
    agent_id          TEXT NOT NULL,
    parent_id         TEXT NOT NULL,
    role              TEXT NOT NULL,
    objective         TEXT NOT NULL,
    write_scope       TEXT NOT NULL,
    dependencies      TEXT NOT NULL,
    interfaces        TEXT NOT NULL,
    produces          TEXT NOT NULL,
    status            TEXT NOT NULL,
    task_version      INTEGER NOT NULL,
    contract_version  INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    PRIMARY KEY(root_id, project_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_coordination_manifests_parent
    ON agent_coordination_manifests(root_id, project_id, parent_id, agent_id);

CREATE TABLE IF NOT EXISTS agent_coordination_events (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id        TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    sender_id      TEXT NOT NULL,
    parent_id      TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    summary        TEXT NOT NULL,
    evidence_refs  TEXT NOT NULL,
    artifact       TEXT,
    created_at     INTEGER NOT NULL,
    expires_at     INTEGER NOT NULL,
    ttl_seconds    INTEGER NOT NULL,
    request_fingerprint TEXT NOT NULL,
    UNIQUE(root_id, project_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_coordination_events_expiry
    ON agent_coordination_events(expires_at, sequence);

CREATE TABLE IF NOT EXISTS agent_coordination_event_recipients (
    root_id        TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    recipient_id   TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    PRIMARY KEY(root_id, project_id, event_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS idx_coordination_recipient_sequence
    ON agent_coordination_event_recipients(root_id, project_id, recipient_id, sequence);

CREATE TABLE IF NOT EXISTS agent_coordination_state (
    root_id            TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    recipient_id       TEXT NOT NULL,
    parent_id          TEXT NOT NULL,
    generation         INTEGER NOT NULL DEFAULT 0,
    ack_generation     INTEGER NOT NULL DEFAULT 0,
    ack_sequence       INTEGER NOT NULL DEFAULT 0,
    dirty              INTEGER NOT NULL DEFAULT 0,
    completed          INTEGER NOT NULL DEFAULT 0,
    last_notified_at   INTEGER NOT NULL DEFAULT 0,
    updated_at         INTEGER NOT NULL,
    PRIMARY KEY(root_id, project_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS agent_coordination_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL,
    quarantined_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS backend_jobs (
    job_id               TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL,
    capability           TEXT NOT NULL,
    payload              TEXT NOT NULL,
    status               TEXT NOT NULL,
    result               TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_work_items (
    work_item_id        TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    repository_id       TEXT,
    local_workspace_id  TEXT,
    agent_key           TEXT NOT NULL,
    kind                TEXT NOT NULL,
    status              TEXT NOT NULL,
    lease_token         TEXT,
    payload             TEXT NOT NULL,
    result              TEXT,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plugin_work_items_status
    ON plugin_work_items(project_id, status);

CREATE TABLE IF NOT EXISTS memory_proposals (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL,
    action               TEXT NOT NULL,
    intent               TEXT NOT NULL,
    summary              TEXT NOT NULL,
    provenance           TEXT NOT NULL,
    status               TEXT NOT NULL,
    reason               TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_cache (
    workspace_binding_id TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    version              TEXT NOT NULL,
    items                TEXT NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox_events (
    event_id     TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    received_at  INTEGER NOT NULL,
    read_at      INTEGER
);

CREATE TABLE IF NOT EXISTS persephone_outbox (
    message_id      TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    sender_agent_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    envelope        TEXT NOT NULL,
    state           TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    last_error      TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persephone_outbox_target_state
    ON persephone_outbox(project_id, target_agent_id, state);
CREATE INDEX IF NOT EXISTS idx_persephone_outbox_next_attempt
    ON persephone_outbox(next_attempt_at);

CREATE TABLE IF NOT EXISTS logbook_outbox (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id            TEXT NOT NULL,
    workspace_binding_id  TEXT NOT NULL,
    actor_agent_id        TEXT NOT NULL,
    idempotency_key       TEXT NOT NULL,
    request_json          TEXT NOT NULL,
    request_digest        TEXT NOT NULL,
    state                 TEXT NOT NULL CHECK(state IN ('pending', 'leased', 'sent', 'dead_letter')),
    lease_token           TEXT,
    lease_expires_at      INTEGER,
    attempts              INTEGER NOT NULL DEFAULT 0,
    next_attempt_at       INTEGER NOT NULL,
    response_id           TEXT,
    last_error            TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    UNIQUE(project_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_logbook_outbox_due
    ON logbook_outbox(state, next_attempt_at, created_at, id);

CREATE TABLE IF NOT EXISTS persephone_inbox (
    message_id       TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    target_agent_id  TEXT NOT NULL,
    envelope         TEXT NOT NULL,
    message_type     TEXT NOT NULL,
    effect           TEXT NOT NULL,
    capability       TEXT NOT NULL,
    state            TEXT NOT NULL,
    received_at      INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    human_decision   TEXT,
    human_decided_by TEXT,
    human_reason     TEXT,
    human_decided_at INTEGER,
    response_message_id TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_persephone_inbox_target_state
    ON persephone_inbox(project_id, target_agent_id, state);
CREATE INDEX IF NOT EXISTS idx_persephone_inbox_recovery
    ON persephone_inbox(state, updated_at, message_id);

CREATE TABLE IF NOT EXISTS persephone_cursors (
    project_id      TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    cursor          TEXT NOT NULL,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY(project_id, target_agent_id)
);

CREATE TABLE IF NOT EXISTS persephone_subscription_deliveries (
    subscription_project_id TEXT NOT NULL,
    subscription_agent_id TEXT NOT NULL,
    subscription_workspace_binding_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL,
    cursor TEXT,
    disposition TEXT NOT NULL,
    envelope_project_id TEXT NOT NULL,
    envelope_target_agent_id TEXT NOT NULL,
    envelope_target_workspace_binding_id TEXT,
    received_at INTEGER NOT NULL,
    PRIMARY KEY(
        subscription_project_id,
        subscription_agent_id,
        subscription_workspace_binding_id,
        message_id
    )
);

CREATE INDEX IF NOT EXISTS idx_persephone_subscription_delivery_message
    ON persephone_subscription_deliveries(message_id);

CREATE TABLE IF NOT EXISTS persephone_message_identities (
    message_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    direction  TEXT NOT NULL CHECK(direction IN ('inbox', 'outbox')),
    envelope   TEXT NOT NULL,
    claimed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


_INITIALIZED_PATHS: set[str] = set()


class PersephoneIdentityMigrationConflict(RuntimeError):
    """Existing queue rows violate the global agent-message ID invariant."""


def _migrate_agent_coordination_schema(conn: sqlite3.Connection) -> None:
    """Quarantine unnamespaced O7 rows and install collision-safe DAG tables."""

    legacy = False
    for table in (
        "agent_coordination_manifests",
        "agent_coordination_events",
        "agent_coordination_event_recipients",
        "agent_coordination_state",
        "agent_coordination_cursors",
    ):
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        required = {"root_id", "project_id"}
        if table == "agent_coordination_events":
            required.add("request_fingerprint")
        if columns and not required.issubset(columns):
            legacy = True
            break
    if not legacy:
        return
    conn.execute(
        """CREATE TABLE IF NOT EXISTS agent_coordination_quarantine (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               source_table TEXT NOT NULL,
               source_key TEXT NOT NULL,
               payload TEXT NOT NULL,
               reason TEXT NOT NULL,
               quarantined_at INTEGER NOT NULL
           )"""
    )
    now = int(time.time())
    for table in (
        "agent_coordination_manifests",
        "agent_coordination_events",
        "agent_coordination_event_recipients",
        "agent_coordination_state",
        "agent_coordination_cursors",
    ):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        for index, row in enumerate(conn.execute(f"SELECT * FROM {table}").fetchall()):
            conn.execute(
                """INSERT INTO agent_coordination_quarantine
                   (source_table, source_key, payload, reason, quarantined_at)
                   VALUES (?, ?, ?, 'missing root_id/project_id namespace', ?)""",
                (table, str(index), json.dumps(dict(row), default=str), now),
            )
    conn.executescript(
        """
        DROP TABLE IF EXISTS agent_coordination_manifests;
        DROP TABLE IF EXISTS agent_coordination_events;
        DROP TABLE IF EXISTS agent_coordination_event_recipients;
        DROP TABLE IF EXISTS agent_coordination_state;
        DROP TABLE IF EXISTS agent_coordination_cursors;
        DROP INDEX IF EXISTS idx_coordination_manifests_parent;
        DROP INDEX IF EXISTS idx_agent_coordination_events_expiry;
        DROP INDEX IF EXISTS idx_coordination_recipient_sequence;
        CREATE TABLE agent_coordination_manifests (
            root_id TEXT NOT NULL, project_id TEXT NOT NULL, agent_id TEXT NOT NULL,
            parent_id TEXT NOT NULL, role TEXT NOT NULL, objective TEXT NOT NULL,
            write_scope TEXT NOT NULL, dependencies TEXT NOT NULL,
            interfaces TEXT NOT NULL, produces TEXT NOT NULL, status TEXT NOT NULL,
            task_version INTEGER NOT NULL, contract_version INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(root_id, project_id, agent_id)
        );
        CREATE INDEX idx_coordination_manifests_parent
            ON agent_coordination_manifests(root_id, project_id, parent_id, agent_id);
        CREATE TABLE agent_coordination_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            root_id TEXT NOT NULL, project_id TEXT NOT NULL, event_id TEXT NOT NULL,
            sender_id TEXT NOT NULL, parent_id TEXT NOT NULL, event_type TEXT NOT NULL,
            summary TEXT NOT NULL, evidence_refs TEXT NOT NULL, artifact TEXT,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            ttl_seconds INTEGER NOT NULL, request_fingerprint TEXT NOT NULL,
            UNIQUE(root_id, project_id, event_id)
        );
        CREATE INDEX idx_agent_coordination_events_expiry
            ON agent_coordination_events(expires_at, sequence);
        CREATE TABLE agent_coordination_event_recipients (
            root_id TEXT NOT NULL, project_id TEXT NOT NULL, event_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            PRIMARY KEY(root_id, project_id, event_id, recipient_id)
        );
        CREATE INDEX idx_coordination_recipient_sequence
            ON agent_coordination_event_recipients(root_id, project_id, recipient_id, sequence);
        CREATE TABLE agent_coordination_state (
            root_id TEXT NOT NULL, project_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL, parent_id TEXT NOT NULL,
            generation INTEGER NOT NULL DEFAULT 0,
            ack_generation INTEGER NOT NULL DEFAULT 0,
            ack_sequence INTEGER NOT NULL DEFAULT 0,
            dirty INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            last_notified_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(root_id, project_id, recipient_id)
        );
        """
    )
    conn.commit()


def _canonical_envelope_json(value: str, *, message_id: str) -> str:
    try:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("envelope is not an object")
        return json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PersephoneIdentityMigrationConflict(
            f"invalid stored Persephone envelope for message_id {message_id!r}: {exc}"
        ) from None


def _migrate_persephone_message_identities(conn: sqlite3.Connection) -> None:
    """Backfill the global ID registry from O2 databases, rejecting ambiguity."""
    from hermes_cli.hades_persephone_messages import parse_envelope

    with write_txn(conn):
        add_column_if_missing(
            conn,
            "persephone_inbox",
            "message_type",
            "message_type TEXT NOT NULL DEFAULT ''",
        )
        add_column_if_missing(
            conn,
            "persephone_inbox",
            "effect",
            "effect TEXT NOT NULL DEFAULT ''",
        )
        add_column_if_missing(
            conn,
            "persephone_inbox",
            "capability",
            "capability TEXT NOT NULL DEFAULT ''",
        )
        add_column_if_missing(
            conn,
            "persephone_inbox",
            "response_message_id",
            "response_message_id TEXT",
        )
        add_column_if_missing(
            conn,
            "persephone_inbox",
            "attempts",
            "attempts INTEGER NOT NULL DEFAULT 0",
        )
        add_column_if_missing(
            conn,
            "persephone_inbox",
            "next_attempt_at",
            "next_attempt_at INTEGER NOT NULL DEFAULT 0",
        )
        add_column_if_missing(
            conn, "persephone_inbox", "last_error", "last_error TEXT"
        )
        add_column_if_missing(
            conn, "persephone_outbox", "sender_agent_id", "sender_agent_id TEXT"
        )
        for table, direction in (
            ("persephone_inbox", "inbox"),
            ("persephone_outbox", "outbox"),
        ):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY message_id").fetchall()
            for row in rows:
                message_id = str(row["message_id"])
                project_id = str(row["project_id"])
                envelope = _canonical_envelope_json(row["envelope"], message_id=message_id)
                decoded = json.loads(envelope)
                try:
                    validated = parse_envelope(decoded, now=0)
                except (TypeError, ValueError) as exc:
                    raise PersephoneIdentityMigrationConflict(
                        f"invalid stored envelope for message_id {message_id!r}: {exc}"
                    ) from None
                if validated.message_id != message_id:
                    raise PersephoneIdentityMigrationConflict(
                        f"row message_id does not match envelope for {message_id!r}"
                    )
                if validated.project_id != project_id:
                    raise PersephoneIdentityMigrationConflict(
                        f"row project_id does not match envelope for {message_id!r}"
                    )
                if str(row["target_agent_id"]) != validated.target_agent_id:
                    raise PersephoneIdentityMigrationConflict(
                        f"row target_agent_id does not match envelope for {message_id!r}"
                    )
                if table == "persephone_outbox":
                    stored_sender = str(row["sender_agent_id"] or "").strip()
                    if stored_sender and stored_sender != validated.sender_agent_id:
                        raise PersephoneIdentityMigrationConflict(
                            "denormalized sender_agent_id does not match envelope for "
                            f"{message_id!r}"
                        )
                authority = (
                    validated.message_type.value,
                    validated.effect.value,
                    validated.capability,
                )
                if table == "persephone_inbox":
                    for field, expected in zip(
                        ("message_type", "effect", "capability"), authority
                    ):
                        existing_value = str(row[field] or "").strip()
                        if existing_value and existing_value != expected:
                            raise PersephoneIdentityMigrationConflict(
                                f"denormalized {field} does not match envelope for "
                                f"{message_id!r}"
                            )
                existing = conn.execute(
                    "SELECT project_id, direction, envelope FROM persephone_message_identities "
                    "WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if existing is not None:
                    existing_envelope = _canonical_envelope_json(
                        existing["envelope"], message_id=message_id
                    )
                    try:
                        existing_validated = parse_envelope(
                            json.loads(existing_envelope), now=0
                        )
                    except (TypeError, ValueError) as exc:
                        raise PersephoneIdentityMigrationConflict(
                            f"invalid global identity for message_id {message_id!r}: {exc}"
                        ) from None
                    if (
                        existing_validated.message_id != message_id
                        or existing_validated.project_id != existing["project_id"]
                        or existing["project_id"] != project_id
                        or existing["direction"] != direction
                        or existing_envelope != envelope
                    ):
                        raise PersephoneIdentityMigrationConflict(
                            "conflicting pre-existing Persephone identity for "
                            f"message_id {message_id!r}"
                        )
                conn.execute(
                    "INSERT OR IGNORE INTO persephone_message_identities "
                    "(message_id, project_id, direction, envelope, claimed_at) VALUES (?, ?, ?, ?, ?)",
                    (message_id, project_id, direction, envelope, _now()),
                )
                if row["envelope"] != envelope:
                    conn.execute(
                        f"UPDATE {table} SET envelope = ? WHERE message_id = ?",
                        (envelope, message_id),
                    )
                if table == "persephone_inbox":
                    conn.execute(
                        "UPDATE persephone_inbox SET message_type = ?, effect = ?, "
                        "capability = ? WHERE message_id = ?",
                        (*authority, message_id),
                    )
                else:
                    conn.execute(
                        "UPDATE persephone_outbox SET sender_agent_id = ? "
                        "WHERE message_id = ?",
                        (validated.sender_agent_id, message_id),
                    )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_persephone_inbox_recovery_covering "
            "ON persephone_inbox(state, message_type, effect, capability, "
            "updated_at, message_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_persephone_outbox_sender_due "
            "ON persephone_outbox(project_id, sender_agent_id, state, "
            "next_attempt_at, created_at, message_id)"
        )


def _has_project_scoped_logbook_idempotency(conn: sqlite3.Connection) -> bool:
    """Return whether the outbox matches the backend's project-wide key."""

    for index in conn.execute("PRAGMA index_list(logbook_outbox)").fetchall():
        if not bool(index[2]):
            continue
        columns = [
            str(column[2])
            for column in conn.execute(f"PRAGMA index_info({index[1]})").fetchall()
        ]
        if columns == ["project_id", "idempotency_key"]:
            return True
    return False


def _migrate_logbook_outbox_actor_identity(conn: sqlite3.Connection) -> None:
    """Persist the stable backend actor separately from replaceable routing."""

    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(logbook_outbox)").fetchall()
    }
    if "actor_agent_id" not in columns:
        with write_txn(conn):
            conn.execute("ALTER TABLE logbook_outbox ADD COLUMN actor_agent_id TEXT")
    with write_txn(conn):
        conn.execute(
            "UPDATE logbook_outbox SET actor_agent_id = ("
            "SELECT workspace_bindings.agent_id FROM workspace_bindings "
            "WHERE workspace_bindings.project_id = logbook_outbox.project_id "
            "AND workspace_bindings.backend_workspace_binding_id = "
            "logbook_outbox.workspace_binding_id LIMIT 1) "
            "WHERE actor_agent_id IS NULL OR actor_agent_id = ''"
        )


def _migrate_logbook_outbox_idempotency(conn: sqlite3.Connection) -> None:
    """Move branch-era binding-scoped outboxes to the backend's identity.

    A collision represents two different durable obligations that the backend
    can never accept under one key.  Refuse to guess which one to discard and
    leave the original table untouched for explicit operator resolution.
    """

    if _has_project_scoped_logbook_idempotency(conn):
        return
    collision = conn.execute(
        "SELECT project_id, idempotency_key FROM logbook_outbox "
        "GROUP BY project_id, idempotency_key HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if collision is not None:
        raise ValueError(
            "logbook outbox has project-scoped idempotency collisions; "
            "resolve the duplicate durable records before retrying"
        )
    with write_txn(conn):
        conn.execute(
            "CREATE TABLE logbook_outbox_replacement ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, "
            "workspace_binding_id TEXT NOT NULL, actor_agent_id TEXT, "
            "idempotency_key TEXT NOT NULL, "
            "request_json TEXT NOT NULL, request_digest TEXT NOT NULL, "
            "state TEXT NOT NULL CHECK(state IN ('pending', 'leased', 'sent', 'dead_letter')), "
            "lease_token TEXT, lease_expires_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0, "
            "next_attempt_at INTEGER NOT NULL, response_id TEXT, last_error TEXT, "
            "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
            "UNIQUE(project_id, idempotency_key))"
        )
        conn.execute(
            "INSERT INTO logbook_outbox_replacement "
            "SELECT id, project_id, workspace_binding_id, actor_agent_id, idempotency_key, request_json, "
            "request_digest, state, lease_token, lease_expires_at, attempts, next_attempt_at, "
            "response_id, last_error, created_at, updated_at FROM logbook_outbox"
        )
        conn.execute("DROP TABLE logbook_outbox")
        conn.execute("ALTER TABLE logbook_outbox_replacement RENAME TO logbook_outbox")
        conn.execute(
            "CREATE INDEX idx_logbook_outbox_due "
            "ON logbook_outbox(state, next_attempt_at, created_at, id)"
        )


def _now() -> int:
    return int(time.time())


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _logbook_request_digest(
    request: dict[str, Any], actor_agent_id: str | None = None
) -> str:
    """Hash the backend mutation, excluding the replaceable routing binding."""

    canonical = dict(request)
    canonical.pop("workspace_binding_id", None)
    canonical.setdefault("correlation_id", None)
    canonical.setdefault("narrative_markdown", None)
    canonical.setdefault("payload", {})
    canonical.setdefault("references", [])
    canonical.setdefault("supersedes_entry_id", None)
    references = canonical.get("references")
    if isinstance(references, list) and all(
        isinstance(reference, dict)
        and isinstance(reference.get("kind"), str)
        and isinstance(reference.get("id"), str)
        for reference in references
    ):
        canonical["references"] = sorted(
            references, key=lambda reference: (reference["kind"], reference["id"])
        )
    canonical["_actor_agent_id"] = actor_agent_id
    encoded = _json_dumps(canonical)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _migrate_logbook_outbox_request_digests(conn: sqlite3.Connection) -> None:
    """Normalize branch-era digests that included workspace routing metadata."""

    rows = conn.execute(
        "SELECT id, actor_agent_id, request_json, request_digest FROM logbook_outbox"
    ).fetchall()
    updates: list[tuple[str, int]] = []
    for row in rows:
        request = _json_loads(row["request_json"])
        if not isinstance(request, dict):
            raise ValueError(f"logbook outbox entry {row['id']} has invalid request JSON")
        actor_agent_id = str(row["actor_agent_id"] or "").strip() or None
        if actor_agent_id is None:
            continue
        digest = _logbook_request_digest(request, actor_agent_id)
        if digest != str(row["request_digest"]):
            updates.append((digest, int(row["id"])))
    if updates:
        with write_txn(conn):
            conn.executemany(
                "UPDATE logbook_outbox SET request_digest = ? WHERE id = ?", updates
            )


def _json_loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except ValueError:
        return {}


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path if db_path is not None else hades_backend_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label="hades_backend.db")
        if resolved not in _INITIALIZED_PATHS:
            # Legacy O7 tables may not have the columns referenced by indexes in
            # SCHEMA_SQL.  Quarantine/rebuild them before the normal idempotent
            # schema pass so partially-created legacy stores migrate safely too.
            _migrate_agent_coordination_schema(conn)
            conn.executescript(SCHEMA_SQL)
            _migrate_logbook_outbox_actor_identity(conn)
            _migrate_logbook_outbox_idempotency(conn)
            _migrate_logbook_outbox_request_digests(conn)
            _migrate_persephone_message_identities(conn)
            _INITIALIZED_PATHS.add(resolved)
    except Exception:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Path | None = None):
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class BackendAgent:
    agent_id: str
    project_id: str
    base_url: str
    label: str
    token_env_key: str
    capabilities: dict[str, Any]


@dataclass(frozen=True)
class WorkspaceBinding:
    workspace_fingerprint: str
    project_id: str
    agent_id: str
    local_project_id: str
    backend_workspace_binding_id: str
    display_path: str
    repo_root: str
    git_remote_display: str
    git_remote_hash: str
    head_commit: str
    status: str


@dataclass(frozen=True)
class BackendJob:
    job_id: str
    project_id: str
    workspace_binding_id: str
    capability: str
    payload: dict[str, Any]
    status: str
    result: dict[str, Any] | None


@dataclass(frozen=True)
class PluginWorkItem:
    work_item_id: str
    project_id: str
    repository_id: str
    local_workspace_id: str
    agent_key: str
    kind: str
    status: str
    lease_token: str
    payload: dict[str, Any]
    result: dict[str, Any] | None


@dataclass(frozen=True)
class MemoryProposal:
    id: str
    project_id: str
    workspace_binding_id: str
    action: str
    intent: str
    summary: str
    provenance: dict[str, Any]
    status: str
    reason: str | None


@dataclass(frozen=True)
class InboxEvent:
    event_id: str
    project_id: str
    event_type: str
    payload: dict[str, Any]
    received_at: int


@dataclass(frozen=True)
class MemoryCache:
    project_id: str
    workspace_binding_id: str
    version: str
    items: list[dict[str, Any]]
    updated_at: int


@dataclass(frozen=True)
class LogbookOutboxEntry:
    id: int
    project_id: str
    workspace_binding_id: str
    actor_agent_id: str | None
    idempotency_key: str
    request: dict[str, Any]
    request_digest: str
    state: str
    lease_token: str | None
    lease_expires_at: int | None
    attempts: int
    next_attempt_at: int
    response_id: str | None
    last_error: str | None
    created_at: int
    updated_at: int


class WorkspaceBindingConflict(ValueError):
    def __init__(self, existing_project_id: str, new_project_id: str) -> None:
        super().__init__(
            f"workspace is already linked to backend project {existing_project_id}; "
            f"refusing link to {new_project_id}"
        )
        self.existing_project_id = existing_project_id
        self.new_project_id = new_project_id


def _agent_from_row(row: sqlite3.Row | None) -> BackendAgent | None:
    if row is None:
        return None
    return BackendAgent(
        agent_id=row["agent_id"],
        project_id=row["project_id"],
        base_url=row["base_url"],
        label=row["label"],
        token_env_key=row["token_env_key"],
        capabilities=_json_loads(row["capabilities"]),
    )


def _binding_from_row(row: sqlite3.Row | None) -> WorkspaceBinding | None:
    if row is None:
        return None
    return WorkspaceBinding(
        workspace_fingerprint=row["workspace_fingerprint"],
        project_id=row["project_id"],
        agent_id=row["agent_id"],
        local_project_id=row["local_project_id"],
        backend_workspace_binding_id=row["backend_workspace_binding_id"],
        display_path=row["display_path"],
        repo_root=row["repo_root"],
        git_remote_display=row["git_remote_display"] or "",
        git_remote_hash=row["git_remote_hash"] or "",
        head_commit=row["head_commit"] or "",
        status=row["status"],
    )


def _job_from_row(row: sqlite3.Row) -> BackendJob:
    return BackendJob(
        job_id=row["job_id"],
        project_id=row["project_id"],
        workspace_binding_id=row["workspace_binding_id"],
        capability=row["capability"],
        payload=_json_loads(row["payload"]),
        status=row["status"],
        result=_json_loads(row["result"]) if row["result"] else None,
    )


def _plugin_work_item_from_row(row: sqlite3.Row) -> PluginWorkItem:
    return PluginWorkItem(
        work_item_id=row["work_item_id"],
        project_id=row["project_id"],
        repository_id=row["repository_id"] or "",
        local_workspace_id=row["local_workspace_id"] or "",
        agent_key=row["agent_key"],
        kind=row["kind"],
        status=row["status"],
        lease_token=row["lease_token"] or "",
        payload=_json_loads(row["payload"]),
        result=_json_loads(row["result"]) if row["result"] else None,
    )


def _proposal_from_row(row: sqlite3.Row) -> MemoryProposal:
    return MemoryProposal(
        id=row["id"],
        project_id=row["project_id"],
        workspace_binding_id=row["workspace_binding_id"],
        action=row["action"],
        intent=row["intent"],
        summary=row["summary"],
        provenance=_json_loads(row["provenance"]),
        status=row["status"],
        reason=row["reason"],
    )


def _event_from_row(row: sqlite3.Row) -> InboxEvent:
    return InboxEvent(
        event_id=row["event_id"],
        project_id=row["project_id"],
        event_type=row["event_type"],
        payload=_json_loads(row["payload"]),
        received_at=row["received_at"],
    )


def save_agent(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    project_id: str,
    base_url: str,
    label: str,
    token_env_key: str,
    capabilities: dict[str, Any] | None = None,
) -> BackendAgent:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO backend_agents "
            "(agent_id, project_id, base_url, label, token_env_key, capabilities, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "project_id = excluded.project_id, base_url = excluded.base_url, "
            "label = excluded.label, token_env_key = excluded.token_env_key, "
            "capabilities = excluded.capabilities, updated_at = excluded.updated_at",
            (
                agent_id,
                project_id,
                base_url,
                label,
                token_env_key,
                _json_dumps(capabilities or {}),
                now,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM backend_route_auth_health WHERE project_id = ? AND agent_id = ?",
            (project_id, agent_id),
        )
    loaded = get_agent(conn, agent_id)
    assert loaded is not None
    return loaded


def get_agent(conn: sqlite3.Connection, agent_id: str) -> BackendAgent | None:
    return _agent_from_row(
        conn.execute("SELECT * FROM backend_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    )


def get_default_agent(conn: sqlite3.Connection) -> BackendAgent | None:
    return _agent_from_row(
        conn.execute(
            "SELECT * FROM backend_agents "
            "ORDER BY updated_at DESC, created_at DESC, rowid DESC, agent_id DESC "
            "LIMIT 1"
        ).fetchone()
    )


def upsert_workspace_binding(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    agent_id: str,
    local_project_id: str,
    workspace_fingerprint: str,
    display_path: str,
    repo_root: str,
    git_remote_display: str,
    git_remote_hash: str,
    head_commit: str,
    backend_workspace_binding_id: str,
) -> WorkspaceBinding:
    existing = get_binding_for_fingerprint(conn, workspace_fingerprint)
    if existing and existing.project_id != project_id and existing.status == "linked":
        raise WorkspaceBindingConflict(existing.project_id, project_id)
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO workspace_bindings "
            "(workspace_fingerprint, project_id, agent_id, local_project_id, backend_workspace_binding_id, "
            " display_path, repo_root, git_remote_display, git_remote_hash, head_commit, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'linked', ?, ?) "
            "ON CONFLICT(workspace_fingerprint) DO UPDATE SET "
            "project_id = excluded.project_id, agent_id = excluded.agent_id, "
            "local_project_id = excluded.local_project_id, "
            "backend_workspace_binding_id = excluded.backend_workspace_binding_id, "
            "display_path = excluded.display_path, repo_root = excluded.repo_root, "
            "git_remote_display = excluded.git_remote_display, git_remote_hash = excluded.git_remote_hash, "
            "head_commit = excluded.head_commit, status = 'linked', updated_at = excluded.updated_at",
            (
                workspace_fingerprint,
                project_id,
                agent_id,
                local_project_id,
                backend_workspace_binding_id,
                display_path,
                repo_root,
                git_remote_display,
                git_remote_hash,
                head_commit,
                now,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM backend_route_auth_health WHERE project_id = ? AND agent_id = ?",
            (project_id, agent_id),
        )
    loaded = get_binding_for_fingerprint(conn, workspace_fingerprint)
    assert loaded is not None
    return loaded


def get_binding_for_fingerprint(conn: sqlite3.Connection, workspace_fingerprint: str) -> WorkspaceBinding | None:
    return _binding_from_row(
        conn.execute(
            "SELECT * FROM workspace_bindings WHERE workspace_fingerprint = ?",
            (workspace_fingerprint,),
        ).fetchone()
    )


def get_binding_for_backend_id(conn: sqlite3.Connection, workspace_binding_id: str) -> WorkspaceBinding | None:
    return _binding_from_row(
        conn.execute(
            "SELECT * FROM workspace_bindings WHERE backend_workspace_binding_id = ?",
            (workspace_binding_id,),
        ).fetchone()
    )


def mark_binding_unlinked(conn: sqlite3.Connection, workspace_fingerprint: str) -> None:
    with write_txn(conn):
        conn.execute(
            "UPDATE workspace_bindings SET status = 'unlinked', updated_at = ? WHERE workspace_fingerprint = ?",
            (_now(), workspace_fingerprint),
        )


def update_workspace_binding_git_metadata(
    conn: sqlite3.Connection,
    workspace_fingerprint: str,
    *,
    git_remote_display: str,
    git_remote_hash: str,
    head_commit: str,
) -> WorkspaceBinding | None:
    with write_txn(conn):
        conn.execute(
            "UPDATE workspace_bindings SET git_remote_display = ?, git_remote_hash = ?, "
            "head_commit = ?, updated_at = ? WHERE workspace_fingerprint = ?",
            (
                git_remote_display,
                git_remote_hash,
                head_commit,
                _now(),
                workspace_fingerprint,
            ),
        )
    return get_binding_for_fingerprint(conn, workspace_fingerprint)


def get_route_auth_health(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    agent_id: str,
) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT * FROM backend_route_auth_health WHERE project_id = ? AND agent_id = ?",
        (project_id, agent_id),
    ).fetchone()
    return dict(row) if row is not None else None


def clear_route_auth_health(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    agent_id: str,
) -> None:
    with write_txn(conn):
        conn.execute(
            "DELETE FROM backend_route_auth_health WHERE project_id = ? AND agent_id = ?",
            (project_id, agent_id),
        )


def record_route_auth_cycle(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    agent_id: str,
    unauthorized: bool,
    now: int | None = None,
) -> dict[str, object]:
    current = int(_now() if now is None else now)
    if not unauthorized:
        clear_route_auth_health(conn, project_id=project_id, agent_id=agent_id)
        return {
            "consecutive_failures": 0,
            "first_failed_at": None,
            "last_failed_at": None,
            "reason_code": None,
            "quarantined": False,
        }

    existing = get_route_auth_health(
        conn, project_id=project_id, agent_id=agent_id
    )
    failures = int(existing["consecutive_failures"]) + 1 if existing else 1
    first_failed_at = int(existing["first_failed_at"]) if existing else current
    quarantined = failures >= 3
    with write_txn(conn):
        conn.execute(
            "INSERT INTO backend_route_auth_health "
            "(project_id, agent_id, consecutive_failures, first_failed_at, "
            " last_failed_at, reason_code, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'unauthorized', ?) "
            "ON CONFLICT(project_id, agent_id) DO UPDATE SET "
            "consecutive_failures = excluded.consecutive_failures, "
            "first_failed_at = excluded.first_failed_at, "
            "last_failed_at = excluded.last_failed_at, "
            "reason_code = excluded.reason_code, updated_at = excluded.updated_at",
            (
                project_id,
                agent_id,
                failures,
                first_failed_at,
                current,
                current,
            ),
        )
        if quarantined:
            conn.execute(
                "UPDATE workspace_bindings SET status = 'auth_failed', updated_at = ? "
                "WHERE project_id = ? AND agent_id = ? AND status = 'linked'",
                (current, project_id, agent_id),
            )
    return {
        "consecutive_failures": failures,
        "first_failed_at": first_failed_at,
        "last_failed_at": current,
        "reason_code": "unauthorized",
        "quarantined": quarantined,
    }


def list_workspace_bindings(conn: sqlite3.Connection, *, status: str | None = None) -> list[WorkspaceBinding]:
    # Automatic workspace selection consumes this order directly: newest
    # update wins, then newest insertion when integer-second timestamps tie.
    # The explicit ID is a final deterministic fallback for future schemas
    # where row identity may no longer be unique or available to the caller.
    order_by_recency = (
        "updated_at DESC, created_at DESC, rowid DESC, "
        "backend_workspace_binding_id DESC"
    )
    if status:
        rows = conn.execute(
            f"SELECT * FROM workspace_bindings WHERE status = ? ORDER BY {order_by_recency}",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM workspace_bindings ORDER BY {order_by_recency}"
        ).fetchall()
    return [b for row in rows if (b := _binding_from_row(row)) is not None]


def upsert_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    project_id: str,
    workspace_binding_id: str,
    capability: str,
    payload: dict[str, Any],
    status: str,
) -> BackendJob:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO backend_jobs "
            "(job_id, project_id, workspace_binding_id, capability, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET updated_at = excluded.updated_at",
            (job_id, project_id, workspace_binding_id, capability, _json_dumps(payload), status, now, now),
        )
    row = conn.execute("SELECT * FROM backend_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job_from_row(row)


def get_job(conn: sqlite3.Connection, job_id: str) -> BackendJob | None:
    row = conn.execute("SELECT * FROM backend_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row is not None else None


def update_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
) -> BackendJob | None:
    with write_txn(conn):
        conn.execute(
            "UPDATE backend_jobs SET status = ?, result = COALESCE(?, result), updated_at = ? WHERE job_id = ?",
            (status, _json_dumps(result) if result is not None else None, _now(), job_id),
        )
    return get_job(conn, job_id)


def list_jobs(conn: sqlite3.Connection, *, statuses: Iterable[str] | None = None) -> list[BackendJob]:
    if statuses:
        values = list(statuses)
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM backend_jobs WHERE status IN ({placeholders}) ORDER BY created_at ASC",
            values,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM backend_jobs ORDER BY created_at ASC").fetchall()
    return [_job_from_row(row) for row in rows]


def upsert_plugin_work_item(
    conn: sqlite3.Connection,
    *,
    work_item_id: str,
    project_id: str,
    repository_id: str | None = None,
    local_workspace_id: str | None = None,
    agent_key: str,
    kind: str,
    status: str,
    payload: dict[str, Any],
    lease_token: str | None = None,
    result: dict[str, Any] | None = None,
) -> PluginWorkItem:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO plugin_work_items "
            "(work_item_id, project_id, repository_id, local_workspace_id, agent_key, kind, status, "
            " lease_token, payload, result, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(work_item_id) DO UPDATE SET "
            "project_id = excluded.project_id, repository_id = excluded.repository_id, "
            "local_workspace_id = COALESCE(excluded.local_workspace_id, plugin_work_items.local_workspace_id), "
            "agent_key = excluded.agent_key, kind = excluded.kind, status = excluded.status, "
            "lease_token = COALESCE(excluded.lease_token, plugin_work_items.lease_token), "
            "payload = excluded.payload, result = COALESCE(excluded.result, plugin_work_items.result), "
            "updated_at = excluded.updated_at",
            (
                work_item_id,
                project_id,
                repository_id,
                local_workspace_id,
                agent_key,
                kind,
                status,
                lease_token,
                _json_dumps(payload),
                _json_dumps(result) if result is not None else None,
                now,
                now,
            ),
        )
    loaded = get_plugin_work_item(conn, work_item_id)
    assert loaded is not None
    return loaded


def get_plugin_work_item(conn: sqlite3.Connection, work_item_id: str) -> PluginWorkItem | None:
    row = conn.execute(
        "SELECT * FROM plugin_work_items WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchone()
    return _plugin_work_item_from_row(row) if row is not None else None


def list_plugin_work_items(conn: sqlite3.Connection, *, statuses: Iterable[str] | None = None) -> list[PluginWorkItem]:
    if statuses:
        values = list(statuses)
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM plugin_work_items WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            values,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM plugin_work_items ORDER BY updated_at DESC").fetchall()
    return [_plugin_work_item_from_row(row) for row in rows]


def update_plugin_work_item_status(
    conn: sqlite3.Connection,
    work_item_id: str,
    status: str,
    *,
    lease_token: str | None = None,
    result: dict[str, Any] | None = None,
) -> PluginWorkItem | None:
    with write_txn(conn):
        conn.execute(
            "UPDATE plugin_work_items SET status = ?, "
            "lease_token = COALESCE(?, lease_token), result = COALESCE(?, result), updated_at = ? "
            "WHERE work_item_id = ?",
            (
                status,
                lease_token,
                _json_dumps(result) if result is not None else None,
                _now(),
                work_item_id,
            ),
        )
    return get_plugin_work_item(conn, work_item_id)


def count_plugin_work_items_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM plugin_work_items GROUP BY status ORDER BY status"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def expire_waiting_jobs(
    conn: sqlite3.Connection,
    *,
    now: int | None = None,
    project_id: str | None = None,
    workspace_binding_ids: Iterable[str] | None = None,
) -> list[BackendJob]:
    current = int(now if now is not None else _now())
    clean_project_id = str(project_id or "").strip()
    clean_binding_ids = {
        str(binding_id).strip()
        for binding_id in (workspace_binding_ids or [])
        if str(binding_id or "").strip()
    }
    expired: list[str] = []
    for job in list_jobs(conn, statuses=["waiting_confirmation"]):
        if clean_project_id and job.project_id != clean_project_id:
            continue
        if clean_binding_ids and job.workspace_binding_id not in clean_binding_ids:
            continue
        deadline = job.payload.get("deadline_at") or job.payload.get("deadline")
        try:
            deadline_value = int(deadline)
        except (TypeError, ValueError):
            continue
        if deadline_value <= current:
            expired.append(job.job_id)
    if not expired:
        return []
    with write_txn(conn):
        for job_id in expired:
            conn.execute(
                "UPDATE backend_jobs SET status = 'expired', updated_at = ? WHERE job_id = ?",
                (current, job_id),
            )
    return [job for job_id in expired if (job := get_job(conn, job_id)) is not None]


def create_memory_proposal(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    workspace_binding_id: str,
    action: str,
    intent: str,
    summary: str,
    provenance: dict[str, Any],
) -> MemoryProposal:
    pid = "mp_" + secrets.token_hex(8)
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO memory_proposals "
            "(id, project_id, workspace_binding_id, action, intent, summary, provenance, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (pid, project_id, workspace_binding_id, action, intent, summary, _json_dumps(provenance), now, now),
        )
    return list_memory_proposals(conn, ids=[pid])[0]


def mark_memory_proposal_status(
    conn: sqlite3.Connection,
    proposal_id: str,
    status: str,
    reason: str | None = None,
) -> None:
    with write_txn(conn):
        conn.execute(
            "UPDATE memory_proposals SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
            (status, reason, _now(), proposal_id),
        )


def list_memory_proposals_by_status(conn: sqlite3.Connection, statuses: Iterable[str]) -> list[MemoryProposal]:
    values = list(statuses)
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    rows = conn.execute(
        f"SELECT * FROM memory_proposals WHERE status IN ({placeholders}) ORDER BY created_at ASC",
        values,
    ).fetchall()
    return [_proposal_from_row(row) for row in rows]


def list_memory_proposals(
    conn: sqlite3.Connection,
    *,
    ids: Iterable[str] | None = None,
) -> list[MemoryProposal]:
    if ids:
        values = list(ids)
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM memory_proposals WHERE id IN ({placeholders}) ORDER BY created_at ASC",
            values,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM memory_proposals ORDER BY created_at ASC").fetchall()
    return [_proposal_from_row(row) for row in rows]


def count_jobs_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM backend_jobs GROUP BY status ORDER BY status ASC"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def count_memory_proposals_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM memory_proposals GROUP BY status ORDER BY status ASC"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def record_sync_state(conn: sqlite3.Connection, key: str, value: dict[str, Any]) -> None:
    record_sync_states(conn, {key: value})


def record_sync_states(
    conn: sqlite3.Connection, values: dict[str, dict[str, Any]]
) -> None:
    """Persist a related set of sync identities in one SQLite transaction."""

    updated_at = _now()
    with write_txn(conn):
        conn.executemany(
            "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [
                (key, _json_dumps(value), updated_at)
                for key, value in sorted(values.items())
            ],
        )


def clear_sync_state(conn: sqlite3.Connection, key: str) -> None:
    with write_txn(conn):
        conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))


def get_sync_state(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    value = _json_loads(row["value"])
    return value if isinstance(value, dict) else {}


def get_sync_state_updated_at(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT updated_at FROM sync_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return int(row["updated_at"])


def cleanup_orphaned_memory_cache(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 90,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    rows = conn.execute(
        """
        SELECT mc.workspace_binding_id, mc.items, mc.updated_at
        FROM memory_cache mc
        LEFT JOIN workspace_bindings wb
          ON wb.backend_workspace_binding_id = mc.workspace_binding_id
         AND wb.status = 'linked'
        WHERE wb.backend_workspace_binding_id IS NULL
        ORDER BY mc.updated_at ASC
        """
    ).fetchall()
    candidates = len(rows)
    cutoff = current - (max(0, int(retention_days)) * 86400)
    remove_ids: list[str] = []
    total_bytes = 0
    for row in rows:
        if include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff:
            remove_ids.append(str(row["workspace_binding_id"]))
            total_bytes += len(str(row["items"]).encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for workspace_binding_id in remove_ids:
                conn.execute(
                    "DELETE FROM memory_cache WHERE workspace_binding_id = ?",
                    (workspace_binding_id,),
                )
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": candidates,
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
    }


def cleanup_terminal_backend_jobs(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 30,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    placeholders = ",".join("?" for _ in TERMINAL_BACKEND_JOB_STATUSES)
    rows = conn.execute(
        f"""
        SELECT job_id, status, payload, result, updated_at
        FROM backend_jobs
        WHERE status IN ({placeholders})
        ORDER BY updated_at ASC
        """,
        list(TERMINAL_BACKEND_JOB_STATUSES),
    ).fetchall()
    remove_ids: list[str] = []
    by_status: dict[str, int] = {}
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["job_id"]))
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        total_bytes += len(str(row["payload"]).encode("utf-8"))
        total_bytes += len(str(row["result"] or "").encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for job_id in remove_ids:
                conn.execute("DELETE FROM backend_jobs WHERE job_id = ?", (job_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        **{f"status_{status}": count for status, count in sorted(by_status.items())},
    }


def cleanup_terminal_plugin_work_items(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 30,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    placeholders = ",".join("?" for _ in TERMINAL_PLUGIN_WORK_ITEM_STATUSES)
    rows = conn.execute(
        f"""
        SELECT work_item_id, status, payload, result, updated_at
        FROM plugin_work_items
        WHERE status IN ({placeholders})
        ORDER BY updated_at ASC
        """,
        list(TERMINAL_PLUGIN_WORK_ITEM_STATUSES),
    ).fetchall()
    remove_ids: list[str] = []
    by_status: dict[str, int] = {}
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["work_item_id"]))
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        total_bytes += len(str(row["payload"]).encode("utf-8"))
        total_bytes += len(str(row["result"] or "").encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for work_item_id in remove_ids:
                conn.execute("DELETE FROM plugin_work_items WHERE work_item_id = ?", (work_item_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        **{f"status_{status}": count for status, count in sorted(by_status.items())},
    }


def cleanup_reviewed_memory_proposals(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 90,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    placeholders = ",".join("?" for _ in REVIEWED_MEMORY_PROPOSAL_STATUSES)
    rows = conn.execute(
        f"""
        SELECT id, status, summary, provenance, reason, updated_at
        FROM memory_proposals
        WHERE status IN ({placeholders})
        ORDER BY updated_at ASC
        """,
        list(REVIEWED_MEMORY_PROPOSAL_STATUSES),
    ).fetchall()
    remove_ids: list[str] = []
    by_status: dict[str, int] = {}
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["id"]))
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        total_bytes += len(str(row["summary"]).encode("utf-8"))
        total_bytes += len(str(row["provenance"]).encode("utf-8"))
        total_bytes += len(str(row["reason"] or "").encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for proposal_id in remove_ids:
                conn.execute("DELETE FROM memory_proposals WHERE id = ?", (proposal_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        **{f"status_{status}": count for status, count in sorted(by_status.items())},
    }


def cleanup_inbox_events(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 30,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    rows = conn.execute(
        """
        SELECT event_id, payload, received_at, read_at
        FROM inbox_events
        ORDER BY received_at ASC
        """
    ).fetchall()
    remove_ids: list[str] = []
    unread = 0
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["received_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["event_id"]))
        if row["read_at"] is None:
            unread += 1
        total_bytes += len(str(row["payload"]).encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for event_id in remove_ids:
                conn.execute("DELETE FROM inbox_events WHERE event_id = ?", (event_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        "unread": unread,
    }


def replace_memory_cache(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    workspace_binding_id: str,
    version: str,
    items: list[dict[str, Any]],
) -> MemoryCache:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO memory_cache (workspace_binding_id, project_id, version, items, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace_binding_id) DO UPDATE SET "
            "project_id = excluded.project_id, version = excluded.version, "
            "items = excluded.items, updated_at = excluded.updated_at",
            (workspace_binding_id, project_id, version, _json_dumps(items), now),
        )
    loaded = get_memory_cache(conn, workspace_binding_id)
    assert loaded is not None
    return loaded


def get_memory_cache(conn: sqlite3.Connection, workspace_binding_id: str) -> MemoryCache | None:
    row = conn.execute(
        "SELECT * FROM memory_cache WHERE workspace_binding_id = ?",
        (workspace_binding_id,),
    ).fetchone()
    if row is None:
        return None
    raw_items = _json_loads(row["items"])
    items = raw_items if isinstance(raw_items, list) else []
    return MemoryCache(
        project_id=row["project_id"],
        workspace_binding_id=row["workspace_binding_id"],
        version=row["version"],
        items=[item for item in items if isinstance(item, dict)],
        updated_at=row["updated_at"],
    )


def save_inbox_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    project_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    with write_txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO inbox_events "
            "(event_id, project_id, event_type, payload, received_at) VALUES (?, ?, ?, ?, ?)",
            (event_id, project_id, event_type, _json_dumps(payload), _now()),
        )


def list_inbox_events(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[InboxEvent]:
    if project_id:
        rows = conn.execute(
            "SELECT * FROM inbox_events WHERE project_id = ? ORDER BY received_at ASC",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM inbox_events ORDER BY received_at ASC").fetchall()
    return [_event_from_row(row) for row in rows]


def count_inbox_events(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread FROM inbox_events"
    ).fetchone()
    return {"total": int(row["total"] or 0), "unread": int(row["unread"] or 0)}


def _logbook_outbox_from_row(row: sqlite3.Row) -> LogbookOutboxEntry:
    request = _json_loads(row["request_json"])
    if not isinstance(request, dict):
        raise ValueError(f"logbook outbox entry {row['id']} has invalid request JSON")
    return LogbookOutboxEntry(
        id=int(row["id"]),
        project_id=str(row["project_id"]),
        workspace_binding_id=str(row["workspace_binding_id"]),
        actor_agent_id=(str(row["actor_agent_id"]) if row["actor_agent_id"] else None),
        idempotency_key=str(row["idempotency_key"]),
        request=request,
        request_digest=str(row["request_digest"]),
        state=str(row["state"]),
        lease_token=str(row["lease_token"]) if row["lease_token"] else None,
        lease_expires_at=(int(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None),
        attempts=int(row["attempts"]),
        next_attempt_at=int(row["next_attempt_at"]),
        response_id=str(row["response_id"]) if row["response_id"] else None,
        last_error=str(row["last_error"]) if row["last_error"] else None,
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def enqueue_logbook_outbox_entry(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    workspace_binding_id: str,
    actor_agent_id: str,
    idempotency_key: str,
    request: dict[str, Any],
    now: int | None = None,
) -> LogbookOutboxEntry:
    """Persist one canonical request before any caller may start network I/O."""

    if not isinstance(actor_agent_id, str) or not actor_agent_id or len(actor_agent_id) > 191:
        raise ValueError("logbook actor agent id must be a non-empty bounded string")
    timestamp = _now() if now is None else int(now)
    encoded = _json_dumps(request)
    digest = _logbook_request_digest(request, actor_agent_id)
    with write_txn(conn):
        existing = conn.execute(
            "SELECT * FROM logbook_outbox WHERE project_id = ? AND idempotency_key = ?",
            (project_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            loaded = _logbook_outbox_from_row(existing)
            if loaded.actor_agent_id is None:
                if _logbook_request_digest(loaded.request) != _logbook_request_digest(request):
                    raise ValueError("logbook idempotency key is already bound to a different request")
                if loaded.state == "sent":
                    raise ValueError(
                        "logbook idempotency key has a sent legacy entry without actor identity"
                    )
            elif _logbook_request_digest(loaded.request, loaded.actor_agent_id) != digest:
                raise ValueError("logbook idempotency key is already bound to a different request")
            if loaded.state == "dead_letter":
                conn.execute(
                    "UPDATE logbook_outbox SET workspace_binding_id = ?, actor_agent_id = ?, request_json = ?, "
                    "request_digest = ?, state = 'pending', lease_token = NULL, "
                    "lease_expires_at = NULL, attempts = 0, next_attempt_at = ?, "
                    "response_id = NULL, last_error = NULL, updated_at = ? WHERE id = ?",
                    (
                        workspace_binding_id, actor_agent_id, encoded, digest,
                        timestamp, timestamp, loaded.id,
                    ),
                )
                existing = conn.execute(
                    "SELECT * FROM logbook_outbox WHERE id = ?", (loaded.id,)
                ).fetchone()
                assert existing is not None
                return _logbook_outbox_from_row(existing)
            if loaded.state in {"pending", "leased"} and (
                loaded.workspace_binding_id != workspace_binding_id
                or loaded.actor_agent_id != actor_agent_id
                or loaded.request != request
            ):
                conn.execute(
                    "UPDATE logbook_outbox SET workspace_binding_id = ?, actor_agent_id = ?, request_json = ?, "
                    "request_digest = ?, updated_at = ? WHERE id = ?",
                    (workspace_binding_id, actor_agent_id, encoded, digest, timestamp, loaded.id),
                )
                existing = conn.execute(
                    "SELECT * FROM logbook_outbox WHERE id = ?", (loaded.id,)
                ).fetchone()
                assert existing is not None
                return _logbook_outbox_from_row(existing)
            return loaded
        cursor = conn.execute(
            "INSERT INTO logbook_outbox "
            "(project_id, workspace_binding_id, actor_agent_id, idempotency_key, request_json, request_digest, state, "
            "attempts, next_attempt_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (
                project_id,
                workspace_binding_id,
                actor_agent_id,
                idempotency_key,
                encoded,
                digest,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM logbook_outbox WHERE id = ?", (cursor.lastrowid,)).fetchone()
    assert row is not None
    return _logbook_outbox_from_row(row)


def get_logbook_outbox_entry(conn: sqlite3.Connection, entry_id: int) -> LogbookOutboxEntry | None:
    row = conn.execute("SELECT * FROM logbook_outbox WHERE id = ?", (int(entry_id),)).fetchone()
    return _logbook_outbox_from_row(row) if row is not None else None


def list_logbook_outbox_entries(
    conn: sqlite3.Connection, *, states: Iterable[str] | None = None
) -> list[LogbookOutboxEntry]:
    values = tuple(str(state) for state in states or ())
    if values:
        placeholders = ", ".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM logbook_outbox WHERE state IN ({placeholders}) ORDER BY id ASC", values
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM logbook_outbox ORDER BY id ASC").fetchall()
    return [_logbook_outbox_from_row(row) for row in rows]


def summarize_logbook_outbox_entries(
    conn: sqlite3.Connection,
    *,
    actor_scopes: Iterable[tuple[str, str]],
    legacy_binding_scopes: Iterable[tuple[str, str]] = (),
) -> dict[str, int]:
    """Count durable obligations without loading the append history into memory."""

    clauses: list[str] = []
    parameters: list[str] = []
    for project_id, actor_agent_id in sorted(set(actor_scopes)):
        clauses.append("(project_id = ? AND actor_agent_id = ?)")
        parameters.extend((project_id, actor_agent_id))
    for project_id, workspace_binding_id in sorted(set(legacy_binding_scopes)):
        clauses.append(
            "(project_id = ? AND actor_agent_id IS NULL AND workspace_binding_id = ?)"
        )
        parameters.extend((project_id, workspace_binding_id))
    if not clauses:
        return {
            "logbook_pending": 0,
            "logbook_sent": 0,
            "logbook_retry": 0,
            "logbook_dead_letter": 0,
        }

    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN state IN ('pending', 'leased') THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN state = 'sent' THEN 1 ELSE 0 END) AS sent, "
        "SUM(CASE WHEN state = 'pending' AND last_error IS NOT NULL THEN 1 ELSE 0 END) AS retry, "
        "SUM(CASE WHEN state = 'dead_letter' THEN 1 ELSE 0 END) AS dead_letter "
        f"FROM logbook_outbox WHERE {' OR '.join(clauses)}",
        parameters,
    ).fetchone()
    assert row is not None
    return {
        "logbook_pending": int(row["pending"] or 0),
        "logbook_sent": int(row["sent"] or 0),
        "logbook_retry": int(row["retry"] or 0),
        "logbook_dead_letter": int(row["dead_letter"] or 0),
    }


def lease_due_logbook_outbox_entries(
    conn: sqlite3.Connection,
    *,
    now: int,
    limit: int = 20,
    lease_seconds: int = 60,
    project_id: str | None = None,
    workspace_binding_id: str | None = None,
) -> list[LogbookOutboxEntry]:
    """Atomically lease no more than the bounded number of due entries."""

    timestamp = int(now)
    bounded_limit = max(1, min(20, int(limit)))
    filters: list[str] = []
    parameters: list[Any] = [timestamp, timestamp]
    if project_id:
        filters.append("project_id = ?")
        parameters.append(project_id)
    if workspace_binding_id:
        filters.append("workspace_binding_id = ?")
        parameters.append(workspace_binding_id)
    where = " AND ".join(filters)
    scope = f" AND {where}" if where else ""
    with write_txn(conn):
        rows = conn.execute(
            "SELECT id FROM logbook_outbox WHERE ("
            "(state = 'pending' AND next_attempt_at <= ?) OR "
            "(state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))"
            f"{scope} ORDER BY next_attempt_at ASC, created_at ASC, id ASC LIMIT ?",
            (*parameters, bounded_limit),
        ).fetchall()
        claimed: list[int] = []
        for row in rows:
            entry_id = int(row["id"])
            token = secrets.token_urlsafe(18)
            changed = conn.execute(
                "UPDATE logbook_outbox SET state = 'leased', lease_token = ?, lease_expires_at = ?, "
                "attempts = attempts + 1, updated_at = ? WHERE id = ? AND "
                "(state = 'pending' OR (state = 'leased' AND lease_expires_at <= ?))",
                (token, timestamp + max(1, int(lease_seconds)), timestamp, entry_id, timestamp),
            ).rowcount
            if changed:
                claimed.append(entry_id)
        leased_rows = [
            conn.execute("SELECT * FROM logbook_outbox WHERE id = ?", (entry_id,)).fetchone()
            for entry_id in claimed
        ]
    return [_logbook_outbox_from_row(row) for row in leased_rows if row is not None]


def resolve_logbook_outbox_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: int,
    lease_token: str,
    state: str,
    now: int,
    next_attempt_at: int | None = None,
    response_id: str | None = None,
    last_error: str | None = None,
) -> LogbookOutboxEntry | None:
    if state not in {"pending", "sent", "dead_letter"}:
        raise ValueError("logbook outbox resolution state is invalid")
    timestamp = int(now)
    due_at = timestamp if next_attempt_at is None else int(next_attempt_at)
    with write_txn(conn):
        changed = conn.execute(
            "UPDATE logbook_outbox SET state = ?, lease_token = NULL, lease_expires_at = NULL, "
            "next_attempt_at = ?, response_id = ?, last_error = ?, updated_at = ? "
            "WHERE id = ? AND state = 'leased' AND lease_token = ?",
            (state, due_at, response_id, last_error, timestamp, int(entry_id), lease_token),
        ).rowcount
        row = conn.execute("SELECT * FROM logbook_outbox WHERE id = ?", (int(entry_id),)).fetchone()
    return _logbook_outbox_from_row(row) if changed and row is not None else None

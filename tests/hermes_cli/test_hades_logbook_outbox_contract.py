from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest


def _request(binding_id: str = "binding_1") -> dict[str, object]:
    return {
        "workspace_binding_id": binding_id,
        "event_type": "change",
        "severity": "info",
        "summary": "Recorded once.",
        "idempotency_key": "project-scoped-key-0001",
        "references": [],
        "correlation_id": None,
        "narrative_markdown": None,
        "payload": {},
        "supersedes_entry_id": None,
    }


def _enqueue(conn, *, project: str = "project_1", binding: str = "binding_1", now: int = 1000):
    from hermes_cli import hades_backend_db as db

    return db.enqueue_logbook_outbox_entry(
        conn,
        project_id=project,
        workspace_binding_id=binding,
        idempotency_key="project-scoped-key-0001",
        request=_request(binding),
        now=now,
    )


def _unique_columns(conn: sqlite3.Connection) -> list[list[str]]:
    indexes = conn.execute("PRAGMA index_list(logbook_outbox)").fetchall()
    return [
        [str(column[2]) for column in conn.execute(f"PRAGMA index_info({index[1]})").fetchall()]
        for index in indexes
        if bool(index[2])
    ]


def _create_binding_scoped_legacy_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE logbook_outbox ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, "
        "workspace_binding_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, "
        "request_json TEXT NOT NULL, request_digest TEXT NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ('pending', 'leased', 'sent', 'dead_letter')), "
        "lease_token TEXT, lease_expires_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0, "
        "next_attempt_at INTEGER NOT NULL, response_id TEXT, last_error TEXT, "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
        "UNIQUE(project_id, workspace_binding_id, idempotency_key))"
    )


def test_new_outbox_schema_uses_backend_project_scoped_idempotency(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")

    assert ["project_id", "idempotency_key"] in _unique_columns(conn)
    assert ["project_id", "workspace_binding_id", "idempotency_key"] not in _unique_columns(conn)


def test_same_project_key_and_mutation_reuses_one_row_across_workspace_routes(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")
    first = _enqueue(conn)

    repeated = _enqueue(conn, binding="binding_2")

    rows = db.list_logbook_outbox_entries(conn)
    assert repeated.id == first.id
    assert [row.id for row in rows] == [first.id]
    assert rows[0].workspace_binding_id == "binding_2"
    assert rows[0].request["workspace_binding_id"] == "binding_2"


def test_same_project_key_rejects_a_different_backend_mutation(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")
    _enqueue(conn)
    changed = _request()
    changed["summary"] = "A different mutation."

    with pytest.raises(ValueError, match="already bound to a different request"):
        db.enqueue_logbook_outbox_entry(
            conn,
            project_id="project_1",
            workspace_binding_id="binding_1",
            idempotency_key="project-scoped-key-0001",
            request=changed,
            now=1001,
        )
    assert len(db.list_logbook_outbox_entries(conn)) == 1


def test_same_key_remains_independent_between_projects(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")

    assert _enqueue(conn, project="project_1").id != _enqueue(conn, project="project_2").id


def test_explicit_reenqueue_revives_identical_dead_letter_but_sync_alone_does_not(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")
    original = _enqueue(conn)
    conn.execute(
        "UPDATE logbook_outbox SET state = 'dead_letter', attempts = 5, "
        "lease_token = 'old', lease_expires_at = 9999, response_id = 'stale', "
        "last_error = 'forbidden' WHERE id = ?",
        (original.id,),
    )
    conn.commit()

    assert db.lease_due_logbook_outbox_entries(conn, now=2000) == []

    revived = _enqueue(conn, now=2001)
    assert revived.id == original.id
    assert revived.state == "pending"
    assert revived.attempts == 0
    assert revived.lease_token is None
    assert revived.lease_expires_at is None
    assert revived.response_id is None
    assert revived.last_error is None
    assert revived.next_attempt_at == 2001
    assert [row.id for row in db.lease_due_logbook_outbox_entries(conn, now=2001)] == [original.id]


def test_dead_letter_can_be_rerouted_after_workspace_reregistration(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")
    original = _enqueue(conn)
    conn.execute(
        "UPDATE logbook_outbox SET state = 'dead_letter', attempts = 5, "
        "last_error = 'capability denied' WHERE id = ?",
        (original.id,),
    )
    conn.commit()

    revived = _enqueue(conn, binding="binding_2", now=2001)
    assert revived.id == original.id
    assert revived.state == "pending"
    assert revived.workspace_binding_id == "binding_2"
    assert revived.request["workspace_binding_id"] == "binding_2"
    assert revived.attempts == 0


def test_leased_obligation_preserves_lease_while_updating_future_routing(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")
    original = _enqueue(conn)
    leased = db.lease_due_logbook_outbox_entries(conn, now=1000)[0]

    rerouted = _enqueue(conn, binding="binding_2", now=1001)
    assert rerouted.id == original.id
    assert rerouted.state == "leased"
    assert rerouted.lease_token == leased.lease_token
    assert rerouted.workspace_binding_id == "binding_2"
    assert rerouted.request["workspace_binding_id"] == "binding_2"

    resolved = db.resolve_logbook_outbox_entry(
        conn, entry_id=rerouted.id, lease_token=leased.lease_token or "",
        state="pending", now=1002,
    )
    assert resolved is not None
    assert [row.id for row in db.lease_due_logbook_outbox_entries(
        conn, now=1002, project_id="project_1", workspace_binding_id="binding_2",
    )] == [original.id]


def test_reenqueue_does_not_reopen_sent_or_pending_rows(tmp_path):
    from hermes_cli import hades_backend_db as db

    conn = db.connect(tmp_path / "backend.db")
    pending = _enqueue(conn)
    assert _enqueue(conn, now=2000) == pending

    conn.execute(
        "UPDATE logbook_outbox SET state = 'sent', response_id = 'entry_1' WHERE id = ?",
        (pending.id,),
    )
    conn.commit()
    sent = _enqueue(conn, now=3000)
    assert sent.id == pending.id
    assert sent.state == "sent"
    assert sent.response_id == "entry_1"


def test_binding_scoped_schema_migrates_without_losing_rows_when_unambiguous(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    _create_binding_scoped_legacy_table(legacy)
    request = _request()
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    legacy.execute(
        "INSERT INTO logbook_outbox "
        "(project_id, workspace_binding_id, idempotency_key, request_json, request_digest, "
        "state, attempts, next_attempt_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 0, 1, 1, 1)",
        ("project_1", "binding_1", "project-scoped-key-0001", encoded, digest),
    )
    legacy.commit()
    legacy.close()

    conn = db.connect(path)
    assert ["project_id", "idempotency_key"] in _unique_columns(conn)
    assert len(db.list_logbook_outbox_entries(conn)) == 1


def test_binding_scoped_schema_collision_aborts_before_rewrite(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "collision.db"
    legacy = sqlite3.connect(path)
    _create_binding_scoped_legacy_table(legacy)
    for binding in ("binding_1", "binding_2"):
        request = _request(binding)
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        legacy.execute(
            "INSERT INTO logbook_outbox "
            "(project_id, workspace_binding_id, idempotency_key, request_json, request_digest, "
            "state, attempts, next_attempt_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 0, 1, 1, 1)",
            (
                "project_1",
                binding,
                "project-scoped-key-0001",
                encoded,
                hashlib.sha256(encoded.encode()).hexdigest(),
            ),
        )
    legacy.commit()
    legacy.close()

    with pytest.raises(ValueError, match="project-scoped idempotency collisions"):
        db.connect(path)

    unchanged = sqlite3.connect(path)
    try:
        assert unchanged.execute("SELECT COUNT(*) FROM logbook_outbox").fetchone()[0] == 2
        assert ["project_id", "workspace_binding_id", "idempotency_key"] in _unique_columns(unchanged)
    finally:
        unchanged.close()


def test_branch_era_route_inclusive_request_digest_is_migrated(tmp_path):
    from hermes_cli import hades_backend_db as db

    path = tmp_path / "old-digest.db"
    conn = db.connect(path)
    entry = _enqueue(conn)
    old_digest = hashlib.sha256(
        json.dumps(entry.request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    conn.execute(
        "UPDATE logbook_outbox SET request_digest = ? WHERE id = ?", (old_digest, entry.id)
    )
    conn.commit()
    conn.close()
    db._INITIALIZED_PATHS.discard(str(path.resolve()))

    migrated = db.connect(path)
    row = db.list_logbook_outbox_entries(migrated)[0]
    assert row.request_digest != old_digest
    assert row.request_digest == db._logbook_request_digest(row.request)

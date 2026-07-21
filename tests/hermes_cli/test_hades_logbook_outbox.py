from __future__ import annotations

from types import SimpleNamespace


def _binding():
    return SimpleNamespace(
        project_id="project_1",
        backend_workspace_binding_id="binding_1",
    )


def _command(key: str = "stable-key") -> dict[str, object]:
    return {
        "event_type": "change",
        "summary": "Persisted before the network call.",
        "severity": "info",
        "idempotency_key": key,
        "references": [{"kind": "commit", "id": "abc123"}],
    }


def test_write_persists_before_network_and_replays_once_after_restart(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_logbook_actions import (
        enqueue_logbook_entry,
        flush_due_logbook_entries,
    )

    class RecordingLogbookClient:
        def __init__(self):
            self.idempotency_keys: list[str] = []

        def create_logbook_entry(self, project_id, **payload):
            self.idempotency_keys.append(payload["idempotency_key"])
            assert project_id == "project_1"
            return {"entry": {"id": "entry_1", "idempotency_key": payload["idempotency_key"]}}

    conn = db.connect(tmp_path / "backend.db")
    enqueue_logbook_entry(conn, command=_command(), binding=_binding(), now=999)
    conn.close()

    reopened = db.connect(tmp_path / "backend.db")
    client = RecordingLogbookClient()
    assert flush_due_logbook_entries(reopened, client, now=1000)["sent"] == 1
    assert client.idempotency_keys == ["stable-key"]
    assert flush_due_logbook_entries(reopened, client, now=1001)["sent"] == 0
    assert db.list_logbook_outbox_entries(reopened)[0].state == "sent"


def test_capability_denial_is_visible_dead_letter_not_success(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_logbook_actions import run_logbook_write

    class CapabilityDeniedClient:
        def create_logbook_entry(self, project_id, **payload):
            raise HadesBackendError(
                "forbidden",
                status_code=403,
                code="logbook_capability_not_allowed",
            )

    conn = db.connect(tmp_path / "backend.db")
    result = run_logbook_write(
        conn,
        command=_command(),
        binding=_binding(),
        client=CapabilityDeniedClient(),
        now=1000,
    )
    assert result.exit_code != 0
    assert result.state == "dead_letter"
    assert "re-register" in result.message
    assert db.list_logbook_outbox_entries(conn)[0].state == "dead_letter"


def test_conflict_only_succeeds_when_backend_confirms_same_idempotency_key(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_logbook_actions import enqueue_logbook_entry, flush_due_logbook_entries

    class MatchingConflictClient:
        def create_logbook_entry(self, project_id, **payload):
            raise HadesBackendError(
                "conflict",
                status_code=409,
                details={"existing_entry": {"id": "entry_1", "idempotency_key": payload["idempotency_key"]}},
            )

    conn = db.connect(tmp_path / "backend.db")
    enqueue_logbook_entry(conn, command=_command(), binding=_binding(), now=999)
    result = flush_due_logbook_entries(conn, MatchingConflictClient(), now=1000)
    assert result["sent"] == 1
    assert db.list_logbook_outbox_entries(conn)[0].response_id == "entry_1"


def test_conflict_with_different_idempotency_key_is_dead_letter(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_logbook_actions import enqueue_logbook_entry, flush_due_logbook_entries

    class MismatchedConflictClient:
        def create_logbook_entry(self, project_id, **payload):
            raise HadesBackendError(
                "conflict",
                status_code=409,
                details={"existing_entry": {"id": "entry_1", "idempotency_key": "other-key"}},
            )

    conn = db.connect(tmp_path / "backend.db")
    enqueue_logbook_entry(conn, command=_command(), binding=_binding(), now=999)
    result = flush_due_logbook_entries(conn, MismatchedConflictClient(), now=1000)
    assert result["sent"] == 0
    assert result["dead_letter"] == 1
    assert db.list_logbook_outbox_entries(conn)[0].state == "dead_letter"


def test_immediate_write_flush_is_scoped_to_its_workspace_binding(tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_logbook_actions import enqueue_logbook_entry, run_logbook_write

    class Client:
        def __init__(self):
            self.projects: list[str] = []

        def create_logbook_entry(self, project_id, **payload):
            self.projects.append(project_id)
            return {"entry": {"id": "entry_1", "idempotency_key": payload["idempotency_key"]}}

    foreign = SimpleNamespace(project_id="project_2", backend_workspace_binding_id="binding_2")
    conn = db.connect(tmp_path / "backend.db")
    enqueue_logbook_entry(conn, command=_command("foreign-key"), binding=foreign, now=999)
    client = Client()
    result = run_logbook_write(conn, command=_command("current-key"), binding=_binding(), client=client, now=1000)
    assert result.state == "sent"
    assert client.projects == ["project_1"]
    assert [entry.state for entry in db.list_logbook_outbox_entries(conn)] == ["pending", "sent"]

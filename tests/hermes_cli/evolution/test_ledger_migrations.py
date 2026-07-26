from __future__ import annotations

import os
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

from hermes_cli.evolution import ledger as ledger_module
from hermes_cli.evolution.ledger import EvolutionLedger, EvolutionLedgerError


REQUIRED_TABLES = {
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
    "observation_envelopes",
    "opportunity_suggestions",
    "opportunity_suggestion_events",
    "telos_approval_requests",
    "telos_approval_decisions",
    "telos_approval_grants",
    "telos_approval_consumptions",
    "telos_approval_quarantine_v4",
    "blueprint_documents",
}


def _create_valid_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in ledger_module._SCHEMA_V1_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_version(singleton, version) VALUES (1, 1)"
    )
    connection.execute(
        """
        INSERT INTO attempts(
            attempt_id, source_kind, source_ref, state, created_at
        ) VALUES (
            'attempt-v1', 'manual', 'ticket-v1', 'draft',
            '2026-07-23T10:00:00.000000Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO suggestions(
            suggestion_id, attempt_id, canonical_digest, state, created_at
        ) VALUES (
            'suggestion-v1', 'attempt-v1', ?, 'draft',
            '2026-07-23T10:00:00.000000Z'
        )
        """,
        ("a" * 64,),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def test_valid_v1_database_migrates_atomically_and_preserves_a2_rows(
    tmp_path,
) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v1_database(path)

    ledger = EvolutionLedger(path)

    assert ledger.schema_version == ledger_module.SCHEMA_VERSION
    assert ledger.connection.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = 'attempt-v1'"
    ).fetchone()[0] == "ticket-v1"
    assert ledger.connection.execute(
        "SELECT canonical_digest FROM suggestions WHERE suggestion_id = 'suggestion-v1'"
    ).fetchone()[0] == "a" * 64
    assert {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    } >= REQUIRED_TABLES


def test_v1_placeholder_authorization_rows_fail_closed_without_inventing_authority(
    tmp_path,
) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v1_database(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        INSERT INTO authorization_requests(
            authorization_id, attempt_id, grant_kind, state,
            request_digest, created_at
        ) VALUES (
            'legacy-auth', 'attempt-v1', 'research', 'requested', ?,
            '2026-07-23T10:00:00.000000Z'
        )
        """,
        ("b" * 64,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        EvolutionLedgerError, match="unmigratable_authorization_records"
    ):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    assert check.execute(
        "SELECT authorization_id FROM authorization_requests"
    ).fetchall() == [("legacy-auth",)]
    assert check.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'authorization_decisions'
        """
    ).fetchone() is None
    check.close()


def test_v1_migration_rolls_back_every_schema_change_on_failure(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v1_database(path)
    original = ledger_module._execute_migration_statement
    calls = 0

    def fail_mid_migration(connection, statement):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise sqlite3.OperationalError("injected migration failure")
        return original(connection, statement)

    monkeypatch.setattr(
        ledger_module, "_execute_migration_statement", fail_mid_migration
    )

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    assert check.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'authorization_decisions'
        """
    ).fetchone() is None
    assert check.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = 'attempt-v1'"
    ).fetchone()[0] == "ticket-v1"
    check.close()


def test_two_connections_that_preflight_v1_concurrently_both_open_v2(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v1_database(path)
    preflight_barrier = threading.Barrier(2)
    migration_barrier = threading.Barrier(2)
    original_preflight = ledger_module._preflight_existing
    original_migrate = EvolutionLedger._migrate_v1_to_v2
    outcomes: list[object] = []
    lock = threading.Lock()
    writable_connection_ids: set[int] = set()

    def synchronized_preflight(candidate_path, guard):
        version = original_preflight(candidate_path, guard)
        preflight_barrier.wait(timeout=10)
        return version

    monkeypatch.setattr(
        ledger_module, "_preflight_existing", synchronized_preflight
    )

    def synchronized_migrate(connection):
        with lock:
            writable_connection_ids.add(id(connection))
        migration_barrier.wait(timeout=10)
        return original_migrate(connection)

    monkeypatch.setattr(
        EvolutionLedger,
        "_migrate_v1_to_v2",
        staticmethod(synchronized_migrate),
    )

    def skip_unrelated_wal_transition(_connection, *, db_label):
        assert db_label == "evolution.db"
        return "delete"

    monkeypatch.setattr(
        ledger_module,
        "apply_wal_with_fallback",
        skip_unrelated_wal_transition,
    )

    def open_ledger() -> None:
        try:
            ledger = EvolutionLedger(path)
        except EvolutionLedgerError as exc:
            outcome: object = str(exc)
        else:
            outcome = ledger.schema_version
            ledger.connection.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=open_ledger) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert len(writable_connection_ids) == 2
    assert outcomes == [
        ledger_module.SCHEMA_VERSION,
        ledger_module.SCHEMA_VERSION,
    ]


def test_current_schema_initializes_and_reopens_with_private_storage(tmp_path) -> None:
    path = tmp_path / "private" / "evolution.db"
    ledger = EvolutionLedger(path)

    assert ledger.schema_version == ledger_module.SCHEMA_VERSION
    assert ledger.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert REQUIRED_TABLES <= tables
    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    reopened = EvolutionLedger(path)
    assert reopened.schema_version == ledger_module.SCHEMA_VERSION
    assert reopened.journal_mode in {"wal", "delete"}


def test_future_schema_fails_closed_without_rewriting_database(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute(
        "INSERT INTO schema_version VALUES (?)",
        (ledger_module.SCHEMA_VERSION + 1,),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="unsupported_schema_version"):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == (
        ledger_module.SCHEMA_VERSION + 1
    )


def test_partial_current_schema_is_rejected_without_being_completed(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute(
        "INSERT INTO schema_version VALUES (?)",
        (ledger_module.SCHEMA_VERSION,),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall() == [("schema_version",)]


def test_current_database_missing_immutability_trigger_is_rejected(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(path)
    ledger.connection.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER lifecycle_events_no_update")
    connection.commit()
    connection.close()

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)


def test_existing_schema_without_version_record_is_rejected(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(path)
    ledger.connection.execute("DELETE FROM schema_version")
    ledger.connection.close()

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)


def _close(ledger: EvolutionLedger) -> None:
    ledger.connection.close()


def test_nonempty_unversioned_database_is_rejected_without_adoption(
    tmp_path,
) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE attacker_payload(value TEXT)")
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)
    before = path.read_bytes()

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    assert path.read_bytes() == before
    check = sqlite3.connect(path)
    assert check.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall() == [("attacker_payload",)]
    check.close()


def test_spoofed_tables_and_version_are_rejected(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version(version INTEGER)")
    connection.execute(
        "INSERT INTO schema_version VALUES (?)",
        (ledger_module.SCHEMA_VERSION,),
    )
    for name in REQUIRED_TABLES - {"schema_version"}:
        connection.execute(f'CREATE TABLE "{name}"(payload TEXT)')
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)


def test_semantically_wrong_schema_constraint_is_rejected(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(path)
    _close(ledger)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA writable_schema=ON")
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='attempts'"
    ).fetchone()
    connection.execute(
        "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='attempts'",
        (row[0].replace("attempt_id TEXT NOT NULL PRIMARY KEY", "attempt_id TEXT PRIMARY KEY"),),
    )
    connection.execute("PRAGMA writable_schema=OFF")
    connection.commit()
    connection.close()

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)


def test_noop_immutability_trigger_is_rejected(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(path)
    _close(ledger)
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER lifecycle_events_no_update")
    connection.execute(
        """
        CREATE TRIGGER lifecycle_events_no_update
        BEFORE UPDATE ON lifecycle_events BEGIN SELECT 1; END
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)


def test_multiple_schema_version_rows_are_rejected(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
    connection.executemany(
        "INSERT INTO schema_version VALUES (?)",
        [(ledger_module.SCHEMA_VERSION,), (ledger_module.SCHEMA_VERSION,)],
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)


def test_rejected_database_is_not_changed_to_wal_or_given_sidecars(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE attacker_payload(value TEXT)")
    connection.commit()
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()
    os.chmod(path, 0o600)
    before = path.read_bytes()

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    assert path.read_bytes() == before
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    check.close()


def test_malformed_database_is_rejected_without_mutation(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    malformed = b"SQLite format 3\x00" + b"attacker-controlled" * 8
    path.write_bytes(malformed)
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    assert path.read_bytes() == malformed
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_schema_enforces_singleton_version_domain_keys_and_generation_digests(
    tmp_path,
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    domain_keys = {
        "attempts": "attempt_id",
        "suggestions": "suggestion_id",
        "suggestion_evidence": "evidence_id",
        "blueprints": "blueprint_id",
        "authorization_requests": "request_id",
        "authorization_decisions": "decision_id",
        "authorization_grants": "grant_id",
        "authorization_consumptions": "consumption_id",
        "candidates": "candidate_id",
        "generations": "generation_id",
        "generation_components": "component_id",
        "canary_runs": "canary_run_id",
        "promotion_reports": "promotion_report_id",
    }
    for table, key in domain_keys.items():
        columns = {
            row["name"]: row
            for row in ledger.connection.execute(f'PRAGMA table_info("{table}")')
        }
        assert columns[key]["pk"] == 1
        assert columns[key]["notnull"] == 1

    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute(
            """
            INSERT INTO schema_version(singleton, version)
            VALUES (1, ?)
            """,
            (ledger_module.SCHEMA_VERSION,),
        )
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        ledger.connection.execute(
            """
            INSERT INTO generations(
                generation_id, attempt_id, canonical_digest, state, created_at
            ) VALUES ('short', ?, ?, 'draft', 'now')
            """,
            (attempt_id, "a" * 64),
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_existing_public_directory_is_rejected_without_chmod(tmp_path) -> None:
    parent = tmp_path / "public"
    parent.mkdir(mode=0o755)
    path = parent / "evolution.db"

    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert not path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_symlink_database_is_rejected_without_touching_target(tmp_path) -> None:
    target = tmp_path / "target.db"
    target.write_bytes(b"not sqlite")
    os.chmod(target, 0o600)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "evolution.db"
    path.symlink_to(target)

    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)

    assert target.read_bytes() == b"not sqlite"


def test_file_swap_during_connect_fails_closed(tmp_path, monkeypatch) -> None:
    path = tmp_path / "evolution.db"
    original_connect = sqlite3.connect
    original_inode: int | None = None

    def swapping_connect(database, *args, **kwargs):
        nonlocal original_inode
        original_inode = path.stat().st_ino
        moved = tmp_path / "retained.db"
        path.rename(moved)
        path.write_bytes(b"")
        os.chmod(path, 0o600)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(ledger_module.sqlite3, "connect", swapping_connect)
    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)

    assert original_inode is not None
    assert path.stat().st_ino != original_inode


def test_directory_swap_during_connect_fails_closed(tmp_path, monkeypatch) -> None:
    parent = tmp_path / "private"
    path = parent / "evolution.db"
    original_connect = sqlite3.connect

    def swapping_connect(database, *args, **kwargs):
        moved = tmp_path / "retained"
        parent.rename(moved)
        parent.mkdir(mode=0o700)
        path.write_bytes(b"")
        os.chmod(path, 0o600)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(ledger_module.sqlite3, "connect", swapping_connect)
    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor contract")
def test_file_swap_and_restore_during_connect_fails_closed(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "evolution.db"
    original_connect = sqlite3.connect

    def swapping_connect(database, *args, **kwargs):
        if database == ":memory:":
            return original_connect(database, *args, **kwargs)
        retained = tmp_path / "retained.db"
        path.rename(retained)
        path.write_bytes(b"")
        os.chmod(path, 0o600)
        connection = original_connect(database, *args, **kwargs)
        path.rename(tmp_path / "attacker.db")
        retained.rename(path)
        return connection

    monkeypatch.setattr(ledger_module.sqlite3, "connect", swapping_connect)
    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)


def test_portable_fallback_without_dir_fd_or_descriptor_introspection(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "evolution.db"
    original_open = os.open

    def open_without_dir_fd(name, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None:
            raise NotImplementedError("dir_fd unavailable")
        return original_open(name, flags, mode)

    monkeypatch.setattr(ledger_module.os, "open", open_without_dir_fd)
    monkeypatch.setattr(ledger_module, "_open_file_descriptors", lambda: None)

    ledger = EvolutionLedger(path)

    assert ledger.schema_version == ledger_module.SCHEMA_VERSION
    assert ledger.verify_chain() == []


def test_portable_fallback_still_rejects_static_symlink_without_nofollow(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "target.db"
    target.write_bytes(b"not sqlite")
    os.chmod(target, 0o600)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "evolution.db"
    path.symlink_to(target)
    original_open = os.open
    opened_static_symlink = False

    def open_without_dir_fd(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal opened_static_symlink
        if dir_fd is not None:
            raise NotImplementedError("dir_fd unavailable")
        if Path(name) == path:
            opened_static_symlink = True
        return original_open(name, flags, mode)

    monkeypatch.setattr(ledger_module.os, "open", open_without_dir_fd)
    monkeypatch.delattr(ledger_module.os, "O_NOFOLLOW", raising=False)
    monkeypatch.setattr(ledger_module, "_open_file_descriptors", lambda: None)

    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)

    assert not opened_static_symlink
    assert target.read_bytes() == b"not sqlite"
    assert not Path(f"{target}-wal").exists()
    assert not Path(f"{target}-shm").exists()


def test_unavailable_platform_open_is_a_bounded_error(tmp_path, monkeypatch) -> None:
    path = tmp_path / "evolution.db"

    def unavailable_open(*_args, **_kwargs):
        raise NotImplementedError("platform open unavailable")

    monkeypatch.setattr(ledger_module.os, "open", unavailable_open)
    monkeypatch.setattr(ledger_module, "_open_file_descriptors", lambda: None)

    with pytest.raises(EvolutionLedgerError, match="unsafe_ledger_path"):
        EvolutionLedger(path)

    assert not path.exists()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_schema_initialization_is_atomic_on_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "evolution.db"
    original_execute = ledger_module._execute_schema_statement
    calls = 0

    def fail_mid_schema(connection, statement):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise sqlite3.OperationalError("injected")
        return original_execute(connection, statement)

    monkeypatch.setattr(ledger_module, "_execute_schema_statement", fail_mid_schema)
    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    assert path.stat().st_size == 0
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def _create_valid_v2_database(path: Path) -> None:
    """Create a v2 database with complete Project A rows."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in ledger_module._SCHEMA_V2_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_version(singleton, version) VALUES (1, 2)"
    )
    now = "2026-07-24T00:00:00.000000Z"
    connection.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?)",
        ("attempt-1", "manual", "ticket-1", "draft", now),
    )
    connection.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?)",
        ("attempt-2", "manual", "ticket-2", "draft", now),
    )
    connection.execute(
        "INSERT INTO suggestions VALUES (?,?,?,?,?)",
        ("sug-1", "attempt-1", "a" * 64, "draft", now),
    )
    connection.execute(
        "INSERT INTO suggestions VALUES (?,?,?,?,?)",
        ("sug-2", "attempt-2", "b" * 64, "draft", now),
    )
    connection.execute(
        "INSERT INTO suggestion_evidence VALUES (?,?,?,?,?)",
        ("evidence-1", "sug-1", "c" * 64, "ref-1", now),
    )
    connection.execute(
        "INSERT INTO suggestion_evidence VALUES (?,?,?,?,?)",
        ("evidence-2", "sug-2", "d" * 64, "ref-2", now),
    )
    connection.execute(
        "INSERT INTO blueprints VALUES (?,?,?,?,?)",
        ("blueprint-1", "attempt-1", "e" * 64, "draft", now),
    )
    connection.execute(
        "INSERT INTO candidates VALUES (?,?,?,?,?)",
        ("candidate-1", "attempt-1", "draft", "f" * 64, now),
    )
    connection.execute(
        "INSERT INTO generations VALUES (?,?,?,?,?)",
        ("g" * 64, "attempt-1", "g" * 64, "draft", now),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def test_v2_to_v3_migration_preserves_all_project_a_rows(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v2_database(path)

    before = {}
    for table in (
        "attempts", "suggestions", "suggestion_evidence", "blueprints",
        "candidates", "generations",
    ):
        conn = sqlite3.connect(path)
        rows = conn.execute(
            f'SELECT * FROM "{table}" ORDER BY 1'
        ).fetchall()
        conn.close()
        before[table] = rows

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION

    for table, expected_rows in before.items():
        after = [
            tuple(row)
            for row in ledger.connection.execute(
                f'SELECT * FROM "{table}" ORDER BY 1'
            )
        ]
        assert after == [tuple(r) for r in expected_rows], f"table {table} changed during v2->v3 migration"


def test_v2_to_v3_adds_project_b_tables(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v2_database(path)

    ledger = EvolutionLedger(path)

    for table in ("observation_envelopes", "opportunity_suggestions", "opportunity_suggestion_events"):
        assert ledger.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone() is not None

    columns = {
        row["name"]
        for row in ledger.connection.execute('PRAGMA table_info("suggestions")')
    }
    assert columns == {"suggestion_id", "attempt_id", "canonical_digest", "state", "created_at"}


def test_v3_reopen_is_idempotent(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    EvolutionLedger(path).connection.close()

    for _ in range(3):
        ledger = EvolutionLedger(path)
        assert ledger.schema_version == ledger_module.SCHEMA_VERSION
        tables = {
            row[0]
            for row in ledger.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert tables >= REQUIRED_TABLES
        ledger.connection.close()


def test_v2_to_v3_rollback_on_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v2_database(path)

    original = ledger_module._execute_migration_statement
    calls = [0]

    def fail_after_first_additive(connection, statement):
        calls[0] += 1
        if calls[0] == 2:
            raise sqlite3.OperationalError("injected rollback")
        return original(connection, statement)

    monkeypatch.setattr(
        ledger_module, "_execute_migration_statement", fail_after_first_additive
    )

    with pytest.raises(EvolutionLedgerError):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    assert check.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observation_envelopes'"
    ).fetchone() is None
    assert check.execute(
        "SELECT canonical_digest FROM suggestions WHERE suggestion_id = 'sug-1'"
    ).fetchone()[0] == "a" * 64
    check.close()


def test_suggestions_and_opportunity_suggestions_coexist(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(path)

    now = "2026-07-24T00:00:00.000000Z"
    with ledger.transaction() as conn:
        conn.execute(
            "INSERT INTO attempts VALUES (?, ?, ?, ?, ?)",
            ("attempt-1", "manual", "ticket-1", "draft", now),
        )
        conn.execute(
            "INSERT INTO suggestions VALUES (?, ?, ?, ?, ?)",
            ("pa-sug", "attempt-1", "z" * 64, "draft", now),
        )
        conn.execute(
            "INSERT INTO opportunity_suggestions(suggestion_id, opportunity_key, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pb-sug", "y" * 64, "observing", now, now),
        )

    assert ledger.connection.execute(
        "SELECT canonical_digest FROM suggestions WHERE suggestion_id = ?", ("pa-sug",)
    ).fetchone()[0] == "z" * 64

    assert ledger.connection.execute(
        "SELECT opportunity_key FROM opportunity_suggestions WHERE suggestion_id = ?", ("pb-sug",)
    ).fetchone()[0] == "y" * 64


def test_future_schema_v5_fails_closed(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute(
        "INSERT INTO schema_version VALUES (?)",
        (ledger_module.SCHEMA_VERSION + 1,),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="unsupported_schema_version"):
        EvolutionLedger(path)


# ── v4→v5 migration chain ──────────────────────────────────────────────────

def _create_valid_v4_database(path: Path) -> None:
    """Create a complete v4 database with Project A + Project B + Telos rows."""
    from hermes_cli.evolution import ledger as _ledger
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in _ledger._SCHEMA_V4_STATEMENTS:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_version VALUES (1, 4)")
    connection.execute(
        "INSERT INTO attempts VALUES ('att-v4', 'manual', 'ticket-v4', 'draft', '2026-07-24T00:00:00.000000Z')"
    )
    connection.execute(
        "INSERT INTO suggestions VALUES ('sug-v4', 'att-v4', ?, 'draft', '2026-07-24T00:00:00.000000Z')",
        ("v" * 64,),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def test_v1_to_v2_to_v3_to_v4_to_v5_to_v6_full_chain(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v1_database(path)
    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6
    assert ledger.connection.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = 'attempt-v1'"
    ).fetchone()[0] == "ticket-v1"
    tables = {row[0] for row in ledger.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}
    assert "observation_envelopes" in tables
    assert "telos_approval_requests" in tables
    assert "telos_approval_quarantine_v4" in tables


def test_v4_to_v5_migration_preserves_v4_rows_byte_for_byte(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v4_database(path)

    conn = sqlite3.connect(path)
    before = {
        "attempts": conn.execute("SELECT * FROM attempts ORDER BY 1").fetchall(),
        "suggestions": conn.execute("SELECT * FROM suggestions ORDER BY 1").fetchall(),
    }
    conn.close()

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6

    for table in ("attempts", "suggestions"):
        after = ledger.connection.execute(
            f'SELECT * FROM "{table}" ORDER BY 1'
        ).fetchall()
        assert [tuple(r) for r in after] == [tuple(r) for r in before[table]]


def test_v4_to_v5_migration_idempotent_reopen(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v4_database(path)
    l1 = EvolutionLedger(path)
    assert l1.schema_version == ledger_module.SCHEMA_VERSION == 6
    l1.connection.close()
    for _ in range(2):
        ledger = EvolutionLedger(path)
        assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6
        ledger.connection.close()


def test_v4_to_v5_rollback_on_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v4_database(path)

    original = ledger_module._execute_migration_statement
    calls = [0]

    def fail_after_first_additive(connection, statement):
        calls[0] += 1
        if calls[0] == 2:
            raise sqlite3.OperationalError("injected rollback")
        return original(connection, statement)

    monkeypatch.setattr(
        ledger_module, "_execute_migration_statement", fail_after_first_additive
    )

    with pytest.raises(EvolutionLedgerError):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 4
    assert check.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'telos_approval_quarantine_v4'"
    ).fetchone() is None
    assert check.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = 'att-v4'"
    ).fetchone()[0] == "ticket-v4"
    check.close()


# ── v4→v5 migration — quarantine + view coherence ──────────────────────────

def test_v4_to_v5_cross_wired_quarantined_and_excluded_from_view(tmp_path):
    """Cross-wired v4 history is preserved and quarantined but never in telos_valid_approval_chains."""
    from hermes_cli.evolution import ledger as _ledger
    path = tmp_path / "evolution.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    d = "a" * 64
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        ("req-A", "org-A", d, "activate", "ctx-A", "n1", "summary-A",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        ("req-B", "org-B", d, "activate", "ctx-B", "n2", "summary-B",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
        ("dec-B", "req-B", "approved", "cli", "actor", "ctx-B",
         "2026-01-01T00:00:00.000000Z"),
    )
    # Cross-wired: grant for req-A but decision for req-B
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-B", "org-A", d, "activate",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
        ("c1", "g1", "org-A", d, "activate", "2026-06-01T00:00:00.000000Z"),
    )
    conn.commit()
    conn.close()
    path.chmod(0o600)

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6

    # Original audit rows preserved
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_requests"
    ).fetchone()[0] == 2
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_grants"
    ).fetchone()[0] == 1
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_consumptions"
    ).fetchone()[0] == 1

    # Quarantine has the cross-wired grant
    q = ledger.connection.execute(
        "SELECT table_name, row_id, reason FROM telos_approval_quarantine_v4"
    ).fetchall()
    assert any(
        r["reason"] == "cross_wired_decision_request"
        and r["table_name"] == "telos_approval_grants"
        and r["row_id"] == "g1"
        for r in q
    )

    # View must be empty — no valid chain
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_valid_approval_chains"
    ).fetchone()[0] == 0


def test_v4_to_v5_wrong_context_quarantined_and_excluded_from_view(tmp_path):
    """Wrong-context v4 history preserved and quarantined but never in the view."""
    from hermes_cli.evolution import ledger as _ledger
    path = tmp_path / "evolution.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    d = "a" * 64
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        ("req-A", "org-A", d, "activate", "expected-ctx", "n1", "summary",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    # Decision has wrong host_context_digest (mismatches expected-ctx)
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
        ("dec-A", "req-A", "approved", "cli", "actor", "wrong-ctx",
         "2026-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-A", "org-A", d, "activate",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
        ("c1", "g1", "org-A", d, "activate", "2026-06-01T00:00:00.000000Z"),
    )
    conn.commit()
    conn.close()
    path.chmod(0o600)

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6

    # Original rows preserved
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_requests"
    ).fetchone()[0] == 1
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_decisions"
    ).fetchone()[0] == 1
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_consumptions"
    ).fetchone()[0] == 1

    # Quarantine has the wrong-context decision
    q = ledger.connection.execute(
        "SELECT table_name, row_id, reason FROM telos_approval_quarantine_v4"
    ).fetchall()
    assert any(
        r["reason"] == "wrong_host_context"
        and r["table_name"] == "telos_approval_decisions"
        and r["row_id"] == "dec-A"
        for r in q
    )

    # View must be empty — context mismatch makes chain invalid
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_valid_approval_chains"
    ).fetchone()[0] == 0


def _create_valid_v5_database(path: Path) -> None:
    """Create a complete v5 database with attempts and blueprints rows."""
    from hermes_cli.evolution import ledger as _ledger
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in _ledger._SCHEMA_V5_STATEMENTS:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_version VALUES (1, 5)")
    connection.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?)", (
            "attempt-v5", "manual", "ticket-v5", "draft",
            "2026-07-25T00:00:00.000000Z",
        )
    )
    connection.execute(
        "INSERT INTO blueprints VALUES (?,?,?,?,?)", (
            "blueprint-v5", "attempt-v5", "a" * 64, "draft",
            "2026-07-25T00:00:00.000000Z",
        )
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


# ── v5→v6 migration chain ──────────────────────────────────────────────────


def test_v5_to_v6_migration_preserves_all_preexisting_rows(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v5_database(path)

    conn = sqlite3.connect(path)
    before = {}
    for table in ledger_module._TABLES_V5:
        if table == "schema_version":
            continue
        before[table] = conn.execute(
            f'SELECT * FROM "{table}" ORDER BY 1'
        ).fetchall()
    conn.close()

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6

    for table, expected_rows in before.items():
        after = ledger.connection.execute(
            f'SELECT * FROM "{table}" ORDER BY 1'
        ).fetchall()
        assert [tuple(r) for r in after] == [tuple(r) for r in expected_rows]


def test_v5_to_v6_adds_immutable_blueprint_documents_table(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v5_database(path)

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6

    now = "2026-07-25T00:00:00.000000Z"
    ledger.connection.execute(
        "INSERT INTO blueprint_documents VALUES (?,?,?,?,?,?)", (
            "blueprint-v5", "attempt-v5", "sug-v5", "a" * 64,
            '{"key": "value"}', now,
        )
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable_blueprint_document"):
        ledger.connection.execute(
            "UPDATE blueprint_documents SET suggestion_id = 'other' WHERE blueprint_id = ?",
            ("blueprint-v5",),
        )

    with pytest.raises(sqlite3.IntegrityError, match="immutable_blueprint_document"):
        ledger.connection.execute(
            "DELETE FROM blueprint_documents WHERE blueprint_id = ?",
            ("blueprint-v5",),
        )


def test_v5_to_v6_rolls_back_all_schema_changes_on_injected_failure(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "evolution.db"
    _create_valid_v5_database(path)

    original = ledger_module._execute_migration_statement
    calls = [0]

    def fail_after_first_additive(connection, statement):
        calls[0] += 1
        if calls[0] == 2:
            raise sqlite3.OperationalError("injected rollback")
        return original(connection, statement)

    monkeypatch.setattr(
        ledger_module, "_execute_migration_statement", fail_after_first_additive
    )

    with pytest.raises(EvolutionLedgerError):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 5
    assert check.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'blueprint_documents'"
    ).fetchone() is None
    assert check.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = 'attempt-v5'"
    ).fetchone()[0] == "ticket-v5"
    check.close()


def test_v4_to_v5_coherent_v4_survives_in_view(tmp_path):
    """Coherent v4 history survives migration and appears in telos_valid_approval_chains."""
    from hermes_cli.evolution import ledger as _ledger
    path = tmp_path / "evolution.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    d = "a" * 64
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        ("req-A", "org-A", d, "activate", "ctx-match", "n1", "summary",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
        ("dec-A", "req-A", "approved", "cli", "actor", "ctx-match",
         "2026-06-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-A", "org-A", d, "activate",
         "2026-06-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
        ("c1", "g1", "org-A", d, "activate", "2026-07-01T00:00:00.000000Z"),
    )
    conn.commit()
    conn.close()
    path.chmod(0o600)

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == ledger_module.SCHEMA_VERSION == 6

    # No quarantined rows
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_quarantine_v4"
    ).fetchone()[0] == 0

    # View has exactly one valid chain
    rows = ledger.connection.execute(
        "SELECT * FROM telos_valid_approval_chains"
    ).fetchall()
    assert len(rows) == 1, f"Expected 1 chain in view, got {len(rows)}"
    row = rows[0]
    assert row["request_id"] == "req-A"
    assert row["decision_id"] == "dec-A"
    assert row["grant_id"] == "g1"
    assert row["consumption_id"] == "c1"
    assert row["organism_id"] == "org-A"
    assert row["telos_digest"] == d
    assert row["action"] == "activate"
    assert row["expected_host_context_digest"] == "ctx-match"
    assert row["host_context_digest"] == "ctx-match"

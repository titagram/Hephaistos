from __future__ import annotations

import os
import sqlite3
import stat
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
    "authorization_grants",
    "candidates",
    "generations",
    "generation_components",
    "canary_runs",
    "promotion_reports",
    "lifecycle_events",
}


def test_schema_v1_initializes_and_reopens_with_private_storage(tmp_path) -> None:
    path = tmp_path / "private" / "evolution.db"
    ledger = EvolutionLedger(path)

    assert ledger.schema_version == 1
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
    assert reopened.schema_version == 1
    assert reopened.journal_mode in {"wal", "delete"}


def test_future_schema_fails_closed_without_rewriting_database(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version VALUES (2)")
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="unsupported_schema_version"):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute("SELECT version FROM schema_version").fetchone()[0] == 2


def test_partial_v1_schema_is_rejected_without_being_completed(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version VALUES (1)")
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)

    with pytest.raises(EvolutionLedgerError, match="invalid_ledger_database"):
        EvolutionLedger(path)

    check = sqlite3.connect(path)
    assert check.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall() == [("schema_version",)]


def test_v1_database_missing_immutability_trigger_is_rejected(tmp_path) -> None:
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
    connection.execute("INSERT INTO schema_version VALUES (1)")
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
    connection.executemany("INSERT INTO schema_version VALUES (?)", [(1,), (1,)])
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
        "authorization_requests": "authorization_id",
        "authorization_grants": "authorization_id",
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
            "INSERT INTO schema_version(singleton, version) VALUES (1, 1)"
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

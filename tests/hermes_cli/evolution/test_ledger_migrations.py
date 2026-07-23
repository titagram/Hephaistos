from __future__ import annotations

import os
import sqlite3
import stat

import pytest

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

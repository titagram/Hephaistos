"""Behavioral tests for migration v3→v4 with literal version targets and Telos tables."""

import pytest
import sqlite3
from pathlib import Path


def _create_valid_v3_database(path: Path) -> None:
    """Create a complete v3 database using the v3 schema statements."""
    from hermes_cli.evolution import ledger as _ledger

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for statement in _ledger._SCHEMA_V3_STATEMENTS:
        conn.execute(statement)
    conn.execute("INSERT INTO schema_version VALUES (1, 3)")
    conn.commit()
    conn.close()
    path.chmod(0o600)


def test_v3_to_v4_migration_adds_telos_tables_and_triggers(tmp_path):
    from hermes_cli.evolution.ledger import EvolutionLedger, SCHEMA_VERSION

    path = tmp_path / "evolution.db"
    _create_valid_v3_database(path)
    ledger = EvolutionLedger(path)
    assert ledger.schema_version == SCHEMA_VERSION == 6

    tables = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "telos_approval_requests" in tables
    assert "telos_approval_decisions" in tables
    assert "telos_approval_grants" in tables
    assert "telos_approval_consumptions" in tables
    assert "telos_approval_quarantine_v4" in tables

    triggers = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert "trg_telos_requests_no_update" in triggers
    assert "trg_telos_decisions_no_update" in triggers
    assert "trg_telos_grants_no_update" in triggers
    assert "trg_telos_consumptions_no_update" in triggers
    assert "trg_telos_requests_no_delete" in triggers
    assert "trg_telos_decisions_no_delete" in triggers
    assert "trg_telos_grants_no_delete" in triggers
    assert "trg_telos_consumptions_no_delete" in triggers
    assert "trg_telos_grant_requires_approved_decision" in triggers
    assert "trg_telos_v5_context_mismatch" in triggers
    assert "trg_telos_v5_decision_request_mismatch" in triggers
    assert "trg_telos_quarantine_no_update" in triggers
    assert "trg_telos_quarantine_no_delete" in triggers

    views = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
    }
    assert "telos_valid_approval_chains" in views
    ledger.connection.close()


def test_v3_to_v4_preserves_v3_rows(tmp_path):
    from hermes_cli.evolution.ledger import EvolutionLedger, SCHEMA_VERSION

    path = tmp_path / "evolution.db"
    _create_valid_v3_database(path)

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == SCHEMA_VERSION == 6
    # Verify the migrated database has all v4 tables
    tables = {
        row[0]
        for row in ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "generations" in tables
    assert "observation_envelopes" in tables
    assert "opportunity_suggestions" in tables
    ledger.connection.close()


def test_migration_v1_to_v2_uses_literal_target():
    """_migrate_v1_to_v2 must INSERT literal (1, 2), not SCHEMA_VERSION."""
    # The code already uses literal (2,) at line ~1389
    from hermes_cli.evolution import ledger as _ledger
    import inspect

    source = inspect.getsource(_ledger.EvolutionLedger._migrate_v1_to_v2)
    assert "(2,)" in source or "(2, )" in source


def test_telos_grant_refuses_denied_decision(tmp_path):
    from hermes_cli.evolution.ledger import EvolutionLedger
    import sqlite3 as _sq

    path = tmp_path / "evolution.db"
    _create_valid_v3_database(path)
    ledger = EvolutionLedger(path)
    ledger.connection.execute(
        "INSERT INTO telos_approval_requests VALUES ("
        "'req-1','org-1','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',"
        "'activate','ctx','1234','test',"
        "'2026-01-01T00:00:00.000000Z','2027-01-01T00:00:00.000000Z')"
    )
    ledger.connection.execute(
        "INSERT INTO telos_approval_decisions VALUES ("
        "'dec-1','req-1','denied','cli','actor','ctx',"
        "'2026-01-01T00:00:00.000000Z')"
    )
    with pytest.raises(_sq.IntegrityError):
        ledger.connection.execute(
            "INSERT INTO telos_approval_grants VALUES ("
            "'grt-1','req-1','dec-1','org-1',"
            "'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',"
            "'activate','2026-01-01T00:00:00.000000Z','2027-01-01T00:00:00.000000Z')"
        )
    ledger.connection.close()


def test_telos_request_immutable_no_update(tmp_path):
    from hermes_cli.evolution.ledger import EvolutionLedger
    import sqlite3 as _sq

    path = tmp_path / "evolution.db"
    _create_valid_v3_database(path)
    ledger = EvolutionLedger(path)
    ledger.connection.execute(
        "INSERT INTO telos_approval_requests VALUES ("
        "'req-1','org-1','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',"
        "'activate','ctx','1234','test',"
        "'2026-01-01T00:00:00.000000Z','2027-01-01T00:00:00.000000Z')"
    )
    with pytest.raises(_sq.IntegrityError):
        ledger.connection.execute(
            "UPDATE telos_approval_requests SET bounded_summary = 'hacked'"
            " WHERE request_id = 'req-1'"
        )
    ledger.connection.close()


def test_telos_consumption_unique_grant(tmp_path):
    from hermes_cli.evolution.ledger import EvolutionLedger
    import sqlite3 as _sq

    path = tmp_path / "evolution.db"
    _create_valid_v3_database(path)
    ledger = EvolutionLedger(path)
    d = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    ledger.connection.execute(
        f"INSERT INTO telos_approval_requests VALUES ("
        f"'req-1','org-1','{d}','activate','ctx','1234','test',"
        f"'2026-01-01T00:00:00.000000Z','2027-01-01T00:00:00.000000Z')"
    )
    ledger.connection.execute(
        "INSERT INTO telos_approval_decisions VALUES ("
        "'dec-1','req-1','approved','cli','actor','ctx',"
        "'2026-01-01T00:00:00.000000Z')"
    )
    ledger.connection.execute(
        f"INSERT INTO telos_approval_grants VALUES ("
        f"'grt-1','req-1','dec-1','org-1','{d}','activate',"
        f"'2026-01-01T00:00:00.000000Z','2027-01-01T00:00:00.000000Z')"
    )
    ledger.connection.execute(
        f"INSERT INTO telos_approval_consumptions VALUES ("
        f"'con-1','grt-1','org-1','{d}','activate','2026-01-01T00:00:00.000000Z')"
    )
    with pytest.raises(_sq.IntegrityError):
        ledger.connection.execute(
            f"INSERT INTO telos_approval_consumptions VALUES ("
            f"'con-2','grt-1','org-1','{d}','activate','2026-01-01T00:00:00.000000Z')"
        )
    ledger.connection.close()

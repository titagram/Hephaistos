"""Test for idempotent, versioned SQLite schema migration of existing Project A evolution.db."""

import sqlite3
from pathlib import Path
import pytest

from hermes_cli.evolution.ledger import EvolutionLedger, SCHEMA_VERSION


def test_fresh_ledger_creates_v6_with_project_a_and_project_b_tables(tmp_path: Path):
    """A fresh EvolutionLedger must be at schema v6 with both Project A and Project B tables."""
    db_path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(db_path)
    assert ledger.schema_version == SCHEMA_VERSION == 6
    tables = {row[0] for row in ledger.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "observation_envelopes" in tables
    assert "telos_approval_requests" in tables
    assert "opportunity_suggestions" in tables
    assert "opportunity_suggestion_events" in tables
    assert "suggestions" in tables  # Project A table still present
    assert "suggestion_evidence" in tables
    assert "telos_approval_quarantine_v4" in tables


def test_project_a_suggestions_still_has_five_column_contract(tmp_path: Path):
    """PRAGMA table_info(suggestions) must return the Project A 5-column contract."""
    db_path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(db_path)
    columns = {
        row["name"]: row
        for row in ledger.connection.execute(
            'PRAGMA table_info("suggestions")'
        )
    }
    assert set(columns) == {
        "suggestion_id", "attempt_id", "canonical_digest", "state", "created_at",
    }
    assert columns["suggestion_id"]["pk"] == 1


def test_project_b_opportunity_suggestions_has_correct_columns(tmp_path: Path):
    """opportunity_suggestions must have the Project B column contract."""
    db_path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(db_path)
    columns = {
        row["name"]
        for row in ledger.connection.execute(
            'PRAGMA table_info("opportunity_suggestions")'
        )
    }
    assert "suggestion_id" in columns
    assert "opportunity_key" in columns
    assert "active_telos_digest" in columns
    assert "score" in columns
    assert "first_observed_at" in columns
    assert "observation_count" in columns


def test_both_suggestion_tables_coexist(tmp_path: Path):
    """Both suggestions (Project A) and opportunity_suggestions (Project B) must coexist."""
    db_path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(db_path)
    with ledger.transaction() as conn:
        conn.execute(
            "INSERT INTO suggestions VALUES (?, ?, ?, ?, ?)",
            ("sug-1", None, "a" * 64, "draft", "2026-07-24T00:00:00.000000Z"),
        )
        conn.execute(
            "INSERT INTO opportunity_suggestions(suggestion_id, opportunity_key, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("opsug-1", "b" * 64, "observing", "2026-07-24T00:00:00.000000Z", "2026-07-24T00:00:00.000000Z"),
        )
    assert ledger.connection.execute(
        "SELECT canonical_digest FROM suggestions WHERE suggestion_id = ?", ("sug-1",)
    ).fetchone()[0] == "a" * 64
    assert ledger.connection.execute(
        "SELECT opportunity_key FROM opportunity_suggestions WHERE suggestion_id = ?", ("opsug-1",)
    ).fetchone()[0] == "b" * 64


# ── v4→v5 migration ────────────────────────────────────────────────────────

def _create_valid_v4_db(path: Path):
    import sqlite3
    from hermes_cli.evolution import ledger as _ledger
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    conn.commit()
    conn.close()
    path.chmod(0o600)


def test_v4_to_v5_migration_preserves_project_a_and_b_tables(tmp_path: Path):
    path = tmp_path / "evolution.db"
    _create_valid_v4_db(path)
    ledger = EvolutionLedger(path)
    assert ledger.schema_version == SCHEMA_VERSION == 6
    tables = {row[0] for row in ledger.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "observation_envelopes" in tables
    assert "telos_approval_requests" in tables
    assert "opportunity_suggestions" in tables
    assert "suggestions" in tables
    assert "telos_approval_quarantine_v4" in tables


def test_v4_to_v5_migration_preserves_attempts_and_suggestions_rows(tmp_path: Path):
    from hermes_cli.evolution import ledger as _ledger
    path = tmp_path / "evolution.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    conn.execute(
        "INSERT INTO attempts VALUES ('att-1', 'manual', 'ticket-1', 'draft', '2026-01-01T00:00:00.000000Z')"
    )
    conn.execute(
        "INSERT INTO suggestions VALUES ('sug-1', 'att-1', ?, 'draft', '2026-01-01T00:00:00.000000Z')",
        ("z" * 64,),
    )
    conn.commit()
    conn.close()
    path.chmod(0o600)

    ledger = EvolutionLedger(path)
    assert ledger.connection.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = 'att-1'"
    ).fetchone()[0] == "ticket-1"
    assert ledger.connection.execute(
        "SELECT canonical_digest FROM suggestions WHERE suggestion_id = 'sug-1'"
    ).fetchone()[0] == "z" * 64


def test_v6_view_and_quarantine_triggers_present(tmp_path: Path) -> None:
    """A fresh v6 ledger must have telos_valid_approval_chains view and quarantine triggers."""
    db_path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(db_path)
    views = {row[0] for row in ledger.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    )}
    assert "telos_valid_approval_chains" in views
    triggers = {row[0] for row in ledger.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    )}
    assert "trg_telos_quarantine_no_update" in triggers
    assert "trg_telos_quarantine_no_delete" in triggers


def test_v5_v4_to_v5_migration_respects_cross_wiring_invariant(tmp_path: Path):
    from hermes_cli.evolution import ledger as _ledger
    path = tmp_path / "evolution.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    d = "a" * 64
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES ('req-A','org-A',?,?,?,?,?,?,?)",
        (d, "activate", "ctx-A", "n1", "s1", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES ('req-B','org-B',?,?,?,?,?,?,?)",
        (d, "activate", "ctx-B", "n2", "s2", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES ('dec-B','req-B','approved','cli','actor','ctx-B','2026-01-01T00:00:00.000000Z')"
    )
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES ('g1','req-A','dec-B','org-A',?,?,?,?)",
        (d, "activate", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.commit()
    conn.close()
    path.chmod(0o600)

    ledger = EvolutionLedger(path)
    assert ledger.schema_version == SCHEMA_VERSION == 6
    q = ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_quarantine_v4"
    ).fetchone()[0]
    assert q >= 1, "cross-wired v4 rows must be quarantined"
    # Also verify the cross-wired chain does NOT appear in the valid view
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_valid_approval_chains"
    ).fetchone()[0] == 0


def test_v4_to_v5_migration_coherent_chain_in_view(tmp_path: Path):
    """A coherent v4 approval chain survives migration and appears in telos_valid_approval_chains."""
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
    assert ledger.schema_version == SCHEMA_VERSION == 6
    rows = ledger.connection.execute(
        "SELECT * FROM telos_valid_approval_chains"
    ).fetchall()
    assert len(rows) == 1, f"Coherent v4 chain must appear in view, got {len(rows)}"
    assert rows[0]["request_id"] == "req-A"
    assert rows[0]["host_context_digest"] == "ctx-match"

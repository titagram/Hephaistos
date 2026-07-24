"""Test for idempotent, versioned SQLite schema migration of existing Project A evolution.db."""

import sqlite3
from pathlib import Path
import pytest

from hermes_cli.evolution.ledger import EvolutionLedger, SCHEMA_VERSION


def test_fresh_ledger_creates_v3_with_project_a_and_project_b_tables(tmp_path: Path):
    """A fresh EvolutionLedger must be at schema v3 with both Project A and Project B tables."""
    db_path = tmp_path / "evolution.db"
    ledger = EvolutionLedger(db_path)
    assert ledger.schema_version == SCHEMA_VERSION == 3

    with ledger.transaction() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "observation_envelopes" in tables
    assert "opportunity_suggestions" in tables
    assert "opportunity_suggestion_events" in tables
    assert "suggestions" in tables  # Project A table still present
    assert "suggestion_evidence" in tables


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

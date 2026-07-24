"""Tests for real Project A migration scanning using EvolutionLedger."""

import sqlite3
from pathlib import Path
import pytest

from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.migration import scan_legacy_profile_states, archive_legacy_provenance
from hermes_constants import get_organism_home


def test_migration_scans_real_project_a_ledger(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    evo_dir = home / "evolution"
    evo_dir.mkdir(mode=0o700)
    db_path = evo_dir / "evolution.db"

    # Initialize a real Project A ledger
    ledger = EvolutionLedger(db_path)
    ledger.connection.close()

    states = scan_legacy_profile_states(home)
    assert len(states) == 1
    st = states[0]
    assert st.has_non_baseline_attempts is False

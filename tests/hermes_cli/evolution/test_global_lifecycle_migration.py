"""Tests for legacy state discovery, migration planning, and global lifecycle init."""

import pytest
import sqlite3
from pathlib import Path

from hermes_cli.evolution import migration_discovery as _md
from hermes_cli.evolution import organism_home as _oh
import hermes_constants as _hc


def _create_v3_baseline_only(path: Path) -> None:
    """Helper: create a minimal v3 DB with one baseline-only generation."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE schema_version (singleton INTEGER PRIMARY KEY, version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES (1, 3)")
    conn.execute(
        "CREATE TABLE generations ("
        "  generation_id TEXT PRIMARY KEY, parent_generation_id TEXT,"
        "  state TEXT NOT NULL, manifest_digest TEXT, created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "INSERT INTO generations VALUES ("
        "'gen-1', NULL, 'active', NULL, '2026-01-01T00:00:00.000000Z')"
    )
    conn.commit()
    conn.close()
    path.chmod(0o600)


# --- 2B.3: Legacy State Discovery ---

def test_discover_legacy_classifies_baseline_only(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    profiles = root / "profiles" / "default"
    (profiles / "evolution").mkdir(parents=True)
    _create_v3_baseline_only(profiles / "evolution" / "evolution.db")

    monkeypatch.setattr(_md, "get_default_hermes_root", lambda: root)
    results = _md.discover_legacy_state()
    assert len(results) == 1
    assert results[0].status == _md.LegacyStatus.COHERENT_BASELINE_ONLY


def test_discover_no_legacy_state(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setattr(_md, "get_default_hermes_root", lambda: root)
    results = _md.discover_legacy_state()
    assert results == []


def test_build_migration_plan_fresh_init(tmp_path):
    from hermes_cli.evolution.migration_discovery import build_migration_plan
    plan = build_migration_plan([], organism_root=tmp_path / "nonexistent")
    assert plan["recommended_action"] == "fresh_initialization"
    assert plan["global_state_exists"] is False


def test_build_migration_plan_already_global(tmp_path):
    from hermes_cli.evolution.migration_discovery import build_migration_plan
    org = tmp_path / "organism"
    org.mkdir(parents=True)
    (org / "identity.json").write_text("{}")
    plan = build_migration_plan([], organism_root=org)
    assert plan["recommended_action"] == "already_global"


def test_build_migration_plan_single_baseline(tmp_path):
    from hermes_cli.evolution.migration_discovery import (
        build_migration_plan,
        LegacyProjectAState,
        LegacyStatus,
    )
    state = LegacyProjectAState(
        status=LegacyStatus.COHERENT_BASELINE_ONLY,
        profile_ref="abc123",
        db_path=tmp_path / "x.db",
        schema_version=3,
        generation_count=1,
    )
    plan = build_migration_plan([state], organism_root=tmp_path / "nonexistent")
    assert plan["recommended_action"] == "migrate_single_baseline"
    assert plan["baseline_candidates"] == 1


def test_build_provenance_manifest(tmp_path):
    from hermes_cli.evolution.migration_discovery import (
        build_provenance_manifest,
        LegacyProjectAState,
        LegacyStatus,
    )
    states = [
        LegacyProjectAState(
            status=LegacyStatus.COHERENT_BASELINE_ONLY,
            profile_ref="abc123",
            db_path=tmp_path / "x.db",
            schema_version=3,
            generation_count=1,
        )
    ]
    manifest = build_provenance_manifest(states)
    assert manifest["schema_version"] == 1
    assert manifest["profile_count"] == 1
    assert len(manifest["profiles"]) == 1

# --- 2B.4: Global Lifecycle Init ---

from hermes_cli.evolution import lifecycle_global as _lg
from hermes_cli.evolution.organism_identity import load_organism_identity as _load_ident


def test_global_lifecycle_init_idempotent(tmp_path, monkeypatch):
    """Repeated initialization returns the same generation."""
    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    first = _lg.ensure_global_lifecycle_initialized()
    second = _lg.ensure_global_lifecycle_initialized()
    assert first.generation_id == second.generation_id
    assert (org / "evolution" / "evolution.db").exists()
    assert (org / "identity.json").exists()


def test_global_lifecycle_init_without_legacy_is_fresh(tmp_path, monkeypatch):
    """Fresh init creates identity with real lineage_root_digest."""
    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    gen = _lg.ensure_global_lifecycle_initialized()
    assert (org / "identity.json").exists()
    ident = _load_ident(org)
    assert ident.lineage_root_digest == gen.generation_id
    assert ident.lineage_root_digest != "0000000000000000000000000000000000000000000000000000000000000000"


def test_identity_lineage_root_matches_baseline(tmp_path, monkeypatch):
    """After init, identity lineage_root_digest matches stored baseline."""
    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    gen = _lg.ensure_global_lifecycle_initialized()
    assert (org / "identity.json").exists()
    ident = _load_ident(org)
    assert ident.lineage_root_digest == gen.generation_id

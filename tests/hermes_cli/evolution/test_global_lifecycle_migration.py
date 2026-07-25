"""Tests for legacy state discovery and migration planning."""

import pytest
import sqlite3
from pathlib import Path

from hermes_cli.evolution import migration_discovery as _md


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


def test_build_migration_plan_fresh_init(tmp_path, monkeypatch):
    from hermes_constants import get_organism_home
    org = tmp_path / "organism"
    monkeypatch.setattr("hermes_constants.get_organism_home", lambda: org)

    plan = _md.build_migration_plan([], organism_root=org)
    assert plan["recommended_action"] == "fresh_initialization"
    assert plan["global_state_exists"] is False


def test_build_migration_plan_already_global(tmp_path, monkeypatch):
    org = tmp_path / "organism"
    org.mkdir(parents=True)
    (org / "identity.json").write_text("{}")
    monkeypatch.setattr("hermes_constants.get_organism_home", lambda: org)

    plan = _md.build_migration_plan([], organism_root=org)
    assert plan["recommended_action"] == "already_global"


def test_build_migration_plan_single_baseline(tmp_path, monkeypatch):
    org = tmp_path / "organism"
    monkeypatch.setattr("hermes_constants.get_organism_home", lambda: org)

    state = _md.LegacyProjectAState(
        status=_md.LegacyStatus.COHERENT_BASELINE_ONLY,
        profile_ref="abc123",
        db_path=tmp_path / "x.db",
        schema_version=3,
        generation_count=1,
    )
    plan = _md.build_migration_plan([state], organism_root=org)
    assert plan["recommended_action"] == "migrate_single_baseline"
    assert plan["baseline_candidates"] == 1


def test_build_provenance_manifest(tmp_path):
    states = [
        _md.LegacyProjectAState(
            status=_md.LegacyStatus.COHERENT_BASELINE_ONLY,
            profile_ref="abc123",
            db_path=tmp_path / "x.db",
            schema_version=3,
            generation_count=1,
        )
    ]
    manifest = _md.build_provenance_manifest(states)
    assert manifest["schema_version"] == 1
    assert manifest["profile_count"] == 1
    assert len(manifest["profiles"]) == 1

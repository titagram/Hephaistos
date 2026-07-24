"""Tests for global organism home resolution, identity, legacy migration scanner, and dual-profile isolation."""

import json
import os
import pytest
from pathlib import Path

from hermes_cli.evolution.organism_home import ensure_organism_directories
from hermes_cli.evolution.organism_identity import (
    OrganismIdentity,
    OrganismIdentityError,
    create_organism_identity,
    load_organism_identity,
    validate_organism_identity,
)
from hermes_cli.evolution.migration import scan_legacy_profile_states, archive_legacy_provenance
from hermes_constants import get_organism_home, get_default_hermes_root


def test_ensure_organism_directories(tmp_path: Path):
    org = tmp_path / "organism"
    res = ensure_organism_directories(org)
    assert res == org.resolve()
    assert (org / "evolution").is_dir()
    assert (org / "telos" / "revisions").is_dir()
    assert (org / "gnothi_seauton").is_dir()
    assert (org / "archives" / "legacy-profile-state").is_dir()


def test_create_and_load_organism_identity(tmp_path: Path):
    org = tmp_path / "organism"
    ident = create_organism_identity(org)
    assert ident.schema_version == 1
    assert len(ident.organism_id) == 36
    assert len(ident.lineage_root_digest) == 64

    loaded = load_organism_identity(org)
    assert loaded == ident


def test_organism_identity_symlink_rejection(tmp_path: Path):
    org = tmp_path / "organism"
    ensure_organism_directories(org)
    ident_path = org / "identity.json"
    fake_target = tmp_path / "fake.json"
    fake_target.write_text("{}")
    ident_path.symlink_to(fake_target)

    with pytest.raises(OrganismIdentityError, match="symlink"):
        load_organism_identity(org)


def test_organism_identity_validation_bad_id():
    ident = OrganismIdentity(
        schema_version=1,
        organism_id="not-a-uuid",
        created_at="2026-07-24T12:00:00.000000Z",
        lineage_root_digest="a" * 64,
    )
    with pytest.raises(OrganismIdentityError, match="organism_id format"):
        validate_organism_identity(ident)


def test_dual_profile_single_global_organism(tmp_path: Path, monkeypatch):
    root = tmp_path / "hermes_root"
    root.mkdir(mode=0o700)
    p1 = root / "profiles" / "prof1"
    p2 = root / "profiles" / "prof2"
    p1.mkdir(parents=True, mode=0o700)
    p2.mkdir(parents=True, mode=0o700)

    # Initialize organism identity at global root
    org_home = root / "organism"
    ident = create_organism_identity(org_home)

    monkeypatch.setenv("HERMES_HOME", str(p1))
    org1 = get_organism_home()

    monkeypatch.setenv("HERMES_HOME", str(p2))
    org2 = get_organism_home()

    # Both profiles resolve to the exact same global organism home
    assert org1.resolve() == org_home.resolve()
    assert org2.resolve() == org_home.resolve()

    ident1 = load_organism_identity(org1)
    ident2 = load_organism_identity(org2)
    assert ident1.organism_id == ident.organism_id
    assert ident2.organism_id == ident.organism_id

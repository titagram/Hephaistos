"""Tests for organism home security — symlink rejection, safe path resolution."""

import json as _json
import pytest
from pathlib import Path

from hermes_cli.evolution import organism_home as _mod


def test_organism_home_rejects_symlink_root(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_home import resolve_organism_root, OrganismHomeError

    real = tmp_path / "real_org"
    real.mkdir()
    link = tmp_path / "organism_link"
    link.symlink_to(real)
    monkeypatch.setattr(_mod, "get_organism_home", lambda: link)
    with pytest.raises(OrganismHomeError, match="observer_database_unsafe"):
        resolve_organism_root()


def test_organism_home_rejects_non_directory(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_home import resolve_organism_root, OrganismHomeError

    f = tmp_path / "not_a_dir"
    f.write_text("x")
    monkeypatch.setattr(_mod, "get_organism_home", lambda: f)
    with pytest.raises(OrganismHomeError, match="observer_database_unsafe"):
        resolve_organism_root()


def test_organism_home_resolves_valid_directory(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_home import resolve_organism_root

    d = tmp_path / "valid_org"
    d.mkdir()
    monkeypatch.setattr(_mod, "get_organism_home", lambda: d)
    result = resolve_organism_root()
    assert result == d
    assert result.is_dir()


def test_organism_home_lstat_each_component(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_home import resolve_organism_root, OrganismHomeError

    nested = tmp_path / "a" / "b" / "organism"
    nested.mkdir(parents=True)
    link = tmp_path / "a" / "b_link"
    (tmp_path / "a" / "b").rename(tmp_path / "a" / "b_orig")
    link.symlink_to(tmp_path / "a" / "b_orig")
    monkeypatch.setattr(
        _mod, "get_organism_home",
        lambda: tmp_path / "a" / "b_link" / "organism",
    )
    with pytest.raises(OrganismHomeError, match="observer_database_unsafe"):
        resolve_organism_root()


# --- Organism Identity Tests ---

def test_organism_identity_atomic_create_rejects_partial(tmp_path, monkeypatch):
    """Malformed identity: load raises, create raises, file preserved."""
    from hermes_cli.evolution.organism_identity import (
        create_organism_identity, load_organism_identity, OrganismIdentityError,
    )
    org = tmp_path / "organism"
    org.mkdir()
    monkeypatch.setattr(_mod, "get_organism_home", lambda: org)
    (org / "identity.json").write_text('{"schema_version":1,"incomplete":')
    with pytest.raises(OrganismIdentityError):
        load_organism_identity(org)
    # Partial file preserved for diagnosis
    assert (org / "identity.json").exists()
    # Identity replacement is not a Project B operation — create must also fail
    with pytest.raises(OrganismIdentityError):
        create_organism_identity(org)


def test_malformed_existing_identity_fails_closed(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_identity import (
        load_organism_identity, OrganismIdentityError,
    )
    org = tmp_path / "organism"
    org.mkdir()
    monkeypatch.setattr(_mod, "get_organism_home", lambda: org)

    (org / "identity.json").write_text(_json.dumps({
        "schema_version": 99, "organism_id": "00000000-0000-0000-0000-000000000000",
        "created_at": "2026-07-24T00:00:00.000000Z",
        "lineage_root_digest": "0000000000000000000000000000000000000000000000000000000000000000",
    }))
    with pytest.raises(OrganismIdentityError):
        load_organism_identity(org)

    (org / "identity.json").write_text(_json.dumps({
        "schema_version": 1, "organism_id": "not-a-uuid",
        "created_at": "2026-07-24T00:00:00.000000Z",
        "lineage_root_digest": "0000000000000000000000000000000000000000000000000000000000000000",
    }))
    with pytest.raises(OrganismIdentityError):
        load_organism_identity(org)

    (org / "identity.json").write_text(_json.dumps({
        "schema_version": 1, "organism_id": "00000000-0000-0000-0000-000000000000",
        "created_at": "2026-07-24T00:00:00.000000Z",
        "lineage_root_digest": "too-short",
    }))
    with pytest.raises(OrganismIdentityError):
        load_organism_identity(org)

    assert (org / "identity.json").exists()
    content = (org / "identity.json").read_text()
    assert "too-short" in content


def test_create_organism_identity_never_replaces_existing(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_identity import (
        create_organism_identity, load_organism_identity,
    )
    org = tmp_path / "organism"
    org.mkdir()
    monkeypatch.setattr(_mod, "get_organism_home", lambda: org)
    first = create_organism_identity(org)
    second = create_organism_identity(org)
    assert second.organism_id == first.organism_id
    loaded = load_organism_identity(org)
    assert loaded.organism_id == first.organism_id


def test_identity_rejects_symlink(tmp_path, monkeypatch):
    from hermes_cli.evolution.organism_identity import (
        load_organism_identity, OrganismIdentityError,
    )
    org = tmp_path / "organism"
    org.mkdir()
    real_ident = tmp_path / "real_identity.json"
    real_ident.write_text(_json.dumps({
        "schema_version": 1,
        "organism_id": "00000000-0000-0000-0000-000000000000",
        "created_at": "2026-07-24T00:00:00.000000Z",
        "lineage_root_digest": "0000000000000000000000000000000000000000000000000000000000000000",
    }))
    (org / "identity.json").symlink_to(real_ident)
    monkeypatch.setattr(_mod, "get_organism_home", lambda: org)
    with pytest.raises(OrganismIdentityError):
        load_organism_identity(org)

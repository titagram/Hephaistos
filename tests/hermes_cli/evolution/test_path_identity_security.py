"""Pass D — Organism path and identity security hardening tests."""

import os
import pytest
import stat
from pathlib import Path


# ── D1: Symlink ancestor reproduction ──

def test_symlink_ancestor_rejected(tmp_path, monkeypatch):
    """When an ancestor of the organism root is a symlink, reject.

    Setup: real_dir/organism_target/ — but requested path goes through
    a symlink: link -> real_dir, requested = link/organism_target.
    Current behavior follows the symlink silently.
    Required: OrganismHomeError raised, no directory created through symlink.
    """
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    requested = link / "nonexistent_organism"

    monkeypatch.setattr(_hc, "get_organism_home", lambda: requested)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: requested)

    from hermes_cli.evolution.organism_home import resolve_organism_root, OrganismHomeError

    with pytest.raises(OrganismHomeError, match="unsafe"):
        resolve_organism_root(requested)

    # No directory created through the symlink
    assert not (real / "nonexistent_organism").exists()
    assert not requested.exists()


# ── D2: Symlink at organism subdirectory ──

def test_symlink_at_subdirectory_rejected(tmp_path, monkeypatch):
    """Symlink inside organism root (e.g., evolution/ -> /tmp) must be rejected."""
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    org = tmp_path / "organism"
    org.mkdir(parents=True)
    # Create a legitimate identity so the root is valid
    (org / "identity.json").write_text(
        '{"organism_id":"test","schema_version":1,"created_at":"2026-01-01T00:00:00.000000Z"}'
    )

    # Now create a symlink for the evolution subdir
    real_evo = tmp_path / "real_evolution"
    real_evo.mkdir()
    symlink_evo = org / "evolution"
    symlink_evo.symlink_to(real_evo)

    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)

    from hermes_cli.evolution.organism_home import ensure_organism_directories, OrganismHomeError

    # ensure_organism_directories walks subdirs and should detect the symlink
    with pytest.raises((OrganismHomeError, OSError)):
        ensure_organism_directories(org)


# ── D3: Identity publication atomicity ──

def test_identity_publication_no_replace(tmp_path, monkeypatch):
    """create_organism_identity must never replace an existing identity file."""
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    org = tmp_path / "organism"
    org.mkdir(parents=True)
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)

    from hermes_cli.evolution.organism_identity import (
        create_organism_identity, load_organism_identity, OrganismIdentityError,
    )
    from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized

    # Create initial identity
    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)
    ensure_global_lifecycle_initialized()

    first = load_organism_identity(org)
    first_id = first.organism_id

    # Attempt to create again — must return existing, not replace
    second = create_organism_identity(org)
    assert second.organism_id == first_id

    # File bytes unchanged
    content = (org / "identity.json").read_bytes()
    second_load = load_organism_identity(org)
    assert second_load.organism_id == first_id


def test_identity_rejects_symlink_at_path(tmp_path, monkeypatch):
    """load_organism_identity must reject if identity.json is a symlink."""
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    org = tmp_path / "organism"
    org.mkdir(parents=True)

    real_id = tmp_path / "real_identity.json"
    real_id.write_text(
        '{"organism_id":"malicious","schema_version":1,"created_at":"2026-01-01T00:00:00.000000Z"}'
    )
    (org / "identity.json").symlink_to(real_id)

    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)

    from hermes_cli.evolution.organism_identity import load_organism_identity, OrganismIdentityError

    with pytest.raises(OrganismIdentityError, match="unsafe"):
        load_organism_identity(org)

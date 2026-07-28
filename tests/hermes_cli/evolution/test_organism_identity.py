"""Tests for read-only organism identity probing."""

import pytest

from hermes_cli.evolution.organism_identity import (
    OrganismIdentityError,
    create_organism_identity,
    probe_organism_identity,
)


def test_probe_missing_identity_does_not_create_root(tmp_path):
    """A missing identity must not cause probe-time filesystem creation."""
    root = tmp_path / "organism"

    assert probe_organism_identity(root) is None
    assert not root.exists()


def test_probe_returns_existing_identity_without_writing(tmp_path):
    """Probing an existing identity leaves its filesystem shape unchanged."""
    root = tmp_path / "organism"
    expected = create_organism_identity(root)
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    assert probe_organism_identity(root) == expected
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


def test_probe_rejects_symlink_and_corrupt_identity(tmp_path):
    """Malformed identity content is rejected instead of being normalized."""
    root = tmp_path / "organism"
    root.mkdir()
    (root / "identity.json").write_text("{}", encoding="utf-8")

    with pytest.raises(OrganismIdentityError, match="organism_identity_corrupted"):
        probe_organism_identity(root)

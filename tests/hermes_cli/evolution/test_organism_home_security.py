"""Tests for organism home security — symlink rejection, safe path resolution."""

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
    # Create symlink at intermediate component
    link = tmp_path / "a" / "b_link"
    (tmp_path / "a" / "b").rename(tmp_path / "a" / "b_orig")
    link.symlink_to(tmp_path / "a" / "b_orig")
    monkeypatch.setattr(
        _mod, "get_organism_home",
        lambda: tmp_path / "a" / "b_link" / "organism",
    )
    with pytest.raises(OrganismHomeError, match="observer_database_unsafe"):
        resolve_organism_root()
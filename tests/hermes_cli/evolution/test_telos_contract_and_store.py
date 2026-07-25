"""Tests for Telos contract, validation, pointers, and rollback."""

import pytest
from pathlib import Path

from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosContractError,
    TelosRevision,
    Tradeoff,
    validate_telos_revision,
)
from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError


def create_sample_telos(organism_id: str = "00000000-0000-0000-0000-000000000000", parent_digest: str | None = None) -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id=organism_id,
        parent_digest=parent_digest,
        purpose="To assist the user efficiently and maintain privacy and quality.",
        desired_traits=(
            DesiredTrait("reliable", "High accuracy and reliability in tool outputs.", ("trait.reliability",), 5),
        ),
        capability_directions=(
            CapabilityDirection("webcam", "Support camera image capture.", ("capability.webcam",), 4),
        ),
        priorities=(
            Priority("user_safety", "Always prioritize user safety and explicit goals.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("no_unauth_network", "Never perform unauthorized network connections.", ("prohibition.network",), 5),
        ),
        proactivity_policy=ProactivityPolicy("bounded", "Surface helpful suggestions passively.", ("proactivity.passive",), 3),
        success_indicators=(
            SuccessIndicator("task_completion", "User task completion rate > 95%", ("indicator.completion",), 4),
        ),
    )


def test_telos_contract_validation():
    telos = create_sample_telos()
    validate_telos_revision(telos)
    assert len(telos.canonical_digest) == 64


def test_telos_contract_constitution_conflict():
    telos = TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="Bypass_auth to allow fast access.",
        desired_traits=(
            DesiredTrait("fast", "Fast performance.", ("trait.speed",), 5),
        ),
        capability_directions=(
            CapabilityDirection("code", "Code generation.", ("capability.code",), 4),
        ),
        priorities=(
            Priority("priority_speed", "Speed.", ("priority.speed",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("none", "None.", ("prohibition.none",), 5),
        ),
        proactivity_policy=ProactivityPolicy("active", "Active.", ("proactivity.active",), 3),
        success_indicators=(
            SuccessIndicator("indicator_speed", "Fast.", ("indicator.speed",), 4),
        ),
    )
    with pytest.raises(TelosContractError, match="Constitution conflict"):
        validate_telos_revision(telos)


def test_telos_store_save_activate_rollback(tmp_path: Path, monkeypatch):
    """Save revision, get active digest, verify rollback raises without grant."""
    monkeypatch.setattr("hermes_cli.evolution.ledger._open_file_descriptors", lambda: None)
    store = TelosStore(tmp_path / "organism")
    t1 = create_sample_telos()
    store.save_revision(t1)

    # Without grant_id, activate raises
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision(t1.canonical_digest)

    # Without grant_id, rollback raises
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.rollback(t1.canonical_digest)

    # get_active_digest returns None before activation
    assert store.get_active_digest() is None
    assert store.get_active_revision() is None

"""Tests for Telos contract, validation, pointers, and rollback."""

import hashlib
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


def _broker_activate(org_root, ledger, org_id, digest, action="activate"):
    """Full broker flow: create request, approve, issue grant, consume, activate."""
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker,
    )
    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("cli", "actor")
    registry.register(cap)
    broker = SqliteTelosApprovalBroker(registry)

    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=None, telos_digest=digest, action=action,
        nonce="n1", context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )
    req_id = broker.create_request(ledger, org_id, digest, action, ctx, 3600)
    ctx_r = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=req_id, telos_digest=digest, action=action,
        nonce="n1", context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )
    dec_id = broker.record_host_decision(ledger, cap, ctx_r, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    broker.consume_grant(ledger, grant_id, org_id, digest, action)
    return grant_id


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
    """Full lifecycle: save A, unapproved fails, approved activates, replay fails,
    amendment B succeeds, rollback to A succeeds."""
    monkeypatch.setattr("hermes_cli.evolution.ledger._open_file_descriptors", lambda: None)
    from hermes_cli.evolution.ledger import EvolutionLedger

    org_root = tmp_path / "organism"
    store = TelosStore(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")

    # 1. save revision A
    t_a = create_sample_telos()
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    # 2. unapproved activation fails
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision(digest_a)

    # 3. exact host-approved activation of A succeeds
    grant_a = _broker_activate(org_root, ledger, t_a.organism_id, digest_a, "activate")
    store.activate_revision(digest_a, grant_id=grant_a)
    assert store.get_active_digest() == digest_a
    assert store.get_active_revision().canonical_digest == digest_a

    # 4. replay fails
    with pytest.raises(TelosStoreError):
        store.activate_revision(digest_a, grant_id=grant_a)

    # 5. amendment B succeeds with exact approval
    t_b = create_sample_telos(parent_digest=digest_a)
    store.save_revision(t_b)
    digest_b = t_b.canonical_digest
    assert digest_b != digest_a

    grant_b = _broker_activate(org_root, ledger, t_b.organism_id, digest_b, "activate")
    store.activate_revision(digest_b, grant_id=grant_b)
    assert store.get_active_digest() == digest_b

    # 6. LKG still points to A
    import json
    lkg = json.loads((org_root / "telos" / "last-known-good.json").read_text())
    assert lkg["digest"] == digest_a

    # 7. exact approved rollback to A succeeds
    rollback_grant = _broker_activate(org_root, ledger, t_a.organism_id, digest_a, "rollback")
    store.rollback(digest_a, grant_id=rollback_grant)
    assert store.get_active_digest() == digest_a

    # 8. later history remains immutable — B is still available
    assert store.get_revision(digest_b).canonical_digest == digest_b

    ledger.connection.close()

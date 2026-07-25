"""Security tests for Telos approval boundary — broker and capability path."""

import hashlib
import pytest
import uuid
from pathlib import Path

from hermes_cli.evolution.organism_home import get_organism_home
import hermes_constants as _hc


def _setup_organism(tmp_path, monkeypatch):
    """Setup global organism for security tests."""
    from hermes_cli.evolution import organism_home as _oh
    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")
    from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
    gen = ensure_global_lifecycle_initialized()
    from hermes_cli.evolution.ledger import EvolutionLedger
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    return org, ledger, gen.generation_id


def test_no_host_receipt_approval_fails(tmp_path, monkeypatch):
    """Without a capability in the registry, approval must fail."""
    org, ledger, gen_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )

    registry = CapabilityRegistry()
    # Capability NOT registered in registry
    cap = HostApprovalCapability._test_create("attacker", "fake_actor")
    broker = SqliteTelosApprovalBroker(registry)

    ctx = HostApprovalContext(
        surface="attacker", actor_ref="fake", session_ref="s",
        request_id="req-1", telos_digest="a" * 64, action="activate",
        nonce="1", context_digest=hashlib.sha256(b"x").hexdigest(),
    )

    with pytest.raises(TelosApprovalError, match="telos_capability_not_verified"):
        broker.record_host_decision(ledger, cap, ctx, "approved")

    ledger.connection.close()


def test_invented_receipt_approval_fails(tmp_path, monkeypatch):
    """A capability from a DIFFERENT registry must be rejected."""
    org, ledger, gen_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )

    reg1 = CapabilityRegistry()
    reg2 = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("gateway", "actor")

    # Register in reg1, but broker uses reg2
    reg1.register(cap)
    broker = SqliteTelosApprovalBroker(reg2)

    ctx = HostApprovalContext(
        surface="gateway", actor_ref="actor", session_ref="s",
        request_id="req-2", telos_digest="b" * 64, action="activate",
        nonce="2", context_digest=hashlib.sha256(b"y").hexdigest(),
    )

    with pytest.raises(TelosApprovalError, match="telos_capability_not_verified"):
        broker.record_host_decision(ledger, cap, ctx, "approved")

    ledger.connection.close()


def test_valid_host_receipt_approval_succeeds(tmp_path, monkeypatch):
    """A registered capability enables approval and grant consumption."""
    org, ledger, gen_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )

    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("classic_cli", "test_actor")
    registry.register(cap)
    broker = SqliteTelosApprovalBroker(registry)

    org_id = str(uuid.uuid4())
    digest = "c" * 64

    ctx_create = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor", session_ref="sess1",
        request_id=None, telos_digest=digest, action="activate",
        nonce="1234", context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )
    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)

    ctx_decide = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor", session_ref="sess1",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="1234", context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )
    dec_id = broker.record_host_decision(ledger, cap, ctx_decide, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)

    # Consume grant
    consumption = broker.consume_grant(ledger, grant_id, org_id, digest, "activate")
    assert consumption is not None

    ledger.connection.close()


def test_replay_or_mismatched_receipt_fails(tmp_path, monkeypatch):
    """Grant consumption must be single-use — replay is rejected."""
    org, ledger, gen_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )

    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("classic_cli", "actor")
    registry.register(cap)
    broker = SqliteTelosApprovalBroker(registry)

    org_id = str(uuid.uuid4())
    digest = "d" * 64

    ctx_create = HostApprovalContext(
        surface="classic_cli", actor_ref="actor", session_ref="s",
        request_id=None, telos_digest=digest, action="activate",
        nonce="r1", context_digest=hashlib.sha256(b"rr").hexdigest(),
    )
    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)
    ctx_dec = HostApprovalContext(
        surface="classic_cli", actor_ref="actor", session_ref="s",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="r1", context_digest=hashlib.sha256(b"rr").hexdigest(),
    )
    dec_id = broker.record_host_decision(ledger, cap, ctx_dec, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)

    # First consumption: success
    broker.consume_grant(ledger, grant_id, org_id, digest, "activate")

    # Replay: must fail
    with pytest.raises(TelosApprovalError, match="telos_consumption_failed"):
        broker.consume_grant(ledger, grant_id, org_id, digest, "activate")

    ledger.connection.close()

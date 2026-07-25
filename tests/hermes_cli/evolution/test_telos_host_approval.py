"""Tests for host approval capability, registry, broker design, and SQLite integration."""

import hashlib
import pytest

from hermes_cli.evolution.telos_approval import (
    HostApprovalContext,
    HostApprovalCapability,
    CapabilityRegistry,
    TelosApprovalPrompt,
    HostApprovalDecision,
    TelosApprovalBroker,
    TelosApprovalError,
)


# --- 2C.0: Capability + Registry Design Tests ---

def test_capability_verified_by_identity_not_value():
    """A different object with identical fields must be rejected."""
    registry = CapabilityRegistry()
    cap1 = HostApprovalCapability._test_create("gateway", "actor1")
    registry.register(cap1)
    assert registry.verify(cap1) is True

    # Different object, same fields — must be rejected
    cap2 = HostApprovalCapability._test_create("gateway", "actor1")
    assert cap2._surface == cap1._surface
    assert cap2._actor_ref == cap1._actor_ref
    assert registry.verify(cap2) is False


def test_capability_revoked_after_removal():
    """After revoke, verify returns False."""
    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("gateway", "actor1")
    registry.register(cap)
    assert registry.verify(cap) is True
    registry.revoke(cap)
    assert registry.verify(cap) is False


def test_capability_from_another_registry_rejected():
    """A capability registered in one registry is not valid in another."""
    reg1 = CapabilityRegistry()
    reg2 = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("gateway", "actor1")
    reg1.register(cap)
    assert reg2.verify(cap) is False


def test_capability_identity_equals_self_only():
    """__eq__ must use identity, not value."""
    cap1 = HostApprovalCapability._test_create("gateway", "actor1")
    cap2 = HostApprovalCapability._test_create("gateway", "actor1")
    assert cap1 == cap1
    assert cap1 != cap2  # Different objects, not equal


def test_registry_active_count():
    """active_count tracks registered capabilities."""
    registry = CapabilityRegistry()
    assert registry.active_count == 0
    cap = HostApprovalCapability._test_create("cli", "a")
    registry.register(cap)
    assert registry.active_count == 1
    registry.revoke(cap)
    assert registry.active_count == 0


def test_host_approval_context_binds_surface_and_session():
    ctx = HostApprovalContext(
        surface="classic_cli",
        actor_ref="user123",
        session_ref="sess-abc",
        request_id="req-1",
        telos_digest="a" * 64,
        action="activate",
        nonce="1234",
        context_digest=hashlib.sha256(b"ctx").hexdigest(),
        expires_at=None,
    )
    assert ctx.surface == "classic_cli"
    assert ctx.request_id == "req-1"
    assert ctx.action == "activate"


def test_broker_abc_requires_capability_parameter():
    """record_host_decision signature must require a capability parameter."""
    import inspect

    sig = inspect.signature(TelosApprovalBroker.record_host_decision)
    params = list(sig.parameters.keys())
    assert "capability" in params


def test_telos_approval_prompt_holds_request_data():
    prompt = TelosApprovalPrompt(
        request_id="req-1",
        organism_id="org-1",
        telos_digest="a" * 64,
        action="activate",
        display_nonce="1234",
        bounded_summary="Test Telos activation",
        host_context_digest="c" * 64,
    )
    assert prompt.request_id == "req-1"
    assert prompt.action == "activate"


def test_host_approval_decision_frozen():
    dec = HostApprovalDecision(
        request_id="req-1",
        decision="approved",
        host_surface="gateway",
        host_actor_ref="user1",
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    assert dec.decision == "approved"
    with pytest.raises(Exception):
        dec.decision = "denied"  # frozen


# --- 2C.1: Broker SQLite Integration Tests ---

import uuid as _uuid_mod
from datetime import datetime, timezone, timedelta
from pathlib import Path as _Path


def _setup_organism_ledger(tmp_path: _Path, monkeypatch) -> tuple:
    """Setup organism with v4 ledger for broker tests."""
    from hermes_cli.evolution import organism_home as _oh
    import hermes_constants as _hc

    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")

    from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_approval import SqliteTelosApprovalBroker

    ensure_global_lifecycle_initialized()
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    registry = CapabilityRegistry()
    broker = SqliteTelosApprovalBroker(registry)
    return org, ledger, registry, broker


def test_broker_issue_and_consume_grant_with_capability(tmp_path, monkeypatch):
    """Full happy path: create request, approve with capability, issue grant, consume."""
    import hashlib

    org, ledger, registry, broker = _setup_organism_ledger(tmp_path, monkeypatch)

    cap = HostApprovalCapability._test_create("classic_cli", "test_actor")
    registry.register(cap)

    org_id = str(_uuid_mod.uuid4())
    digest = "a" * 64
    ctx = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor",
        session_ref="sess1", request_id=None,
        telos_digest=digest, action="activate",
        nonce="1234",
        context_digest=hashlib.sha256(b"ctx").hexdigest(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )

    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx, 3600)
    assert isinstance(req_id, str)

    ctx_with_req = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor",
        session_ref="sess1", request_id=req_id,
        telos_digest=digest, action="activate",
        nonce="1234",
        context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )

    dec_id = broker.record_host_decision(ledger, cap, ctx_with_req, "approved")
    assert isinstance(dec_id, str)

    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    assert isinstance(grant_id, str)

    consumption = broker.consume_grant(ledger, grant_id, org_id, digest, "activate")
    assert consumption is not None

    # Replay: second consumption must fail (UNIQUE on grant_id)
    with pytest.raises(TelosApprovalError, match="telos_consumption_failed"):
        broker.consume_grant(ledger, grant_id, org_id, digest, "activate")

    ledger.connection.close()


def test_broker_rejects_capability_not_in_registry(tmp_path, monkeypatch):
    """A capability not in the registry must be rejected."""
    import hashlib

    org, ledger, registry, broker = _setup_organism_ledger(tmp_path, monkeypatch)

    # Capability NOT registered
    cap = HostApprovalCapability._test_create("gateway", "attacker")
    ctx = HostApprovalContext(
        surface="gateway", actor_ref="attacker", session_ref="s",
        request_id="req-x", telos_digest="a" * 64, action="activate",
        nonce="x", context_digest=hashlib.sha256(b"x").hexdigest(), expires_at=None,
    )

    with pytest.raises(TelosApprovalError, match="telos_capability_not_verified"):
        broker.record_host_decision(ledger, cap, ctx, "approved")

    ledger.connection.close()


def test_broker_denied_decision_prevents_grant(tmp_path, monkeypatch):
    """A denied decision must not produce a grant (trigger-enforced)."""
    import hashlib

    org, ledger, registry, broker = _setup_organism_ledger(tmp_path, monkeypatch)

    cap = HostApprovalCapability._test_create("cli", "actor")
    registry.register(cap)

    digest = "b" * 64
    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=None, telos_digest=digest, action="activate",
        nonce="1", context_digest=hashlib.sha256(b"c").hexdigest(),
    )

    req_id = broker.create_request(ledger, "org-1", digest, "activate", ctx, 60)
    ctx_r = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="1", context_digest=hashlib.sha256(b"c").hexdigest(),
    )

    broker.record_host_decision(ledger, cap, ctx_r, "denied")

    with pytest.raises(TelosApprovalError, match="telos_grant_failed"):
        broker.issue_grant(ledger, req_id, "some-dec-id")

    ledger.connection.close()


def test_broker_get_pending_requests(tmp_path, monkeypatch):
    """Pending requests must be queryable."""
    import hashlib

    org, ledger, registry, broker = _setup_organism_ledger(tmp_path, monkeypatch)

    digest = "c" * 64
    ctx = HostApprovalContext(
        surface="gateway", actor_ref="a", session_ref="s",
        request_id=None, telos_digest=digest, action="activate",
        nonce="1", context_digest=hashlib.sha256(b"p").hexdigest(),
    )

    req_id = broker.create_request(ledger, "pending-org", digest, "activate", ctx, 60)
    pending = broker.get_pending_requests(ledger, "pending-org")
    assert len(pending) >= 1
    assert any(p["request_id"] == req_id for p in pending)

    # Other org has none
    assert len(broker.get_pending_requests(ledger, "other-org")) == 0

    ledger.connection.close()


# --- 2C.3: CLI Prompt Tests ---

def test_cli_telos_approval_prompt_timeout_is_deny():
    """Timeout must produce denied, not crash."""
    from unittest import mock
    from hermes_cli.evolution.telos_approval import telos_approval_prompt, TelosApprovalPrompt

    prompt = TelosApprovalPrompt(
        request_id="req-1", organism_id="org-1", telos_digest="a" * 64,
        action="activate", display_nonce="1234",
        bounded_summary="Test Telos activation",
        host_context_digest="c" * 64,
    )
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt", side_effect=TimeoutError):
        decision = telos_approval_prompt(prompt, timeout=1)
        assert decision.decision == "denied"


def test_cli_telos_approval_accepts_y():
    """'y' must produce approved decision."""
    from unittest import mock
    from hermes_cli.evolution.telos_approval import telos_approval_prompt, TelosApprovalPrompt

    prompt = TelosApprovalPrompt(
        request_id="req-1", organism_id="org-1", telos_digest="a" * 64,
        action="activate", display_nonce="1234",
        bounded_summary="Test", host_context_digest="c" * 64,
    )
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt", return_value="y"):
        decision = telos_approval_prompt(prompt, timeout=30)
        assert decision.decision == "approved"


def test_cli_telos_approval_rejects_invalid_input():
    """Anything other than 'y'/'yes' must be denied."""
    from unittest import mock
    from hermes_cli.evolution.telos_approval import telos_approval_prompt, TelosApprovalPrompt

    prompt = TelosApprovalPrompt(
        request_id="req-1", organism_id="org-1", telos_digest="a" * 64,
        action="activate", display_nonce="1234",
        bounded_summary="Test", host_context_digest="c" * 64,
    )
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt", return_value="maybe"):
        decision = telos_approval_prompt(prompt, timeout=30)
        assert decision.decision == "denied"

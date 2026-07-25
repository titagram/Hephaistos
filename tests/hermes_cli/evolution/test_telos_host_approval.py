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

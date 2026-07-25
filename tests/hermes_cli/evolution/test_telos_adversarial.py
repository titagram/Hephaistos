"""Adversarial security tests: forged SQLite rows, capability binding, exact-scope enforcement.

Pass A — Restore Honest Security Tests. These tests must fail on the current HEAD
because the audit found the authorization boundary is bypassable.
"""

import hashlib
import json
import pytest
import uuid
from pathlib import Path

import hermes_constants as _hc
from hermes_cli.evolution.telos_contract import (
    TelosRevision, DesiredTrait, CapabilityDirection,
    Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
)


def _setup_organism(tmp_path: Path, monkeypatch):
    """Create a real organism with v4 ledger for adversarial tests."""
    from hermes_cli.evolution import organism_home as _oh
    from hermes_cli.evolution.lifecycle_global import (
        ensure_global_lifecycle_initialized,
    )

    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")
    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    gen = ensure_global_lifecycle_initialized()
    from hermes_cli.evolution.organism_identity import load_organism_identity

    ident = load_organism_identity(org)
    return org, ident.organism_id


def _make_telos(org_id: str, purpose: str, parent: str | None = None):
    return TelosRevision(
        schema_version=1, organism_id=org_id, parent_digest=parent,
        purpose=purpose,
        desired_traits=(DesiredTrait("t1", "d", ("t1",), 5),),
        capability_directions=(CapabilityDirection("c1", "d", ("c1",), 4),),
        priorities=(Priority("p1", "d", ("p1",), 5),),
        tradeoffs=(), prohibitions=(Prohibition("none", "None", ("none",), 5),),
        proactivity_policy=ProactivityPolicy("pass", "d", ("pass",), 3),
        success_indicators=(SuccessIndicator("i1", "d", ("i1",), 4),),
    )


def _make_telos_b(org_id: str, purpose: str, parent: str | None = None):
    return TelosRevision(
        schema_version=1, organism_id=org_id, parent_digest=parent,
        purpose=purpose,
        desired_traits=(DesiredTrait("t1", "d", ("t1",), 5),),
        capability_directions=(CapabilityDirection("c2", "d", ("c2",), 4),),
        priorities=(Priority("p1", "d", ("p1",), 5),),
        tradeoffs=(), prohibitions=(Prohibition("none", "None", ("none",), 5),),
        proactivity_policy=ProactivityPolicy("pass", "d", ("pass",), 3),
        success_indicators=(SuccessIndicator("i1", "d", ("i1",), 4),),
    )


def _open_ledger(org: Path):
    from hermes_cli.evolution.ledger import EvolutionLedger
    return EvolutionLedger(org / "evolution" / "evolution.db")


def _forge_approval_rows(
    ledger,
    organism_id: str,
    digest_a: str,
    action: str = "activate",
) -> str:
    """Direct SQLite INSERT of request + approved decision + grant + consumption.

    Returns the forged grant_id.
    """
    import sqlite3
    forged_request = f"forged-req-{uuid.uuid4().hex[:8]}"
    forged_decision = f"forged-dec-{uuid.uuid4().hex[:8]}"
    forged_grant = f"forged-grt-{uuid.uuid4().hex[:8]}"
    forged_consumption = f"forged-con-{uuid.uuid4().hex[:8]}"
    now = "2026-07-24T12:00:00.000000Z"
    expires = "2027-07-24T12:00:00.000000Z"

    conn = ledger.connection
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        (forged_request, organism_id, digest_a, action,
         "forged-context", "forged-nonce", "forged summary", now, expires),
    )
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
        (forged_decision, forged_request, "approved",
         "forged_surface", "forged_actor", "forged-context-digest", now),
    )
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        (forged_grant, forged_request, forged_decision,
         organism_id, digest_a, action, now, expires),
    )
    conn.execute(
        "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
        (forged_consumption, forged_grant, organism_id, digest_a, action, now),
    )
    conn.commit()
    return forged_grant


def _broker_approve(ledger, org_id, digest, action, surface="cli", actor="actor"):
    """Full broker flow: create request, approve, issue grant, consume.

    Closes the ledger after consumption since activate_revision re-opens it.
    """
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker,
    )
    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create(surface, actor)
    registry.register(cap)
    broker = SqliteTelosApprovalBroker(registry)

    ctx = HostApprovalContext(
        surface=surface, actor_ref=actor, session_ref="s",
        request_id=None, telos_digest=digest, action=action,
        nonce="n1", context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )
    req_id = broker.create_request(ledger, org_id, digest, action, ctx, 3600)
    ctx_r = HostApprovalContext(
        surface=surface, actor_ref=actor, session_ref="s",
        request_id=req_id, telos_digest=digest, action=action,
        nonce="n1", context_digest=hashlib.sha256(b"ctx").hexdigest(),
    )
    dec_id = broker.record_host_decision(ledger, cap, ctx_r, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    broker.consume_grant(ledger, grant_id, org_id, digest, action)
    return grant_id

def _make_cap(surface="cli", organism_id=None, digest=None, action=None):
    """Create a host capability, bind it, and register it in the host registry."""
    from hermes_cli.evolution.telos_approval import (
        HostApprovalCapability, set_host_capability,
    )
    cap = HostApprovalCapability._test_create(surface, "test_actor")
    if organism_id is not None and digest is not None and action is not None:
        cap.bind_to_request(organism_id, digest, action)
    set_host_capability(cap)
    return cap


# ── A1: Forged persistent rows cannot activate Telos ──

def test_forged_rows_cannot_activate_telos(tmp_path, monkeypatch):
    """Direct SQLite INSERT of approval rows must not authorize Telos activation.

    The audit found this bypass: persistent rows are treated as authority.
    Expected: TelosStoreError, no active pointer, no LKG, no mutation.

    RED OUTPUT: DID NOT RAISE TelosStoreError — the forged grant bypassed security.
    """
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    ledger = _open_ledger(org)

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError

    store = TelosStore(org)
    telos = _make_telos(org_id, "Test Telos for adversary test")
    store.save_revision(telos)
    digest_a = telos.canonical_digest

    # Forge approval rows directly via SQLite
    forged_grant = _forge_approval_rows(ledger, org_id, digest_a)
    ledger.connection.close()

    # Attempt activation with forged grant — use UNREGISTERED capability
    # (the model cannot register in the host registry)
    from hermes_cli.evolution.telos_approval import HostApprovalCapability
    unreg = HostApprovalCapability._test_create("model", "model")
    with pytest.raises(TelosStoreError):
        store.activate_revision(digest_a, grant_id=forged_grant, capability=unreg)

    # No side effects
    assert store.get_active_digest() is None


# ── A2: Grant for revision A cannot activate revision B ──

def test_grant_for_a_cannot_activate_b(tmp_path, monkeypatch):
    """A legitimate grant for digest A must not authorize activation of digest B."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError

    store = TelosStore(org)

    t_a = _make_telos(org_id, "Revision A")
    store.save_revision(t_a)
    t_b = _make_telos_b(org_id, "Revision B", parent=t_a.canonical_digest)
    store.save_revision(t_b)
    digest_a = t_a.canonical_digest
    digest_b = t_b.canonical_digest
    assert digest_a != digest_b

    # Get a legitimate grant for A via broker
    ledger = _open_ledger(org)
    grant_id_a = _broker_approve(ledger, org_id, digest_a, "activate")
    ledger.connection.close()

    # Activate A legitimately — this opens its own ledger internally
    store.activate_revision(digest_a, grant_id=grant_id_a,
                            capability=_make_cap(digest=digest_a, organism_id=org_id, action="activate"))
    assert store.get_active_digest() == digest_a

    # Try to use grant_id_a (for A) to activate B — must fail
    with pytest.raises(TelosStoreError):
        store.activate_revision(digest_b, grant_id=grant_id_a, capability=_make_cap(digest=digest_b, organism_id=org_id, action="activate"))

    # A must still be active
    assert store.get_active_digest() == digest_a


# ── A3: Rollback requires exact rollback authorization ──

def test_activate_grant_cannot_authorize_rollback(tmp_path, monkeypatch):
    """An 'activate' grant must never authorize 'rollback'."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError

    store = TelosStore(org)
    t_a = _make_telos(org_id, "Revision A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    # Get an ACTIVATE grant via broker
    ledger = _open_ledger(org)
    grant_id = _broker_approve(ledger, org_id, digest_a, "activate")
    ledger.connection.close()

    # Activate succeeds
    store.activate_revision(digest_a, grant_id=grant_id, capability=_make_cap(digest=digest_a, organism_id=org_id, action="activate"))
    assert store.get_active_digest() == digest_a

    # Try to rollback with the ACTIVATE grant — must fail
    with pytest.raises(TelosStoreError):
        store.rollback(digest_a, grant_id=grant_id, capability=_make_cap(digest=digest_a, organism_id=org_id, action="activate"))


# ── A4: Capability binding and lifecycle ──

def test_capability_must_match_registry(tmp_path, monkeypatch):
    """Foreign registry — capability from registry A rejected by broker using registry B."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )
    ledger = _open_ledger(org)

    reg_a = CapabilityRegistry()
    reg_b = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("gateway", "actor")
    reg_a.register(cap)
    broker = SqliteTelosApprovalBroker(reg_b)

    ctx = HostApprovalContext(
        surface="gateway", actor_ref="actor", session_ref="s",
        request_id="r1", telos_digest="a" * 64, action="activate",
        nonce="n", context_digest=hashlib.sha256(b"x").hexdigest(),
    )
    with pytest.raises(TelosApprovalError, match="not_verified"):
        broker.record_host_decision(ledger, cap, ctx, "approved")
    ledger.connection.close()


def test_unregistered_capability_fails(tmp_path, monkeypatch):
    """Capability never registered anywhere must be rejected."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )
    ledger = _open_ledger(org)
    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("cli", "actor")
    # NOT registered
    broker = SqliteTelosApprovalBroker(registry)
    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id="r2", telos_digest="b" * 64, action="activate",
        nonce="n", context_digest=hashlib.sha256(b"y").hexdigest(),
    )
    with pytest.raises(TelosApprovalError, match="not_verified"):
        broker.record_host_decision(ledger, cap, ctx, "approved")
    ledger.connection.close()


def test_revoked_capability_fails(tmp_path, monkeypatch):
    """After revocation, a previously-valid capability fails."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )
    ledger = _open_ledger(org)
    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("cli", "actor")
    registry.register(cap)
    broker = SqliteTelosApprovalBroker(registry)

    # Create a real request first
    req_id_r3 = broker.create_request(ledger, org_id, "c" * 64, "activate",
        HostApprovalContext(surface="cli", actor_ref="actor", session_ref="s",
            request_id=None, telos_digest="c" * 64, action="activate",
            nonce="n", context_digest=hashlib.sha256(b"z").hexdigest()), 3600)

    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=req_id_r3, telos_digest="c" * 64, action="activate",
        nonce="n", context_digest=hashlib.sha256(b"z").hexdigest(),
    )
    broker.record_host_decision(ledger, cap, ctx, "approved")  # works

    registry.revoke(cap)
    req_id_r4 = broker.create_request(ledger, org_id, "d" * 64, "activate",
        HostApprovalContext(surface="cli", actor_ref="actor", session_ref="s",
            request_id=None, telos_digest="d" * 64, action="activate",
            nonce="n2", context_digest=hashlib.sha256(b"w").hexdigest()), 3600)
    ctx2 = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=req_id_r4, telos_digest="d" * 64, action="activate",
        nonce="n2", context_digest=hashlib.sha256(b"w").hexdigest(),
    )
    with pytest.raises(TelosApprovalError, match="not_verified"):
        broker.record_host_decision(ledger, cap, ctx2, "approved")
    ledger.connection.close()


# ── HONEST RED TESTS — reproducing the real audit defects ──


def test_registered_unrelated_capability_cannot_authorize_forged_rows(tmp_path, monkeypatch):
    """A registered but unrelated capability must NOT authorize forged SQLite rows.

    The audit found: a capability registered in the host registry for a different
    surface/actor/request could still authorize any activation — registry membership
    alone was treated as authority.

    RED: Current HEAD allows this because verify_host_capability only checks
    registry identity, not context binding.
    """
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    ledger = _open_ledger(org)

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError

    store = TelosStore(org)
    telos = _make_telos(org_id, "Test Telos for unrelated capability test")
    store.save_revision(telos)
    digest_a = telos.canonical_digest

    # Forge approval rows directly via SQLite
    forged_grant = _forge_approval_rows(ledger, org_id, digest_a)
    ledger.connection.close()

    # Register a capability for a DIFFERENT surface/actor — this is a LIVE,
    # REGISTERED capability, just for the wrong context
    from hermes_cli.evolution.telos_approval import (
        HostApprovalCapability, set_host_capability, clear_host_capability,
    )
    unrelated_cap = HostApprovalCapability._test_create("gateway", "other_actor")
    set_host_capability(unrelated_cap)

    try:
        # Attempt activation with a cap registered for a different surface/actor
        # This MUST fail — the capability must be bound to exact request context
        with pytest.raises(TelosStoreError):
            store.activate_revision(digest_a, grant_id=forged_grant, capability=unrelated_cap)

        # No side effects
        assert store.get_active_digest() is None
    finally:
        clear_host_capability(unrelated_cap)


def test_activate_does_not_close_caller_owned_ledger(tmp_path, monkeypatch):
    """TelosStore.activate_revision must NOT close a caller-owned ledger.

    Both activate_revision() and rollback() close ledger.connection in finally
    even when the caller supplied the ledger. The caller's ledger must remain
    usable after activation.

    RED: Current HEAD closes the caller's ledger in the finally block.
    """
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore

    store = TelosStore(org)
    t_a = _make_telos(org_id, "Revision A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    # Get legitimate broker approval
    ledger = _open_ledger(org)
    grant_id = _broker_approve(ledger, org_id, digest_a, "activate")
    # Keep ledger open — caller owns it, do NOT close before activation
    # (The activate_revision will internally close-and-reopen but must NOT
    #  close the caller-owned connection)

    store.activate_revision(digest_a, grant_id=grant_id, capability=_make_cap(digest=digest_a, organism_id=org_id, action="activate"),
                            ledger=ledger)

    # Caller's ledger must remain usable after activation
    try:
        # Simple query to verify the connection is still alive
        result = ledger.connection.execute("SELECT 1").fetchone()
        assert result is not None, "Caller-owned ledger was closed by activate_revision!"
    except Exception as e:
        pytest.fail(f"Caller-owned ledger closed by activate_revision: {e}")
    finally:
        ledger.connection.close()


def test_rollback_does_not_close_caller_owned_ledger(tmp_path, monkeypatch):
    """TelosStore.rollback must NOT close a caller-owned ledger.

    RED: Current HEAD closes the caller's ledger in the finally block.
    """
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore

    store = TelosStore(org)
    t_a = _make_telos(org_id, "Revision A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    # First activate A with a broker-approved grant
    ledger = _open_ledger(org)
    grant_activate = _broker_approve(ledger, org_id, digest_a, "activate")
    ledger.connection.close()

    store.activate_revision(digest_a, grant_id=grant_activate, capability=_make_cap(digest=digest_a, organism_id=org_id, action="activate"))
    assert store.get_active_digest() == digest_a

    # Now get rollback grant
    ledger2 = _open_ledger(org)
    rollback_grant = _broker_approve(ledger2, org_id, digest_a, "rollback")

    # Rollback with caller-owned ledger
    store.rollback(digest_a, grant_id=rollback_grant, capability=_make_cap(digest=digest_a, organism_id=org_id, action="rollback"),
                   ledger=ledger2)

    # Caller's ledger must remain usable after rollback
    try:
        result = ledger2.connection.execute("SELECT 1").fetchone()
        assert result is not None, "Caller-owned ledger was closed by rollback!"
    except Exception as e:
        pytest.fail(f"Caller-owned ledger closed by rollback: {e}")
    finally:
        ledger2.connection.close()

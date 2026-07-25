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

    # Attempt activation with forged grant
    with pytest.raises(TelosStoreError):
        store.activate_revision(digest_a, grant_id=forged_grant)

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
    store.activate_revision(digest_a, grant_id=grant_id_a)
    assert store.get_active_digest() == digest_a

    # Try to use grant_id_a (for A) to activate B — must fail
    with pytest.raises(TelosStoreError):
        store.activate_revision(digest_b, grant_id=grant_id_a)

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
    store.activate_revision(digest_a, grant_id=grant_id)
    assert store.get_active_digest() == digest_a

    # Try to rollback with the ACTIVATE grant — must fail
    with pytest.raises(TelosStoreError):
        store.rollback(digest_a, grant_id=grant_id)


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

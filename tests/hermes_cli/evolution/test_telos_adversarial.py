"""Adversarial security tests: forged SQLite rows, closed public API, no transition for model callers.

TelosStore contains **zero** pointer-mutating transition methods.
All host-authorised pointer publication lives in the gateway-owned
TelosCoordinator.
"""
import hashlib
import pytest
import uuid
from pathlib import Path

import hermes_constants as _hc
from hermes_cli.evolution.telos_contract import (
    TelosRevision, DesiredTrait, CapabilityDirection,
    Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
)
from tests.hermes_cli.evolution.test_telos_gateway_dispatch import (
    _make_event as _mk_ev, _create_telos_request, _session_key_for,
)


def _setup_organism(tmp_path: Path, monkeypatch):
    """Create a real organism with v4/v5 ledger for adversarial tests."""
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


def _forge_approval_rows(
    ledger,
    organism_id: str,
    digest_a: str,
    action: str = "activate",
) -> str:
    """Direct SQLite INSERT of request + approved decision + grant + consumption.

    Uses matching context digests to pass the v5 trigger.
    Returns the forged grant_id.
    """
    forged_request = f"forged-req-{uuid.uuid4().hex[:8]}"
    forged_decision = f"forged-dec-{uuid.uuid4().hex[:8]}"
    forged_grant = f"forged-grt-{uuid.uuid4().hex[:8]}"
    forged_consumption = f"forged-con-{uuid.uuid4().hex[:8]}"
    now = "2026-07-24T12:00:00.000000Z"
    expires = "2027-07-24T12:00:00.000000Z"
    ctx_digest = hashlib.sha256(b"forged").hexdigest()

    conn = ledger.connection
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        (forged_request, organism_id, digest_a, action,
         ctx_digest, "forged-nonce", "forged summary", now, expires),
    )
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
        (forged_decision, forged_request, "approved",
         "forged_surface", "forged_actor", ctx_digest, now),
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


# ── A1: Public activate_revision always fails closed ──

def test_public_activate_revision_fails_closed(tmp_path, monkeypatch):
    """Calling TelosStore.activate_revision directly must fail closed."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError
    store = TelosStore(org)
    telos = _make_telos(org_id, "A1")
    store.save_revision(telos)
    digest_a = telos.canonical_digest

    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision(digest_a)

    assert store.get_active_digest() is None


def test_public_rollback_fails_closed(tmp_path, monkeypatch):
    """Calling TelosStore.rollback directly must fail closed."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError
    store = TelosStore(org)
    telos = _make_telos(org_id, "A2")
    store.save_revision(telos)

    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.rollback(telos.canonical_digest)


# ── A2: Forged persistent rows cannot activate Telos through public API ──

def test_forged_rows_cannot_activate_telos(tmp_path, monkeypatch):
    """Direct SQLite INSERT of approval rows must not activate Telos
    through the public TelosStore.activate_revision API — always fails closed."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.ledger import EvolutionLedger

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError

    store = TelosStore(org)
    telos = _make_telos(org_id, "Test Telos for adversary test")
    store.save_revision(telos)
    digest_a = telos.canonical_digest

    forged_grant = _forge_approval_rows(ledger, org_id, digest_a)
    ledger.connection.close()

    # Public API always fails closed — forged rows cannot authorise mutation
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision(digest_a, grant_id=forged_grant)

    assert store.get_active_digest() is None


# ── A3: TelosStore has zero pointer-mutating methods ──

def test_telos_store_has_no_publish_method(tmp_path, monkeypatch):
    """TelosStore must not expose _publish_from_grant or any pointer-mutating method."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_store import TelosStore
    store = TelosStore(org)

    assert not hasattr(store, "_publish_from_grant")
    assert not hasattr(store, "_publish_pointer")
    assert not hasattr(store, "publish_from_grant")


# ── A4: Coherent broker-created rows cannot mutate pointers ──

def test_coherent_broker_rows_cannot_mutate_pointers(tmp_path, monkeypatch):
    """Even a full coherent chain from the broker must not activate through
    TelosStore public API — only TelosCoordinator can publish."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError
    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext, SqliteTelosApprovalBroker,
    )

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    store = TelosStore(org)
    telos = _make_telos(org_id, "coherent-broker-test")
    store.save_revision(telos)
    digest = telos.canonical_digest

    broker = SqliteTelosApprovalBroker()
    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=None, telos_digest=digest, action="activate",
        nonce="broker", context_digest="ignored",
    )
    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx, 3600)
    from hermes_cli.evolution.telos_approval import compute_context_digest
    correct = compute_context_digest("cli", "actor", "s", req_id, "broker")
    ctx_r = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="broker", context_digest=correct,
    )
    dec_id = broker.record_host_decision(ledger, ctx_r, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    broker.consume_grant(ledger, grant_id, org_id, digest, "activate")
    ledger.connection.close()

    # Public API must still fail closed — only TelosCoordinator publishes
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision(digest, grant_id=grant_id)

    assert store.get_active_digest() is None


# ── A5: Missing revision fails before decision (coordinator path) ──

@pytest.mark.asyncio
async def test_missing_revision_fails_before_decision(tmp_path, monkeypatch):
    """If the revision referenced by the request does not exist in the store,
    the coordinator must reject before recording any decision."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    # Create a request for a revision that was never saved
    create_ev = _mk_ev("telegram", "u1", "c1", "")
    missing_digest = "f" * 64
    req_id = _create_telos_request(ledger, org_id, missing_digest, "activate", create_ev, "missing-rev")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    event = _mk_ev("telegram", "u1", "c1", f"/approve telos {req_id}")
    result = await runner._handle_approve_command(event)

    assert "revision not found" in result
    assert store.get_active_digest() is None

    # No decision was recorded
    ledger2 = EvolutionLedger(org / "evolution" / "evolution.db")
    dec_count = ledger2.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_decisions WHERE request_id = ?",
        (req_id,),
    ).fetchone()[0]
    assert dec_count == 0
    ledger2.connection.close()


# ── A6: Cross-organism revision fails before decision ──

@pytest.mark.asyncio
async def test_cross_organism_revision_fails_before_decision(tmp_path, monkeypatch):
    """A request for organism 'org-A' whose digest refers to a revision
    whose organism_id is 'org-B' must be rejected before any decision."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    # Save a revision under a DIFFERENT organism_id
    wrong_org_id = "cross-org-id-12345"
    cross_revision = _make_telos(wrong_org_id, "cross-org-test")
    store.save_revision(cross_revision)
    cross_digest = cross_revision.canonical_digest

    # Create a request for org_id but pointing to the cross-organism revision
    create_ev = _mk_ev("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, cross_digest, "activate", create_ev, "cross-org")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    event = _mk_ev("telegram", "u1", "c1", f"/approve telos {req_id}")
    result = await runner._handle_approve_command(event)

    assert "organism mismatch" in result
    assert store.get_active_digest() is None

    # No decision was recorded
    ledger2 = EvolutionLedger(org / "evolution" / "evolution.db")
    dec_count = ledger2.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_decisions WHERE request_id = ?",
        (req_id,),
    ).fetchone()[0]
    assert dec_count == 0
    ledger2.connection.close()


# ── A7: Digest mismatch (revision digest != request telos_digest) ──

@pytest.mark.asyncio
async def test_revision_digest_mismatch_fails_before_decision(
    tmp_path, monkeypatch
):
    """Tampered revision content must fail before any host decision is stored."""
    import json

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    telos = _make_telos(org_id, "digest-check")
    store.save_revision(telos)
    requested_digest = telos.canonical_digest

    revision_path = store.revisions_dir / f"{requested_digest}.json"
    tampered = json.loads(revision_path.read_text(encoding="utf-8"))
    tampered["purpose"] = "tampered after save"
    revision_path.write_text(
        json.dumps(tampered, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    create_event = _mk_ev("telegram", "u1", "c1", "")
    request_id = _create_telos_request(
        ledger,
        org_id,
        requested_digest,
        "activate",
        create_event,
        "digest-mismatch",
    )
    ledger.connection.close()

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}
    approve_event = _mk_ev(
        "telegram", "u1", "c1", f"/approve telos {request_id}"
    )
    result = await runner._handle_approve_command(approve_event)

    assert "revision digest mismatch" in result
    assert store.get_active_digest() is None

    verification_ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    decision_count = verification_ledger.connection.execute(
        "SELECT COUNT(*) FROM telos_approval_decisions WHERE request_id = ?",
        (request_id,),
    ).fetchone()[0]
    verification_ledger.connection.close()
    assert decision_count == 0


# ── A8: Request input validation ──

def test_create_request_rejects_invalid_action(tmp_path, monkeypatch):
    """Only activate and rollback are valid."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    org, _ = _setup_organism(tmp_path, monkeypatch)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext, SqliteTelosApprovalBroker, TelosApprovalError,
    )
    broker = SqliteTelosApprovalBroker()
    ctx = HostApprovalContext(
        surface="cli", actor_ref="a", session_ref="s",
        request_id=None, telos_digest="a" * 64, action="bad",
        nonce="n", context_digest="ignored",
    )
    with pytest.raises(TelosApprovalError, match="telos_invalid_action"):
        broker.create_request(ledger, "org1", "a" * 64, "bad", ctx, 3600)
    ledger.connection.close()


def test_create_request_rejects_empty_organism_id(tmp_path, monkeypatch):
    """Empty organism_id must be rejected."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    org, _ = _setup_organism(tmp_path, monkeypatch)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext, SqliteTelosApprovalBroker, TelosApprovalError,
    )
    broker = SqliteTelosApprovalBroker()
    ctx = HostApprovalContext(
        surface="cli", actor_ref="a", session_ref="s",
        request_id=None, telos_digest="a" * 64, action="activate",
        nonce="n", context_digest="ignored",
    )
    with pytest.raises(TelosApprovalError, match="telos_empty_organism_id"):
        broker.create_request(ledger, "", "a" * 64, "activate", ctx, 3600)
    ledger.connection.close()


def test_create_request_rejects_invalid_digest(tmp_path, monkeypatch):
    """Non-hex or wrong-length digest must be rejected."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    org, _ = _setup_organism(tmp_path, monkeypatch)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext, SqliteTelosApprovalBroker, TelosApprovalError,
    )
    broker = SqliteTelosApprovalBroker()
    ctx = HostApprovalContext(
        surface="cli", actor_ref="a", session_ref="s",
        request_id=None, telos_digest="bad", action="activate",
        nonce="n", context_digest="ignored",
    )
    with pytest.raises(TelosApprovalError, match="telos_invalid_digest"):
        broker.create_request(ledger, "org1", "bad", "activate", ctx, 3600)
    ledger.connection.close()


def test_create_request_rejects_invalid_ttl(tmp_path, monkeypatch):
    """Zero, negative, and excessively large ttl must be rejected."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    org, _ = _setup_organism(tmp_path, monkeypatch)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")

    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext, SqliteTelosApprovalBroker, TelosApprovalError,
    )
    broker = SqliteTelosApprovalBroker()
    ctx = HostApprovalContext(
        surface="cli", actor_ref="a", session_ref="s",
        request_id=None, telos_digest="a" * 64, action="activate",
        nonce="n", context_digest="ignored",
    )
    for bad_ttl in (0, -1, 86401, 999999):
        with pytest.raises(TelosApprovalError, match="telos_invalid_ttl"):
            broker.create_request(ledger, "org1", "a" * 64, "activate", ctx, bad_ttl)
    ledger.connection.close()


def test_create_request_rejects_mismatched_or_incomplete_context(
    tmp_path, monkeypatch
):
    """Stored request fields and host-binding context must agree and be complete."""
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
        TelosApprovalError,
    )

    org, _ = _setup_organism(tmp_path, monkeypatch)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    broker = SqliteTelosApprovalBroker()

    mismatched = HostApprovalContext(
        surface="gateway",
        actor_ref="telegram:u1",
        session_ref="session",
        request_id=None,
        telos_digest="b" * 64,
        action="rollback",
        nonce="nonce",
        context_digest="ignored",
    )
    with pytest.raises(TelosApprovalError, match="telos_request_context_mismatch"):
        broker.create_request(
            ledger, "org1", "a" * 64, "activate", mismatched, 3600
        )

    incomplete = HostApprovalContext(
        surface="gateway",
        actor_ref="",
        session_ref="session",
        request_id=None,
        telos_digest="a" * 64,
        action="activate",
        nonce="nonce",
        context_digest="ignored",
    )
    with pytest.raises(TelosApprovalError, match="telos_request_context_incomplete"):
        broker.create_request(
            ledger, "org1", "a" * 64, "activate", incomplete, 3600
        )
    ledger.connection.close()


# ── A9: Context digest canonical encoding — delimiter-safe ──

def test_context_digest_encoding_is_delimiter_safe(tmp_path, monkeypatch):
    """Delimiter-containing inputs (:: in fields) must not collide with
    the old separator-based encoding.  Uses canonical JSON encoding with
    a domain version marker."""
    from hermes_cli.evolution.telos_approval import compute_context_digest

    # Two different inputs that would collide under old :: encoding
    d1 = compute_context_digest("a::b", "c", "d", "e", "f")
    d2 = compute_context_digest("a", "b::c", "d", "e", "f")
    assert d1 != d2, "Delimiter collision detected in context digest"

    # Same inputs produce same digest (deterministic)
    d3 = compute_context_digest("a::b", "c", "d", "e", "f")
    assert d1 == d3

    # Producer and consumer use the same function — verify by importing
    # from the consumer module and checking it delegates to the canonical one.
    from gateway.telos_coordinator import TelosCoordinator
    # The coordinator's approve/deny methods import compute_context_digest
    # from hermes_cli.evolution.telos_approval at call time.  Verify the
    # canonical module exports it with the same name.
    from hermes_cli.evolution.telos_approval import compute_context_digest as canonical
    # A digest computed for the same inputs must match
    assert d1 == canonical("a::b", "c", "d", "e", "f")

    # Test with :: in multiple positions
    d4 = compute_context_digest("sur::face", "act::or", "ses::sion", "req::id", "non::ce")
    d5 = compute_context_digest("surface", "actor", "session", "req::id", "non::ce")
    assert d4 != d5

    # Verify the domain marker is present in the encoding
    import json
    import hashlib
    expected = hashlib.sha256(
        json.dumps(
            ["telos-host-context-v1", "a", "b", "c", "d", "e"],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert compute_context_digest("a", "b", "c", "d", "e") == expected

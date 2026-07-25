"""Gateway dispatch tests for Telos coordinator — actor, session, context, replay, and denial.

All tests go through the actual approve/deny routing entrypoints
(``GatewaySlashCommandsMixin._handle_approve_command`` /
``_handle_deny_command``) with a real ``MessageEvent`` text — never call
the telos-specific handler or ``TelosCoordinator`` directly.
"""
import hashlib
import json
import pytest
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import hermes_constants as _hc
from hermes_cli.evolution.telos_contract import (
    TelosRevision, DesiredTrait, CapabilityDirection,
    Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
)


# ── helpers ──


def _setup_organism(tmp_path: Path, monkeypatch):
    """Create a real organism with v4/v5 ledger.  Returns (org_root, organism_id)."""
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

    ensure_global_lifecycle_initialized()
    from hermes_cli.evolution.organism_identity import load_organism_identity
    ident = load_organism_identity(org)
    return org, ident.organism_id


def _open_ledger(org: Path):
    from hermes_cli.evolution.ledger import EvolutionLedger
    return EvolutionLedger(org / "evolution" / "evolution.db")


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


def _make_event(platform_value: str, user_id: str, chat_id: str, text: str):
    """Create a minimal MessageEvent for telos gateway dispatch tests."""
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource
    from gateway.config import Platform

    plat = next(
        (p for p in Platform.__members__.values() if p.value == platform_value),
        Platform.TELEGRAM,
    )
    source = SessionSource(
        platform=plat,
        user_id=user_id,
        chat_id=chat_id,
        chat_type="dm",
    )
    return MessageEvent(text=text, source=source, message_id="m1")


def _session_key_for(event):
    """Return the session key the gateway runner would compute for this event."""
    from gateway.session import build_session_key
    return build_session_key(event.source)


def _create_telos_request(ledger, org_id, digest, action, event, nonce="n1"):
    """Create a pending telos request via the broker using the event's context.

    Returns the request_id.
    """
    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext, SqliteTelosApprovalBroker,
    )

    broker = SqliteTelosApprovalBroker()
    platform_val = event.source.platform
    pv = platform_val.value if hasattr(platform_val, "value") else str(platform_val or "?")
    user_id = getattr(event.source, "user_id", "") or ""
    actor = f"{pv}:{user_id}"
    session_key = _session_key_for(event)

    ctx = HostApprovalContext(
        surface="gateway", actor_ref=actor, session_ref=session_key,
        request_id=None, telos_digest=digest, action=action,
        nonce=nonce, context_digest="ignored",
    )
    return broker.create_request(ledger, org_id, digest, action, ctx, 3600)


# ── H1: Gateway dispatch activates pointer ──


@pytest.mark.asyncio
async def test_gateway_approve_activates_pointer(tmp_path, monkeypatch):
    """Full gateway dispatch: /approve telos <id> activates the pointer."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "gateway-test")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    event = _make_event("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, digest_a, "activate", event, "approve-nonce")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    approve_event = _make_event("telegram", "u1", "c1", f"/approve telos {req_id}")
    result = await runner._handle_approve_command(approve_event)

    assert store.get_active_digest() == digest_a, f"Pointer not activated: {result}"
    assert "Telos activate completed" in result


@pytest.mark.asyncio
async def test_gateway_approve_revision_b_updates_lkg(tmp_path, monkeypatch):
    """Amendment B: LKG=A before active=B.  Exact rollback: active=A, B preserved."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "LKG-test-A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    t_b = _make_telos(org_id, "LKG-test-B", parent=digest_a)
    store.save_revision(t_b)
    digest_b = t_b.canonical_digest

    base_event = _make_event("telegram", "u1", "c1", "")

    # Activate A
    req_a = _create_telos_request(ledger, org_id, digest_a, "activate", base_event, "n-a")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    event_a = _make_event("telegram", "u1", "c1", f"/approve telos {req_a}")
    r1 = await runner._handle_approve_command(event_a)
    assert store.get_active_digest() == digest_a, f"Activate A failed: {r1}"

    # Activate B — LKG should point to A
    ledger2 = _open_ledger(org)
    req_b = _create_telos_request(ledger2, org_id, digest_b, "activate", base_event, "n-b")
    ledger2.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    runner2 = object.__new__(GatewayRunner)
    runner2._telos_coordinator = TelosCoordinator()
    runner2._pending_approvals = {}

    event_b = _make_event("telegram", "u1", "c1", f"/approve telos {req_b}")
    r2 = await runner2._handle_approve_command(event_b)
    assert store.get_active_digest() == digest_b, f"Activate B failed: {r2}"

    lkg = json.loads((org / "telos" / "last-known-good.json").read_text())
    assert lkg["digest"] == digest_a

    # Rollback to A — active=A, B revision preserved
    ledger3 = _open_ledger(org)
    req_rb = _create_telos_request(ledger3, org_id, digest_a, "rollback", base_event, "n-rb")
    ledger3.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    runner3 = object.__new__(GatewayRunner)
    runner3._telos_coordinator = TelosCoordinator()
    runner3._pending_approvals = {}

    event_rb = _make_event("telegram", "u1", "c1", f"/approve telos {req_rb}")
    r3 = await runner3._handle_approve_command(event_rb)
    assert store.get_active_digest() == digest_a, f"Rollback failed: {r3}"

    assert store.get_revision(digest_b).canonical_digest == digest_b


# ── H2: Actor mismatch rejected ──


@pytest.mark.asyncio
async def test_gateway_approve_wrong_actor(tmp_path, monkeypatch):
    """A different actor (different user_id) must be rejected."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "actor-mismatch")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    create_event = _make_event("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, digest_a, "activate", create_event, "n-act")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    # Different user_id => different actor => context mismatch
    event = _make_event("telegram", "u2", "c1", f"/approve telos {req_id}")
    result = await runner._handle_approve_command(event)

    assert "context mismatch" in result
    assert store.get_active_digest() is None


# ── H3: Wrong session rejected ──


@pytest.mark.asyncio
async def test_gateway_approve_wrong_session(tmp_path, monkeypatch):
    """A different session/channel must be rejected."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "session-mismatch")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    create_event = _make_event("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, digest_a, "activate", create_event, "n-sess")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    # Different chat_id => different session_key => context mismatch
    event = _make_event("telegram", "u1", "c-different", f"/approve telos {req_id}")
    result = await runner._handle_approve_command(event)

    assert "context mismatch" in result
    assert store.get_active_digest() is None


# ── H4: Non-existent request ID rejected ──


@pytest.mark.asyncio
async def test_gateway_approve_unknown_request(tmp_path, monkeypatch):
    """A non-existent request ID must be rejected."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    event = _make_event("telegram", "u1", "c1", "/approve telos nonexistent-req")
    result = await runner._handle_approve_command(event)

    assert "not found" in result
    assert store.get_active_digest() is None


# ── H5: Denial path — records denial, never publishes ──


@pytest.mark.asyncio
async def test_gateway_deny_records_no_publish(tmp_path, monkeypatch):
    """/deny telos <id> must record the denial and never activate."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "deny-test")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    create_event = _make_event("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, digest_a, "activate", create_event, "n-deny")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    deny_event = _make_event("telegram", "u1", "c1", f"/deny telos {req_id}")
    result = await runner._handle_deny_command(deny_event)

    assert "denied" in result
    assert store.get_active_digest() is None

    # Verify denial was recorded (cannot now approve)
    runner2 = object.__new__(GatewayRunner)
    runner2._telos_coordinator = TelosCoordinator()
    runner2._pending_approvals = {}

    approve_event = _make_event("telegram", "u1", "c1", f"/approve telos {req_id}")
    result2 = await runner2._handle_approve_command(approve_event)

    assert "not found" in result2 or "already decided" in result2
    assert store.get_active_digest() is None


# ── H6: Deny also verifies context ──


@pytest.mark.asyncio
async def test_gateway_deny_wrong_context(tmp_path, monkeypatch):
    """Deny with a different actor must be rejected (context verified before denial)."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "deny-ctx")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    create_event = _make_event("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, digest_a, "activate", create_event, "n-dctx")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}

    event = _make_event("telegram", "u2", "c1", f"/deny telos {req_id}")
    result = await runner._handle_deny_command(event)

    assert "context mismatch" in result
    assert store.get_active_digest() is None


# ── H7: Replay rejected — already decided request ──


@pytest.mark.asyncio
async def test_gateway_approve_replay_rejected(tmp_path, monkeypatch):
    """A request that already has a decision must be rejected on second attempt."""
    from hermes_cli.evolution.telos_store import TelosStore

    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)
    ledger = _open_ledger(org)

    t_a = _make_telos(org_id, "replay-test")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    create_event = _make_event("telegram", "u1", "c1", "")
    req_id = _create_telos_request(ledger, org_id, digest_a, "activate", create_event, "n-rp")
    ledger.connection.close()

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from gateway.run import GatewayRunner
    from gateway.telos_coordinator import TelosCoordinator

    # First approve — succeeds
    runner1 = object.__new__(GatewayRunner)
    runner1._telos_coordinator = TelosCoordinator()
    runner1._pending_approvals = {}

    event1 = _make_event("telegram", "u1", "c1", f"/approve telos {req_id}")
    r1 = await runner1._handle_approve_command(event1)
    assert store.get_active_digest() == digest_a, f"First activate failed: {r1}"

    # Second approve — replay rejected (request already decided)
    runner2 = object.__new__(GatewayRunner)
    runner2._telos_coordinator = TelosCoordinator()
    runner2._pending_approvals = {}

    event2 = _make_event("telegram", "u1", "c1", f"/approve telos {req_id}")
    r2 = await runner2._handle_approve_command(event2)

    assert "not found" in r2 or "already decided" in r2

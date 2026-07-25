"""Tests for host approval broker design, and SQLite integration.

Capability infrastructure has been removed from telos_approval.py.
The broker is an internal implementation detail used by the gateway-owned
TelosCoordinator.  Coherent SQLite rows alone never invoke a pointer mutation.
"""

import hashlib
import pytest

from hermes_cli.evolution.telos_approval import (
    HostApprovalContext,
    TelosApprovalPrompt,
    HostApprovalDecision,
    TelosApprovalBroker,
    TelosApprovalError,
)


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


def test_broker_record_host_decision_has_context_and_decision_params():
    """record_host_decision signature accepts context and decision, not capability."""
    import inspect

    sig = inspect.signature(TelosApprovalBroker.record_host_decision)
    params = list(sig.parameters.keys())
    assert "capability" not in params
    assert "context" in params
    assert "decision" in params


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
    broker = SqliteTelosApprovalBroker()
    return org, ledger, broker


def _canonical_digest(surface, actor, session, request_id, nonce):
    """Compute the canonical context digest — delegate to the canonical implementation."""
    from hermes_cli.evolution.telos_approval import compute_context_digest
    return compute_context_digest(surface, actor, session, request_id, nonce)


def test_broker_issue_and_consume_grant(tmp_path, monkeypatch):
    """Full happy path: create request, approve, issue grant, consume."""
    import hashlib

    org, ledger, broker = _setup_organism_ledger(tmp_path, monkeypatch)
    org_id = str(_uuid_mod.uuid4())
    digest = "a" * 64
    ctx = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor",
        session_ref="sess1", request_id=None,
        telos_digest=digest, action="activate",
        nonce="1234",
        context_digest="ignored",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )

    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx, 3600)
    assert isinstance(req_id, str)

    correct_digest = _canonical_digest("classic_cli", "test_actor", "sess1", req_id, "1234")
    ctx_with_req = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor",
        session_ref="sess1", request_id=req_id,
        telos_digest=digest, action="activate",
        nonce="1234",
        context_digest=correct_digest,
    )

    dec_id = broker.record_host_decision(ledger, ctx_with_req, "approved")
    assert isinstance(dec_id, str)

    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    assert isinstance(grant_id, str)

    consumption = broker.consume_grant(ledger, grant_id, org_id, digest, "activate")
    assert consumption is not None

    # Replay: second consumption must fail (UNIQUE on grant_id)
    with pytest.raises(TelosApprovalError, match="telos_consumption_failed"):
        broker.consume_grant(ledger, grant_id, org_id, digest, "activate")

    ledger.connection.close()


def test_broker_denied_decision_prevents_grant(tmp_path, monkeypatch):
    """A denied decision must not produce a grant (trigger-enforced)."""
    import hashlib

    org, ledger, broker = _setup_organism_ledger(tmp_path, monkeypatch)

    digest = "b" * 64
    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=None, telos_digest=digest, action="activate",
        nonce="1", context_digest="ignored",
    )

    req_id = broker.create_request(ledger, "org-1", digest, "activate", ctx, 60)
    correct_digest = _canonical_digest("cli", "actor", "s", req_id, "1")
    ctx_r = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="s",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="1", context_digest=correct_digest,
    )

    broker.record_host_decision(ledger, ctx_r, "denied")

    with pytest.raises(TelosApprovalError, match="telos_grant_failed"):
        broker.issue_grant(ledger, req_id, "some-dec-id")

    ledger.connection.close()


def test_broker_get_pending_requests(tmp_path, monkeypatch):
    """Pending requests must be queryable."""
    import hashlib

    org, ledger, broker = _setup_organism_ledger(tmp_path, monkeypatch)

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

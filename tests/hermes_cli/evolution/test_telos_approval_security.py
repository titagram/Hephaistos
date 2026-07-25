"""Security tests for Telos approval boundary — broker and internal publication path."""
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
    from hermes_cli.evolution.ledger import EvolutionLedger
    gen = ensure_global_lifecycle_initialized()
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    return org, ledger, gen.generation_id


def _canonical_digest(surface, actor, session, request_id, nonce):
    """Compute the canonical context digest — delegate to the canonical implementation."""
    from hermes_cli.evolution.telos_approval import compute_context_digest
    return compute_context_digest(surface, actor, session, request_id, nonce)


def test_valid_host_receipt_approval_succeeds(tmp_path, monkeypatch):
    """A registered host approval enables decision, grant, and consumption."""
    org, ledger, gen_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
    )

    broker = SqliteTelosApprovalBroker()
    org_id = str(uuid.uuid4())
    digest = "c" * 64

    ctx_create = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor", session_ref="sess1",
        request_id=None, telos_digest=digest, action="activate",
        nonce="1234", context_digest="ignored",
    )
    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)

    correct_digest = _canonical_digest("classic_cli", "test_actor", "sess1", req_id, "1234")
    ctx_decide = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor", session_ref="sess1",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="1234", context_digest=correct_digest,
    )
    dec_id = broker.record_host_decision(ledger, ctx_decide, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)

    # Consume grant
    consumption = broker.consume_grant(ledger, grant_id, org_id, digest, "activate")
    assert consumption is not None

    ledger.connection.close()


def test_replay_or_mismatched_receipt_fails(tmp_path, monkeypatch):
    """Grant consumption must be single-use — replay is rejected."""
    org, ledger, gen_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
        TelosApprovalError,
    )

    broker = SqliteTelosApprovalBroker()
    org_id = str(uuid.uuid4())
    digest = "d" * 64

    ctx_create = HostApprovalContext(
        surface="classic_cli", actor_ref="actor", session_ref="s",
        request_id=None, telos_digest=digest, action="activate",
        nonce="r1", context_digest="ignored",
    )
    req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)
    correct_digest = _canonical_digest("classic_cli", "actor", "s", req_id, "r1")
    ctx_dec = HostApprovalContext(
        surface="classic_cli", actor_ref="actor", session_ref="s",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="r1", context_digest=correct_digest,
    )
    dec_id = broker.record_host_decision(ledger, ctx_dec, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)

    # First consumption: success
    broker.consume_grant(ledger, grant_id, org_id, digest, "activate")

    # Replay: must fail
    with pytest.raises(TelosApprovalError, match="telos_consumption_failed"):
        broker.consume_grant(ledger, grant_id, org_id, digest, "activate")

    ledger.connection.close()

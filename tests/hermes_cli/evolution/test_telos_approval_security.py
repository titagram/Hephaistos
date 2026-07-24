"""Security tests for Telos approval boundary and single-use host receipt enforcement."""

import pytest
import uuid
from pathlib import Path
from argparse import Namespace

from hermes_cli.evolution.command import evolution_command
from hermes_cli.evolution.bootstrap import ensure_evolution_initialized
from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.authorization import create_authorization_request, issue_grant
from hermes_cli.evolution.contract import content_digest
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)
from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError


def create_sample_telos() -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="To assist the user securely.",
        desired_traits=(DesiredTrait("trait1", "Trait 1", ("t1",), 5),),
        capability_directions=(CapabilityDirection("cap1", "Cap 1", ("c1",), 4),),
        priorities=(Priority("p1", "P1", ("p1",), 5),),
        tradeoffs=(),
        prohibitions=(Prohibition("pro1", "Pro 1", ("pr1",), 5),),
        proactivity_policy=ProactivityPolicy("pass", "Pass", ("pass",), 3),
        success_indicators=(SuccessIndicator("ind1", "Ind 1", ("i1",), 4),),
    )


def test_no_host_receipt_approval_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    tstore = TelosStore(org_root)

    telos = create_sample_telos()
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    # Calling telos_approve without a valid host receipt must fail
    args = Namespace(action="telos_approve", digest=digest, receipt=None, json=True)
    res = evolution_command(args)
    assert res == 1
    assert tstore.get_active_digest() is None


def test_invented_receipt_approval_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    tstore = TelosStore(org_root)

    telos = create_sample_telos()
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    # Fake invented receipt
    args = Namespace(action="telos_approve", digest=digest, receipt="fake-receipt-12345", json=True)
    res = evolution_command(args)
    assert res == 1
    assert tstore.get_active_digest() is None


def test_valid_host_receipt_approval_succeeds(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    tstore = TelosStore(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")
    
    # We must insert a fake attempt because attempt_id is an FK in authorization_requests
    attempt_id = ledger.create_attempt("operator", "test-source")

    telos = create_sample_telos()
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    # Host issues real grant via ledger
    req = create_authorization_request(
        ledger,
        attempt_id=attempt_id,
        kind="telos_activation",
        subject_digest=digest,
        scope={"action": "activate"},
        ttl_seconds=3600
    )
    grant = issue_grant(
        ledger,
        request_id=req.request_id,
        approved_by="host_user",
        confirmation_digest=content_digest(req.canonical_payload(), domain="hades-evolution-authorization-request-v1")
    )

    args = Namespace(action="telos_approve", digest=digest, receipt=grant.grant_id, org_root=str(org_root), json=True)
    res = evolution_command(args)
    assert res == 0
    assert tstore.get_active_digest() == digest


def test_replay_or_mismatched_receipt_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    tstore = TelosStore(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")
    
    attempt_id = ledger.create_attempt("operator", "test-source")

    telos = create_sample_telos()
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    req = create_authorization_request(
        ledger,
        attempt_id=attempt_id,
        kind="telos_activation",
        subject_digest=digest,
        scope={"action": "activate"},
        ttl_seconds=3600
    )
    grant = issue_grant(
        ledger,
        request_id=req.request_id,
        approved_by="host_user",
        confirmation_digest=content_digest(req.canonical_payload(), domain="hades-evolution-authorization-request-v1")
    )
    
    args = Namespace(action="telos_approve", digest=digest, receipt=grant.grant_id, org_root=str(org_root), json=True)
    res1 = evolution_command(args)
    assert res1 == 0

    # Replay attempt with same consumed receipt must fail
    res2 = evolution_command(args)
    assert res2 == 1

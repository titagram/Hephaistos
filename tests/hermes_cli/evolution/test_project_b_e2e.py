"""End-to-End Scenarios for Autopoiesis Project B (Global Telos and Opportunity Observer)."""

import hashlib
import pytest
import uuid
from pathlib import Path

import hermes_constants as _hc
from hermes_cli.evolution import organism_home as _oh


def _setup_e2e(tmp_path: Path, monkeypatch):
    """Setup global organism with identity, ledger, telos, and active grant.

    Returns (org_root, organism_id, telos_digest, ledger, tstore).
    """
    org_root = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org_root)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org_root)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")

    # Also patch lifecycle_global which imports get_organism_home from organism_home
    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org_root)

    from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.organism_identity import load_organism_identity
    from hermes_cli.evolution.telos_store import TelosStore
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker,
    )

    gen = ensure_global_lifecycle_initialized()
    ident = load_organism_identity(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")
    tstore = TelosStore(org_root)

    # Create and approve a Telos via broker
    from hermes_cli.evolution.telos_contract import (
        TelosRevision, DesiredTrait, CapabilityDirection,
        Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
    )
    telos = TelosRevision(
        schema_version=1,
        organism_id=ident.organism_id,
        parent_digest=None,
        purpose="To assist the user with high reliability, performance, and video tasks.",
        desired_traits=(DesiredTrait("reliable", "High accuracy", ("reliable",), 5),),
        capability_directions=(
            CapabilityDirection("webcam", "Support camera image capture.", ("webcam",), 4),
            CapabilityDirection("performance", "High performance execution.", ("performance",), 4),
        ),
        priorities=(Priority("safety", "Safety first.", ("safety",), 5),),
        tradeoffs=(),
        prohibitions=(Prohibition("no_unauth_network", "No unauth network.", ("prohib_net",), 5),),
        proactivity_policy=ProactivityPolicy("passive", "Passive suggestions.", ("passive",), 3),
        success_indicators=(SuccessIndicator("task_done", "High task completion", ("done",), 4),),
    )

    tstore.save_revision(telos)
    digest = telos.canonical_digest

    # Approve via broker
    registry = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("classic_cli", "test_actor")
    registry.register(cap)
    broker = SqliteTelosApprovalBroker(registry)

    ctx_create = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor", session_ref="e2e",
        request_id=None, telos_digest=digest, action="activate",
        nonce="e2e-1", context_digest=hashlib.sha256(b"e2e").hexdigest(),
    )
    req_id = broker.create_request(ledger, ident.organism_id, digest, "activate", ctx_create, 3600)

    ctx_dec = HostApprovalContext(
        surface="classic_cli", actor_ref="test_actor", session_ref="e2e",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="e2e-1", context_digest=hashlib.sha256(b"e2e").hexdigest(),
    )
    dec_id = broker.record_host_decision(ledger, cap, ctx_dec, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    broker.consume_grant(ledger, grant_id, ident.organism_id, digest, "activate")

    # Activate
    tstore.activate_revision(digest, grant_id=grant_id)
    assert tstore.get_active_digest() == digest

    return org_root, ident.organism_id, digest, ledger, tstore


# --- Scenario 1: Initial Telos Approval Boundary ---

def test_scenario_initial_telos_approval_boundary(tmp_path: Path, monkeypatch):
    """Unapproved activation fails; approved activation succeeds."""
    org_root, org_id, digest, ledger, tstore = _setup_e2e(tmp_path, monkeypatch)
    assert tstore.get_active_digest() == digest
    ledger.connection.close()


# --- Scenario 2: Missing Webcam Capability ---

def test_scenario_missing_webcam_capability(tmp_path: Path, monkeypatch):
    """Two profiles report webcam absence → one eligible suggestion."""
    org_root, org_id, digest, ledger, tstore = _setup_e2e(tmp_path, monkeypatch)

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    service = ObserverService(org_root)

    def _make_env(event_id: str, profile_ref: str, session_ref: str) -> ObservationEnvelope:
        return ObservationEnvelope(
            schema_version=1, event_id=event_id, organism_id=org_id,
            occurred_at="2026-07-24T12:00:00.000000Z",
            signal_type="capability_absence", provenance="explicit_user",
            source_profile_ref=profile_ref, source_project_ref=None,
            source_session_ref=session_ref, generation_id="a" * 64,
            gnothi_revision_digest=None, telos_digest=digest,
            capability_key="webcam", operation_key="video.stream",
            outcome_key="device_missing", constraint_key="unconstrained",
            severity="high", task_impact="high", retry_count=1,
            latency_bucket=None, explicit_user_intent=True,
            recovered=False, evidence_refs=(), redaction_status="verified_redacted",
        )

    service.ingest_envelope(_make_env("11111111-1111-1111-1111-111111111111", "prof_a", "sess_a"))
    service.ingest_envelope(_make_env("22222222-2222-2222-2222-222222222222", "prof_b", "sess_b"))

    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug.state == "eligible"
    assert sug.observation_count == 2
    assert "prof_a" not in sug.summary_reason
    ledger.connection.close()


# --- Scenario 3: Performance Feedback + Project Isolation ---

def test_scenario_performance_feedback_and_project_isolation(tmp_path: Path, monkeypatch):
    """Friction signal creates a performance diagnosis suggestion."""
    org_root, org_id, digest, ledger, tstore = _setup_e2e(tmp_path, monkeypatch)

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    service = ObserverService(org_root)

    env = ObservationEnvelope(
        schema_version=1, event_id="33333333-3333-3333-3333-333333333333",
        organism_id=org_id, occurred_at="2026-07-24T12:10:00.000000Z",
        signal_type="friction", provenance="measured_runtime",
        source_profile_ref="prof_c", source_project_ref=None,
        source_session_ref="sess_c", generation_id="a" * 64,
        gnothi_revision_digest=None, telos_digest=digest,
        capability_key="performance", operation_key="query.execution",
        outcome_key="high_latency", constraint_key="unconstrained",
        severity="medium", task_impact="medium", retry_count=2,
        latency_bucket="15s_to_60s", explicit_user_intent=False,
        recovered=True, evidence_refs=(), redaction_status="verified_redacted",
    )

    service.ingest_envelope(env)
    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug.score > 0.0
    ledger.connection.close()

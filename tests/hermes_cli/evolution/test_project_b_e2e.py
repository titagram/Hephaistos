"""End-to-End Scenarios for Autopoiesis Project B (Global Telos and Opportunity Observer)."""

import pytest
from pathlib import Path

from hermes_cli.evolution.bootstrap import ensure_evolution_initialized
from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.authorization import create_authorization_request, issue_grant
from hermes_cli.evolution.contract import content_digest
from hermes_cli.evolution.observation_contract import ObservationEnvelope
from hermes_cli.evolution.observer_service import ObserverService
from hermes_cli.evolution.organism_home import ensure_organism_directories
from hermes_cli.evolution.organism_identity import create_organism_identity
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


def create_test_telos(org_id: str) -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id=org_id,
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


def _issue_test_grant(org_root: Path, digest: str) -> str:
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")
    try:
        attempt_id = ledger.get_active_attempt_id()
    except Exception:
        attempt_id = ledger.create_attempt("operator", "test-source")
    
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
    return grant.grant_id


def test_scenario_initial_telos_approval_boundary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'organism'))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    ident = create_organism_identity(org_root)
    tstore = TelosStore(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")

    telos = create_test_telos(ident.organism_id)
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    # Unapproved activation must fail without a receipt
    with pytest.raises(Exception):
        tstore.activate_revision(digest, "fake_receipt_id", ledger)

    # Issue single-use approval receipt via ledger
    grant_id = _issue_test_grant(org_root, digest)
    tstore.activate_revision(digest, grant_id, ledger)

    assert tstore.get_active_digest() == digest


def test_scenario_missing_webcam_capability(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'organism'))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    ident = create_organism_identity(org_root)
    tstore = TelosStore(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")

    telos = create_test_telos(ident.organism_id)
    tstore.save_revision(telos)
    attempt_id = ledger.create_attempt("operator", "test-source")
    req = create_authorization_request(
        ledger, attempt_id=attempt_id, kind="telos_activation", subject_digest=telos.canonical_digest, scope={"action": "activate"}, ttl_seconds=3600
    )
    grant = issue_grant(
        ledger, request_id=req.request_id, approved_by="host_user", confirmation_digest=content_digest(req.canonical_payload(), domain="hades-evolution-authorization-request-v1")
    )
    receipt = grant.grant_id
    tstore.activate_revision(telos.canonical_digest, receipt, ledger)

    service = ObserverService(org_root)

    # Profile A signal
    env_a = ObservationEnvelope(
        schema_version=1,
        event_id="11111111-1111-1111-1111-111111111111",
        organism_id=ident.organism_id,
        occurred_at="2026-07-24T12:00:00.000000Z",
        signal_type="capability_absence",
        provenance="explicit_user",
        source_profile_ref="prof_a",
        source_project_ref="proj_a",
        source_session_ref="sess_a",
        generation_id="a" * 64,

        gnothi_revision_digest=None,
        telos_digest=telos.canonical_digest,
        capability_key="webcam",
        operation_key="video.stream",
        outcome_key="device_missing",
        constraint_key="unconstrained",
        severity="high",
        task_impact="high",
        retry_count=1,
        latency_bucket=None,
        explicit_user_intent=True,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )

    # Profile B signal
    env_b = ObservationEnvelope(
        schema_version=1,
        event_id="22222222-2222-2222-2222-222222222222",
        organism_id=ident.organism_id,
        occurred_at="2026-07-24T12:05:00.000000Z",
        signal_type="capability_absence",
        provenance="explicit_user",
        source_profile_ref="prof_b",
        source_project_ref="proj_b",
        source_session_ref="sess_b",
        generation_id="a" * 64,

        gnothi_revision_digest=None,
        telos_digest=telos.canonical_digest,
        capability_key="webcam",
        operation_key="video.stream",
        outcome_key="device_missing",
        constraint_key="unconstrained",
        severity="high",
        task_impact="high",
        retry_count=1,
        latency_bucket=None,
        explicit_user_intent=True,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )

    service.ingest_envelope(env_a)
    service.ingest_envelope(env_b)

    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug.state == "eligible"
    assert sug.observation_count == 2
    assert sug.distinct_session_count == 2
    # Verify no raw project IDs or secret paths appear in user-facing summary
    assert "prof_a" not in sug.summary_reason
    assert "proj_a" not in sug.summary_reason


def test_scenario_performance_feedback_and_project_isolation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'organism'))
    org_root = tmp_path / "organism"
    ensure_evolution_initialized(global_root=org_root)
    ident = create_organism_identity(org_root)
    tstore = TelosStore(org_root)
    ledger = EvolutionLedger(org_root / "evolution" / "evolution.db")

    telos = create_test_telos(ident.organism_id)
    tstore.save_revision(telos)
    attempt_id = ledger.create_attempt("operator", "test-source")
    req = create_authorization_request(
        ledger, attempt_id=attempt_id, kind="telos_activation", subject_digest=telos.canonical_digest, scope={"action": "activate"}, ttl_seconds=3600
    )
    grant = issue_grant(
        ledger, request_id=req.request_id, approved_by="host_user", confirmation_digest=content_digest(req.canonical_payload(), domain="hades-evolution-authorization-request-v1")
    )
    receipt = grant.grant_id
    tstore.activate_revision(telos.canonical_digest, receipt, ledger)

    service = ObserverService(org_root)

    env_perf = ObservationEnvelope(
        schema_version=1,
        event_id="33333333-3333-3333-3333-333333333333",
        organism_id=ident.organism_id,
        occurred_at="2026-07-24T12:10:00.000000Z",
        signal_type="friction",
        provenance="measured_runtime",
        source_profile_ref="prof_c",
        source_project_ref="proj_c",
        source_session_ref="sess_c",
        generation_id="a" * 64,

        gnothi_revision_digest=None,
        telos_digest=telos.canonical_digest,
        capability_key="performance",
        operation_key="query.execution",
        outcome_key="high_latency",
        constraint_key="unconstrained",
        severity="medium",
        task_impact="medium",
        retry_count=2,
        latency_bucket="15s_to_60s",
        explicit_user_intent=False,
        recovered=True,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )

    service.ingest_envelope(env_perf)
    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug.score > 0.0

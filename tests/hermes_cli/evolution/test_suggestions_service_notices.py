"""Tests for suggestions store, ObserverService scanning, circuit breaker, and notices."""

import pytest
from pathlib import Path

from hermes_cli.evolution.observation_contract import ObservationEnvelope
from hermes_cli.evolution.observer_service import ObserverService, CircuitBreakerOpen
from hermes_cli.evolution.notices import generate_notices
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)
from hermes_cli.evolution.telos_store import TelosStore


def create_sample_telos() -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="To assist the user efficiently.",
        desired_traits=(
            DesiredTrait("reliable", "Reliability.", ("trait.reliability",), 5),
        ),
        capability_directions=(
            CapabilityDirection("webcam", "Camera image capture.", ("webcam",), 4),
        ),
        priorities=(
            Priority("safety", "Safety.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("prohib", "Prohibited.", ("prohib",), 5),
        ),
        proactivity_policy=ProactivityPolicy("passive", "Passive.", ("passive",), 3),
        success_indicators=(
            SuccessIndicator("ind", "Indicator.", ("ind",), 4),
        ),
    )


def create_env(event_id: str, cap: str = "webcam") -> ObservationEnvelope:
    return ObservationEnvelope(
        schema_version=1,
        event_id=event_id,
        organism_id="00000000-0000-0000-0000-000000000000",
        occurred_at="2026-07-24T12:00:00.000000Z",
        signal_type="capability_absence",
        provenance="explicit_user",
        source_profile_ref="prof1",
        source_project_ref=None,
        source_session_ref="sess1",
        generation_id="a" * 64,

        gnothi_revision_digest=None,
        telos_digest="b" * 64,

        capability_key=cap,
        operation_key="op1",
        outcome_key="missing",
        constraint_key="none",
        severity="high",
        task_impact="medium",
        retry_count=1,
        latency_bucket=None,
        explicit_user_intent=True,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )


def test_observer_service_ingest_and_scan(tmp_path: Path):
    org_root = tmp_path / "organism"
    tstore = TelosStore(org_root)
    telos = create_sample_telos()
    tstore.save_revision(telos)
    rec = tstore.issue_approval_receipt(telos.canonical_digest, action="activate")
    tstore.activate_revision(telos.canonical_digest, rec.receipt_id)

    service = ObserverService(org_root)
    env1 = create_env("11111111-1111-1111-1111-111111111111")
    assert service.ingest_envelope(env1) is True

    # Duplicate envelope ingestion -> False
    assert service.ingest_envelope(env1) is False

    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    assert suggestions[0].state == "eligible"

    notices = generate_notices(suggestions, notice_min_score=0.50)
    assert len(notices) == 1
    assert "Autopoiesis opportunity detected" in notices[0].text


def test_circuit_breaker(tmp_path: Path):
    service = ObserverService(tmp_path / "organism", max_consecutive_errors=2)
    service.consecutive_errors = 2
    service.circuit_open = True

    with pytest.raises(CircuitBreakerOpen):
        service.ingest_envelope(create_env("22222222-2222-2222-2222-222222222222"))

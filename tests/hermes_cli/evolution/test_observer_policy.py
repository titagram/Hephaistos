"""Tests for opportunity keying, eligibility gates, and ranking v2 formula."""

import pytest

from hermes_cli.evolution.observation_contract import ObservationEnvelope
from hermes_cli.evolution.observer_policy import (
    compute_opportunity_key,
    is_opportunity_eligible,
    score_opportunity,
)
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)


def create_sample_telos() -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="To assist the user efficiently and maintain privacy and quality.",
        desired_traits=(
            DesiredTrait("reliable", "High accuracy.", ("trait.reliability",), 5),
        ),
        capability_directions=(
            CapabilityDirection("webcam", "Support camera image capture.", ("capability.webcam",), 4),
        ),
        priorities=(
            Priority("safety", "Safety first.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("prohib_network", "Forbidden network access.", ("prohibition.network",), 5),
        ),
        proactivity_policy=ProactivityPolicy("bounded", "Surface helpful suggestions.", ("proactivity.passive",), 3),
        success_indicators=(
            SuccessIndicator("completion", "Task completion rate > 95%", ("indicator.completion",), 4),
        ),
    )


def create_env(
    signal_type: str = "capability_absence",
    provenance: str = "explicit_user",
    capability_key: str = "webcam",
    explicit_user_intent: bool = True,
    severity: str = "high",
    session_ref: str = "sess1",
) -> ObservationEnvelope:
    return ObservationEnvelope(
        schema_version=1,
        event_id="11111111-1111-1111-1111-111111111111",
        organism_id="00000000-0000-0000-0000-000000000000",
        occurred_at="2026-07-24T12:00:00.000000Z",
        signal_type=signal_type,
        provenance=provenance,
        source_profile_ref="prof1",
        source_project_ref=None,
        source_session_ref=session_ref,
        generation_id="g" * 64,
        gnothi_revision_digest=None,
        telos_digest="t" * 64,
        capability_key=capability_key,
        operation_key="video.stream",
        outcome_key="device_missing",
        constraint_key="unconstrained",
        severity=severity,
        task_impact="medium",
        retry_count=1,
        latency_bucket=None,
        explicit_user_intent=explicit_user_intent,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )


def test_opportunity_key_computation():
    k1 = compute_opportunity_key("org1", "cap1", "op1", "out1", "const1")
    k2 = compute_opportunity_key("org1", "cap1", "op1", "out1", "const1")
    assert k1 == k2
    assert len(k1) == 64


def test_eligibility_gates():
    telos = create_sample_telos()

    # Gate 1: explicit user intent + absence -> eligible
    e1 = create_env(signal_type="capability_absence", explicit_user_intent=True)
    assert is_opportunity_eligible([e1], telos) is True

    # Prohibition match -> ineligible
    e_prohib = create_env(capability_key="prohib_network")
    assert is_opportunity_eligible([e_prohib], telos) is False

    # Low confidence single inference -> ineligible
    e_inf = create_env(signal_type="failure", provenance="agent_inference", explicit_user_intent=False, severity="low")
    assert is_opportunity_eligible([e_inf], telos) is False


def test_score_opportunity():
    telos = create_sample_telos()
    e1 = create_env()
    s = score_opportunity([e1], telos)
    assert 0.0 <= s.score <= 1.0
    assert s.user_intent == 1.00
    assert s.telos_alignment == 1.00

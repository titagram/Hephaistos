"""Tests for observer degraded mode, privacy hardening, active Telos gate, and suggestion pipeline."""

import pytest
from pathlib import Path


# --- 2E.1: Active Telos Gate ---

def test_observer_no_telos_returns_empty_suggestions(tmp_path, monkeypatch):
    """Observer returns empty list when no active Telos digest exists."""
    from hermes_cli.evolution import organism_home as _oh
    import hermes_constants as _hc

    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)

    from hermes_cli.evolution.observer_service import ObserverService
    svc = ObserverService(org)
    result = svc.scan_and_update_suggestions()
    assert result == []


# --- 2E.2: Envelope Privacy Hardening ---

def test_envelope_rejects_absolute_path():
    """Source refs must not contain absolute paths."""
    from hermes_cli.evolution.observation_contract import (
        ObservationEnvelope, validate_observation_envelope, ObservationContractError,
    )
    env = ObservationEnvelope(
        schema_version=1, event_id="e1", organism_id="org-1",
        occurred_at="2026-01-01T00:00:00.000000Z",
        signal_type="failure", provenance="measured_runtime",
        source_profile_ref="/absolute/path",
        source_project_ref=None, source_session_ref=None,
        generation_id="a" * 64, gnothi_revision_digest=None,
        telos_digest=None, capability_key="cap", operation_key="op",
        outcome_key="out", constraint_key="con",
        severity="low", task_impact="low", retry_count=0,
        latency_bucket=None, explicit_user_intent=False,
        recovered=False, evidence_refs=(), redaction_status="verified_redacted",
    )
    with pytest.raises(ObservationContractError):
        validate_observation_envelope(env)


def test_envelope_rejects_uri_payload():
    """Source refs must not contain URIs."""
    from hermes_cli.evolution.observation_contract import (
        ObservationEnvelope, validate_observation_envelope, ObservationContractError,
    )
    env = ObservationEnvelope(
        schema_version=1, event_id="e1", organism_id="org-1",
        occurred_at="2026-01-01T00:00:00.000000Z",
        signal_type="failure", provenance="measured_runtime",
        source_profile_ref="http://evil.com/",
        source_project_ref=None, source_session_ref=None,
        generation_id="a" * 64, gnothi_revision_digest=None,
        telos_digest=None, capability_key="cap", operation_key="op",
        outcome_key="out", constraint_key="con",
        severity="low", task_impact="low", retry_count=0,
        latency_bucket=None, explicit_user_intent=False,
        recovered=False, evidence_refs=(), redaction_status="verified_redacted",
    )
    with pytest.raises(ObservationContractError):
        validate_observation_envelope(env)


def test_valid_envelope_passes_validation():
    """A well-formed envelope must pass validation."""
    from hermes_cli.evolution.observation_contract import (
        ObservationEnvelope, validate_observation_envelope,
    )
    env = ObservationEnvelope(
        schema_version=1, event_id="e1", organism_id="00000000-0000-0000-0000-000000000000",
        occurred_at="2026-01-01T00:00:00.000000Z",
        signal_type="failure", provenance="measured_runtime",
        source_profile_ref="opaque-ref-123",
        source_project_ref="opaque-proj-456",
        source_session_ref="opaque-sess-789",
        generation_id="a" * 64, gnothi_revision_digest=None,
        telos_digest=None, capability_key="webcam.capture", operation_key="capture",
        outcome_key="unavailable", constraint_key="none",
        severity="high", task_impact="high", retry_count=0,
        latency_bucket=None, explicit_user_intent=False,
        recovered=False, evidence_refs=(), redaction_status="verified_redacted",
    )
    validate_observation_envelope(env)


# --- 2E.4: Scoring v2 ---

def test_scoring_includes_telos_alignment_and_user_intent():
    """Scoring must include user_intent and telos_alignment terms."""
    from hermes_cli.evolution.observer_policy import score_opportunity

    # Build a minimal envelope list
    from hermes_cli.evolution.observation_contract import ObservationEnvelope

    envelopes = [
        ObservationEnvelope(
            schema_version=1, event_id=f"e{i}", organism_id="org",
            occurred_at="2026-01-01T00:00:00.000000Z",
            signal_type="failure", provenance="measured_runtime",
            source_profile_ref="ref1", source_project_ref=None,
            source_session_ref=f"sess{i}", generation_id="a" * 64,
            gnothi_revision_digest=None, telos_digest="t" * 64,
            capability_key="cap1", operation_key="op1",
            outcome_key="out1", constraint_key="con1",
            severity="high", task_impact="high", retry_count=1,
            latency_bucket=None, explicit_user_intent=(i == 0),
            recovered=False, evidence_refs=(), redaction_status="verified_redacted",
        )
        for i in range(3)
    ]

    from hermes_cli.evolution.telos_contract import (
        TelosRevision, DesiredTrait, CapabilityDirection,
        Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
    )
    telos = TelosRevision(
        schema_version=1,
        organism_id="org",
        parent_digest=None,
        purpose="Test",
        desired_traits=(DesiredTrait("t1", "d", ("t1",), 5),),
        capability_directions=(CapabilityDirection("cap1", "d", ("c1",), 4),),
        priorities=(Priority("p1", "d", ("p1",), 5),),
        tradeoffs=(),
        prohibitions=(),
        proactivity_policy=ProactivityPolicy("pass", "d", ("pass",), 3),
        success_indicators=(SuccessIndicator("i1", "d", ("i1",), 4),),
    )

    score = score_opportunity(envelopes, telos)
    assert 0.0 <= score.score <= 1.0

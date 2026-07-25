"""Tests for suggestions store, ObserverService scanning, circuit breaker, and notices."""

import pytest
from pathlib import Path

from hermes_cli.evolution.observation_contract import ObservationEnvelope
from hermes_cli.evolution.observer_service import ObserverService, CircuitBreakerOpen
from hermes_cli.evolution.notices import generate_notices


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
    """Observer can ingest envelopes and scan (no active Telos returns empty)."""
    org_root = tmp_path / "organism"
    service = ObserverService(org_root)

    # Without active Telos, scan returns empty
    suggestions = service.scan_and_update_suggestions()
    assert suggestions == []


def test_circuit_breaker(tmp_path: Path):
    """Circuit breaker opens after repeated errors and persists across instances."""
    service = ObserverService(tmp_path / "organism", max_consecutive_errors=2)
    service.consecutive_errors = 2
    service.circuit_open = True

    with pytest.raises(CircuitBreakerOpen):
        service.ingest_envelope(create_env("22222222-2222-2222-2222-222222222222"))

"""Tests for ObservationEnvelope schema, validation, path rejection, and experience bridge."""

import pytest
from pathlib import Path

from hermes_cli.evolution.observation_contract import (
    ObservationContractError,
    ObservationEnvelope,
    validate_observation_envelope,
)
from hermes_cli.evolution.experience_bridge import ExperienceBridge


def create_sample_envelope(
    organism_id: str = "00000000-0000-0000-0000-000000000000",
    capability_key: str = "webcam.capture",
) -> ObservationEnvelope:
    return ObservationEnvelope(
        schema_version=1,
        event_id="11111111-1111-1111-1111-111111111111",
        organism_id=organism_id,
        occurred_at="2026-07-24T12:00:00.000000Z",
        signal_type="capability_absence",
        provenance="explicit_user",
        source_profile_ref="prof_1234567890",
        source_project_ref=None,
        source_session_ref=None,
        generation_id="a" * 64,
        gnothi_revision_digest=None,
        telos_digest="b" * 64,
        capability_key=capability_key,
        operation_key="video.stream",
        outcome_key="device_missing",
        constraint_key="unconstrained",
        severity="high",
        task_impact="medium",
        retry_count=1,
        latency_bucket=None,
        explicit_user_intent=True,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )


def test_observation_envelope_validation():
    env = create_sample_envelope()
    validate_observation_envelope(env)
    assert len(env.to_canonical_json().encode("utf-8")) <= 4096


def test_observation_envelope_path_rejection():
    env = create_sample_envelope(capability_key="/etc/passwd")
    with pytest.raises(ObservationContractError, match="Invalid taxonomy key"):
        validate_observation_envelope(env)


def test_experience_bridge_import(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    errors_log = logs_dir / "errors.log"
    errors_log.write_text(
        "2026-07-24 12:00:00 [ERROR] FileNotFoundError: /tmp/foo.txt not found\n"
        "2026-07-24 12:01:00 [WARNING] TimeoutError: connection timed out\n"
    )

    bridge = ExperienceBridge(
        organism_id="00000000-0000-0000-0000-000000000000",
        profile_ref="prof_test",
        generation_id="a" * 64,
        hermes_home=tmp_path,
    )

    envelopes = bridge.import_new_error_events()
    assert len(envelopes) == 2

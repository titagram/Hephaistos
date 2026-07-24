"""Strict privacy and fail-closed validation tests for ObservationEnvelope."""

import pytest
from hermes_cli.evolution.observation_contract import (
    ObservationContractError,
    ObservationEnvelope,
    validate_observation_envelope,
)


def create_valid_envelope() -> ObservationEnvelope:
    return ObservationEnvelope(
        schema_version=1,
        event_id="obs_0123456789ab",
        organism_id="00000000-0000-0000-0000-000000000000",
        occurred_at="2026-07-24T12:00:00.000000Z",
        signal_type="failure",
        provenance="measured_runtime",
        source_profile_ref="prof_alpha",
        source_project_ref="proj_beta",
        source_session_ref="sess_gamma",
        generation_id="a" * 64,
        gnothi_revision_digest="b" * 64,
        telos_digest="c" * 64,
        capability_key="terminal.exec",
        operation_key="run_command",
        outcome_key="exit_code_non_zero",
        constraint_key="timeout",
        severity="medium",
        task_impact="low",
        retry_count=1,
        latency_bucket="1s_to_5s",
        explicit_user_intent=False,
        recovered=True,
        evidence_refs=("d" * 64,),
        redaction_status="verified_redacted",
    )


def test_valid_envelope_passes():
    env = create_valid_envelope()
    validate_observation_envelope(env)


def test_rejects_file_uri_and_paths_in_refs():
    env = create_valid_envelope()
    # Path in source_project_ref
    bad_env = ObservationEnvelope(**{**env.__dict__, "source_project_ref": "file:///Users/gabriele/secret.py"})
    with pytest.raises(ObservationContractError, match="path|URI|invalid"):
        validate_observation_envelope(bad_env)

    # Windows path in source_profile_ref
    bad_env2 = ObservationEnvelope(**{**env.__dict__, "source_profile_ref": r"C:\Users\Secret\profile"})
    with pytest.raises(ObservationContractError, match="path|URI|invalid"):
        validate_observation_envelope(bad_env2)


def test_rejects_invalid_timestamp():
    env = create_valid_envelope()
    bad_env = ObservationEnvelope(**{**env.__dict__, "occurred_at": "not-a-timestamp"})
    with pytest.raises(ObservationContractError, match="timestamp"):
        validate_observation_envelope(bad_env)


def test_rejects_invalid_digests():
    env = create_valid_envelope()
    bad_env = ObservationEnvelope(**{**env.__dict__, "generation_id": "short-digest"})
    with pytest.raises(ObservationContractError, match="generation_id|digest"):
        validate_observation_envelope(bad_env)


def test_rejects_credential_and_secret_patterns():
    env = create_valid_envelope()
    bad_env = ObservationEnvelope(**{**env.__dict__, "source_session_ref": "sk-proj-1234567890abcdef12345678"})
    with pytest.raises(ObservationContractError, match="secret|credential|invalid"):
        validate_observation_envelope(bad_env)

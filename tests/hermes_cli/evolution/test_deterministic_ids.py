"""Tests for deterministic ID generation and replay stability of opportunity keys and suggestions."""

import pytest
from pathlib import Path

from hermes_cli.evolution.observer_policy import compute_opportunity_key, OpportunityScore
from hermes_cli.evolution.suggestions import SuggestionRepository
from hermes_cli.evolution.observation_contract import ObservationEnvelope


def create_sample_envelope(session_ref: str = "sess_1") -> ObservationEnvelope:
    return ObservationEnvelope(
        schema_version=1,
        event_id="obs_0123456789ab",
        organism_id="00000000-0000-0000-0000-000000000000",
        occurred_at="2026-07-24T12:00:00.000000Z",
        signal_type="failure",
        provenance="measured_runtime",
        source_profile_ref="prof_alpha",
        source_project_ref=None,
        source_session_ref=session_ref,
        generation_id="a" * 64,
        gnothi_revision_digest=None,
        telos_digest=None,
        capability_key="terminal.exec",
        operation_key="run_command",
        outcome_key="timeout",
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


def test_deterministic_opportunity_key():
    k1 = compute_opportunity_key("00000000-0000-0000-0000-000000000000", "terminal.exec", "run_command", "timeout", "unconstrained")
    k2 = compute_opportunity_key("00000000-0000-0000-0000-000000000000", "terminal.exec", "run_command", "timeout", "unconstrained")
    assert k1 == k2
    assert len(k1) == 64


    k3 = compute_opportunity_key("00000000-0000-0000-0000-000000000000", "terminal.exec", "run_command", "success", "unconstrained")
    assert k1 != k3


def test_deterministic_suggestion_id(tmp_path: Path):
    db1 = tmp_path / "db1.db"
    db2 = tmp_path / "db2.db"

    from hermes_cli.evolution.ledger import EvolutionLedger
    EvolutionLedger(db1)
    EvolutionLedger(db2)

    repo1 = SuggestionRepository(db1)
    repo2 = SuggestionRepository(db2)

    opp_key = compute_opportunity_key("00000000-0000-0000-0000-000000000000", "terminal.exec", "run_command", "timeout", "unconstrained")
    score = OpportunityScore(0.8, 1.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.1, 0.2, "v2")
    env = create_sample_envelope()

    sug1 = repo1.upsert_suggestion(opp_key, "observing", "t" * 64, score, [env], "Summary")
    sug2 = repo2.upsert_suggestion(opp_key, "observing", "t" * 64, score, [env], "Summary")

    assert sug1.suggestion_id == sug2.suggestion_id
    assert sug1.opportunity_key == sug2.opportunity_key

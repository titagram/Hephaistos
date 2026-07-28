"""Behavioral separation between public research and evolution authority."""

from __future__ import annotations

import pytest

from hermes_cli.evolution.authorization import (
    create_authorization_request,
    issue_grant,
)
from hermes_cli.evolution.contract import content_digest
from hermes_cli.evolution.ledger import EvolutionLedger, EvolutionLedgerError
from hermes_cli.evolution.state_machine import TransitionRequest
from toolsets import resolve_toolset


DIGEST = "a" * 64
RESEARCH_SCOPE = {
    "source_classes": ["documentation"],
    "domains": ["example.com"],
    "operations": ["search", "retrieve"],
    "duration": 60,
}


def _issue_research_grant(ledger: EvolutionLedger, attempt_id: str):
    request = create_authorization_request(
        ledger,
        attempt_id=attempt_id,
        kind="research",
        subject_digest=DIGEST,
        scope=RESEARCH_SCOPE,
        ttl_seconds=120,
    )
    return issue_grant(
        ledger,
        request_id=request.request_id,
        approved_by="local-operator",
        confirmation_digest=content_digest(
            request.canonical_payload(),
            domain="hades-evolution-authorization-request-v1",
        ),
    )


def test_public_web_capability_does_not_require_research_authorization():
    assert "web_search" in resolve_toolset("hermes-cli")


def test_research_grant_cannot_advance_build_or_promotion(tmp_path):
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    try:
        build_attempt = ledger.create_attempt("manual", "build-policy")
        build_grant = _issue_research_grant(ledger, build_attempt)
        ledger.transition(
            TransitionRequest(
                attempt_id=build_attempt,
                prior_state="draft",
                next_state="research_authorized",
                actor="operator",
                input_digests=(DIGEST,),
                authorization_id=build_grant.grant_id,
                reason="research lifecycle approval",
            )
        )
        ledger.transition(
            TransitionRequest(
                attempt_id=build_attempt,
                prior_state="research_authorized",
                next_state="blueprint_ready",
                actor="workshop",
                input_digests=(DIGEST,),
                authorization_id=None,
                reason="blueprint prepared",
            )
        )

        with pytest.raises(
            EvolutionLedgerError, match="transition_authorization_grant_mismatch"
        ):
            ledger.transition(
                TransitionRequest(
                    attempt_id=build_attempt,
                    prior_state="blueprint_ready",
                    next_state="build_approved",
                    actor="operator",
                    input_digests=(DIGEST,),
                    authorization_id=build_grant.grant_id,
                    reason="attempt build with research grant",
                )
            )
        assert ledger.connection.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?", (build_attempt,)
        ).fetchone()[0] == "blueprint_ready"

        promotion_attempt = ledger.create_attempt("manual", "promotion-policy")
        promotion_grant = _issue_research_grant(ledger, promotion_attempt)
        ledger.connection.execute(
            "UPDATE attempts SET state = 'promotion_ready' WHERE attempt_id = ?",
            (promotion_attempt,),
        )

        with pytest.raises(
            EvolutionLedgerError, match="transition_authorization_grant_mismatch"
        ):
            ledger.transition(
                TransitionRequest(
                    attempt_id=promotion_attempt,
                    prior_state="promotion_ready",
                    next_state="active",
                    actor="supervisor",
                    input_digests=(DIGEST,),
                    authorization_id=promotion_grant.grant_id,
                    reason="attempt promotion with research grant",
                )
            )
        assert ledger.connection.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?", (promotion_attempt,)
        ).fetchone()[0] == "promotion_ready"
    finally:
        ledger.connection.close()

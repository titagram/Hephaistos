from __future__ import annotations

from itertools import product

import pytest

from hermes_cli.evolution.contract import EvolutionContractError
from hermes_cli.evolution.state_machine import (
    AttemptState,
    TransitionRequest,
    validate_transition,
)

DIGEST = "0123456789abcdef" * 4

STATES: tuple[AttemptState, ...] = (
    "draft",
    "research_authorized",
    "blueprint_ready",
    "build_approved",
    "building",
    "quarantined",
    "canary_running",
    "promotion_ready",
    "active",
    "stable",
    "rejected",
    "research_expired",
    "build_failed",
    "canary_failed",
    "rolled_back",
    "retired",
)

ALLOWED_TRANSITIONS: tuple[
    tuple[AttemptState, AttemptState, str, str | None], ...
] = (
    ("draft", "research_authorized", "operator", "research"),
    ("research_authorized", "blueprint_ready", "workshop", None),
    ("blueprint_ready", "build_approved", "operator", "build"),
    ("build_approved", "building", "builder", None),
    ("building", "quarantined", "builder", None),
    ("quarantined", "canary_running", "supervisor", None),
    ("canary_running", "promotion_ready", "supervisor", None),
    ("promotion_ready", "active", "supervisor", "promotion"),
    ("active", "stable", "supervisor", None),
    ("draft", "rejected", "operator", None),
    ("research_authorized", "rejected", "operator", None),
    ("research_authorized", "research_expired", "workshop", None),
    ("blueprint_ready", "rejected", "operator", None),
    ("build_approved", "rejected", "operator", None),
    ("building", "build_failed", "builder", None),
    ("canary_running", "canary_failed", "supervisor", None),
    ("promotion_ready", "rejected", "operator", None),
    ("active", "rolled_back", "supervisor", None),
    ("stable", "rolled_back", "supervisor", None),
    ("stable", "retired", "supervisor", None),
    ("rolled_back", "retired", "supervisor", None),
)

ALLOWED_EDGES = frozenset(
    (prior_state, next_state)
    for prior_state, next_state, _actor, _authorization in ALLOWED_TRANSITIONS
)


def request(
    prior_state: AttemptState,
    next_state: AttemptState,
    actor: str,
    *,
    authorization_id: str | None = None,
    input_digests: tuple[str, ...] = (DIGEST,),
) -> TransitionRequest:
    return TransitionRequest(
        attempt_id="attempt-1",
        prior_state=prior_state,
        next_state=next_state,
        actor=actor,
        input_digests=input_digests,
        authorization_id=authorization_id,
        reason="contract test",
    )


@pytest.mark.parametrize(
    ("prior_state", "next_state", "actor", "authorization_kind"),
    ALLOWED_TRANSITIONS,
)
def test_complete_allowed_transition_policy(
    prior_state: AttemptState,
    next_state: AttemptState,
    actor: str,
    authorization_kind: str | None,
) -> None:
    authorization_id = (
        f"{authorization_kind}-grant-1" if authorization_kind else None
    )

    assert (
        validate_transition(
            request(
                prior_state,
                next_state,
                actor,
                authorization_id=authorization_id,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("prior_state", "next_state"),
    [
        edge
        for edge in product(STATES, repeat=2)
        if edge not in ALLOWED_EDGES
    ],
)
def test_every_unlisted_transition_is_rejected(
    prior_state: AttemptState, next_state: AttemptState
) -> None:
    with pytest.raises(EvolutionContractError) as error:
        validate_transition(request(prior_state, next_state, "operator"))

    assert error.value.code == "transition_not_allowed"


@pytest.mark.parametrize(
    ("prior_state", "next_state", "required_actor", "_authorization_kind"),
    ALLOWED_TRANSITIONS,
)
def test_allowed_transitions_require_the_designated_actor(
    prior_state: AttemptState,
    next_state: AttemptState,
    required_actor: str,
    _authorization_kind: str | None,
) -> None:
    wrong_actor = next(
        actor
        for actor in ("operator", "workshop", "builder", "supervisor")
        if actor != required_actor
    )

    with pytest.raises(EvolutionContractError) as error:
        validate_transition(
            request(
                prior_state,
                next_state,
                wrong_actor,
                authorization_id="grant-1",
            )
        )

    assert error.value.code == "transition_actor_mismatch"


@pytest.mark.parametrize(
    ("prior_state", "next_state", "actor"),
    [
        ("draft", "research_authorized", "operator"),
        ("blueprint_ready", "build_approved", "operator"),
        ("promotion_ready", "active", "supervisor"),
    ],
)
def test_approval_bound_entries_require_authorization(
    prior_state: AttemptState, next_state: AttemptState, actor: str
) -> None:
    with pytest.raises(EvolutionContractError) as error:
        validate_transition(request(prior_state, next_state, actor))

    assert error.value.code == "transition_authorization_required"


def test_transition_request_is_frozen() -> None:
    transition = request("building", "quarantined", "builder")

    with pytest.raises((AttributeError, TypeError)):
        transition.actor = "operator"  # type: ignore[misc]


def test_transition_rejects_noncanonical_input_digest() -> None:
    with pytest.raises(EvolutionContractError) as error:
        validate_transition(
            request(
                "building",
                "quarantined",
                "builder",
                input_digests=("not-a-digest",),
            )
        )

    assert error.value.code == "invalid_digest"

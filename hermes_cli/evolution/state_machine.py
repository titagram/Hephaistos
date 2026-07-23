"""Closed lifecycle transition policy for local evolution attempts."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

from .contract import EvolutionContractError, require_digest

AttemptState: TypeAlias = Literal[
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
]
GrantKind: TypeAlias = Literal["research", "build", "promotion"]


@dataclass(frozen=True)
class TransitionRequest:
    attempt_id: str
    prior_state: AttemptState
    next_state: AttemptState
    actor: str
    input_digests: tuple[str, ...]
    authorization_id: str | None
    reason: str


@dataclass(frozen=True)
class _TransitionPolicy:
    actor: str
    authorization_kind: GrantKind | None = None


_TRANSITION_POLICY = MappingProxyType(
    {
        ("draft", "research_authorized"): _TransitionPolicy(
            "operator", "research"
        ),
        ("research_authorized", "blueprint_ready"): _TransitionPolicy(
            "workshop"
        ),
        ("blueprint_ready", "build_approved"): _TransitionPolicy(
            "operator", "build"
        ),
        ("build_approved", "building"): _TransitionPolicy("builder"),
        ("building", "quarantined"): _TransitionPolicy("builder"),
        ("quarantined", "canary_running"): _TransitionPolicy("supervisor"),
        ("canary_running", "promotion_ready"): _TransitionPolicy("supervisor"),
        ("promotion_ready", "active"): _TransitionPolicy(
            "supervisor", "promotion"
        ),
        ("active", "stable"): _TransitionPolicy("supervisor"),
        ("draft", "rejected"): _TransitionPolicy("operator"),
        ("research_authorized", "rejected"): _TransitionPolicy("operator"),
        ("research_authorized", "research_expired"): _TransitionPolicy(
            "workshop"
        ),
        ("blueprint_ready", "rejected"): _TransitionPolicy("operator"),
        ("build_approved", "rejected"): _TransitionPolicy("operator"),
        ("building", "build_failed"): _TransitionPolicy("builder"),
        ("canary_running", "canary_failed"): _TransitionPolicy("supervisor"),
        ("promotion_ready", "rejected"): _TransitionPolicy("operator"),
        ("active", "rolled_back"): _TransitionPolicy("supervisor"),
        ("stable", "rolled_back"): _TransitionPolicy("supervisor"),
        ("stable", "retired"): _TransitionPolicy("supervisor"),
        ("rolled_back", "retired"): _TransitionPolicy("supervisor"),
    }
)


def validate_transition(request: TransitionRequest) -> None:
    """Reject any lifecycle transition outside the closed v1 policy."""

    if not isinstance(request, TransitionRequest):
        raise EvolutionContractError("invalid_transition_request")

    policy = _TRANSITION_POLICY.get((request.prior_state, request.next_state))
    if policy is None:
        raise EvolutionContractError("transition_not_allowed")
    if request.actor != policy.actor:
        raise EvolutionContractError("transition_actor_mismatch")
    if policy.authorization_kind is not None and request.authorization_id is None:
        raise EvolutionContractError("transition_authorization_required")
    if not isinstance(request.input_digests, tuple):
        raise EvolutionContractError("invalid_input_digests")
    for digest in request.input_digests:
        require_digest(digest)

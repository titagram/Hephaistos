"""Pure functional observer policy: opportunity key computation, eligibility gating, and ranking v2."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from .observation_contract import ObservationEnvelope, PROVENANCE_WEIGHTS
from .telos_contract import TelosRevision

IMPACT_WEIGHTS = {
    "critical": 1.00,
    "high": 0.80,
    "medium": 0.50,
    "low": 0.25,
    "unknown": 0.10,
}

RISK_TABLE = {
    "observe": 0.10,
    "local_read": 0.25,
    "local_write": 0.50,
    "device": 0.65,
    "network": 0.70,
    "privileged": 0.95,
    "unknown": 0.85,
}

COST_TABLE = {
    "trivial": 0.10,
    "small": 0.25,
    "medium": 0.50,
    "large": 0.75,
    "unknown": 0.80,
}


@dataclass(frozen=True)
class OpportunityScore:
    score: float
    user_intent: float
    telos_alignment: float
    impact: float
    recurrence: float
    confidence: float
    reuse: float
    risk: float
    expected_cost: float
    policy_version: str = "v2"


def compute_opportunity_key(
    organism_id: str,
    capability_key: str,
    operation_key: str,
    outcome_key: str,
    constraint_key: str,
) -> str:
    raw = f"opportunity_v1:{organism_id}:{capability_key}:{operation_key}:{outcome_key}:{constraint_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_telos_prohibition(telos: TelosRevision, capability_key: str, constraint_key: str) -> bool:
    """Return True if Telos prohibits this capability or constraint."""
    for p in telos.prohibitions:
        if p.id == capability_key or p.id == constraint_key:
            return True
        for tag in p.tags:
            if tag == capability_key or tag == constraint_key:
                return True
    return False


def is_opportunity_eligible(
    envelopes: Sequence[ObservationEnvelope],
    active_telos: TelosRevision | None,
) -> bool:
    if not envelopes or active_telos is None:
        return False

    valid_envelopes = [e for e in envelopes if e.redaction_status == "verified_redacted"]
    if not valid_envelopes:
        return False

    first = valid_envelopes[0]
    if check_telos_prohibition(active_telos, first.capability_key, first.constraint_key):
        return False

    # Check 4 eligibility gates
    # Gate 1: explicit user intent + capability absence
    gate1 = any(e.explicit_user_intent and e.signal_type == "capability_absence" for e in valid_envelopes)
    if gate1:
        return True

    # Gate 2: explicit high-impact user feedback
    gate2 = any(e.signal_type == "user_feedback" and e.severity in ("critical", "high") for e in valid_envelopes)
    if gate2:
        return True

    # Gate 3: at least 3 compatible observations across distinct sessions
    distinct_sessions = {e.source_session_ref for e in valid_envelopes if e.source_session_ref}
    gate3 = len(distinct_sessions) >= 3
    if gate3:
        return True

    # Gate 4: Gnothi-verified gap + operational event
    has_gnothi = any(e.provenance == "gnothi_verified" for e in valid_envelopes)
    has_ops = any(e.provenance in ("measured_runtime", "structured_tool_result") or e.signal_type == "failure" for e in valid_envelopes)
    gate4 = has_gnothi and has_ops
    if gate4:
        return True

    return False


def calculate_user_intent_term(envelopes: Sequence[ObservationEnvelope]) -> float:
    max_val = 0.0
    for e in envelopes:
        if e.explicit_user_intent and e.signal_type == "capability_absence":
            val = 1.00
        elif e.signal_type == "user_feedback":
            val = 0.90
        elif e.provenance == "explicit_user":
            val = 0.85
        elif e.provenance == "measured_runtime":
            val = 0.30
        elif e.provenance == "agent_inference":
            val = 0.10
        else:
            val = 0.10
        if val > max_val:
            max_val = val
    return max_val


def calculate_telos_alignment_term(active_telos: TelosRevision, capability_key: str) -> float:
    # Exact capability direction match
    for cap in active_telos.capability_directions:
        if cap.id == capability_key or capability_key in cap.tags:
            return 1.00

    # Desired trait match
    for trait in active_telos.desired_traits:
        if trait.id == capability_key or capability_key in trait.tags:
            return 0.75

    # Success indicator match
    for ind in active_telos.success_indicators:
        if ind.id == capability_key or capability_key in ind.tags:
            return 0.60

    return 0.25


def score_opportunity(
    envelopes: Sequence[ObservationEnvelope],
    active_telos: TelosRevision,
) -> OpportunityScore:
    valid_envelopes = [e for e in envelopes if e.redaction_status == "verified_redacted"]
    if not valid_envelopes:
        return OpportunityScore(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.85, 0.80)

    user_intent = calculate_user_intent_term(valid_envelopes)
    telos_alignment = calculate_telos_alignment_term(active_telos, valid_envelopes[0].capability_key)

    impact = max(
        max(IMPACT_WEIGHTS.get(e.severity, 0.10), IMPACT_WEIGHTS.get(e.task_impact, 0.10))
        for e in valid_envelopes
    )

    recurrence = min(1.0, len(valid_envelopes) / 10.0)

    max_prov_weight = max(PROVENANCE_WEIGHTS.get(e.provenance, 0.30) for e in valid_envelopes)
    distinct_provs = {e.provenance for e in valid_envelopes}
    additional_provs = max(0, len(distinct_provs) - 1)
    distinct_sessions = {e.source_session_ref for e in valid_envelopes if e.source_session_ref}
    additional_sessions = max(0, len(distinct_sessions) - 1)

    confidence = min(
        1.0,
        max_prov_weight
        + 0.10 * min(2, additional_provs)
        + 0.05 * min(2, additional_sessions),
    )

    distinct_profiles = {e.source_profile_ref for e in valid_envelopes if e.source_profile_ref}
    distinct_projects = {e.source_project_ref for e in valid_envelopes if e.source_project_ref}
    distinct_ops = {e.operation_key for e in valid_envelopes}

    reuse = min(
        1.0,
        len(distinct_profiles) / 4.0
        + len(distinct_projects) / 8.0
        + len(distinct_ops) / 8.0,
    )

    risk = RISK_TABLE.get("unknown", 0.85)
    expected_cost = COST_TABLE.get("unknown", 0.80)

    raw_score = (
        0.24 * user_intent
        + 0.20 * telos_alignment
        + 0.16 * impact
        + 0.14 * recurrence
        + 0.10 * confidence
        + 0.08 * reuse
        + 0.04 * (1.0 - risk)
        + 0.04 * (1.0 - expected_cost)
    )

    final_score = round(raw_score, 6)

    return OpportunityScore(
        score=final_score,
        user_intent=round(user_intent, 4),
        telos_alignment=round(telos_alignment, 4),
        impact=round(impact, 4),
        recurrence=round(recurrence, 4),
        confidence=round(confidence, 4),
        reuse=round(reuse, 4),
        risk=round(risk, 4),
        expected_cost=round(expected_cost, 4),
    )

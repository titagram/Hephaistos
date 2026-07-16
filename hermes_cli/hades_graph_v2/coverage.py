"""Zero-versus-unknown count construction for graph v2 coverage."""

from __future__ import annotations

from collections.abc import Iterable

from .model import CapabilityStatus, CountKnowledge, Knowledge, ReasonCode


def _non_negative_count(value: int, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def count_knowledge(
    represented: int,
    omitted: int,
    capability_status: CapabilityStatus | str,
    reasons: Iterable[ReasonCode | str] = (),
) -> CountKnowledge:
    """Build the frozen exact/absence/unknown count union.

    A partial or unsupported family is unknown even when it currently
    represents zero records.  The default resource-budget reason is used when
    the caller reports an omission without a more specific counted reason.
    """

    represented = _non_negative_count(represented, "represented")
    omitted = _non_negative_count(omitted, "omitted")
    try:
        status = CapabilityStatus(capability_status)
        normalized_reasons = tuple(ReasonCode(reason) for reason in reasons)
    except ValueError as exc:
        raise ValueError("count knowledge uses only frozen contract enums") from exc

    complete = status in {
        CapabilityStatus.FULL,
        CapabilityStatus.NOT_APPLICABLE,
    }
    if complete and omitted == 0:
        if represented == 0:
            return CountKnowledge(
                represented=0,
                value=0,
                knowledge=Knowledge.ABSENCE_VERIFIED,
                reason=None,
            )
        return CountKnowledge(
            represented=represented,
            value=represented,
            knowledge=Knowledge.EXACT,
            reason=None,
        )

    reason = min(
        normalized_reasons or (ReasonCode.RESOURCE_BUDGET_REACHED,),
        key=lambda item: item.value,
    )
    return CountKnowledge(
        represented=represented,
        value=None,
        knowledge=Knowledge.UNKNOWN,
        reason=reason,
    )


__all__ = ["count_knowledge"]

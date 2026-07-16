"""Deterministic, non-guessing call-target resolution for lifecycle IR.

Resolution intentionally stops at one of three outcomes: one exact target, a
small proven exhaustive candidate set, or an unresolved frontier with required
verification.  It does not perform a depth-limited graph walk and it does not
select an arbitrary implementation from a dynamic call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from hermes_cli.hades_graph_v2.model import CandidateSetKnowledge

from .model import (
    AdapterResult,
    CallSite,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    ExecutableDeclaration,
    TargetExpressionKind,
)


class ResolutionDisposition(str, Enum):
    EXACT = "exact"
    EXHAUSTIVE_CANDIDATES = "exhaustive_candidates"
    UNRESOLVED_FRONTIER = "unresolved_frontier"


@dataclass(frozen=True, slots=True)
class CallResolution:
    """A closed resolution decision for one call site.

    Candidate targets are declaration local keys rather than canonical IDs;
    the graph builder is the sole authority which assigns public node/edge IDs
    and materializes the corresponding uncertainty record.
    """

    call_site_key: str
    disposition: ResolutionDisposition
    target_declaration_keys: tuple[str, ...]
    candidate_set_knowledge: CandidateSetKnowledge
    reason_code: str | None
    required_uncertainty: bool

    def __post_init__(self) -> None:
        if self.disposition is ResolutionDisposition.EXACT:
            if len(self.target_declaration_keys) != 1:
                raise ValueError("exact resolution requires exactly one target")
            if self.candidate_set_knowledge is not CandidateSetKnowledge.NOT_APPLICABLE:
                raise ValueError("exact resolution cannot claim a candidate set")
            if self.reason_code is not None or self.required_uncertainty:
                raise ValueError("exact resolution cannot require uncertainty")
        elif self.disposition is ResolutionDisposition.EXHAUSTIVE_CANDIDATES:
            if not 1 <= len(self.target_declaration_keys) <= 20:
                raise ValueError("exhaustive candidates must contain 1-20 targets")
            if self.candidate_set_knowledge is not CandidateSetKnowledge.COMPLETE:
                raise ValueError("exhaustive candidates require complete knowledge")
            if self.reason_code is not None or not self.required_uncertainty:
                raise ValueError("complete dynamic candidates require uncertainty")
        else:
            if self.target_declaration_keys:
                raise ValueError(
                    "unresolved frontier cannot expose a partial native candidate set"
                )
            if self.candidate_set_knowledge is not CandidateSetKnowledge.NOT_APPLICABLE:
                raise ValueError("unresolved frontier has no native candidate set")
            if self.reason_code is None or not self.required_uncertainty:
                raise ValueError(
                    "unresolved frontier requires a reason and uncertainty"
                )


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """All resolution outcomes and explicit partial coverage events."""

    calls: tuple[CallResolution, ...]
    coverage_events: tuple[CoverageEvent, ...]


_UNRESOLVABLE_KINDS = frozenset({
    TargetExpressionKind.REFLECTION,
    TargetExpressionKind.EVAL,
})
_DYNAMIC_KINDS = frozenset({
    TargetExpressionKind.CALLABLE_VALUE,
    TargetExpressionKind.DYNAMIC_MEMBER,
    TargetExpressionKind.FRAMEWORK_SERVICE,
})
_STATIC_EXACT_KINDS = frozenset({
    TargetExpressionKind.DIRECT_FUNCTION,
    TargetExpressionKind.DIRECT_STATIC_METHOD,
    TargetExpressionKind.DIRECT_INSTANCE_METHOD,
    TargetExpressionKind.CONSTRUCTOR,
})


def _declaration_names(declaration: ExecutableDeclaration) -> frozenset[str]:
    return frozenset(
        value
        for value in (
            declaration.name,
            declaration.qualified_name,
        )
        if value is not None
    )


def _static_exact_targets(
    site: CallSite, declarations: tuple[ExecutableDeclaration, ...]
) -> tuple[ExecutableDeclaration, ...]:
    """Return only target sets supported by an exact static proof.

    Import/namespace/container proof is deliberately absent from this frozen
    IR until the language/framework adapters emit it.  In that situation this
    resolver returns no target rather than pretending a same-name declaration
    is imported or registered.
    """

    if site.target_expression_kind not in _STATIC_EXACT_KINDS:
        return ()
    call_path = site.locator.source_location.path

    # 1. An explicit fully qualified/static target is authoritative.
    if site.fully_qualified_target is not None:
        return tuple(
            declaration
            for declaration in declarations
            if site.fully_qualified_target in _declaration_names(declaration)
        )

    if site.lexical_target is None:
        return ()
    # 1. Same-file lexical declaration takes precedence over a homonym in a
    # different module.  The declaration order is fixed later by local key.
    same_file = tuple(
        declaration
        for declaration in declarations
        if declaration.locator.source_location.path == call_path
        and site.lexical_target in _declaration_names(declaration)
    )
    if same_file:
        return same_file

    # 3. A receiver type can prove an owner/type relationship without relying
    # on a ranking heuristic.  The current IR records owner declaration keys;
    # if no exact owner proof is present, resolution remains unresolved.
    leaf = site.lexical_target.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    if site.receiver_type is not None:
        owner_keys = {
            declaration.local_key
            for declaration in declarations
            if site.receiver_type in _declaration_names(declaration)
        }
        typed = tuple(
            declaration
            for declaration in declarations
            if declaration.owner_declaration_key in owner_keys
            and declaration.name == leaf
        )
        if typed:
            return typed

    return ()


def _dynamic_candidate_targets(
    site: CallSite, declarations: tuple[ExecutableDeclaration, ...]
) -> tuple[ExecutableDeclaration, ...]:
    """Return every in-scope textual candidate without promoting any one.

    This function is only called for the dynamic expression variants.  The
    caller subsequently requires explicit full symbol-resolution coverage
    before representing even a singleton as a complete candidate set.
    """

    reference = site.fully_qualified_target or site.lexical_target
    if reference is None:
        return ()
    direct = tuple(
        declaration
        for declaration in declarations
        if reference in _declaration_names(declaration)
    )
    if direct:
        return direct
    leaf = reference.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    return tuple(
        declaration for declaration in declarations if declaration.name == leaf
    )


def _coverage_event(
    site: CallSite, declaration: ExecutableDeclaration | None, reason: str
) -> CoverageEvent:
    location = site.locator.source_location
    return CoverageEvent(
        language=declaration.language if declaration is not None else "unknown",
        capability=CoverageCapability.CALL_GRAPH,
        outcome=CoverageOutcome.PARTIAL,
        reason_code=reason,
        path=location.path,
        represented_count=0,
        omitted_count=1,
    )


def _closed_world_symbol_resolution_proven(
    results: tuple[AdapterResult, ...], language: str
) -> bool:
    """Require positive full coverage before emitting a complete candidate set."""

    relevant = tuple(
        event
        for result in results
        for event in result.coverage_events
        if event.language == language
        and event.capability is CoverageCapability.SYMBOL_RESOLUTION
    )
    return bool(relevant) and all(
        event.outcome is CoverageOutcome.FULL for event in relevant
    )


def resolve_call_sites(results: Sequence[AdapterResult]) -> ResolutionResult:
    """Resolve calls without breadth-first traversal, depth caps, or guessing.

    The sequence is the explicit closed-world inventory.  A caller can only
    receive a complete candidate set when that supplied inventory proves the
    finite universe and it fits the v2 contract's twenty-target maximum.
    """

    collected = tuple(results)
    for result in collected:
        result.validate()
    declarations = tuple(
        declaration for result in collected for declaration in result.declarations
    )
    declaration_by_key = {
        declaration.local_key: declaration for declaration in declarations
    }
    if len(declaration_by_key) != len(declarations):
        raise ValueError("adapter results must not reuse declaration local keys")

    calls = tuple(
        sorted(
            (site for result in collected for site in result.call_sites),
            key=lambda site: site.local_key,
        )
    )
    if len({site.local_key for site in calls}) != len(calls):
        raise ValueError("adapter results must not reuse call-site local keys")

    decisions: list[CallResolution] = []
    coverage: list[CoverageEvent] = []
    for site in calls:
        caller = declaration_by_key.get(site.caller_declaration_key)
        if site.target_expression_kind in _UNRESOLVABLE_KINDS:
            reason = "reflection_or_generated_code"
            decisions.append(
                CallResolution(
                    site.local_key,
                    ResolutionDisposition.UNRESOLVED_FRONTIER,
                    (),
                    CandidateSetKnowledge.NOT_APPLICABLE,
                    reason,
                    True,
                )
            )
            coverage.append(_coverage_event(site, caller, reason))
            continue

        if site.target_expression_kind in _DYNAMIC_KINDS:
            targets = tuple(
                sorted(
                    _dynamic_candidate_targets(site, declarations),
                    key=lambda item: item.local_key,
                )
            )
            if (
                1 <= len(targets) <= 20
                and caller is not None
                and _closed_world_symbol_resolution_proven(collected, caller.language)
            ):
                decisions.append(
                    CallResolution(
                        site.local_key,
                        ResolutionDisposition.EXHAUSTIVE_CANDIDATES,
                        tuple(target.local_key for target in targets),
                        CandidateSetKnowledge.COMPLETE,
                        None,
                        True,
                    )
                )
                continue
            reason = "dynamic_dispatch"
        else:
            targets = tuple(
                sorted(
                    _static_exact_targets(site, declarations),
                    key=lambda item: item.local_key,
                )
            )
            reason = "call_target_unresolved"
        if len(targets) == 1 and site.target_expression_kind in _STATIC_EXACT_KINDS:
            decisions.append(
                CallResolution(
                    site.local_key,
                    ResolutionDisposition.EXACT,
                    (targets[0].local_key,),
                    CandidateSetKnowledge.NOT_APPLICABLE,
                    None,
                    False,
                )
            )
            continue
        if (
            1 <= len(targets) <= 20
            and caller is not None
            and _closed_world_symbol_resolution_proven(collected, caller.language)
        ):
            decisions.append(
                CallResolution(
                    site.local_key,
                    ResolutionDisposition.EXHAUSTIVE_CANDIDATES,
                    tuple(target.local_key for target in targets),
                    CandidateSetKnowledge.COMPLETE,
                    None,
                    True,
                )
            )
            continue
        decisions.append(
            CallResolution(
                site.local_key,
                ResolutionDisposition.UNRESOLVED_FRONTIER,
                (),
                CandidateSetKnowledge.NOT_APPLICABLE,
                reason,
                True,
            )
        )
        coverage.append(_coverage_event(site, caller, reason))

    return ResolutionResult(
        tuple(decisions),
        tuple(
            sorted(
                coverage,
                key=lambda event: (
                    event.language,
                    event.reason_code or "",
                    event.path or "",
                ),
            )
        ),
    )


__all__ = [
    "CallResolution",
    "ResolutionDisposition",
    "ResolutionResult",
    "resolve_call_sites",
]

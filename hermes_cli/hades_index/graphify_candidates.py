"""Optional, strictly non-authoritative Graphify candidate attachment.

Graphify may add bounded inferred hints to a native unresolved assertion.  It
cannot create the assertion, turn a hint set into a complete set, or change
coverage/completeness.  Public IDs and artifact-level uncertainty ownership
remain the graph builder's responsibility.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from hermes_cli.hades_graph_v2.model import (
    CandidateSetKnowledge,
    EdgeFlow,
    EvidenceOrigin,
    NodeKind,
    Relation,
    ResolutionKind,
)

from .lifecycle.model import (
    AdapterResult,
    CallSiteSubjectIR,
    EdgeFactIR,
    EdgeSubjectIR,
    IREvidence,
    LocalNodeTarget,
    StructureKind,
    UnresolvedFact,
    local_record_key,
)


_ALLOWED_KINDS: dict[ResolutionKind, frozenset[NodeKind]] = {
    ResolutionKind.CALL_TARGET: frozenset({
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.CONTROLLER,
        NodeKind.SERVICE,
        NodeKind.DOMAIN,
        NodeKind.REPOSITORY,
        NodeKind.MIDDLEWARE,
        NodeKind.GUARD,
        NodeKind.AUTHORIZATION,
        NodeKind.VALIDATOR,
        NodeKind.BINDING,
        NodeKind.LISTENER,
        NodeKind.JOB,
    }),
    ResolutionKind.ENTRYPOINT_HANDLER: frozenset({
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.CONTROLLER,
        NodeKind.SERVICE,
        NodeKind.LISTENER,
        NodeKind.JOB,
    }),
    ResolutionKind.ASYNC_TARGET: frozenset({
        NodeKind.EVENT,
        NodeKind.LISTENER,
        NodeKind.JOB,
        NodeKind.QUEUE,
        NodeKind.ASYNC_BOUNDARY,
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.SERVICE,
    }),
    ResolutionKind.EXCEPTION_TARGET: frozenset({
        NodeKind.EXCEPTION,
        NodeKind.LISTENER,
        NodeKind.FRAMEWORK_BOUNDARY,
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.SERVICE,
    }),
    ResolutionKind.FRAMEWORK_TARGET: frozenset({
        NodeKind.MIDDLEWARE,
        NodeKind.GUARD,
        NodeKind.AUTHORIZATION,
        NodeKind.VALIDATOR,
        NodeKind.BINDING,
        NodeKind.CONTROLLER,
        NodeKind.FRAMEWORK_BOUNDARY,
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.SERVICE,
    }),
    ResolutionKind.EXTERNAL_TARGET: frozenset({
        NodeKind.INTEGRATION,
        NodeKind.EXTERNAL_BOUNDARY,
        NodeKind.MODEL,
        NodeKind.REPOSITORY,
        NodeKind.TABLE,
        NodeKind.QUERY,
        NodeKind.CACHE,
        NodeKind.STORAGE,
        NodeKind.QUEUE,
    }),
}


def _call_site_structure_key(result: AdapterResult, call_site_key: str) -> str | None:
    site = next(
        (item for item in result.call_sites if item.local_key == call_site_key), None
    )
    if site is None:
        return None
    matching = tuple(
        item.local_key
        for item in result.structures
        if item.kind is StructureKind.CALL_SITE
        and item.owner_declaration_key == site.caller_declaration_key
        and item.structural_path == site.locator.structural_path
        and item.ordinal == site.locator.ordinal
    )
    return matching[0] if len(matching) == 1 else None


def _native_subject_edge(
    result: AdapterResult, fact: UnresolvedFact
) -> EdgeFactIR | None:
    if type(fact.subject) is EdgeSubjectIR:
        return next(
            (
                edge
                for edge in result.edge_facts
                if edge.local_key == fact.subject.local_key
            ),
            None,
        )
    if type(fact.subject) is not CallSiteSubjectIR:
        return None
    structure_key = _call_site_structure_key(result, fact.subject.local_key)
    if structure_key is None:
        return None
    matches = tuple(
        edge
        for edge in result.edge_facts
        if edge.relation is Relation.INVOKES and edge.call_site_key == structure_key
    )
    return matches[0] if len(matches) == 1 else None


def _mapping_values(
    candidates: Mapping[str, Sequence[str]], fact: UnresolvedFact
) -> Sequence[str] | None:
    """Accept the stable fact key, or its exact pre-existing subject key."""

    if fact.local_key in candidates:
        return candidates[fact.local_key]
    subject_key = fact.subject.local_key
    return candidates.get(subject_key)


def attach_graphify_hints(
    result: AdapterResult,
    candidates: Mapping[str, Sequence[str]],
    *,
    enabled: bool,
) -> AdapterResult:
    """Attach at most twenty existing inferred hint targets per native fact.

    ``enabled=False`` is deliberately an identity operation.  This keeps the
    default configuration incapable of changing the graph, including ordering
    or diagnostics.  Candidate values are adapter local node keys at this
    producer boundary; the builder later maps them to canonical public IDs.
    """

    if not enabled or not candidates:
        return result
    result.validate()
    node_kinds = {
        declaration.local_key: declaration.declaration_kind
        for declaration in result.declarations
    }
    by_key = {fact.local_key: fact for fact in result.unresolved_facts}
    added_edges: list[EdgeFactIR] = []
    replacements: dict[str, UnresolvedFact] = {}

    for fact in result.unresolved_facts:
        raw_values = _mapping_values(candidates, fact)
        if raw_values is None:
            continue
        subject_edge = _native_subject_edge(result, fact)
        if subject_edge is None:
            # No native v2 subject means only a local caller diagnostic is
            # permissible.  Do not manufacture graph topology or uncertainty.
            continue
        allowed = _ALLOWED_KINDS[fact.resolution_kind]
        existing_targets = set(fact.candidate_target_local_keys)
        existing_edge_keys = set(fact.candidate_edge_local_keys)
        capacity = min(20 - len(existing_targets), 20 - len(existing_edge_keys))
        if capacity <= 0:
            continue
        target_keys = tuple(
            sorted({
                key
                for key in raw_values
                if isinstance(key, str)
                and key not in existing_targets
                and node_kinds.get(key) in allowed
            })
        )[:capacity]
        if not target_keys:
            continue
        language = next(
            (
                declaration.language
                for declaration in result.declarations
                if declaration.local_key == subject_edge.source_node_local_key
            ),
            "unknown",
        )
        for ordinal, target_key in enumerate(target_keys):
            if target_key in existing_targets:
                continue
            locator = subject_edge.locator
            local_key = local_record_key(
                language,
                locator.source_location.path,
                "graphify_hint_edge",
                locator.kind,
                locator.structural_path
                if hasattr(locator, "structural_path")
                else locator.structural_pointer,
                ordinal,
            )
            # A collision cannot be repaired by choosing a different semantic
            # identity.  Skip it rather than overwriting an existing fact.
            if any(edge.local_key == local_key for edge in result.edge_facts) or any(
                edge.local_key == local_key for edge in added_edges
            ):
                continue
            edge = EdgeFactIR(
                local_key=local_key,
                source_node_local_key=subject_edge.source_node_local_key,
                target=LocalNodeTarget(target_key),
                relation=subject_edge.relation,
                flow=subject_edge.flow,
                condition=subject_edge.condition,
                branch_group_key=subject_edge.branch_group_key,
                call_site_key=subject_edge.call_site_key,
                exception_scope_key=subject_edge.exception_scope_key,
                order=subject_edge.order,
                locator=locator,
                evidence=IREvidence(
                    origin=EvidenceOrigin.INFERRED,
                    extractor="graphify.candidates",
                    locator=locator,
                    inference_rule="graphify_candidate",
                ),
            )
            added_edges.append(edge)
            existing_targets.add(target_key)
            existing_edge_keys.add(local_key)
        if existing_targets and existing_edge_keys:
            replacements[fact.local_key] = replace(
                fact,
                candidate_set_knowledge=CandidateSetKnowledge.INCOMPLETE,
                candidate_target_local_keys=tuple(sorted(existing_targets)),
                candidate_edge_local_keys=tuple(sorted(existing_edge_keys)),
            )

    if not replacements:
        return result
    enriched = replace(
        result,
        edge_facts=tuple(
            sorted((*result.edge_facts, *added_edges), key=lambda edge: edge.local_key)
        ),
        unresolved_facts=tuple(
            sorted(
                (
                    replacements.get(fact.local_key, fact)
                    for fact in result.unresolved_facts
                ),
                key=lambda fact: fact.local_key,
            )
        ),
    )
    enriched.validate()
    return enriched


__all__ = ["attach_graphify_hints"]

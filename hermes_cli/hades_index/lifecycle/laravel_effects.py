"""Native Tree-sitter facts to conservative Laravel Graph-v2 effects.

This module never reads source files.  It consumes the bounded, source-free
``StructuralCall`` facts produced by the required parser and only recognises
facades/classes whose ownership is statically established.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from hermes_cli.hades_graph_v2.model import (
    CandidateSetKnowledge,
    EdgeFlow,
    EvidenceOrigin,
    NodeKind,
    Priority,
    Relation,
    ResolutionKind,
)
from hermes_cli.hades_index.tree_sitter_adapter import StructuralCall, SyntaxIR

from .model import (
    AdapterResult,
    AstLocatorIR,
    BlockEffectSource,
    BoundaryTarget,
    CallSiteEffectSource,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    Effect,
    EffectKind,
    EdgeFactIR,
    EdgeSubjectIR,
    ExtractionContext,
    FrameworkBoundaryDescriptor,
    IREvidence,
    UnresolvedFact,
    SourceLocationIR,
    local_record_key,
)


_FACADE_NAMES = frozenset({
    "DB",
    "Cache",
    "Storage",
    "Http",
    "Mail",
    "Notification",
    "Event",
    "Bus",
    "Queue",
})
_READ_METHODS = frozenset({
    "select",
    "selectOne",
    "scalar",
    "get",
    "first",
    "find",
    "value",
    "pluck",
    "count",
    "exists",
    "all",
})
_WRITE_METHODS = frozenset({
    "insert",
    "update",
    "delete",
    "upsert",
    "create",
    "save",
    "increment",
    "decrement",
    "statement",
})
_CACHE_READ = frozenset({"get", "remember", "has"})
_CACHE_WRITE = frozenset({
    "put",
    "add",
    "forever",
    "forget",
    "flush",
    "increment",
    "decrement",
})
_STORAGE_READ = frozenset({"get", "readStream", "exists", "download", "url"})
_STORAGE_WRITE = frozenset({"put", "writeStream", "delete", "copy", "move"})
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "send", "request"})
_MAIL_METHODS = frozenset({"send", "queue", "sendNow"})
_QUEUE_METHODS = frozenset({"push", "later", "bulk"})


def _facade_aliases(syntax: SyntaxIR) -> dict[str, str]:
    aliases = {name: name for name in _FACADE_NAMES}
    for imported in syntax.imports:
        short_name = imported.target.rsplit("\\", 1)[-1]
        if short_name not in _FACADE_NAMES:
            continue
        aliases[imported.alias or short_name] = short_name
        aliases[imported.target] = short_name
        aliases[f"\\{imported.target}"] = short_name
    return aliases


def _facade_for(receiver: str | None, aliases: dict[str, str]) -> str | None:
    if receiver is None:
        return None
    return aliases.get(receiver) or aliases.get(
        receiver.lstrip("\\").rsplit("\\", 1)[-1]
    )


def _literal(call: StructuralCall, index: int = 0) -> str | None:
    if index >= len(call.arguments):
        return None
    argument = call.arguments[index]
    return argument.value if argument.kind == "literal" else None


def _class_reference(call: StructuralCall) -> str | None:
    for argument in call.arguments:
        if argument.kind == "class_reference":
            return argument.value
    return None


def _short_name(value: str | None) -> str | None:
    return value.rsplit("\\", 1)[-1] if value else None


def _locator(
    context: ExtractionContext,
    syntax: SyntaxIR,
    call: StructuralCall,
    ordinal: int,
) -> AstLocatorIR:
    digest = next(
        item.file_sha256 for item in context.inventory_files if item.path == syntax.path
    )
    return AstLocatorIR(
        SourceLocationIR(syntax.path, call.line, call.line, digest),
        call.structural_path,
        ordinal,
    )


def _known_model_names(result: AdapterResult) -> frozenset[str]:
    return frozenset(
        item.name for item in result.data_nodes if item.kind is NodeKind.MODEL
    )


def _known_class_names(syntax: Sequence[SyntaxIR], directory: str) -> frozenset[str]:
    return frozenset(
        symbol.name.rsplit(".", 1)[-1]
        for item in syntax
        for symbol in item.symbols
        if symbol.kind == "class" and item.path.startswith(directory)
    )


def _effect_for_call(
    call: StructuralCall,
    *,
    aliases: dict[str, str],
    table_by_line: dict[int, str],
    model_names: frozenset[str],
    job_names: frozenset[str],
    event_names: frozenset[str],
) -> tuple[EffectKind, str, str | None, str | None] | None:
    """Recognise only APIs whose receiver and operation are source-proven."""

    member = call.member
    receiver = _facade_for(call.receiver, aliases)
    if member is None:
        return None
    if receiver == "DB" and member in _READ_METHODS | _WRITE_METHODS:
        table = table_by_line.get(call.line)
        if table is None:
            return None
        kind = (
            EffectKind.DATA_READ if member in _READ_METHODS else EffectKind.DATA_WRITE
        )
        return kind, member, table, None
    if call.call_form == "scoped" and call.receiver in model_names:
        if member in _READ_METHODS:
            return EffectKind.DATA_READ, member, call.receiver, None
        if member in _WRITE_METHODS:
            return EffectKind.DATA_WRITE, member, call.receiver, None
    if receiver == "Cache":
        resource = _literal(call)
        if resource is None:
            return None
        if member in _CACHE_READ:
            return EffectKind.CACHE_READ, member, resource, None
        if member in _CACHE_WRITE:
            return EffectKind.CACHE_WRITE, member, resource, None
    if receiver == "Storage":
        resource = _literal(call)
        if resource is None:
            return None
        if member in _STORAGE_READ:
            return EffectKind.STORAGE_READ, member, resource, None
        if member in _STORAGE_WRITE:
            return EffectKind.STORAGE_WRITE, member, resource, None
    if receiver == "Http" and member in _HTTP_METHODS:
        endpoint = _literal(call, 1 if member in {"send", "request"} else 0)
        if endpoint is None or not endpoint.startswith(("http://", "https://")):
            return EffectKind.EXTERNAL_CALL, member, "http", "http"
        return EffectKind.EXTERNAL_CALL, member, endpoint, "http"
    if receiver in {"Mail", "Notification"} and member in _MAIL_METHODS:
        integration = _class_reference(call)
        if integration is not None:
            return EffectKind.EXTERNAL_CALL, member, integration, "mail"
    if (receiver == "Event" and member == "dispatch") or (
        receiver is None and member == "event"
    ):
        event = _class_reference(call)
        if _short_name(event) in event_names:
            return EffectKind.EVENT_EMIT, member, _short_name(event), None
    if receiver in {"Bus"} and member == "dispatch":
        job = _class_reference(call)
        if _short_name(job) in job_names:
            return EffectKind.JOB_DISPATCH, member, _short_name(job), None
    if receiver is None and member == "dispatch":
        job = _class_reference(call)
        if _short_name(job) in job_names:
            return EffectKind.JOB_DISPATCH, member, _short_name(job), None
    if (
        call.call_form == "scoped"
        and call.receiver in job_names
        and member == "dispatch"
    ):
        return EffectKind.JOB_DISPATCH, member, call.receiver, None
    if receiver == "Queue" and member in _QUEUE_METHODS:
        job = _short_name(_class_reference(call))
        if job in job_names:
            return EffectKind.QUEUE_DISPATCH, member, job, None
    return None


def _potential_effect_kind(
    call: StructuralCall,
    *,
    aliases: dict[str, str],
    model_names: frozenset[str],
    job_names: frozenset[str],
    event_names: frozenset[str],
) -> EffectKind | None:
    """Classify a recognised API even when its public target is dynamic."""

    member = call.member
    receiver = _facade_for(call.receiver, aliases)
    if member is None:
        return None
    if receiver == "DB" or (
        call.call_form == "scoped" and call.receiver in model_names
    ):
        if member in _READ_METHODS:
            return EffectKind.DATA_READ
        if member in _WRITE_METHODS:
            return EffectKind.DATA_WRITE
    if receiver == "Cache":
        if member in _CACHE_READ:
            return EffectKind.CACHE_READ
        if member in _CACHE_WRITE:
            return EffectKind.CACHE_WRITE
    if receiver == "Storage":
        if member in _STORAGE_READ:
            return EffectKind.STORAGE_READ
        if member in _STORAGE_WRITE:
            return EffectKind.STORAGE_WRITE
    if receiver == "Http" and member in _HTTP_METHODS:
        return EffectKind.EXTERNAL_CALL
    if receiver in {"Mail", "Notification"} and member in _MAIL_METHODS:
        return EffectKind.EXTERNAL_CALL
    if (receiver == "Event" and member == "dispatch") or (
        receiver is None and member == "event"
    ):
        return EffectKind.EVENT_EMIT
    if (receiver == "Bus" and member == "dispatch") or (
        receiver is None and member == "dispatch"
    ):
        return EffectKind.JOB_DISPATCH
    if (
        call.call_form == "scoped"
        and call.receiver in job_names
        and member == "dispatch"
    ):
        return EffectKind.JOB_DISPATCH
    if receiver == "Queue" and member in _QUEUE_METHODS:
        return EffectKind.QUEUE_DISPATCH
    return None


def _effect_relation(kind: EffectKind) -> tuple[Relation, EdgeFlow]:
    if kind in {EffectKind.DATA_READ, EffectKind.CACHE_READ, EffectKind.STORAGE_READ}:
        return Relation.READS, EdgeFlow.ALWAYS
    if kind in {
        EffectKind.DATA_WRITE,
        EffectKind.CACHE_WRITE,
        EffectKind.STORAGE_WRITE,
    }:
        return Relation.WRITES, EdgeFlow.ALWAYS
    if kind is EffectKind.EXTERNAL_CALL:
        return Relation.CALLS_EXTERNAL, EdgeFlow.ALWAYS
    if kind is EffectKind.EVENT_EMIT:
        return Relation.EMITS, EdgeFlow.ASYNC
    return Relation.DISPATCHES, EdgeFlow.ASYNC


def apply_laravel_effects(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    result: AdapterResult,
) -> AdapterResult:
    """Append native Laravel effects to structural IR without raw source fallback."""

    if not any(
        item.name == "laravel" and item.language == "php"
        for item in context.detected_frameworks
    ):
        return result
    declarations = {
        (
            item.language,
            item.locator.source_location.path,
            item.qualified_name or item.name,
        ): item
        for item in result.declarations
    }
    effects: list[Effect] = list(result.effects)
    edges: list[EdgeFactIR] = list(result.edge_facts)
    unresolved: list[UnresolvedFact] = list(result.unresolved_facts)
    coverage: list[CoverageEvent] = list(result.coverage_events)
    model_names = _known_model_names(result)
    job_names = _known_class_names(syntax, "app/Jobs/")
    event_names = _known_class_names(syntax, "app/Events/")
    for item in sorted(
        (row for row in syntax if row.language == "php"), key=lambda row: row.path
    ):
        aliases = _facade_aliases(item)
        table_by_caller_line: dict[tuple[str, int], str] = {}
        for call in item.calls:
            if _facade_for(call.receiver, aliases) == "DB" and call.member == "table":
                table = _literal(call)
                if table is not None:
                    table_by_caller_line[(call.caller, call.line)] = table
        emitted = 0
        omitted = 0
        call_sites = {
            (
                site.locator.source_location.path,
                site.locator.structural_path,
            ): site.local_key
            for site in result.call_sites
        }
        for ordinal, call in enumerate(item.calls):
            declaration = declarations.get(("php", item.path, call.caller))
            if declaration is None:
                continue
            effect = _effect_for_call(
                call,
                aliases=aliases,
                table_by_line={
                    line: table
                    for (caller, line), table in table_by_caller_line.items()
                    if caller == call.caller
                },
                model_names=model_names,
                job_names=job_names,
                event_names=event_names,
            )
            if effect is None:
                potential = _potential_effect_kind(
                    call,
                    aliases=aliases,
                    model_names=model_names,
                    job_names=job_names,
                    event_names=event_names,
                )
                if potential is None:
                    continue
                locator = _locator(context, item, call, ordinal)
                if potential in {
                    EffectKind.EVENT_EMIT,
                    EffectKind.JOB_DISPATCH,
                    EffectKind.QUEUE_DISPATCH,
                }:
                    # The frozen unresolved-edge matrix intentionally has no
                    # async effect target branch.  Keep coverage explicit
                    # rather than inventing an incompatible target edge.
                    omitted += 1
                    continue
                edge_key = local_record_key(
                    "php",
                    item.path,
                    "laravel_effect_unresolved_edge",
                    "ast",
                    call.structural_path,
                    ordinal,
                )
                relation, flow = _effect_relation(potential)
                evidence = IREvidence(
                    EvidenceOrigin.UNRESOLVED,
                    "laravel.effects-v2",
                    locator,
                    None,
                )
                edges.append(
                    EdgeFactIR(
                        edge_key,
                        declaration.entry_block_key,
                        BoundaryTarget(
                            FrameworkBoundaryDescriptor(
                                "laravel", "effect_target", None, locator, evidence
                            )
                        ),
                        relation,
                        flow,
                        None,
                        None,
                        None,
                        None,
                        ordinal,
                        locator,
                        evidence,
                    )
                )
                unresolved.append(
                    UnresolvedFact(
                        local_record_key(
                            "php",
                            item.path,
                            "laravel_effect_unresolved",
                            "ast",
                            call.structural_path,
                            ordinal,
                        ),
                        EdgeSubjectIR(edge_key),
                        ResolutionKind.EXTERNAL_TARGET,
                        CandidateSetKnowledge.NOT_APPLICABLE,
                        "laravel_effect_resource_unresolved",
                        "Which public resource is reached by this recognised Laravel API call?",
                        ("inspect_static_call_arguments",),
                        (locator,),
                        (),
                        (),
                        Priority.NORMAL,
                        "The API operation is known, but its public target is not statically verified.",
                    )
                )
                omitted += 1
                continue
            kind, operation, resource, protocol = effect
            locator = _locator(context, item, call, ordinal)
            effects.append(
                Effect(
                    local_record_key(
                        "php",
                        item.path,
                        "laravel_effect",
                        "ast",
                        call.structural_path,
                        ordinal,
                    ),
                    (
                        CallSiteEffectSource(
                            call_sites[(item.path, call.structural_path)]
                        )
                        if (item.path, call.structural_path) in call_sites
                        else BlockEffectSource(declaration.entry_block_key)
                    ),
                    kind,
                    operation,
                    resource,
                    protocol,
                    locator,
                )
            )
            emitted += 1
        if emitted or omitted:
            coverage.append(
                CoverageEvent(
                    "php",
                    CoverageCapability.DATA_ACCESS,
                    CoverageOutcome.FULL if not omitted else CoverageOutcome.PARTIAL,
                    None if not omitted else "laravel_effect_resource_unresolved",
                    item.path,
                    emitted,
                    omitted,
                )
            )
    merged = replace(
        result,
        effects=tuple(sorted(effects, key=lambda row: row.local_key)),
        edge_facts=tuple(sorted(edges, key=lambda row: row.local_key)),
        unresolved_facts=tuple(sorted(unresolved, key=lambda row: row.local_key)),
        coverage_events=tuple(
            sorted(
                coverage,
                key=lambda row: (
                    row.language,
                    row.capability.value,
                    row.outcome.value,
                    row.reason_code or "",
                    row.path or "",
                ),
            )
        ),
    )
    merged.validate()
    return merged


__all__ = ["apply_laravel_effects"]
